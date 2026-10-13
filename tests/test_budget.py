import json
import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from guild import budget, shops
from guild.bounty import Claim, Offer, Verdict
from guild.wallet import Envelope, Policy, Wallet, WalletError
from tests.test_wallet_api import Block, FakeClaude, app, open_wallet, turn

PAYEE = {"email_address": "seller@business.example.com"}
DEMO = {"budget": "400", "max_per_purchase": "350", "approve_above": "250",
        "envelopes": {"shopping": {"limit": "340", "max_per_purchase": "340", "approve_above": "250"},
                      "marketing": {"limit": "60"}}}


def ok(price, url):
    claim = Claim("c", shops.DEMO_WANT.id, "agent", url, Decimal(price), "USD", 0.0)
    return Verdict(claim, True, ["ok"], Offer(name="Sony WH-1000XM5", price=Decimal(price), currency="USD",
                                              availability="instock"), {})


def wallet(budget_="400", shopping="300", marketing="100", ask="350"):
    return Wallet("w", "a", Policy(Decimal(budget_), Decimal("350"), Decimal(ask), shops.DEMO_WANT, {
        "shopping": Envelope(Decimal(shopping), Decimal(shopping), Decimal(ask) if Decimal(ask) <= Decimal(shopping)
                             else Decimal(shopping)),
        "marketing": Envelope(Decimal(marketing), Decimal(marketing), Decimal(marketing))}), Decimal(budget_))


def buy(w, price, n, category="shopping"):
    p = w.decide(f"https://a.example/{n}", f"thing {n}", ok(price, f"https://a.example/{n}"), payee=PAYEE,
                 category=category)
    w.record_payment(p.id, order_id=f"B{n}")
    return p


class NumbersTests(unittest.TestCase):
    def test_numbers_pace_and_a_spike(self):
        w = wallet()
        for n, price in enumerate(("10", "10", "50"), 1):
            buy(w, price, n)
        a = budget.analyze(w, elapsed_s=600)
        n = a["numbers"]
        self.assertEqual((n["loaded"], n["spent"], n["available"], n["paid_count"], n["average_payment"]),
                         ("400.00", "70.00", "330.00", 3, "23.33"))
        self.assertEqual((a["pace"]["minutes_open"], a["pace"]["per_minute"], a["pace"]["lasts_minutes"]),
                         (10.0, "7.00", 47.1))
        self.assertTrue(a["pace"]["known"])
        self.assertEqual([x["key"] for x in a["watch"]], ["spike:w-p3"])
        self.assertIn("more than twice your usual payment ($10.00)", a["watch"][0]["text"])
        self.assertEqual(n["by_limit"][0], {"name": "shopping", "spent": "70.00", "limit": "300.00", "used_pct": 23})

    def test_nearly_full_limit_and_a_proposal_that_stays_within_the_budget(self):
        w = wallet(shopping="300", marketing="100")
        buy(w, "50", 1, "marketing")
        w.decide("https://a.example/2", "ad", ok("35", "https://a.example/2"), payee=PAYEE, category="marketing")
        a = budget.analyze(w, 60)
        self.assertEqual([x["key"] for x in a["watch"]], ["limit:marketing"])   # 85 of 100 used
        self.assertIn("85% used", a["watch"][0]["text"])
        (prop,) = a["proposals"]
        self.assertEqual(prop["limits"], {"marketing": "150.00", "shopping": "250.00"})
        w.set_limits(prop["limits"])                                            # apply it
        self.assertEqual((w.policy.envelopes["marketing"].limit, w.policy.envelopes["shopping"].limit),
                         (Decimal("150.00"), Decimal("250.00")))
        self.assertEqual(budget.analyze(w, 60)["proposals"], [])                  # nothing nearly full now

    def test_blocked_attempts_name_the_most_common_failure(self):
        w = wallet()
        for n in range(3):
            claim = Claim("c", shops.DEMO_WANT.id, "agent", f"https://x.example/{n}", Decimal("20"), "USD", 0.0)
            w.decide(claim.url, "x", Verdict(claim, False, ["no independent source lists this seller"], None,
                                             {"seller": False, "name": True}), payee=PAYEE)
        a = budget.analyze(w, 60)
        (alert,) = [x for x in a["watch"] if x["key"] == "blocked"]
        self.assertEqual(alert["level"], "alert")
        self.assertIn("3 purchases that failed Guild's checks ($60.00)", alert["text"])
        self.assertIn("the seller isn't listed anywhere independent", alert["text"])
        # four different failures: no single "most often"
        w2 = wallet()
        for n, check in enumerate(("seller", "sanity", "claimed_price", "name")):
            claim = Claim("c", shops.DEMO_WANT.id, "agent", f"https://y.example/{n}", Decimal("20"), "USD", 0.0)
            w2.decide(claim.url, "x", Verdict(claim, False, ["no"], None, {check: False}), payee=PAYEE)
        (alert2,) = budget.analyze(w2, 60)["watch"]
        self.assertTrue(alert2["text"].endswith("Nothing was paid."))

    def test_a_promotion_about_to_run_out_of_time(self):
        w = wallet()
        p = w.book("Promotion by Maya", Decimal("40"), "marketing", PAYEE, shops.CREATOR_CHANNEL,
                   {"creator": "Maya", "deadline_at": 100}, True)
        w.record_hold(p.id, authorization_id="H")
        self.assertEqual(budget.analyze(w, 10)["watch"], [])
        (warn,) = budget.analyze(w, 70)["watch"]
        self.assertIn("about 30s left", warn["text"])

    def test_an_empty_wallet(self):
        w = Wallet("w", "a", Policy(Decimal("400"), Decimal("350"), Decimal("250"), shops.DEMO_WANT), Decimal("0"))
        a = budget.analyze(w, 0)
        self.assertEqual((a["pace"]["per_minute"], a["pace"]["lasts_minutes"], a["watch"], a["proposals"]),
                         ("0.00", None, [], []))
        self.assertEqual(budget.template_summary(a), "The wallet is empty: add money to let your AI start.")


