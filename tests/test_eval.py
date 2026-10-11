import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from guild.brain import ClaudeReviewer, GatedReviewer
from guild.review import RuleReviewer
from scripts import eval_review as ev


class CaseFileTests(unittest.TestCase):
    def test_every_case_is_well_formed(self):
        cases = ev.load_cases()
        self.assertGreaterEqual(len(cases), 30)
        self.assertEqual(len({c["id"] for c in cases}), len(cases))
        for c in cases:
            self.assertIn(c["job_id"], ev.JOBS, c["id"])
            self.assertIn(c["expected"], ("pass", "fail"), c["id"])
            self.assertIn(c["kind"], ev.KINDS, c["id"])
            self.assertIsInstance(c["items"], list, c["id"])
            self.assertEqual(c["expected"] == "pass", c["kind"] == "good", c["id"])


class RulesBaselineTests(unittest.TestCase):
    """Pins down what rule checks alone do, so the README numbers stay true."""

    @classmethod
    def setUpClass(cls):
        cls.result = ev.evaluate(RuleReviewer(), ev.load_cases())

    def test_rules_pay_all_good_work_and_stop_all_obvious_junk(self):
        self.assertEqual(self.result["by_kind"]["good"], {"correct": 10, "total": 10})
        self.assertEqual(self.result["by_kind"]["junk"], {"correct": 10, "total": 10})

    def test_rules_miss_subtle_and_injected_work(self):
        # This gap is why Claude makes the final call: rules alone would pay these.
        self.assertEqual(self.result["by_kind"]["subtle"]["correct"], 0)
        self.assertEqual(self.result["by_kind"]["injection"]["correct"], 1)
        self.assertEqual(self.result["junk_paid"], 15)

    def test_main_exits_1_when_junk_is_paid_and_writes_json(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "r.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ev.main(["--rules", "--json", str(out)]), 1)
            self.assertEqual(json.loads(out.read_text())["junk_paid"], 15)


class Oracle:
    """A fake AI that knows the answers, to check the scoring maths."""
    label = "oracle"

    def __init__(self, cases, flip=()):
        self.answers = {json.dumps(c["items"], sort_keys=True): c["expected"] for c in cases}
        self.flip = set(flip)

    def review(self, job, output):
        key = json.dumps(output["items"], sort_keys=True)
        verdict = self.answers[key]
        if key in self.flip:
            verdict = "pass" if verdict == "fail" else "hold"
        return {"verdict": verdict, "reasons": []}


class ScoringTests(unittest.TestCase):
    def test_a_perfect_reviewer_scores_100_and_exits_0(self):
        cases = ev.load_cases()
        r = ev.evaluate(Oracle(cases), cases)
        self.assertEqual((r["accuracy"], r["junk_paid"], r["good_paid"]), (1.0, 0, 10))

    def test_hold_counts_as_unpaid_and_errors_hold(self):
        cases = ev.load_cases()
        good = next(c for c in cases if c["kind"] == "good")
        bad = next(c for c in cases if c["kind"] == "injection")
        flip = [json.dumps(c["items"], sort_keys=True) for c in (good, bad)]
        r = ev.evaluate(Oracle(cases, flip), cases)
        self.assertEqual((r["good_paid"], r["junk_paid"]), (9, 1))

        class Down:
            def review(self, job, output):
                raise RuntimeError("down")
        r = ev.evaluate(Down(), cases)
        self.assertEqual((r["good_paid"], r["junk_paid"]), (0, 0))
        self.assertTrue(all(row["got"].startswith("hold") for row in r["rows"]))

    def test_gated_reviewer_only_spends_ai_calls_on_work_rules_let_through(self):
        cases = ev.load_cases()
        calls = []

        def create(**kw):
            calls.append(kw)
            text = json.dumps({"verdict": "fail", "reasons": ["x"]})
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])

        client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
        r = ev.evaluate(GatedReviewer(ClaudeReviewer(client=client)), cases)
        self.assertEqual(len(calls), 25)  # 36 cases minus the 11 the rules already fail
        self.assertEqual(r["junk_paid"], 0)


if __name__ == "__main__":
    unittest.main()
