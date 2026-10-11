import json
import os
import sys
import unittest
from decimal import Decimal

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from guild.agents import Doer, agent_modes, default_agents
from guild.lead import DEMO_GOAL, DEMO_JOBS, ScriptedLead
from guild.market import AgentCard, Job
from guild.review import RuleReviewer
from guild.run import run
from guild.zapier import Zapier, ZapierError, ZapierThrottled

EMAIL = next(j for j, _ in DEMO_JOBS if j.id == "email")
HOOK = "https://hooks.zapier.com/hooks/catch/123/abc/"


class FakeHook:
    def __init__(self, response=None):
        self.response = response
        self.posts = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.posts.append((str(request.url), json.loads(request.content)))
        return self.response or httpx.Response(200, json={"status": "success", "id": "z1", "request_id": "req-9"})


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def zap(hook, clock=None, interval=600):
    return Zapier(HOOK, min_interval_s=interval, transport=httpx.MockTransport(hook), clock=clock or Clock())


def doer(hook, **kw):
    return Doer(AgentCard("doer", "Doer", "d@x", {"email"}, 0.95), zapier=zap(hook, **kw))


class ZapierClientTests(unittest.TestCase):
    def test_posts_json_to_the_hook_and_returns_request_id(self):
        hook = FakeHook()
        self.assertEqual(zap(hook).send({"a": 1}), "req-9")
        self.assertEqual(hook.posts, [(HOOK, {"a": 1})])

    def test_only_zapier_catch_hooks_are_accepted(self):
        for url in ("https://evil.example/hooks/catch/1/", "http://hooks.zapier.com/hooks/catch/1/",
                    "https://hooks.zapier.com/other/1/", ""):
            with self.assertRaises(ValueError):
                Zapier(url)

    def test_errors(self):
        for resp in (httpx.Response(410), httpx.Response(200, content=b"ok"),
                     httpx.Response(200, json={"status": "error"})):
            with self.assertRaises(ZapierError):
                zap(FakeHook(resp)).send({})

    def test_throttle_allows_one_send_per_interval_and_counts_failures(self):
        clock, hook = Clock(), FakeHook()
        z = zap(hook, clock=clock)
        z.send({})
        clock.t += 599
        with self.assertRaises(ZapierThrottled):
            z.send({})
        clock.t += 1
        z.send({})
        self.assertEqual(len(hook.posts), 2)
        failing = zap(FakeHook(httpx.Response(500)), clock=clock)
        with self.assertRaises(ZapierError):
            failing.send({})
        with self.assertRaises(ZapierThrottled):  # a broken Zap isn't hammered either
            failing.send({})


class DoerTests(unittest.TestCase):
    def test_live_email_passes_the_rule_checks(self):
        hook = FakeHook()
        out = doer(hook).work(EMAIL)
        self.assertEqual(out["mode"], "live")
        self.assertEqual(len(out["items"]), 6)
        self.assertEqual(out["items"][0]["zapier_request"], "req-9")
        self.assertEqual(RuleReviewer().review(EMAIL, out)["verdict"], "pass")
        payload = hook.posts[0][1]
        self.assertEqual(payload["subject"], "Lisbon offsite plan")
        self.assertEqual(payload["recipients"], Doer.RECIPIENTS)
        self.assertTrue(all(r.endswith("@team.example") for r in payload["recipients"]))

    def test_second_run_within_ten_minutes_uses_labelled_sample_data(self):
        d = doer(FakeHook())
        self.assertEqual(d.work(EMAIL)["mode"], "live")
        again = d.work(EMAIL)
        self.assertEqual(again["mode"], "simulated")
        self.assertIn("next allowed in", again["note"])
        self.assertEqual(RuleReviewer().review(EMAIL, again)["verdict"], "pass")

    def test_failure_falls_back_with_a_note(self):
        out = doer(FakeHook(httpx.Response(500))).work(EMAIL)
        self.assertEqual(out["mode"], "simulated")
        self.assertIn("Zapier not used (HTTP 500)", out["note"])

    def test_other_jobs_never_trigger_the_zap(self):
        hook = FakeHook()
        doer(hook).work(Job("invite", "Send invites", "email", ("x",), Decimal("0.2")))
        self.assertEqual(hook.posts, [])

    def test_without_a_hook_everything_is_simulated(self):
        saved = os.environ.pop("ZAPIER_HOOK_URL", None)
        try:
            self.assertIsNone(default_agents()["doer"].zapier)
            self.assertEqual(agent_modes()["doer"], "simulated")
            os.environ["ZAPIER_HOOK_URL"] = "https://evil.example/x"
            self.assertIsNone(default_agents()["doer"].zapier)
        finally:
            os.environ.pop("ZAPIER_HOOK_URL", None)
            if saved is not None:
                os.environ["ZAPIER_HOOK_URL"] = saved

    def test_full_run_with_live_doer_tells_the_same_story(self):
        events = []
        run(DEMO_GOAL, Decimal("3.00"), default_agents(zapier=zap(FakeHook())), ScriptedLead(), RuleReviewer(),
            events.append)
        work = next(e for e in events if e["type"] == "work" and e["agent_id"] == "doer")
        self.assertEqual(work["mode"], "live")
        self.assertEqual(events[-1]["spent"], "2.00")


if __name__ == "__main__":
    unittest.main()
