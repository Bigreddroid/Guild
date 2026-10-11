import json
import os
import sys
import unittest
from decimal import Decimal

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from guild import venues
from guild.agents import Researcher, agent_modes, default_agents
from guild.elastic import Elastic, ElasticError
from guild.lead import DEMO_GOAL, DEMO_JOBS, ScriptedLead
from guild.market import AgentCard, Job
from guild.review import RuleReviewer
from guild.run import run

VENUE = next(j for j, _ in DEMO_JOBS if j.id == "venue")


class FakeElasticsearch:
    """In-memory stand-in for the three Elasticsearch calls we make. It applies
    the bool filter (term + range gte) and the sort, so the tests check what the
    query actually selects, not just its shape."""

    def __init__(self, fail=None, exists=False):
        self.docs, self.calls, self.fail, self.exists = {}, [], fail or {}, exists

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path, request.headers.get("authorization"),
                           request.headers.get("content-type"), request.url.params.get("refresh")))
        key = (request.method, path.split("/")[-1] if path != "/_bulk" else "_bulk")
        if key in self.fail:
            return self.fail[key]
        if request.method == "PUT":
            if self.exists:
                return httpx.Response(400, json={"error": {"type": "resource_already_exists_exception"}})
            self.exists = True
            return httpx.Response(200, json={"acknowledged": True})
        if path == "/_bulk":
            lines = request.content.decode().strip().split("\n")
            for action, doc in zip(lines[::2], lines[1::2]):
                self.docs[json.loads(action)["index"]["_id"]] = json.loads(doc)
            return httpx.Response(200, json={"errors": False, "items": []})
        if path.endswith("/_search"):
            q = json.loads(request.content)
            term = q["query"]["bool"]["filter"][0]["term"]["city"]
            gte = q["query"]["bool"]["filter"][1]["range"]["capacity"]["gte"]
            hits = [d for d in self.docs.values() if d["city"] == term and d["capacity"] >= gte]
            hits.sort(key=lambda d: (d["price_per_day"], -d["capacity"]))
            return httpx.Response(200, json={"hits": {"hits": [{"_source": d} for d in hits[:q["size"]]]}})
        return httpx.Response(404)


def es(fake):
    return Elastic("https://es.test:443", "a2V5OnNlY3JldA==", transport=httpx.MockTransport(fake))


def researcher(fake):
    return Researcher(AgentCard("researcher", "Researcher", "r@x", {"research"}, 0.9), elastic=es(fake))


class ElasticClientTests(unittest.TestCase):
    def test_seed_then_shortlist_picks_cheapest_lisbon_venues_that_seat_six(self):
        fake = FakeElasticsearch()
        client = es(fake)
        client.seed()
        got = client.shortlist("Lisbon", 6)
        self.assertEqual([v["name"] for v in got],
                         ["Alfama Courtyard Studio", "Príncipe Real Salon", "Chiado Meeting Room"])
        self.assertEqual(got[0], {"name": "Alfama Courtyard Studio", "capacity": 8, "price_per_day": "310",
                                  "neighbourhood": "Alfama"})
        # Graça Attic (seats 4) and cheaper Porto venues must not appear.
        self.assertNotIn("Graça Attic", [v["name"] for v in got])

    def test_requests_match_the_elasticsearch_rest_api(self):
        fake = FakeElasticsearch()
        client = es(fake)
        client.seed()
        client.shortlist("lisbon", 6)
        put, bulk, search = fake.calls
        self.assertEqual(put[:2], ("PUT", f"/{venues.INDEX}"))
        self.assertEqual((bulk[1], bulk[3], bulk[4]), ("/_bulk", "application/x-ndjson", "wait_for"))
        self.assertEqual(search[1], f"/{venues.INDEX}/_search")
        self.assertTrue(all(c[2] == "ApiKey a2V5OnNlY3JldA==" for c in fake.calls))
        self.assertEqual(len(fake.docs), len(venues.VENUES))

    def test_seed_is_idempotent_and_tolerates_an_existing_index(self):
        fake = FakeElasticsearch(exists=True)
        client = es(fake)
        client.seed()
        client.seed()  # second call does nothing in this process
        self.assertEqual([c[0] for c in fake.calls], ["PUT", "POST"])
        fake2 = FakeElasticsearch()
        es(fake2).seed()
        es(fake2).seed()  # a new process re-loads the same _ids: no duplicates
        self.assertEqual(len(fake2.docs), len(venues.VENUES))

    def test_errors_raise_elastic_error(self):
        for fail in ({("PUT", venues.INDEX): httpx.Response(401)},
                     {("PUT", venues.INDEX): httpx.Response(400, json={"error": {"type": "mapper_parsing_exception"}})},
                     {("POST", "_bulk"): httpx.Response(200, json={"errors": True})},
                     {("POST", "_search"): httpx.Response(500)},
                     {("POST", "_search"): httpx.Response(200, content=b"<html>")}):
            client = es(FakeElasticsearch(fail=fail))
            with self.assertRaises(ElasticError, msg=str(fail)):
                client.seed()
                client.shortlist("lisbon", 6)

    def test_malformed_hits_are_dropped(self):
        def search(request):
            return httpx.Response(200, json={"hits": {"hits": [
                {"_source": {"name": "Ok", "capacity": 9, "price_per_day": 200}},
                {"_source": {"name": "No capacity", "price_per_day": 100}},
                {"_source": {"name": "Bad", "capacity": "many", "price_per_day": 100}},
                "junk", {"no_source": True}]}})
        self.assertEqual([v["name"] for v in es(search).shortlist("lisbon", 6)], ["Ok"])

    def test_needs_https_url_and_key(self):
        for url, key in (("http://es.test", "k"), ("https://es.test", "")):
            with self.assertRaises(ValueError):
                Elastic(url, key)


class ResearcherTests(unittest.TestCase):
    def test_live_venues_pass_the_rule_checks(self):
        out = researcher(FakeElasticsearch()).work(VENUE)
        self.assertEqual(out["mode"], "live")
        self.assertEqual(len(out["items"]), 3)
        self.assertEqual(RuleReviewer().review(VENUE, out)["verdict"], "pass")

    def test_failure_falls_back_to_labelled_sample_data(self):
        out = researcher(FakeElasticsearch(fail={("POST", "_search"): httpx.Response(503)})).work(VENUE)
        self.assertEqual(out["mode"], "simulated")
        self.assertIn("Elastic unavailable (HTTP 503", out["note"])

    def test_jobs_without_a_search_stay_simulated(self):
        fake = FakeElasticsearch()
        out = researcher(fake).work(Job("caterer", "Find a caterer", "research", ("3 caterers",), Decimal("0.5")))
        self.assertEqual(out["mode"], "simulated")
        self.assertEqual(fake.calls, [])

    def test_without_config_everything_is_simulated(self):
        saved = {k: os.environ.pop(k, None) for k in ("ELASTIC_URL", "ELASTIC_API_KEY")}
        try:
            self.assertIsNone(default_agents()["researcher"].elastic)
            self.assertEqual(agent_modes()["researcher"], "simulated")
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_full_run_with_live_researcher_tells_the_same_story(self):
        events = []
        run(DEMO_GOAL, Decimal("3.00"), default_agents(elastic=es(FakeElasticsearch())), ScriptedLead(),
            RuleReviewer(), events.append)
        work = next(e for e in events if e["type"] == "work" and e["agent_id"] == "researcher")
        self.assertEqual(work["mode"], "live")
        self.assertEqual(events[-1]["spent"], "2.00")


if __name__ == "__main__":
    unittest.main()
