import json
import os
import sys
import unittest
import urllib.parse
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from guild import funding, shops
from guild.bounty import Claim, Offer, Verdict
from guild.paypal import PayPalClient, PayPalError
from guild.wallet import APPROVED, FAILED, PAID, REFUNDED, Policy, Wallet, WalletError
from guild.walletpay import WalletPay

PUBLIC = "https://guild.example"
SELLER = {"email_address": "harbor@business.example.com"}


class FakeSandbox:
    """The PayPal sandbox calls Guild Pay makes, at the transport level."""

    def __init__(self, fail=None, vault_status="COMPLETED"):
        self.calls, self.fail, self.vault_status = [], fail or {}, vault_status
        self.orders, self.tokens = {}, set()

    def __call__(self, method, url, headers, body):
        path = urllib.parse.urlsplit(url).path
        data = json.loads(body) if body and body.startswith(b"{") else None
        self.calls.append((method, path, headers.get("PayPal-Request-Id"), data))
        for key, err in self.fail.items():
            if key in path:
                raise err
        if path == "/v1/oauth2/token":
            return {"access_token": "tok", "expires_in": 3600}
        if path == "/v3/vault/setup-tokens":
            return {"id": "SETUP1", "status": "PAYER_ACTION_REQUIRED",
                    "links": [{"rel": "approve", "href": "https://www.sandbox.paypal.com/agreements/approve?approval_session_id=SETUP1"}]}
        if path == "/v3/vault/payment-tokens":
            self.tokens.add("PT1")
            return {"id": "PT1", "payment_source": {"paypal": {"email_address": "owner@personal.example.com"}}}
        if path.startswith("/v3/vault/payment-tokens/") and method == "DELETE":
            self.tokens.discard(path.rsplit("/", 1)[1])
            return {}
        if path == "/v2/checkout/orders":
            oid = f"ORDER{len(self.orders) + 1}"
            self.orders[oid] = data
            if "vault_id" in data["payment_source"]["paypal"]:
                return {"id": oid, "status": self.vault_status, "purchase_units": [{"payments": {"captures": [
                    {"id": f"CAP-{oid}", "status": "COMPLETED"}]}}] if self.vault_status == "COMPLETED" else []}
            return {"id": oid, "status": "PAYER_ACTION_REQUIRED",
                    "links": [{"rel": "payer-action", "href": f"https://www.sandbox.paypal.com/checkoutnow?token={oid}"}]}
        if path.endswith("/capture"):
            oid = path.split("/")[-2]
            return {"id": oid, "status": "COMPLETED", "purchase_units": [{"payments": {"captures": [
                {"id": f"CAP-{oid}", "status": "COMPLETED"}]}}]}
        if path.endswith("/refund"):
            return {"id": "REF1", "status": "COMPLETED"}
        raise AssertionError(f"unexpected {method} {path}")

    def paths(self):
        return [(m, p) for m, p, _, _ in self.calls if p != "/v1/oauth2/token"]


def ok(price, url):
    claim = Claim("c", shops.DEMO_WANT.id, "agent", url, Decimal(price), "USD", 0.0)
    return Verdict(claim, True, ["ok"], Offer(name="Sony WH-1000XM5", price=Decimal(price), currency="USD",
                                              availability="instock"), {})


def setup(fake=None, public=PUBLIC):
    fake = fake or FakeSandbox()
    pay = WalletPay(PayPalClient(funding.SANDBOX_URL, "id", "secret", transport=fake), public)
    wallet = Wallet("w1", "shopper", Policy(Decimal("400"), Decimal("350"), Decimal("250"), shops.DEMO_WANT))
    return fake, pay, wallet


def link_of(url):
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["link"][0]


class MandateTests(unittest.TestCase):
    def test_owner_approves_once_and_guild_keeps_only_the_token(self):
        fake, pay, wallet = setup()
        url = pay.start_mandate(wallet)
        self.assertIn("sandbox.paypal.com", url)
        body = fake.calls[-1][3]["payment_source"]["paypal"]
        return_url = body["experience_context"]["return_url"]
        self.assertTrue(return_url.startswith(f"{PUBLIC}/api/wallets/paypal/mandate?link="))
        pay.finish_mandate(link_of(return_url), {"w1": wallet})
        self.assertEqual(wallet.mandate_id, "PT1")
        with self.assertRaises(WalletError):  # links are single-use
            pay.finish_mandate(link_of(return_url), {"w1": wallet})
        self.assertEqual(pay.revoke(wallet), {"type": "mandate", "revoked": True})
        self.assertEqual((wallet.mandate_id, fake.tokens), ("", set()))
        self.assertIn(("DELETE", "/v3/vault/payment-tokens/PT1"), fake.paths())

    def test_preview_without_keys(self):
        pay = WalletPay(None)
        wallet = Wallet("w1", "a", Policy(Decimal("400"), Decimal("350"), Decimal("250"), shops.DEMO_WANT))
        self.assertEqual(pay.mode, "preview")
        self.assertIsNone(pay.start_mandate(wallet))
        p = wallet.decide("https://a.example/1", "x", ok("10", "https://a.example/1"), payee=SELLER)
        event = pay.pay(wallet, p)
        self.assertEqual((event["type"], event["mode"], p.order_id), ("paid", "preview", f"PREVIEW-{p.id}"))
        self.assertEqual(pay.refund(wallet, p)["type"], "refunded")


