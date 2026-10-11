import json
import os
import time
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

from guild.lead import ScriptedLead
from guild.review import RuleReviewer
from guild.server import RateLimiter, create_app


def offline():
    return ScriptedLead(), RuleReviewer()


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(create_app(brain=offline))

    def run_events(self, **kw):
        r = self.c.post("/api/runs", **kw)
        self.assertEqual(r.status_code, 200, r.text)
        run_id = r.json()["run_id"]
        for _ in range(200):
            body = self.c.get(f"/api/runs/{run_id}").json()
            if body["done"]:
                return run_id, body["events"]
            time.sleep(0.02)
        self.fail("run did not finish")

    def test_healthz_says_what_is_simulated(self):
        body = self.c.get("/healthz").json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["agents"], "simulated")

    def test_run_returns_the_full_story(self):
        _, events = self.run_events(json={"budget": "3.00"})
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "plan")
        self.assertEqual(types[-1], "done")
        settled = [e for e in events if e["type"] == "settled"]
        self.assertIn("unpaid", [e["outcome"] for e in settled])  # CheapBot's junk
        self.assertEqual(events[-1]["spent"], "2.00")

    def test_empty_body_uses_default_budget(self):
        self.assertEqual(self.run_events()[1][-1]["budget"], "3.00")

    def test_bad_budgets_are_rejected(self):
        for b in ("0", "-1", "11", "abc", "NaN", "Infinity", None):
            if b is None:
                r = self.c.post("/api/runs", content=b"[1]", headers={"content-type": "application/json"})
            else:
                r = self.c.post("/api/runs", json={"budget": b})
            self.assertEqual(r.status_code, 400, b)
        self.assertEqual(self.c.post("/api/runs", content=b"{bad").status_code, 400)

    def test_small_budget_leaves_jobs_unfunded(self):
        _, events = self.run_events(json={"budget": 0.5})
        self.assertTrue(any(e["type"] == "unfunded" for e in events))
        self.assertLessEqual(float(events[-1]["spent"]), 0.5)

    def test_stream_replays_every_event_then_end(self):
        run_id, events = self.run_events()
        with self.c.stream("GET", f"/api/runs/{run_id}/events?delay=0") as r:
            self.assertEqual(r.headers["content-type"].split(";")[0], "text/event-stream")
            text = "".join(r.iter_text())
        data = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]
        self.assertEqual(data[:-1], events)
        self.assertIn("event: end", text)

    def test_unknown_run_is_404(self):
        self.assertEqual(self.c.get("/api/runs/nope/events").status_code, 404)
        self.assertEqual(self.c.get("/api/runs/nope").status_code, 404)

    def test_a_crashing_reviewer_ends_the_run_without_paying(self):
        class Broken:
            label = "broken"
            def review(self, job, output):
                raise RuntimeError("down")
        c = TestClient(create_app(brain=lambda: (ScriptedLead(), Broken())))
        self.c = c
        _, events = self.run_events()
        self.assertEqual(events[-1]["spent"], "0")
        self.assertTrue(all(e["outcome"] == "held" for e in events if e["type"] == "settled"))

    def test_a_crashing_lead_reports_an_error_event(self):
        class Broken:
            label = "broken"
            def plan(self, goal):
                raise RuntimeError("down")
        self.c = TestClient(create_app(brain=lambda: (Broken(), RuleReviewer())))
        _, events = self.run_events()
        self.assertEqual(events, [{"type": "error", "message": "run failed: RuntimeError"}])

    def test_concurrent_runs_are_capped(self):
        import threading
        gate = threading.Event()
        class Slow:
            label = "slow"
            def plan(self, goal):
                gate.wait(5)
                return ScriptedLead().plan(goal)
        c = TestClient(create_app(brain=lambda: (Slow(), RuleReviewer())))
        codes = [c.post("/api/runs").status_code for _ in range(4)]
        gate.set()
        self.assertEqual(codes, [200, 200, 200, 429])


class RateLimiterTests(unittest.TestCase):
    def test_blocks_after_limit(self):
        rl = RateLimiter(limit=2, window_s=60)
        self.assertEqual([rl.allow("a"), rl.allow("a"), rl.allow("a"), rl.allow("b")],
                         [True, True, False, True])


if __name__ == "__main__":
    unittest.main()
