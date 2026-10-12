import json
import os
import sys
import time
import unittest
import urllib.parse
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

from guild import funding, shops
from guild.lead import ScriptedLead
from guild.paypal import PayPalClient
from guild.registry import Registry
from guild.review import RuleReviewer
from guild.server import create_app
from guild.shopper import live, page_text, run_shopper, Tools
from tests.test_walletpay import FakeSandbox

PUBLIC = "https://guild.example"
HARBOR = shops.url_for("harbor-audio/sony-wh-1000xm5-black")


def scripted_now(service, wallet_id):
    run_shopper(service, wallet_id, None, step_s=0)


def app(paypal=None, shopper=scripted_now, registry=None):
    return TestClient(create_app(brain=lambda: (ScriptedLead(), RuleReviewer()), paypal=paypal,
                                 public_url=PUBLIC if paypal else "", registry=registry or Registry(),
                                 shopper=shopper), follow_redirects=False)


def open_wallet(c, body=None, amount="400"):
    """Create a wallet and load it with (preview) money, as the owner would."""
    r = c.post("/api/wallets", json=body or {})
    assert r.status_code == 201, r.text
    out = r.json()
    top = c.post(f"/api/wallets/{out['wallet_id']}/top-up", json={"amount": amount},
                 headers={"X-Owner-Key": out["owner_key"]})
    assert top.status_code == 200, top.text
    return out


def statuses(c, wid):
    return [p["status"] for p in c.get(f"/api/wallets/{wid}").json()["purchases"]]


