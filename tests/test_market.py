import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from guild.market import AgentCard, Bid, Job, Market, NoEligibleBid, reputation

D = Decimal


def agents():
    return {
        "sourcer": AgentCard("sourcer", "Sourcer", "sourcer@owner.example", {"source"}, prior=0.9,
                             passes=12, fails=0),
        "cheapbot": AgentCard("cheapbot", "CheapBot", "cheap@owner.example", {"source"}, prior=0.6),
        "doer": AgentCard("doer", "Doer", "doer@owner.example", {"email"}, prior=0.9, passes=8),
    }


def job(**kw):
    base = dict(id="j1", title="Find 6 notebooks", skill="source",
                acceptance=("6 items", "each under $5"), max_price=D("1.00"))
    base.update(kw)
    return Job(**base)


class ReputationTests(unittest.TestCase):
    def test_new_agent_starts_at_prior(self):
        self.assertAlmostEqual(reputation(0, 0, 0.6), 0.6)

    def test_failure_lowers_and_pass_raises(self):
        self.assertLess(reputation(0, 1, 0.6), 0.6)
        self.assertGreater(reputation(1, 0, 0.6), 0.6)

    def test_bounded(self):
        for p, f in [(0, 100), (100, 0)]:
            self.assertTrue(0.0 <= reputation(p, f, 0.6) <= 1.0)


class AuctionTests(unittest.TestCase):
    def setUp(self):
        self.m = Market(agents(), budget=D("10.00"))

    def bids(self, cheap=D("0.10"), good=D("0.40")):
        return [Bid("cheapbot", "j1", cheap, 30), Bid("sourcer", "j1", good, 120)]

    def test_unknown_cheap_agent_can_win_once(self):
        self.assertEqual(self.m.award(job(), self.bids()).agent_id, "cheapbot")

    def test_after_a_failure_the_cheap_agent_loses(self):
        self.m.record_review("cheapbot", passed=False)
        self.assertEqual(self.m.award(job(), self.bids()).agent_id, "sourcer")

    def test_bids_from_agents_without_the_skill_are_ignored(self):
        winner = self.m.award(job(), [Bid("doer", "j1", D("0.01"), 1)] + self.bids())
        self.assertNotEqual(winner.agent_id, "doer")

    def test_bids_above_job_cap_are_ignored(self):
        winner = self.m.award(job(max_price=D("0.20")), self.bids())
        self.assertEqual(winner.agent_id, "cheapbot")

    def test_bids_above_remaining_budget_are_ignored(self):
        m = Market(agents(), budget=D("0.30"))
        m.record_review("cheapbot", passed=False)
        with self.assertRaises(NoEligibleBid):
            m.award(job(), [Bid("sourcer", "j1", D("0.40"), 10)])

    def test_unknown_agent_or_wrong_job_or_bad_price_ignored(self):
        bad = [Bid("ghost", "j1", D("0.01"), 1), Bid("sourcer", "j2", D("0.01"), 1),
               Bid("sourcer", "j1", D("0"), 1), Bid("sourcer", "j1", D("-1"), 1)]
        with self.assertRaises(NoEligibleBid):
            self.m.award(job(), bad)

    def test_tie_breaks_deterministically(self):
        a = agents()
        a["twin"] = AgentCard("twin", "Twin", "t@x", {"source"}, prior=0.9, passes=12)
        m = Market(a, budget=D("10"))
        bids = [Bid("twin", "j1", D("0.40"), 120), Bid("sourcer", "j1", D("0.40"), 120)]
        self.assertEqual(m.award(job(), bids).agent_id, m.award(job(), list(reversed(bids))).agent_id)

    def test_faster_eta_wins_a_value_tie(self):
        a = agents()
        a["twin"] = AgentCard("twin", "Twin", "t@x", {"source"}, prior=0.9, passes=12)
        m = Market(a, budget=D("10"))
        bids = [Bid("twin", "j1", D("0.40"), 60), Bid("sourcer", "j1", D("0.40"), 120)]
        self.assertEqual(m.award(job(), bids).agent_id, "twin")


class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.m = Market(agents(), budget=D("1.00"))

    def test_pass_pays_owner_and_spends_budget(self):
        win = self.m.award(job(), [Bid("sourcer", "j1", D("0.40"), 10)])
        self.assertEqual(self.m.settle(job(), win, "pass"), "paid")
        self.assertEqual(self.m.spent, D("0.40"))
        self.assertEqual(self.m.owed(), {"sourcer@owner.example": D("0.40")})

    def test_fail_pays_nothing_and_lowers_reputation(self):
        before = self.m.agents["cheapbot"].reputation
        win = self.m.award(job(), [Bid("cheapbot", "j1", D("0.10"), 10)])
        self.assertEqual(self.m.settle(job(), win, "fail"), "unpaid")
        self.assertEqual(self.m.spent, D("0"))
        self.assertLess(self.m.agents["cheapbot"].reputation, before)

    def test_hold_or_unknown_verdict_pays_nothing_and_keeps_reputation(self):
        for verdict in ("hold", "error", "APPROVE", None):
            m = Market(agents(), budget=D("1.00"))
            before = m.agents["sourcer"].reputation
            win = m.award(job(), [Bid("sourcer", "j1", D("0.40"), 10)])
            self.assertEqual(m.settle(job(), win, verdict), "held", verdict)
            self.assertEqual(m.spent, D("0"))
            self.assertEqual(m.agents["sourcer"].reputation, before)

    def test_job_cannot_be_paid_twice(self):
        win = self.m.award(job(), [Bid("sourcer", "j1", D("0.40"), 10)])
        self.m.settle(job(), win, "pass")
        with self.assertRaises(ValueError):
            self.m.settle(job(), win, "pass")

    def test_failed_job_can_go_to_another_agent_but_not_the_same_one(self):
        cheap = self.m.award(job(), [Bid("cheapbot", "j1", D("0.10"), 10)])
        self.assertEqual(self.m.settle(job(), cheap, "fail"), "unpaid")
        with self.assertRaises(ValueError):
            self.m.settle(job(), cheap, "fail")  # no double penalty
        good = self.m.award(job(), [Bid("sourcer", "j1", D("0.40"), 10)])
        self.assertEqual(self.m.settle(job(), good, "pass"), "paid")

    def test_settlement_never_exceeds_budget(self):
        m = Market(agents(), budget=D("0.50"))
        w1 = m.award(job(), [Bid("sourcer", "j1", D("0.40"), 10)])
        m.settle(job(), w1, "pass")
        j2 = job(id="j2")
        with self.assertRaises(NoEligibleBid):
            m.award(j2, [Bid("sourcer", "j2", D("0.40"), 10)])

    def test_owed_groups_by_owner(self):
        m = Market(agents(), budget=D("5"))
        for jid in ("a", "b"):
            j = job(id=jid)
            m.settle(j, m.award(j, [Bid("sourcer", jid, D("0.40"), 10)]), "pass")
        self.assertEqual(m.owed(), {"sourcer@owner.example": D("0.80")})


if __name__ == "__main__":
    unittest.main()