class LimitTests(unittest.TestCase):
    def test_limits_change_together_and_never_break_the_rules(self):
        w = wallet(shopping="300", marketing="100")
        buy(w, "80", 1, "marketing")
        for bad in ({"marketing": "70"},                       # below what it already spent
                    {"shopping": "320"},                       # 320 + 100 > 400
                    {"travel": "10"}, {"marketing": "0"}):
            with self.assertRaises(WalletError, msg=bad):
                w.set_limits(bad)
        self.assertEqual(w.policy.envelopes["marketing"].limit, Decimal("100"))   # nothing half-applied
        w.set_limits({"shopping": "250", "marketing": "150"})
        env = w.policy.envelopes["shopping"]
        self.assertEqual((env.limit, env.max_per_purchase, env.approve_above),
                         (Decimal("250.00"), Decimal("250.00"), Decimal("250.00")))      # per-purchase shrinks too
        w.stop()
        with self.assertRaises(WalletError):
            w.set_limits({"marketing": "140"})


class SummaryTests(unittest.TestCase):
    def analysis(self):
        w = wallet()
        buy(w, "100", 1)
        return budget.analyze(w, 120)

    def test_template_without_a_key(self):
        s = budget.summarize(self.analysis(), None)
        self.assertEqual(s["by"], "Guild (rule-based)")
        self.assertTrue(s["text"].startswith("Your AI spent $100.00 of the $400.00 you loaded (shopping $100.00)."))
        self.assertTrue(s["text"].endswith("$300.00 is still available."))   # 2 minutes, 1 payment: no forecast yet
        self.assertEqual((self.analysis()["pace"]["known"], self.analysis()["pace"]["lasts_minutes"]), (False, None))

    def test_claude_sees_only_the_numbers_and_falls_back_when_it_cannot_help(self):
        a = self.analysis()
        fake = FakeClaude([turn(Block(type="text", text="You spent $100 on shopping."), stop="end_turn")])
        s = budget.summarize(a, fake)
        self.assertEqual(s, {"text": "You spent $100 on shopping.", "by": "Claude"})
        sent = fake.sent[0]
        self.assertEqual(json.loads(sent["messages"][0]["content"]), json.loads(json.dumps(a)))
        self.assertEqual((sent["model"], sent["output_config"], sent["fallbacks"]),
                         ("claude-opus-5-5", {"effort": "low"}, "default"))
        for bad in ([turn(stop="refusal")], [turn(Block(type="text", text="  "), stop="end_turn")], []):
            self.assertEqual(budget.summarize(a, FakeClaude(bad))["by"], "Guild (rule-based)")