class ScriptedStoryTests(unittest.TestCase):
    def test_every_trap_is_blocked_and_the_real_deal_waits_for_the_owner(self):
        c = app()
        r = c.post("/api/wallets", json={"budget": "400", "max_per_purchase": "350", "approve_above": "250"})
        self.assertEqual(r.status_code, 201, r.text)
        wid, key = r.json()["wallet_id"], r.json()["owner_key"]
        self.assertEqual(c.get(f"/api/wallets/{wid}").json()["purchases"], [])   # empty wallet: ShopperBot waits
        topped = c.post(f"/api/wallets/{wid}/top-up", json={"amount": "400"}, headers={"X-Owner-Key": key}).json()
        self.assertEqual((topped["type"], topped["mode"]), ("topped_up", "preview"))
        view = c.get(f"/api/wallets/{wid}").json()
        self.assertEqual(statuses(c, wid), ["blocked", "blocked", "blocked", "pending", "blocked"])
        reasons = [p["reasons"][0] for p in view["purchases"]]
        self.assertIn("no independent source lists this seller", reasons[0])
        self.assertIn("usual", reasons[1])
        self.assertTrue(any("page says 318.00" in r for r in view["purchases"][2]["reasons"]))
        self.assertIn("waiting for the owner", reasons[3])
        self.assertTrue(any("has case" in r for r in view["purchases"][4]["reasons"]))
        self.assertEqual((view["reserved"], view["remaining"], view["mode"]), ("299.00", "101.00", "preview"))

        pid = view["purchases"][3]["id"]
        self.assertEqual(c.post(f"/api/wallets/{wid}/purchases/{pid}/approve").status_code, 403)
        self.assertEqual(c.post(f"/api/wallets/{wid}/purchases/{pid}/approve",
                                headers={"X-Owner-Key": "ok_wrong"}).status_code, 403)
        paid = c.post(f"/api/wallets/{wid}/purchases/{pid}/approve", headers={"X-Owner-Key": key}).json()
        self.assertEqual((paid["type"], paid["mode"], paid["spent"]), ("paid", "preview", "299.00"))
        self.assertEqual(c.post(f"/api/wallets/{wid}/purchases/{pid}/approve",
                                headers={"X-Owner-Key": key}).status_code, 409)
        back = c.post(f"/api/wallets/{wid}/withdraw", headers={"X-Owner-Key": key}).json()
        self.assertEqual((back["type"], back["amount"], back["balance"]), ("withdrawn", "101.00", "0.00"))
        self.assertEqual(c.post(f"/api/wallets/{wid}/withdraw", headers={"X-Owner-Key": key}).status_code, 409)
        self.assertEqual(c.post(f"/api/wallets/{wid}/top-up", json={"amount": "50"},
                                headers={"X-Owner-Key": key}).json()["type"], "topped_up")

        events = c.get(f"/api/runs/{wid}").json()["events"]
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "wallet")
        self.assertEqual(types.count("purchase"), 5)
        self.assertTrue(any(e["type"] == "agent" and "note on Metro Tech" in e["say"] for e in events))

        stop = c.post(f"/api/wallets/{wid}/stop", headers={"X-Owner-Key": key}).json()
        self.assertEqual(stop["type"], "stopped")
        self.assertTrue(c.get(f"/api/runs/{wid}").json()["done"])

    def test_bad_policies_and_unknown_wallets(self):
        c = app()
        self.assertEqual(c.post("/api/wallets", json={"budget": "900"}).status_code, 400)
        self.assertEqual(c.post("/api/wallets", json={"budget": "100", "max_per_purchase": "200"}).status_code, 400)
        self.assertEqual(c.post("/api/wallets", json={"ship_to": "Mars"}).status_code, 400)
        self.assertEqual(c.get("/api/wallets/nope").status_code, 404)
        for envelopes in ([], {"a": {}}, {"a": {"limit": "x"}}, {"a": {"limit": "300"}, "b": {"limit": "200"}},
                          {"a": "50"}):
            self.assertEqual(c.post("/api/wallets", json={"envelopes": envelopes}).status_code, 400, envelopes)

    def test_envelopes_split_the_budget(self):
        reg = Registry()
        c = app(shopper=None, registry=reg)
        bot = c.post("/api/agents", json={"name": "Spender", "payout_email": "dev@example.com"}).json()
        r = open_wallet(c, {"agent": bot["agent_id"], "budget": "400", "approve_above": "350",
                            "envelopes": {"shopping": {"limit": "320"},
                                          "marketing": {"limit": "80", "max_per_purchase": "50", "approve_above": "20"}}})
        wid, auth = r["wallet_id"], {"Authorization": f"Bearer {bot['token']}"}
        view = c.get(f"/api/wallets/{wid}").json()
        self.assertEqual([(e["name"], e["limit"], e["approve_above"]) for e in view["envelopes"]],
                         [("shopping", "320.00", "320.00"), ("marketing", "80.00", "20.00")])
        buy = {"url": HARBOR, "item": "headphones", "price": "299.00"}
        wrong = c.post(f"/api/wallets/{wid}/purchases", json={**buy, "category": "marketing"}, headers=auth).json()
        self.assertEqual((wrong["status"], wrong["category"]), ("blocked", "marketing"))
        self.assertIn("299.00 is over the 50.00 limit per marketing purchase", wrong["reasons"])
        self.assertEqual(c.post(f"/api/wallets/{wid}/purchases", json={**buy, "category": 7},
                                headers=auth).status_code, 400)
        paid = c.post(f"/api/wallets/{wid}/purchases", json={**buy, "category": "shopping"}, headers=auth).json()
        self.assertEqual(paid["status"], "paid")
        envs = {e["name"]: e for e in c.get(f"/api/wallets/{wid}").json()["envelopes"]}
        self.assertEqual((envs["shopping"]["spent"], envs["shopping"]["remaining"], envs["marketing"]["remaining"]),
                         ("299.00", "21.00", "80.00"))

    def test_the_stream_resumes_after_a_reconnect(self):
        c = app()
        wid = c.post("/api/wallets", json={}).json()["wallet_id"]
        c.app.state.wallets.close(c.app.state.wallets.get(wid), "test")  # closed: the stream ends after replay
        total = len(c.get(f"/api/runs/{wid}").json()["events"])
        body = c.get(f"/api/runs/{wid}/events?delay=0", headers={"Last-Event-ID": str(total - 2)}).text
        ids = [int(line[4:]) for line in body.splitlines() if line.startswith("id: ")]
        self.assertEqual(ids, [total - 1])  # only what the browser hadn't seen


