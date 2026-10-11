import json
import os
import sys
import time
import unittest

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

from guild.deals import DealLog
from guild.elastic import DEALS_INDEX, Elastic
from guild.hunters import default_hunters
from guild.lead import ScriptedLead
from guild.registry import Registry
from guild.review import RuleReviewer
from guild.server import create_app
from guild.zapier import Zapier

HOOK = "https://hooks.zapier.com/hooks/catch/123/abc/"


class FakeDealsIndex:
    """Stand-in for the deals index: stores documents by id, answers the search
    with newest-first order and a real sum of `saved`."""

    def __init__(self, down=False):
        self.docs, self.calls, self.down, self.created = {}, [], down, False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path, request.url.params.get("refresh")))
        if self.down:
            return httpx.Response(503)
        if request.method == "PUT" and path == f"/{DEALS_INDEX}":
            if self.created:
                return httpx.Response(400, json={"error": {"type": "resource_already_exists_exception"}})
            self.created = True
            return httpx.Response(200, json={"acknowledged": True})
        if request.method == "PUT" and path.startswith(f"/{DEALS_INDEX}/_doc/"):
            self.docs[path.rsplit("/", 1)[1]] = json.loads(request.content)
            return httpx.Response(201, json={"result": "created"})
        if path == f"/{DEALS_INDEX}/_search":
            q = json.loads(request.content)
            hits = sorted(self.docs.values(), key=lambda d: d["found_at"], reverse=True)[:q["size"]]
            return httpx.Response(200, json={"hits": {"total": {"value": len(self.docs)}, "hits": [{"_source": d} for d in hits]},
                                             "aggregations": {"saved": {"value": sum(d["saved"] for d in self.docs.values())}}})
        return httpx.Response(404)


class FakeHook:
    def __init__(self):
        self.posts = []

    def __call__(self, request):
        self.posts.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "success", "request_id": "req-7"})


def app(deals, deadline=30):
    return TestClient(create_app(brain=lambda: (ScriptedLead(), RuleReviewer()), paypal=None, hunters=default_hunters,
                                 hunt_speed=0, registry=Registry(), hunt_deadline_s=deadline, deals=deals))


def hunt(c, **body):
    run_id = c.post("/api/hunts", json={"bounty": "2.00", **body}).json()["run_id"]
    for _ in range(300):
        r = c.get(f"/api/runs/{run_id}").json()
        if r["done"]:
            return run_id, r["events"]
        time.sleep(0.02)
    raise AssertionError("hunt did not finish")


class DealLogTests(unittest.TestCase):
    def test_a_verified_deal_goes_to_elastic_and_fires_one_alert(self):
        es, hook = FakeDealsIndex(), FakeHook()
        log = DealLog(elastic=Elastic("https://es.test", "k", transport=httpx.MockTransport(es)),
                      zapier=Zapier(HOOK, transport=httpx.MockTransport(hook)))
        c = app(log)
        run_id, events = hunt(c)
        types = [e["type"] for e in events]
        self.assertEqual(types[types.index("winner") + 1: types.index("winner") + 3], ["recorded", "alert"])
        self.assertEqual(types[-2:], ["done", "payment"])
        self.assertEqual(next(e for e in events if e["type"] == "recorded")["store"], "Elastic")
        self.assertTrue(next(e for e in events if e["type"] == "alert")["sent"])
        doc = es.docs[run_id]
        self.assertEqual((doc["price"], doc["saved"], doc["agent"], doc["shop"], doc["demo"]),
                         (299.0, 31.0, "Browser", "harbor-audio (demo)", True))
        self.assertIn(("PUT", f"/{DEALS_INDEX}/_doc/{run_id}", "wait_for"), es.calls)
        alert = hook.posts[0]
        self.assertEqual((alert["event"], alert["price"]), ("deal_found", 299.0))
        self.assertIn("$299.00", alert["subject"])
        recent = c.get("/api/deals").json()
        self.assertEqual((recent["store"], recent["count"], recent["saved"]), ("Elastic", 1, 31.0))
        health = c.get("/healthz").json()
        self.assertEqual((health["deal_history"], health["deal_alerts"]), ("Elastic", "Zapier"))

        # A second deal is stored; the alert is throttled, and the deal still stands.
        _, events = hunt(c)
        alert = next(e for e in events if e["type"] == "alert")
        self.assertFalse(alert["sent"])
        self.assertIn("next allowed", alert["note"])
        self.assertEqual(c.get("/api/deals").json()["count"], 2)
        self.assertEqual(len(hook.posts), 1)

    def test_no_winner_means_nothing_recorded_or_sent(self):
        hook = FakeHook()
        log = DealLog(zapier=Zapier(HOOK, transport=httpx.MockTransport(hook)))
        _, events = hunt(app(log), must_have=["Kindle"])
        self.assertFalse(any(e["type"] in ("recorded", "alert") for e in events))
        self.assertEqual(hook.posts, [])

    def test_without_keys_history_is_kept_in_memory(self):
        c = app(DealLog())
        _, events = hunt(c)
        self.assertEqual(next(e for e in events if e["type"] == "recorded")["store"], "memory")
        self.assertFalse(next(e for e in events if e["type"] == "alert")["sent"])
        recent = c.get("/api/deals?size=500").json()
        self.assertEqual((recent["store"], recent["count"], recent["deals"][0]["price"]), ("memory", 1, 299.0))

    def test_elastic_down_falls_back_to_memory_and_the_bounty_is_still_paid(self):
        log = DealLog(elastic=Elastic("https://es.test", "k", transport=httpx.MockTransport(FakeDealsIndex(down=True))))
        c = app(log)
        _, events = hunt(c)
        rec = next(e for e in events if e["type"] == "recorded")
        self.assertEqual(rec["store"], "memory")
        self.assertIn("Elastic failed", rec["note"])
        self.assertEqual(events[-1]["payouts"][0]["amount"], "2.00")
        recent = c.get("/api/deals").json()
        self.assertEqual((recent["store"], recent["count"]), ("memory", 1))
        self.assertIn("Elastic failed", recent["note"])

    def test_a_crashing_after_deal_step_cannot_change_the_result(self):
        class Broken(DealLog):
            def record(self, d):
                raise RuntimeError("boom")
        _, events = hunt(app(Broken()))
        self.assertTrue(any(e["type"] == "notice" for e in events))
        self.assertEqual(next(e for e in events if e["type"] == "winner")["agent_id"], "browser")
        self.assertEqual(events[-1]["type"], "payment")

    def test_alert_text_is_one_line_whatever_the_buyer_typed(self):
        hook = FakeHook()
        log = DealLog(zapier=Zapier(HOOK, transport=httpx.MockTransport(hook)))
        hunt(app(log), title="Sony\nWH-1000XM5\r\nBcc: x@y.example", must_have=["Sony", "WH-1000XM5"])
        self.assertNotIn("\n", hook.posts[0]["subject"])
        self.assertNotIn("\r", hook.posts[0]["subject"])


if __name__ == "__main__":
    unittest.main()