class ApiTests(unittest.TestCase):
    def test_insights_and_applying_a_proposal(self):
        c = app()
        r = open_wallet(c, DEMO)                      # the scripted ShopperBot runs once money is in
        wid, owner = r["wallet_id"], {"X-Owner-Key": r["owner_key"]}
        self.assertEqual(c.get(f"/api/wallets/{wid}/insights").status_code, 403)
        a = c.get(f"/api/wallets/{wid}/insights", headers=owner).json()
        self.assertEqual((a["numbers"]["blocked_count"], a["numbers"]["blocked_total"], a["numbers"]["waiting_count"]),
                         (4, "515.00", 1))
        self.assertEqual({x["key"] for x in a["watch"]}, {"limit:shopping", "blocked"})   # $299 waiting = 88%
        self.assertEqual(a["summary"]["by"], "Guild (rule-based)")
        self.assertIn("Guild stopped 4 bad purchase attempts worth $515.00", a["summary"]["text"])
        (prop,) = a["proposals"]
        self.assertEqual(prop["limits"], {"shopping": "370.00", "marketing": "30.00"})
        done = c.post(f"/api/wallets/{wid}/limits", json={"limits": prop["limits"]}, headers=owner).json()
        self.assertEqual(done["type"], "limits")
        self.assertEqual({e["name"]: e["limit"] for e in done["envelopes"]}, {"shopping": "370.00", "marketing": "30.00"})
        self.assertEqual(c.post(f"/api/wallets/{wid}/limits", json={"limits": {"shopping": "390"}},
                                headers=owner).status_code, 409)          # 390 + 30 > 400
        self.assertEqual(c.post(f"/api/wallets/{wid}/limits", json={"limits": "x"}, headers=owner).status_code, 400)
        self.assertEqual(c.post(f"/api/wallets/{wid}/limits", json={"limits": {"shopping": "300"}}).status_code, 403)
        self.assertIn("limits", [e["type"] for e in c.get(f"/api/runs/{wid}").json()["events"]])

    def test_the_summary_is_cached_until_the_ledger_changes(self):
        c = app(shopper=None)
        r = open_wallet(c, DEMO)
        wid, owner = r["wallet_id"], {"X-Owner-Key": r["owner_key"]}
        service = c.app.state.wallets
        reply = lambda text: turn(Block(type="text", text=text), stop="end_turn")
        service.budget_client = FakeClaude([reply("first"), reply("second")])
        self.assertEqual(c.get(f"/api/wallets/{wid}/insights", headers=owner).json()["summary"]["text"], "first")
        self.assertEqual(c.get(f"/api/wallets/{wid}/insights", headers=owner).json()["summary"]["text"], "first")
        c.post(f"/api/wallets/{wid}/top-up", json={"amount": "0"}, headers=owner)          # refused: no change
        c.post(f"/api/wallets/{wid}/withdraw", headers=owner)                               # the ledger changes
        self.assertEqual(c.get(f"/api/wallets/{wid}/insights", headers=owner).json()["summary"]["text"], "second")
        self.assertEqual(len(service.budget_client.sent), 2)


if __name__ == "__main__":
    unittest.main()
