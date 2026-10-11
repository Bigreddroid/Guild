import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

from guild.server import RateLimiter, create_app


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(create_app())

    def test_healthz_says_what_is_simulated(self):
        body = self.c.get("/healthz").json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["agents"], "simulated")

    def test_run_returns_the_full_story(self):
        r = self.c.post("/api/runs", json={"budget": "3.00"})
        self.assertEqual(r.status_code, 200)
        types = [e["type"] for e in r.json()["events"]]
        self.assertEqual(types[0], "plan")
        self.assertEqual(types[-1], "done")
        settled = [e for e in r.json()["events"] if e["type"] == "settled"]
        self.assertIn("unpaid", [e["outcome"] for e in settled])  # CheapBot's junk
        self.assertEqual(r.json()["events"][-1]["spent"], "2.00")

    def test_empty_body_uses_default_budget(self):
        self.assertEqual(self.c.post("/api/runs").json()["events"][-1]["budget"], "3.00")

    def test_bad_budgets_are_rejected(self):
        for b in ("0", "-1", "11", "abc", "NaN", "Infinity", None):
            if b is None:
                r = self.c.post("/api/runs", content=b"[1]", headers={"content-type": "application/json"})
            else:
                r = self.c.post("/api/runs", json={"budget": b})
            self.assertEqual(r.status_code, 400, b)
        self.assertEqual(self.c.post("/api/runs", content=b"{bad").status_code, 400)

    def test_small_budget_leaves_jobs_unfunded(self):
        events = self.c.post("/api/runs", json={"budget": 0.5}).json()["events"]
        self.assertTrue(any(e["type"] == "unfunded" for e in events))
        self.assertLessEqual(float(events[-1]["spent"]), 0.5)

    def test_stream_replays_every_event_then_end(self):
        run = self.c.post("/api/runs").json()
        with self.c.stream("GET", f"/api/runs/{run['run_id']}/events?delay=0") as r:
            self.assertEqual(r.headers["content-type"].split(";")[0], "text/event-stream")
            text = "".join(r.iter_text())
        data = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]
        self.assertEqual(data[:-1], run["events"])
        self.assertIn("event: end", text)

    def test_unknown_run_is_404(self):
        self.assertEqual(self.c.get("/api/runs/nope/events").status_code, 404)


class RateLimiterTests(unittest.TestCase):
    def test_blocks_after_limit(self):
        rl = RateLimiter(limit=2, window_s=60)
        self.assertEqual([rl.allow("a"), rl.allow("a"), rl.allow("a"), rl.allow("b")],
                         [True, True, False, True])


if __name__ == "__main__":
    unittest.main()
