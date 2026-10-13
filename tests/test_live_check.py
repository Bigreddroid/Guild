import json
import os
import sys
import tempfile
import unittest
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from guild import funding, readiness
from guild.paypal import PayPalClient
from scripts import live_check
from scripts.live_check import CheckFailed, LiveCheck
from tests.test_wallet_api import PUBLIC, app
from tests.test_walletpay import FakeSandbox

ACCOUNTS = "harbor-audio=sb-harbor@business.example.com,maya-reviews=sb-maya@personal.example.com"


def client_call(c):
    def call(method, path, body=None, headers=None):
        r = c.request(method, path, json=body, headers=headers or {})
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {}
    return call


class Env:
    """Set environment variables for one test and put them back afterwards."""
    def __init__(self, **values):
        self.values, self.old = values, {}

    def __enter__(self):
        for k, v in self.values.items():
            self.old[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def __exit__(self, *exc):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class ReadinessTests(unittest.TestCase):
    def test_says_what_is_missing_and_never_shows_values(self):
        with Env(PAYPAL_CLIENT_ID=None, PAYPAL_CLIENT_SECRET=None, GUILD_SELLER_ACCOUNTS=None):
            r = readiness.check(None, "")
        missing = {i["key"] for i in r["items"] if i["ok"] is False}
        self.assertEqual((r["mode"], r["ready_for_live"]), ("preview", False))
        self.assertTrue({"paypal_keys", "public_url", "seller_account", "creator_account"} <= missing)
        self.assertTrue(all(i["how"] for i in r["items"] if i["ok"] is False))
        with Env(PAYPAL_CLIENT_ID="id-123", PAYPAL_CLIENT_SECRET="secret-456", GUILD_SELLER_ACCOUNTS=ACCOUNTS):
            paypal = PayPalClient(funding.SANDBOX_URL, "id-123", "secret-456", transport=FakeSandbox())
            r = readiness.check(paypal, PUBLIC)
        self.assertEqual((r["mode"], r["ready_for_live"]), ("sandbox", True))
        text = json.dumps(r)
        for secret in ("id-123", "secret-456", "sb-harbor", "sb-maya"):
            self.assertNotIn(secret, text)

    def test_a_live_base_url_is_called_out(self):
        with Env(PAYPAL_CLIENT_ID="x", PAYPAL_CLIENT_SECRET="y", PAYPAL_BASE_URL="https://api-m.paypal.com"):
            item = readiness.check(None, PUBLIC)["items"][0]
        self.assertFalse(item["ok"])
        self.assertIn("api-m.sandbox.paypal.com", item["how"])

    def test_healthz_and_the_endpoint_carry_it(self):
        c = app()
        self.assertEqual(c.get("/healthz").json()["readiness"]["mode"], "preview")
        self.assertIn("items", c.get("/api/readiness").json())


class LiveCheckTests(unittest.TestCase):
    def test_the_whole_story_against_a_sandbox_server(self):
        fake = FakeSandbox()
        with Env(GUILD_SELLER_ACCOUNTS=ACCOUNTS):
            c = app(paypal=PayPalClient(funding.SANDBOX_URL, "id", "secret", transport=fake))

            def pay_in_paypal(url):   # the buyer approves; PayPal sends them back with the order token
                ret = fake.calls[-1][3]["payment_source"]["paypal"]["experience_context"]["return_url"]
                token = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["token"][0]
                parts = urllib.parse.urlsplit(ret)
                self.assertEqual(c.get(f"{parts.path}?{parts.query}&token={token}").status_code, 303)

            said = []
            result = LiveCheck(client_call(c), approve=pay_in_paypal, sleep=lambda s: None, wait_s=10,
                               say=said.append).run()
        self.assertTrue(result["ok"])
        steps = {s["step"]: s for s in result["steps"]}
        self.assertEqual(list(steps), ["readiness", "open wallet", "top up", "agent shops", "pay the seller",
                                       "book creator", "pay the creator", "return unspent"])
        self.assertIn("4 blocked ($515.00)", steps["agent shops"]["detail"])
        self.assertTrue(steps["pay the seller"]["payout_batch_id"].startswith("BATCH-"))
        self.assertTrue(steps["pay the creator"]["payout_batch_id"].startswith("BATCH-"))
        self.assertEqual(steps["return unspent"]["detail"], "$61.00 refunded to the buyer")
        self.assertIn("s***@personal.example.com", steps["book creator"]["detail"])   # masked
        self.assertNotIn("sb-maya", json.dumps(result))
        self.assertEqual([i["items"][0]["receiver"] for i in fake.payouts()],
                         ["sb-harbor@business.example.com", "sb-maya@personal.example.com"])
        self.assertEqual(fake.refunds[-1][:2], ("CAP-ORDER1", "61.00"))

    def test_a_server_without_keys_is_refused_unless_it_is_a_dry_run(self):
        c = app()
        with self.assertRaises(CheckFailed) as caught:
            LiveCheck(client_call(c), say=lambda s: None).run()
        self.assertIn("PayPal sandbox app keys", str(caught.exception))
        said = []
        with self.assertRaises(CheckFailed):
            LiveCheck(client_call(c), say=said.append).run()
        self.assertTrue(any("GUILD_SELLER_ACCOUNTS" in line for line in said))   # each fix is spelled out
        result = LiveCheck(client_call(app()), allow_preview=True, sleep=lambda s: None, wait_s=10,
                           say=lambda s: None).run()
        self.assertEqual((result["ok"], result["mode"]), (True, "preview"))

    def test_main_writes_the_evidence_file(self):
        c = app()
        out = os.path.join(tempfile.mkdtemp(), "live.json")
        real = live_check.http_call
        live_check.http_call = lambda base: client_call(c)
        try:
            code = live_check.main(["--url", "https://guild.example", "--out", out, "--wait", "5"])
        finally:
            live_check.http_call = real
        with open(out) as f:
            evidence = json.load(f)
        self.assertEqual((code, evidence["ok"], evidence["server"]), (1, False, "https://guild.example"))
        self.assertIn("readiness", evidence["error"])


if __name__ == "__main__":
    unittest.main()
