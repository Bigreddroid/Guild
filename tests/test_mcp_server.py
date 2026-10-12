import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples", "mcp_server"))

from guild import shops
from guild_pay_mcp import Server
from tests.test_wallet_api import app


def through(client, token):
    def call(method, path, body=None):
        r = client.request(method, path, json=body, headers={"Authorization": f"Bearer {token}"})
        return r.status_code, r.json()
    return call


class McpServerTests(unittest.TestCase):
    def setUp(self):
        self.c = app(shopper=None)
        agent = self.c.post("/api/agents", json={"name": "Desk Agent", "payout_email": "d@example.com"}).json()
        self.wid = self.c.post("/api/wallets", json={"agent": agent["agent_id"]}).json()["wallet_id"]
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
        self.assertEqual([t["name"] for t in replies[1]["result"]["tools"]], ["wallet_status", "search_products", "buy"])
        self.assertEqual(replies[2]["error"]["code"], -32601)

    def test_an_mcp_agent_buys_and_the_wallet_decides(self):
        def call(n, name, args):
            return {"jsonrpc": "2.0", "id": n, "method": "tools/call", "params": {"name": name, "arguments": args}}
        replies = self.rpc(call(1, "search_products", {"query": "sony"}),
                           call(2, "buy", {"url": shops.TRAP_URL, "item": "XM5", "price": "149.00"}),
                           call(3, "buy", {"url": shops.url_for("harbor-audio/sony-wh-1000xm5-black"),
                                           "item": "XM5", "price": "299.00"}),
                           call(4, "wallet_status", {}))
        listings = json.loads(replies[0]["result"]["content"][0]["text"])["listings"]
        self.assertEqual(len(listings), len(shops.PRODUCTS))
        self.assertEqual(json.loads(replies[1]["result"]["content"][0]["text"])["status"], "blocked")
        self.assertEqual(json.loads(replies[2]["result"]["content"][0]["text"])["status"], "pending")
        status = json.loads(replies[3]["result"]["content"][0]["text"])
        self.assertEqual((status["reserved"], status["remaining"]), ("299.00", "101.00"))
        self.assertFalse(any(r["result"]["isError"] for r in replies))

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