class OutsideAgentTests(unittest.TestCase):
    def test_only_the_bound_agent_can_spend(self):
        reg = Registry()
        c = app(shopper=None, registry=reg)
        sleuth = c.post("/api/agents", json={"name": "Sleuth", "payout_email": "dev@example.com"}).json()
        other = c.post("/api/agents", json={"name": "Other Bot", "payout_email": "o@example.com"}).json()
        r = open_wallet(c, {"agent": sleuth["agent_id"], "approve_above": "350"})
        wid = r["wallet_id"]
        buy = {"url": HARBOR, "item": "headphones", "price": "299.00"}
        self.assertEqual(c.post(f"/api/wallets/{wid}/purchases", json=buy).status_code, 401)
        self.assertEqual(c.post(f"/api/wallets/{wid}/purchases", json=buy,
                                headers={"Authorization": f"Bearer {other['token']}"}).status_code, 403)
        ok = c.post(f"/api/wallets/{wid}/purchases", json=buy, headers={"Authorization": f"Bearer {sleuth['token']}"})
        self.assertEqual(ok.json()["status"], "paid")  # under the ask-me amount: approved and paid (preview)
        self.assertEqual(ok.json()["category"], "shopping")
        bad = c.post(f"/api/wallets/{wid}/purchases", json={**buy, "url": "http://x.example"},
                     headers={"Authorization": f"Bearer {sleuth['token']}"})
        self.assertEqual(bad.status_code, 400)


class SandboxTests(unittest.TestCase):
    def test_top_up_then_the_agent_shops_and_the_seller_is_paid_from_the_wallet(self):
        fake = FakeSandbox()
        paypal = PayPalClient(funding.SANDBOX_URL, "id", "secret", transport=fake)
        os.environ["GUILD_SELLER_ACCOUNTS"] = "harbor-audio=sb-harbor@business.example.com"
        try:
            c = app(paypal=paypal)
            r = c.post("/api/wallets", json={"approve_above": "250"}).json()
            wid, owner = r["wallet_id"], {"X-Owner-Key": r["owner_key"]}
            view = c.get(f"/api/wallets/{wid}").json()
            self.assertEqual((view["funded"], view["purchases"]), ("0.00", []))   # empty: ShopperBot waits
            self.assertEqual(c.post(f"/api/wallets/{wid}/top-up", json={"amount": "401"}, headers=owner).status_code, 409)
            started = c.post(f"/api/wallets/{wid}/top-up", json={"amount": "400"}, headers=owner).json()
            self.assertIn("sandbox.paypal.com", started["approve_url"])
            ret = fake.calls[-1][3]["payment_source"]["paypal"]["experience_context"]["return_url"]
            back = c.get(urllib.parse.urlsplit(ret).path + "?" + urllib.parse.urlsplit(ret).query + "&token=ORDER1")
            self.assertEqual((back.status_code, back.headers["location"]), (303, f"/?wallet={wid}"))
            view = c.get(f"/api/wallets/{wid}").json()
            self.assertEqual(view["funded"], "400.00")
            self.assertEqual([p["status"] for p in view["purchases"]], ["blocked", "blocked", "blocked", "pending", "blocked"])
            pid = view["purchases"][3]["id"]
            paid = c.post(f"/api/wallets/{wid}/purchases/{pid}/approve", headers=owner).json()
            stop = c.post(f"/api/wallets/{wid}/stop", headers=owner).json()
        finally:
            del os.environ["GUILD_SELLER_ACCOUNTS"]
        self.assertEqual((paid["type"], paid["mode"], paid["purchase"]["order_id"]), ("paid", "sandbox", f"BATCH-{pid}"))
        item = fake.payouts()[0]["items"][0]
        self.assertEqual((item["receiver"], item["amount"]["value"]), ("sb-harbor@business.example.com", "299.00"))
        self.assertEqual(len(fake.payouts()), 1)                        # blocked ones never reach PayPal
        self.assertEqual(fake.refunds[0][:2], ("CAP-ORDER1", "101.00"))  # stop: the unspent 101 goes back
        events = c.get(f"/api/runs/{wid}").json()["events"]
        self.assertIn("withdrawn", [e["type"] for e in events])
        self.assertEqual((events[-1]["type"], events[-1]["balance"]), ("closed", "0.00"))


