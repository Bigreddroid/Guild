import json
import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from guild.payments import build_payout, settle_run
from guild.paypal import PayPalClient, PayPalError

D = Decimal


class Recorder:
    def __init__(self, *responses):
        self.calls, self.queue = [], list(responses)

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, json.loads(body) if body and body.startswith(b"{") else body))
        if url.endswith("/v1/oauth2/token"):
            return {"access_token": "t", "expires_in": 3600}
        nxt = self.queue.pop(0) if self.queue else {"id": "X", "status": "COMPLETED"}
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def api(self):
        return [c for c in self.calls if not c[1].endswith("/token")]


def client(t):
    return PayPalClient("https://api-m.sandbox.paypal.com", "id", "secret", t)


class BuildPayoutTests(unittest.TestCase):
    def test_deterministic_batch_and_items(self):
        p1 = build_payout("run42", {"b@x": D("0.80"), "a@x": D("0.40")}, "USD")
        p2 = build_payout("run42", {"a@x": D("0.40"), "b@x": D("0.80")}, "USD")
        self.assertEqual(p1, p2)
        self.assertEqual(p1["sender_batch_header"]["sender_batch_id"], "guild-run42")
        self.assertEqual([i["receiver"] for i in p1["items"]], ["a@x", "b@x"])
        self.assertEqual(p1["items"][1]["amount"], {"value": "0.80", "currency": "USD"})
        self.assertEqual(p1["items"][0]["recipient_type"], "EMAIL")

    def test_zero_or_negative_amounts_are_dropped(self):
        p = build_payout("r", {"a@x": D("0"), "b@x": D("-1"), "c@x": D("0.10")}, "USD")
        self.assertEqual([i["receiver"] for i in p["items"]], ["c@x"])

    def test_empty_owed_builds_nothing(self):
        self.assertIsNone(build_payout("r", {}, "USD"))


class SettleRunTests(unittest.TestCase):
    def test_spend_is_captured_then_owners_paid(self):
        t = Recorder({"id": "CAP1", "status": "COMPLETED"},
                     {"batch_header": {"payout_batch_id": "PB1", "batch_status": "PENDING"}})
        r = settle_run(client(t), "run1", "AUTH1", D("1.20"), {"a@x": D("1.20")}, "USD")
        (m1, u1, h1, b1), (m2, u2, h2, b2) = t.api()
        self.assertTrue(u1.endswith("/v2/payments/authorizations/AUTH1/capture"))
        self.assertEqual(b1["amount"]["value"], "1.20")
        self.assertTrue(b1["final_capture"])  # releases the unused part of the budget
        self.assertTrue(u2.endswith("/v1/payments/payouts"))
        self.assertEqual(h2["PayPal-Request-Id"], "guild-run1")
        self.assertEqual((r["capture_id"], r["payout_batch_id"]), ("CAP1", "PB1"))

    def test_nothing_spent_voids_the_budget(self):
        t = Recorder({})
        r = settle_run(client(t), "run2", "AUTH2", D("0"), {}, "USD")
        self.assertEqual(len(t.api()), 1)
        self.assertTrue(t.api()[0][1].endswith("/AUTH2/void"))
        self.assertEqual(r["voided"], True)

    def test_owed_must_match_spend(self):
        with self.assertRaises(ValueError):
            settle_run(client(Recorder()), "r", "A", D("1.00"), {"a@x": D("0.50")}, "USD")

    def test_capture_failure_means_no_payout(self):
        t = Recorder(PayPalError(422, {"name": "AUTHORIZATION_EXPIRED", "debug_id": "d1"}))
        with self.assertRaises(PayPalError):
            settle_run(client(t), "r", "A", D("1.00"), {"a@x": D("1.00")}, "USD")
        self.assertEqual(len(t.api()), 1)  # only the capture was attempted

    def test_payout_status_lookup(self):
        t = Recorder({"batch_header": {"batch_status": "SUCCESS"}, "items": []})
        self.assertEqual(client(t).get_payout_batch("PB1")["batch_header"]["batch_status"], "SUCCESS")
        method, url, _, body = t.api()[0]
        self.assertEqual((method, body), ("GET", None))
        self.assertTrue(url.endswith("/v1/payments/payouts/PB1"))


if __name__ == "__main__":
    unittest.main()
