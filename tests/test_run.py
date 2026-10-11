import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from guild.agents import default_agents
from guild.lead import ScriptedLead, DEMO_GOAL
from guild.review import RuleReviewer
from guild.run import run

D = Decimal


def go(budget="3.00"):
    events = []
    result = run(DEMO_GOAL, D(budget), default_agents(), ScriptedLead(), RuleReviewer(), events.append)
    return result, events


class DemoRunTests(unittest.TestCase):
    def test_cheapbot_wins_once_fails_unpaid_then_loses(self):
        result, events = go()
        awards = [e for e in events if e["type"] == "awarded"]
        settles = [e for e in events if e["type"] == "settled"]
        first = settles[0]
        self.assertEqual((first["agent_id"], first["outcome"]), ("cheapbot", "unpaid"))
        self.assertNotIn("cheapbot", [a["agent_id"] for a in awards[1:]])
        self.assertNotIn("cheap@owners.example", result["owed"])

    def test_every_job_ends_paid(self):
        result, events = go()
        paid = {e["job_id"] for e in events if e["type"] == "settled" and e["outcome"] == "paid"}
        self.assertEqual(paid, {j["id"] for j in result["jobs"]})

    def test_money_adds_up(self):
        result, _ = go()
        self.assertEqual(sum(result["owed"].values(), D("0")), result["spent"])
        self.assertLessEqual(result["spent"], result["budget"])

    def test_tight_budget_stops_cleanly(self):
        result, events = go(budget="0.50")
        self.assertLessEqual(result["spent"], D("0.50"))
        self.assertTrue(any(e["type"] == "unfunded" for e in events))

    def test_events_are_labelled_simulated(self):
        _, events = go()
        work = [e for e in events if e["type"] == "work"]
        self.assertTrue(work and all(e["mode"] == "simulated" for e in work))

    def test_reviewer_crash_holds_and_pays_nothing(self):
        class Broken:
            label = "broken"

            def review(self, job, output):
                raise TimeoutError("down")
        events = []
        result = run(DEMO_GOAL, D("3.00"), default_agents(), ScriptedLead(), Broken(), events.append)
        self.assertEqual(result["spent"], D("0"))
        self.assertTrue(all(e["outcome"] == "held" for e in events if e["type"] == "settled"))

    def test_deterministic(self):
        r1, e1 = go()
        r2, e2 = go()
        self.assertEqual(r1["owed"], r2["owed"])
        self.assertEqual([e["type"] for e in e1], [e["type"] for e in e2])


if __name__ == "__main__":
    unittest.main()
