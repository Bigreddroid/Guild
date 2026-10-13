import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples", "mcp_server"))

from guild import shops
from guild_pay_mcp import Server
from tests.test_wallet_api import open_wallet, app


def through(client, token):
    def call(method, path, body=None):
        r = client.request(method, path, json=body, headers={"Authorization": f"Bearer {token}"})
        return r.status_code, r.json()
    return call


class McpServerTests(unittest.TestCase):
    def setUp(self):
        self.c = app(shopper=None)
        agent = self.c.post("/api/agents", json={"name": "Desk Agent", "payout_email": "d@example.com"}).json()
        r = open_wallet(self.c, {"agent": agent["agent_id"], "budget": "400", "max_per_purchase": "350",
                                 "approve_above": "250", "envelopes": {
                                     "shopping": {"limit": "300", "max_per_purchase": "300", "approve_above": "250"},
                                     "marketing": {"limit": "60"}, "tools": {"limit": "40"}}})
        self.wid, self.owner = r["wallet_id"], {"X-Owner-Key": r["owner_key"]}
        self.server = Server(through(self.c, agent["token"]), self.wid)

    def rpc(self, *messages):
        out = io.StringIO()
        self.server.serve(io.StringIO("".join(json.dumps(m) + "\n" for m in messages)), out)
        return [json.loads(line) for line in out.getvalue().splitlines()]

    def test_handshake_and_tool_list(self):
        replies = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                           {"jsonrpc": "2.0", "method": "notifications/initialized"},
                           {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                           {"jsonrpc": "2.0", "id": 3, "method": "nope"})
        self.assertEqual([r["id"] for r in replies], [1, 2, 3])  # the notification gets no reply
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual([t["name"] for t in replies[1]["result"]["tools"]], ["wallet_status", "search_products", "buy", "book_promotion", "send_proof", "subscribe_tool",
                          "report_tool_use"])
        self.assertEqual(replies[2]["error"]["code"], -32601)

    def test_an_mcp_agent_buys_and_the_wallet_decides(self):
        def call(n, name, args):
            return {"jsonrpc": "2.0", "id": n, "method": "tools/call", "params": {"name": name, "arguments": args}}
        replies = self.rpc(call(1, "search_products", {"query": "sony"}),
                           call(2, "buy", {"url": shops.TRAP_URL, "item": "XM5", "price": "149.00"}),
                           call(3, "buy", {"url": shops.url_for("harbor-audio/sony-wh-1000xm5-black"),
                                           "item": "XM5", "price": "299.00"}),
                           call(4, "wallet_status", {}),
                           call(5, "buy", {"url": shops.url_for("harbor-audio/sony-wh-1000xm5-black"),
                                           "item": "XM5", "price": "299.00", "category": "marketing"}))
        listings = json.loads(replies[0]["result"]["content"][0]["text"])["listings"]
        self.assertEqual(len(listings), len(shops.PRODUCTS))
        self.assertEqual(json.loads(replies[1]["result"]["content"][0]["text"])["status"], "blocked")
        self.assertEqual(json.loads(replies[2]["result"]["content"][0]["text"])["status"], "pending")
        status = json.loads(replies[3]["result"]["content"][0]["text"])
        self.assertEqual((status["reserved"], status["remaining"]), ("299.00", "101.00"))
        self.assertEqual(status["envelopes"][0]["name"], "shopping")
        wrong = json.loads(replies[4]["result"]["content"][0]["text"])
        self.assertEqual((wrong["category"], wrong["status"]), ("marketing", "blocked"))   # over the $60 marketing limit
        self.assertFalse(any(r["result"]["isError"] for r in replies))

    def tool(self, n, name, args):
        (reply,) = self.rpc({"jsonrpc": "2.0", "id": n, "method": "tools/call",
                             "params": {"name": name, "arguments": args}})
        return json.loads(reply["result"]["content"][0]["text"]), reply["result"]["isError"]

    def approve(self, pid):
        r = self.c.post(f"/api/wallets/{self.wid}/purchases/{pid}/approve", headers=self.owner)
        self.assertEqual(r.status_code, 200, r.text)

    def test_an_agent_books_a_creator_who_is_paid_only_on_a_real_post(self):
        brief = shops.demo_brief()
        booking, err = self.tool(1, "book_promotion", {k: brief[k] for k in (
            "creator", "channel", "paypal_email", "amount", "link", "words", "deadline_minutes", "category")})
        self.assertFalse(err)
        self.assertEqual(booking["status"], "pending")                     # the agent chose who gets paid: owner OKs
        proof, err = self.tool(2, "send_proof", {"purchase_id": booking["id"], "url": brief["proofs"]["good"]})
        self.assertTrue(err)                                                # not held yet: nothing to prove
        self.approve(booking["id"])
        bad, err = self.tool(3, "send_proof", {"purchase_id": booking["id"], "url": brief["proofs"]["hidden_ad"]})
        self.assertEqual((err, bad["valid"]), (False, False))
        self.assertTrue(bad["reasons"])
        paid, err = self.tool(4, "send_proof", {"purchase_id": booking["id"], "url": brief["proofs"]["good"]})
        self.assertEqual((err, paid["type"], paid["purchase"]["status"]), (False, "paid", "paid"))

    def test_an_agent_keeps_a_tool_the_owner_okd_and_reports_using_it(self):
        sub, err = self.tool(1, "subscribe_tool", {k: v for k, v in shops.demo_renewal().items() if k != "must_have"})
        self.assertEqual((err, sub["status"], sub["booked_by"]), (False, "waiting", "agent"))
        first = self.c.get(f"/api/wallets/{self.wid}").json()["purchases"][-1]
        self.approve(first["id"])
        used, err = self.tool(2, "report_tool_use", {"renewal_id": sub["id"]})
        self.assertFalse(err)
        ren = self.c.get(f"/api/wallets/{self.wid}").json()["renewals"][0]
        self.assertEqual((ren["status"], ren["charges"], ren["last_used_s_ago"] is not None), ("active", 1, True))

    def test_ids_cannot_reach_other_paths(self):
        calls = []
        spy = Server(lambda *a: calls.append(a) or (200, {}), self.wid)
        for name, args in (("send_proof", {"purchase_id": "../../stop", "url": "https://x.example"}),
                           ("report_tool_use", {"renewal_id": "r1/../../close"}), ("report_tool_use", {})):
            text, err = spy.tool(name, args)
            self.assertTrue(err, name)
        self.assertEqual(calls, [])

    def test_errors_come_back_as_tool_errors(self):
        bad = Server(through(self.c, "gk_wrong"), self.wid)
        out = io.StringIO()
        bad.serve(io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "buy", "arguments": {"url": "https://x.example", "item": "x", "price": "1"}}}) + "\nnot json\n"), out)
        first, second = [json.loads(l) for l in out.getvalue().splitlines()]
        self.assertTrue(first["result"]["isError"])
        self.assertEqual(second["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main()
