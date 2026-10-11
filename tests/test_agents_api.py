import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

from guild import shops
from guild.hunters import default_hunters
from guild.lead import ScriptedLead
from guild.registry import NEW_AGENT_PRIOR, Registry, RegistryError
from guild.review import RuleReviewer
from guild.server import create_app

HARBOR = shops.url_for("harbor-audio/sony-wh-1000xm5-black")
REFURB = shops.url_for("loop-renew/sony-wh-1000xm5-refurb")


def without_browser():
    """Guild's own agents minus the one that finds the real deal, so an outside agent can win."""
    return {k: h for k, h in default_hunters().items() if k != "browser"}


class RegistryTests(unittest.TestCase):
    def test_register_returns_a_token_once_and_stores_only_its_hash(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "agents.json"
            reg = Registry(path)
            agent_id, token = reg.register("Sleuth", "dev@example.com", "finds things")
            self.assertTrue(token.startswith("gk_"))
            self.assertNotIn(token, path.read_text())
            card = reg.authenticate(token)
            self.assertEqual((card.id, card.owner_email, card.reputation), (agent_id, "dev@example.com", NEW_AGENT_PRIOR))
            self.assertIsNone(reg.authenticate(token + "x"))
            self.assertIsNone(reg.authenticate(""))

    def test_reputation_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "agents.json"
            reg = Registry(path)
            _, token = reg.register("Sleuth", "dev@example.com")
            card = reg.authenticate(token)
            card.passes, card.fails = 3, 1
            reg.save()
            again = Registry(path).authenticate(token)
            self.assertEqual((again.passes, again.fails), (3, 1))

    def test_bad_names_emails_and_duplicates_are_refused(self):
        reg = Registry()
        reg.register("Sleuth", "dev@example.com")
        for name, email in (("x", "dev@example.com"), ("<script>", "dev@example.com"), ("Good Name", "not-an-email"),
                            ("Good Name", "a@b"), ("Scout", "dev@example.com"), ("Guild", "dev@example.com"),
                            ("sleuth", "other@example.com")):
            with self.assertRaises(RegistryError, msg=(name, email)):
                reg.register(name, email)
        self.assertEqual(len(reg), 1)

    def test_leaderboard_hides_payout_emails(self):
        reg = Registry()
        reg.register("Sleuth", "dev@example.com")
        row = reg.leaderboard()[0]
        self.assertEqual(row["name"], "Sleuth")
        self.assertNotIn("dev@example.com", json.dumps(row))


class AgentApiTests(unittest.TestCase):
    def make(self, deadline=1.5, hunters=without_browser):
        self.reg = Registry()
        return TestClient(create_app(brain=lambda: (ScriptedLead(), RuleReviewer()), paypal=None, hunters=hunters,
                                     hunt_speed=0, registry=self.reg, hunt_deadline_s=deadline))

    def register(self, c, name="Sleuth", email="dev@example.com"):
        r = c.post("/api/agents", json={"name": name, "payout_email": email})
        self.assertEqual(r.status_code, 201, r.text)
        return {"Authorization": f"Bearer {r.json()['token']}"}

    def open_bounty(self, c):
        hunt_id = c.post("/api/hunts", json={"bounty": "2.00"}).json()["run_id"]
        for _ in range(100):
            ids = [b["id"] for b in c.get("/api/bounties").json()["bounties"]]
            if hunt_id in ids:
                return hunt_id
            time.sleep(0.01)
        self.fail("bounty never opened")

    def finish(self, c, hunt_id):
        for _ in range(400):
            body = c.get(f"/api/runs/{hunt_id}").json()
            if body["done"]:
                return body["events"]
            time.sleep(0.02)
        self.fail("hunt did not finish")

    def test_an_outside_agent_wins_and_its_developer_is_paid(self):
        c = self.make()
        auth = self.register(c)
        hunt_id = self.open_bounty(c)
        listed = c.get("/api/bounties").json()["bounties"][0]
        self.assertEqual((listed["max_price"], listed["ship_to"], listed["bounty"]), ("330", "US", "2.00"))
        r = c.post(f"/api/bounties/{hunt_id}/claims", json={"url": HARBOR, "price": "299.00"}, headers=auth)
        self.assertEqual(r.status_code, 202, r.text)
        events = self.finish(c, hunt_id)
        mine = [e for e in events if e["type"] == "claim" and e["outside"]]
        self.assertEqual(len(mine), 1)
        self.assertTrue(mine[0]["valid"], mine[0]["reasons"])
        winner = next(e for e in events if e["type"] == "winner")
        self.assertEqual((winner["agent_id"], winner["price"]), (mine[0]["agent_id"], "299.00"))
        payment = events[-1]
        self.assertEqual(payment["type"], "payment")
        self.assertEqual(json.dumps(payment).count("example.com"), 1)  # the masked payout email
        self.assertEqual(c.get("/api/agents/me", headers=auth).json()["passes"], 1)
        self.assertEqual(c.post(f"/api/bounties/{hunt_id}/claims", json={"url": HARBOR, "price": "299"},
                                headers=auth).status_code, 404)  # closed and gone

    def test_new_agents_get_one_claim_per_bounty_and_lose_reputation_for_a_bad_one(self):
        c = self.make()
        auth = self.register(c)
        hunt_id = self.open_bounty(c)
        for url in (REFURB, HARBOR):
            self.assertEqual(c.post(f"/api/bounties/{hunt_id}/claims", json={"url": url, "price": "219.00"},
                                    headers=auth).status_code, 202)
        events = self.finish(c, hunt_id)
        mine = [e for e in events if e["type"] == "claim" and e["outside"]]
        self.assertEqual(len(mine), 1)
        self.assertFalse(mine[0]["valid"])
        self.assertTrue(any(e["type"] == "claim_refused" for e in events))
        self.assertEqual(next(e for e in events if e["type"] == "no_winner")["type"], "no_winner")
        self.assertEqual(c.get("/api/agents/me", headers=auth).json()["fails"], 1)

    def test_claims_need_a_token_a_known_bounty_and_a_sane_body(self):
        c = self.make()
        auth = self.register(c)
        self.assertEqual(c.get("/api/agents/me").status_code, 401)
        self.assertEqual(c.get("/api/agents/me", headers={"Authorization": "Bearer gk_nope"}).status_code, 401)
        self.assertEqual(c.post("/api/bounties/nope/claims", json={"url": HARBOR, "price": "1"}).status_code, 401)
        self.assertEqual(c.post("/api/bounties/nope/claims", json={"url": HARBOR, "price": "1"},
                                headers=auth).status_code, 404)
        hunt_id = self.open_bounty(c)
        for body in ({"url": "http://x.example/p", "price": "1"}, {"url": HARBOR}, {"url": HARBOR, "price": [1]},
                     {"url": "https://x.example/" + "a" * 600, "price": "1"}):
            self.assertEqual(c.post(f"/api/bounties/{hunt_id}/claims", json=body, headers=auth).status_code, 400, body)
        self.finish(c, hunt_id)

    def test_registration_errors_and_rate_limit(self):
        c = self.make()
        self.assertEqual(c.post("/api/agents", json={"name": "x", "payout_email": "dev@example.com"}).status_code, 400)
        self.assertEqual(c.post("/api/agents", content=b"[]").status_code, 400)
        codes = [c.post("/api/agents", json={"name": f"Agent {i:02d}", "payout_email": "dev@example.com"}).status_code
                 for i in range(6)]
        self.assertEqual(codes, [201] * 3 + [429] * 3)  # the two 400s above used two of the five
        names = [a["name"] for a in c.get("/api/agents").json()["agents"]]
        self.assertEqual(len(names), 3)

    def test_without_outside_agents_the_hunt_closes_as_soon_as_house_agents_finish(self):
        c = self.make(deadline=30, hunters=default_hunters)
        t0 = time.monotonic()
        hunt_id = c.post("/api/hunts", json={"bounty": "2.00"}).json()["run_id"]
        events = self.finish(c, hunt_id)
        self.assertLess(time.monotonic() - t0, 10)
        self.assertEqual(next(e for e in events if e["type"] == "winner")["agent_id"], "browser")

    def test_house_agents_reputation_carries_from_bounty_to_bounty(self):
        c = self.make(deadline=30, hunters=default_hunters)
        stars = []
        for _ in range(2):
            events = self.finish(c, c.post("/api/hunts", json={"bounty": "2.00"}).json()["run_id"])
            stars.append({a["id"]: (a["stars"], a["max_claims"]) for a in events[0]["agents"]})
        self.assertLess(stars[1]["scout"][0], stars[0]["scout"][0])      # three wrong claims cost Scout
        self.assertGreater(stars[1]["browser"][0], stars[0]["browser"][0])
        board = {a["id"]: a for a in c.get("/api/agents").json()["agents"]}
        self.assertTrue(board["browser"]["house"])
        self.assertEqual(board["scout"]["fails"], 2 + 6)
        self.assertNotIn("@", json.dumps(board))

    def test_a_custom_want_is_hunted_and_bad_ones_are_refused(self):
        c = self.make(deadline=30, hunters=default_hunters)
        r = c.post("/api/hunts", json={"bounty": "1.00", "must_have": ["Kindle"], "max_price": "120", "ship_to": "IN"})
        self.assertEqual(r.status_code, 200, r.text)
        events = self.finish(c, r.json()["run_id"])
        self.assertEqual((events[0]["must_have"], events[0]["ship_to"]), (["Kindle"], "IN"))
        self.assertTrue(any(e["type"] == "no_winner" for e in events))
        self.assertEqual(c.post("/api/hunts", json={"must_have": []}).status_code, 400)

    def test_demo_catalogue_points_at_pages_guild_serves(self):
        c = self.make()
        listings = c.get("/api/demo/catalogue").json()["listings"]
        self.assertEqual(len(listings), len(shops.PRODUCTS))
        page = c.get(listings[0]["page"])
        self.assertEqual(page.status_code, 200)
        self.assertIn("application/ld+json", page.text)
        self.assertEqual(c.get("/shops/nope").status_code, 404)


if __name__ == "__main__":
    unittest.main()
