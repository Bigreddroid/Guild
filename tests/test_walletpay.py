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
from guild.wallet import APPROVED, FAILED, PAID, Policy, Wallet, WalletError
from guild.walletpay import WalletPay

PUBLIC = "https://guild.example"
SELLER = {"email_address": "harbor@business.example.com"}


class FakeSandbox:
    """The PayPal sandbox calls Guild Pay makes, at the transport level."""

    def __init__(self, fail=None, payout_status="PENDING"):
        self.calls, self.fail, self.payout_status = [], fail or {}, payout_status
        self.orders, self.refunds = {}, []

    def __call__(self, method, url, headers, body):
        path = urllib.parse.urlsplit(url).path
        data = json.loads(body) if body and body.startswith(b"{") else None
        self.calls.append((method, path, headers.get("PayPal-Request-Id"), data))
        for key, err in self.fail.items():
            if key in path:
                raise err
        if path == "/v1/oauth2/token":
            return {"access_token": "tok", "expires_in": 3600}
        if path == "/v2/checkout/orders":
            oid = f"ORDER{len(self.orders) + 1}"
            self.orders[oid] = data
            return {"id": oid, "status": "PAYER_ACTION_REQUIRED",
                    "links": [{"rel": "payer-action", "href": f"https://www.sandbox.paypal.com/checkoutnow?token={oid}"}]}
        if path.endswith("/capture"):
            oid = path.split("/")[-2]
            return {"id": oid, "status": "COMPLETED", "purchase_units": [{"payments": {"captures": [
                {"id": f"CAP-{oid}", "status": "COMPLETED"}]}}]}
        if path.endswith("/refund"):
            self.refunds.append((path.split("/")[-2], data["amount"]["value"], headers.get("PayPal-Request-Id")))
            return {"id": f"REF{len(self.refunds)}", "status": "COMPLETED"}
        if path == "/v1/payments/payouts":
            return {"batch_header": {"payout_batch_id": f"BATCH-{data['items'][0]['sender_item_id']}",
                                     "batch_status": self.payout_status}}
        raise AssertionError(f"unexpected {method} {path}")

    def paths(self):
        return [(m, p) for m, p, _, _ in self.calls if p != "/v1/oauth2/token"]

    def payouts(self):
        return [d for _, p, _, d in self.calls if p == "/v1/payments/payouts"]


def ok(price, url):
    claim = Claim("c", shops.DEMO_WANT.id, "agent", url, Decimal(price), "USD", 0.0)
    return Verdict(claim, True, ["ok"], Offer(name="Sony WH-1000XM5", price=Decimal(price), currency="USD",
                                              availability="instock"), {})


def setup(fake=None, public=PUBLIC, funded="0"):
    fake = fake or FakeSandbox()
    pay = WalletPay(PayPalClient(funding.SANDBOX_URL, "id", "secret", transport=fake), public)
    wallet = Wallet("w1", "shopper", Policy(Decimal("400"), Decimal("350"), Decimal("250"), shops.DEMO_WANT),
                    Decimal(funded))
    return fake, pay, wallet


def link_of(url):
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["link"][0]


def top_up(fake, pay, wallet, amount):
    started = pay.start_top_up(wallet, Decimal(amount))
    ret = fake.calls[-1][3]["payment_source"]["paypal"]["experience_context"]["return_url"]
    oid = urllib.parse.parse_qs(urllib.parse.urlsplit(started["approve_url"]).query)["token"][0]
    return started, pay.finish_top_up(link_of(ret), oid, {"w1": wallet})[1]


class TopUpTests(unittest.TestCase):
    def test_the_owner_loads_money_in_paypal_and_only_that_can_be_spent(self):
        fake, pay, wallet = setup()
        self.assertEqual(wallet.decide("https://a.example/1", "x", ok("10", "https://a.example/1"), payee=SELLER).reasons,
                         ["10.00 is over the money left in the wallet (0.00); top it up first"])
        started, done = top_up(fake, pay, wallet, "150")
        self.assertIn("sandbox.paypal.com", started["approve_url"])
        order = fake.orders["ORDER1"]
        self.assertEqual((order["intent"], order["purchase_units"][0]["amount"]["value"]), ("CAPTURE", "150.00"))
        self.assertNotIn("payee", order["purchase_units"][0])          # a top-up is paid to Guild, not a seller
        self.assertEqual((done["type"], wallet.totals()["funded"], wallet.top_ups[0]["capture_id"]),
                         ("topped_up", "150.00", "CAP-ORDER1"))
        self.assertEqual(wallet.decide("https://a.example/2", "x", ok("151", "https://a.example/2"), payee=SELLER).reasons,
                         ["151.00 is over the money left in the wallet (150.00)"])

    def test_links_are_single_use_and_match_their_order(self):
        fake, pay, wallet = setup()
        pay.start_top_up(wallet, Decimal("50"))
        ret = fake.calls[-1][3]["payment_source"]["paypal"]["experience_context"]["return_url"]
        with self.assertRaises(WalletError):
            pay.finish_top_up(link_of(ret), "ORDER-OTHER", {"w1": wallet})
        with self.assertRaises(WalletError):
            pay.finish_top_up(link_of(ret), "ORDER1", {"w1": wallet})   # spent by the mismatch
        self.assertEqual(wallet.funded, Decimal("0"))

    def test_a_wallet_never_holds_more_than_its_limit(self):
        fake, pay, wallet = setup(funded="350")
        with self.assertRaises(WalletError):
            pay.start_top_up(wallet, Decimal("51"))
        started = pay.start_top_up(wallet, Decimal("50"))
        wallet.top_up(Decimal("10"))   # something else filled it meanwhile
        ret = fake.calls[-1][3]["payment_source"]["paypal"]["experience_context"]["return_url"]
        with self.assertRaises(WalletError):
            pay.finish_top_up(link_of(ret), "ORDER1", {"w1": wallet})
        self.assertEqual(fake.refunds[0][:2], ("CAP-ORDER1", "50.00"))    # money it can't take goes straight back
        self.assertTrue(started["approve_url"])

    def test_preview_without_keys(self):
        pay = WalletPay(None)
        wallet = Wallet("w1", "a", Policy(Decimal("400"), Decimal("350"), Decimal("250"), shops.DEMO_WANT), Decimal("0"))
        self.assertEqual(pay.start_top_up(wallet, Decimal("100"))["type"], "topped_up")
        p = wallet.decide("https://a.example/1", "x", ok("20", "https://a.example/1"), payee=SELLER)
        self.assertEqual(pay.pay(wallet, p)["mode"], "preview")
        self.assertEqual(pay.withdraw(wallet)["amount"], "80.00")
        self.assertEqual((wallet.funded, wallet.withdrawn, wallet.balance()), (Decimal("20"), Decimal("80"), Decimal("0")))