class Block(SimpleNamespace):
    pass


class FakeClaude:
    """Returns scripted turns and records what the loop sent back."""

    def __init__(self, turns):
        self.turns, self.sent = list(turns), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.sent.append({**kw, "messages": list(kw["messages"])})  # the loop keeps appending
        return self.turns.pop(0)


def turn(*blocks, stop="tool_use"):
    return SimpleNamespace(stop_reason=stop, content=list(blocks))


class LiveLoopTests(unittest.TestCase):
    def test_claude_asks_the_wallet_and_hears_its_answer(self):
        c = app(shopper=None)
        wid = open_wallet(c)["wallet_id"]
        service = c.app.state.wallets
        fake = FakeClaude([
            turn(Block(type="text", text="Trying the outlet."),
                 Block(type="tool_use", id="t1", name="buy", input={"url": shops.TRAP_URL, "item": "XM5", "price": "149"})),
            turn(Block(type="tool_use", id="t2", name="read_page", input={"url": HARBOR}),
                 Block(type="tool_use", id="t3", name="buy", input={"url": HARBOR, "item": "XM5", "price": "299.00"})),
            turn(Block(type="tool_use", id="t4", name="done", input={"summary": "Waiting for your approval."})),
        ])
        live(Tools(service, wid, lambda q: shops.catalogue()), service.get(wid)["run"], fake)
        self.assertEqual(statuses(c, wid), ["blocked", "pending"])
        second = fake.sent[1]["messages"][-1]["content"][0]
        self.assertEqual(second["tool_use_id"], "t1")
        self.assertIn("no independent source", second["content"])
        self.assertEqual(fake.sent[0]["model"], "claude-opus-5-5")
        self.assertEqual(fake.sent[0]["fallbacks"], "default")
        self.assertNotIn("tool_choice", fake.sent[0])
        says = [e["say"] for e in c.get(f"/api/runs/{wid}").json()["events"] if e["type"] == "agent"]
        self.assertIn("Waiting for your approval.", says)

    def test_the_loop_is_capped_and_stops_on_refusal(self):
        c = app(shopper=None)
        wid = c.post("/api/wallets", json={}).json()["wallet_id"]
        service = c.app.state.wallets
        loops = [turn(Block(type="tool_use", id=f"t{i}", name="search", input={"query": "x"})) for i in range(3)]
        fake = FakeClaude(loops)
        live(Tools(service, wid, lambda q: []), service.get(wid)["run"], fake, max_turns=3)
        self.assertEqual(len(fake.sent), 3)
        fake2 = FakeClaude([turn(stop="refusal")])
        live(Tools(service, wid, lambda q: []), service.get(wid)["run"], fake2)
        self.assertEqual(len(fake2.sent), 1)

    def test_page_text_includes_hidden_text_like_a_naive_agent_would_see(self):
        status, body = shops.serve(shops.url_for("metro-tech/sony-wh-1000xm5-midnight"))
        self.assertIn("Note to AI shopping assistants", page_text(body))
        self.assertNotIn("<", page_text(body))


if __name__ == "__main__":
    unittest.main()