class PaymentTests(unittest.TestCase):
    def test_with_a_mandate_the_seller_is_paid_directly(self):
        fake, pay, wallet = setup()
        wallet.mandate_id = "PT1"
        p = wallet.decide("https://a.example/1", "Sony WH-1000XM5", ok("199", "https://a.example/1"), payee=SELLER)
        event = pay.pay(wallet, p)
        self.assertEqual((event["type"], event["via"], p.status, p.capture_id), ("paid", "mandate", PAID, "CAP-ORDER1"))
        order = fake.orders["ORDER1"]
        unit = order["purchase_units"][0]
        self.assertEqual((order["intent"], unit["payee"], unit["amount"]["value"], unit["reference_id"]),
                         ("CAPTURE", SELLER, "199.00", p.id))
        self.assertEqual(order["payment_source"], {"paypal": {"vault_id": "PT1"}})
        self.assertEqual(fake.calls[-1][2], f"{p.id}-pay")  # a retry can't charge twice

    def test_without_a_mandate_the_owner_pays_in_checkout_then_it_is_captured(self):
        fake, pay, wallet = setup()
        p = wallet.decide("https://a.example/1", "x", ok("199", "https://a.example/1"), payee=SELLER)
        event = pay.pay(wallet, p)
        self.assertEqual(event["type"], "checkout")
        self.assertEqual(p.status, APPROVED)  # still reserved until PayPal confirms
        ctx = fake.orders["ORDER1"]["payment_source"]["paypal"]["experience_context"]
        link = link_of(ctx["return_url"])
        with self.assertRaises(WalletError):
            pay.finish_checkout(link, "ORDER-OTHER", {"w1": wallet})  # token must match; link now spent
        p2 = wallet.decide("https://a.example/2", "x", ok("20", "https://a.example/2"), payee=SELLER)
        pay.pay(wallet, p2)
        ctx2 = fake.orders["ORDER2"]["payment_source"]["paypal"]["experience_context"]
        _, done = pay.finish_checkout(link_of(ctx2["return_url"]), "ORDER2", {"w1": wallet})
        self.assertEqual((done["type"], p2.status, p2.capture_id), ("paid", PAID, "CAP-ORDER2"))
        self.assertEqual(fake.orders["ORDER2"]["purchase_units"][0]["payee"], SELLER)

    def test_payment_failures_release_the_budget_and_say_why(self):
        err = PayPalError(422, {"name": "PAYEE_ACCOUNT_RESTRICTED", "debug_id": "dbg1"})
        fake, pay, wallet = setup(FakeSandbox(fail={"/v2/checkout/orders": err}))
        wallet.mandate_id = "PT1"
        p = wallet.decide("https://a.example/1", "x", ok("199", "https://a.example/1"), payee=SELLER)
        self.assertEqual(pay.pay(wallet, p)["type"], "failed")
        self.assertEqual((p.status, p.reasons), (FAILED, ["payment failed: PAYEE_ACCOUNT_RESTRICTED (debug id dbg1)"]))
        self.assertEqual(wallet.totals()["spent"], "0.00")
        fake2, pay2, wallet2 = setup(FakeSandbox(vault_status="PAYER_ACTION_REQUIRED"))
        wallet2.mandate_id = "PT1"
        p2 = wallet2.decide("https://a.example/1", "x", ok("10", "https://a.example/1"), payee=SELLER)
        self.assertEqual(pay2.pay(wallet2, p2)["type"], "failed")
        self.assertIn("PAYER_ACTION_REQUIRED", p2.reasons[0])

    def test_refunds_go_back_to_the_owner_and_free_the_budget(self):
        fake, pay, wallet = setup()
        wallet.mandate_id = "PT1"
        p = wallet.decide("https://a.example/1", "x", ok("199", "https://a.example/1"), payee=SELLER)
        pay.pay(wallet, p)
        event = pay.refund(wallet, p)
        self.assertEqual((event["type"], p.status, wallet.totals()["spent"]), ("refunded", REFUNDED, "0.00"))
        method, path, request_id, body = fake.calls[-1]
        self.assertEqual((path, request_id, body["amount"]["value"]),
                         ("/v2/payments/captures/CAP-ORDER1/refund", "CAP-ORDER1-refund", "199.00"))
        with self.assertRaises(WalletError):
            pay.refund(wallet, p)

    def test_only_approved_purchases_can_be_paid(self):
        fake, pay, wallet = setup()
        pending = wallet.decide("https://a.example/1", "x", ok("300", "https://a.example/1"), payee=SELLER)
        with self.assertRaises(WalletError):
            pay.pay(wallet, pending)
        self.assertEqual(fake.paths(), [])

    def test_paypal_is_only_ever_the_sandbox(self):
        self.assertTrue(funding.is_paypal_sandbox(funding.SANDBOX_URL))
        for live in ("https://api-m.paypal.com", "https://api-m.sandbox.paypal.com.evil.example"):
            self.assertFalse(funding.is_paypal_sandbox(live), live)

if __name__ == "__main__":
    unittest.main()