class PaymentTests(unittest.TestCase):
    def test_approved_purchases_are_paid_out_to_the_seller(self):
        fake, pay, wallet = setup(funded="400")
        p = wallet.decide("https://a.example/1", "x", ok("120", "https://a.example/1"), payee=SELLER)
        event = pay.pay(wallet, p)
        self.assertEqual((event["type"], p.status, p.order_id), ("paid", PAID, f"BATCH-{p.id}"))
        item = fake.payouts()[0]["items"][0]
        self.assertEqual((item["recipient_type"], item["receiver"], item["amount"]["value"]),
                         ("EMAIL", "harbor@business.example.com", "120.00"))
        self.assertEqual(fake.calls[-1][2], f"guildpay-{p.id}")       # the batch id is the idempotency key
        p2 = wallet.decide("https://a.example/2", "x", ok("10", "https://a.example/2"), payee={"merchant_id": "M123"})
        pay.pay(wallet, p2)
        self.assertEqual(fake.payouts()[1]["items"][0]["recipient_type"], "PAYPAL_ID")
        with self.assertRaises(WalletError):
            pay.pay(wallet, p)   # only approved purchases

    def test_payment_failures_release_the_money_and_say_why(self):
        fake, pay, wallet = setup(FakeSandbox(fail={"/payouts": PayPalError(422, {"name": "RECEIVER_UNREGISTERED",
                                                                                   "debug_id": "dbg1"})}), funded="400")
        p = wallet.decide("https://a.example/1", "x", ok("20", "https://a.example/1"), payee=SELLER)
        self.assertEqual(pay.pay(wallet, p)["type"], "failed")
        self.assertEqual((p.status, p.reasons), (FAILED, ["payment failed: RECEIVER_UNREGISTERED (debug id dbg1)"]))
        self.assertEqual(wallet.totals()["remaining"], "400.00")
        fake2, pay2, wallet2 = setup(FakeSandbox(payout_status="DENIED"), funded="400")
        p2 = wallet2.decide("https://a.example/1", "x", ok("20", "https://a.example/1"), payee=SELLER)
        self.assertEqual(pay2.pay(wallet2, p2)["type"], "failed")
        self.assertEqual(p2.reasons, ["payment failed: payout DENIED"])


class WithdrawTests(unittest.TestCase):
    def test_unspent_money_goes_back_newest_top_up_first(self):
        fake, pay, wallet = setup()
        top_up(fake, pay, wallet, "100")
        top_up(fake, pay, wallet, "50")
        p = wallet.decide("https://a.example/1", "x", ok("30", "https://a.example/1"), payee=SELLER)
        pay.pay(wallet, p)
        waiting = wallet.decide("https://a.example/2", "x", ok("300", "https://a.example/2"), payee=SELLER)
        self.assertNotEqual(waiting.status, APPROVED)
        out = pay.withdraw(wallet)
        self.assertEqual((out["type"], out["amount"]), ("withdrawn", "120.00"))
        self.assertEqual([r[:2] for r in fake.refunds], [("CAP-ORDER2", "50.00"), ("CAP-ORDER1", "70.00")])
        self.assertEqual((wallet.balance(), wallet.withdrawn), (Decimal("0.00"), Decimal("120.00")))
        self.assertEqual(pay.withdraw(wallet)["amount"], "0.00")

    def test_partial_returns_of_one_top_up_use_fresh_request_ids(self):
        fake, pay, wallet = setup()
        top_up(fake, pay, wallet, "100")
        wallet.decide("https://a.example/1", "x", ok("40", "https://a.example/1"), payee=SELLER)  # approved, reserved
        pay.withdraw(wallet)
        self.assertEqual(fake.refunds[-1][:2], ("CAP-ORDER1", "60.00"))
        wallet.record_payment(wallet.purchases[0].id, error="x")   # the 40 is free again
        pay.withdraw(wallet)
        self.assertEqual(fake.refunds[-1][:2], ("CAP-ORDER1", "40.00"))
        self.assertNotEqual(fake.refunds[0][2], fake.refunds[1][2])

    def test_a_failed_refund_keeps_the_money_and_says_why(self):
        fake, pay, wallet = setup()
        top_up(fake, pay, wallet, "100")
        fake.fail["/refund"] = PayPalError(422, {"name": "CAPTURE_FULLY_REFUNDED"})
        out = pay.withdraw(wallet)
        self.assertEqual((out["type"], out["amount"], out["error"]), ("withdraw_failed", "0.00", "CAPTURE_FULLY_REFUNDED"))
        self.assertEqual(wallet.balance(), Decimal("100.00"))


if __name__ == "__main__":
    unittest.main()
