import json
import os
import sys
import unittest
from decimal import Decimal

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from guild.agents import Sourcer, agent_modes, default_agents
from guild.channel3 import Channel3, Channel3Error
from guild.lead import DEMO_GOAL, DEMO_JOBS, ScriptedLead
from guild.market import AgentCard, Job
from guild.review import RuleReviewer
from guild.run import run

JOBS = {j.id: j for j, _ in DEMO_JOBS}


def offer(price, url="https://shop.example/x", availability="InStock", currency="USD", domain="shop.example"):
    return {"url": url, "domain": domain, "price": {"price": price, "currency": currency},
            "availability": availability}


def product(pid, title, *offers, brand="Acme"):
    return {"id": pid, "title": title, "brands": [{"id": "b1", "name": brand}], "offers": list(offers)}


CATALOG = {
    "A5 notebook": [product("p1", "A5 dotted notebook", offer(7.5, "https://a.example/nb"), offer(6.9, "https://b.example/nb")),
                    product("p2", "Leather A5 journal", offer(14.0, "https://c.example/j"))],
    "black gel pen": [product("p3", "Gel pen, black, 0.5 mm", offer(1.4, "https://a.example/pen"))],
    "custom logo stainless steel water bottle": [product("p4", "750 ml bottle, custom logo", offer(18.5))],
}


class FakeChannel3Api:
    """httpx transport that behaves like POST /v1/search and records requests."""

    def __init__(self, status=200, catalog=CATALOG, body=None):
        self.status, self.catalog, self.body, self.requests = status, catalog, body, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.requests.append({"path": request.url.path, "headers": request.headers, "json": payload})
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": "nope"})
        if self.body is not None:
            return httpx.Response(200, content=self.body)
        return httpx.Response(200, json={"products": self.catalog.get(payload["query"], []), "next_page_token": None})


def client(api):
    return Channel3("test-key", base_url="https://c3.test", transport=httpx.MockTransport(api))


class ClientTests(unittest.TestCase):
    def test_request_matches_the_sdk(self):
        api = FakeChannel3Api()
        client(api).search("A5 notebook", limit=99, max_price=15)
        req = api.requests[0]
        self.assertEqual(req["path"], "/v1/search")
        self.assertEqual(req["headers"]["x-api-key"], "test-key")
        self.assertEqual(req["json"]["limit"], 30)  # capped at the API maximum
        self.assertEqual(req["json"]["filters"], {"availability": ["InStock"], "price": {"max_price": 15.0}})
        self.assertEqual(req["json"]["config"]["currency"], "USD")

    def test_cheapest_in_stock_offer_per_product_cheapest_first(self):
        hits = client(FakeChannel3Api()).search("A5 notebook")
        self.assertEqual([h["title"] for h in hits], ["A5 dotted notebook", "Leather A5 journal"])
        self.assertEqual((hits[0]["price"], hits[0]["url"], hits[0]["brand"]), ("6.90", "https://b.example/nb", "Acme"))

    def test_unusable_offers_are_skipped(self):
        bad = [product("x", "bad", offer(5, availability="OutOfStock"), offer(5, url="http://insecure.example"),
                       offer(5, currency="EUR"), offer("abc"), offer(0), offer(-3), offer(None)),
               "not a product", product("y", "good", offer(9.99))]
        hits = client(FakeChannel3Api(catalog={"q": bad})).search("q")
        self.assertEqual([h["title"] for h in hits], ["good"])

    def test_errors_raise_channel3_error(self):
        for api in (FakeChannel3Api(status=401), FakeChannel3Api(status=500), FakeChannel3Api(body=b"<html>")):
            with self.assertRaises(Channel3Error):
                client(api).search("q")

        def boom(request):
            raise httpx.ConnectError("down")
        with self.assertRaises(Channel3Error):
            client(boom).search("q")

    def test_needs_a_key(self):
        with self.assertRaises(ValueError):
            Channel3("")


class SourcerTests(unittest.TestCase):
    def sourcer(self, api):
        return Sourcer(AgentCard("sourcer", "Sourcer", "s@x", {"source"}, 0.9), channel3=client(api))

    def test_live_supplies_pass_the_rule_checks(self):
        out = self.sourcer(FakeChannel3Api()).work(JOBS["supplies"])
        self.assertEqual(out["mode"], "live")
        self.assertEqual([(i["title"], i["qty"]) for i in out["items"]],
                         [("A5 dotted notebook", 6), ("Gel pen, black, 0.5 mm", 6)])
        self.assertEqual(RuleReviewer().review(JOBS["supplies"], out)["verdict"], "pass")

    def test_nothing_found_leaves_the_list_short_and_review_fails_it(self):
        out = self.sourcer(FakeChannel3Api(catalog={})).work(JOBS["supplies"])
        self.assertEqual((out["mode"], out["items"]), ("live", []))
        self.assertEqual(RuleReviewer().review(JOBS["supplies"], out)["verdict"], "fail")

    def test_api_failure_falls_back_to_labelled_sample_data(self):
        out = self.sourcer(FakeChannel3Api(status=401)).work(JOBS["swag"])
        self.assertEqual(out["mode"], "simulated")
        self.assertIn("Channel3 unavailable (HTTP 401)", out["note"])

    def test_other_jobs_search_their_title(self):
        api = FakeChannel3Api()
        self.sourcer(api).work(Job("chairs", "Find 6 folding chairs", "source", ("6 chairs",), Decimal("0.5")))
        self.assertEqual(api.requests[0]["json"]["query"], "Find 6 folding chairs")


class WiringTests(unittest.TestCase):
    def test_without_a_key_everything_is_simulated(self):
        old = os.environ.pop("CHANNEL3_API_KEY", None)
        try:
            self.assertIsNone(default_agents()["sourcer"].channel3)
            self.assertEqual(agent_modes()["sourcer"], "simulated")
        finally:
            if old is not None:
                os.environ["CHANNEL3_API_KEY"] = old

    def test_full_run_with_live_sourcer_tells_the_same_story(self):
        events = []
        run(DEMO_GOAL, Decimal("3.00"), default_agents(channel3=client(FakeChannel3Api())), ScriptedLead(),
            RuleReviewer(), events.append)
        work = [e for e in events if e["type"] == "work" and e["agent_id"] == "sourcer"]
        self.assertEqual([e["mode"] for e in work], ["live", "live"])
        self.assertEqual(events[-1]["spent"], "2.00")
        self.assertTrue(any(e["type"] == "settled" and e["outcome"] == "unpaid" for e in events))

    def test_fallback_note_reaches_the_event_stream(self):
        events = []
        run(DEMO_GOAL, Decimal("3.00"), default_agents(channel3=client(FakeChannel3Api(status=503))),
            ScriptedLead(), RuleReviewer(), events.append)
        notes = [e["note"] for e in events if e["type"] == "work" and "note" in e]
        self.assertEqual(len(notes), 2)
        self.assertIn("HTTP 503", notes[0])


if __name__ == "__main__":
    unittest.main()
