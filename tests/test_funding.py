import json
import os
import sys
import time
import unittest
import urllib.parse
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

from guild import funding
from guild.lead import ScriptedLead
from guild.paypal import PayPalClient, PayPalError
from guild.registry import Registry
from guild.review import RuleReviewer
from guild.server import create_app

PUBLIC = "https://guild.example"
OWNERS = "sourcer=src@buyer.example,researcher=res@buyer.example,web_operator=web@buyer.example,doer=doer@buyer.example"


class FakePayPal:
    """Stands in for the PayPal sandbox REST API at the transport level."""

    def __init__(self, fail=None):
        self.calls, self.fail = [], fail or {}
        self.orders = {}

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
        if path.endswith("/authorize"):
            return {"purchase_units": [{"payments": {"authorizations": [{"id": "AUTH1", "status": "CREATED"}]}}]}
        if path.endswith("/capture"):
            return {"id": "CAP1", "status": "COMPLETED"}
        if path.endswith("/void"):
            return {}
        if path == "/v1/payments/payouts":
            return {"batch_header": {"payout_batch_id": "BATCH1", "batch_status": "PENDING"}}
        raise AssertionError(f"unexpected {method} {path}")

    def paths(self):
        return [p for _, p, _, _ in self.calls if p != "/v1/oauth2/token"]


def client(fake):
    return PayPalClient(funding.SANDBOX_URL, "id", "secret", transport=fake)


class Env:
    def __init__(self, **kv):
        self.kv = kv

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in self.kv}
        for k, v in self.kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


class GuardTests(unittest.TestCase):
    def test_only_the_sandbox_host_counts(self):
        self.assertTrue(funding.is_paypal_sandbox("https://api-m.sandbox.paypal.com"))
        for url in ("https://api-m.paypal.com", "https://api-m.paypal.com/?sandbox", "http://api-m.sandbox.paypal.com",
                    "https://api-m.sandbox.paypal.com.evil.example", ""):
            self.assertFalse(funding.is_paypal_sandbox(url), url)

    def test_env_client_refuses_live_and_missing_keys(self):
        with Env(PAYPAL_CLIENT_ID="a", PAYPAL_CLIENT_SECRET="b", PAYPAL_BASE_URL="https://api-m.paypal.com"):
            self.assertIsNone(funding.paypal_from_env())
        with Env(PAYPAL_CLIENT_ID=None, PAYPAL_CLIENT_SECRET="b", PAYPAL_BASE_URL=None):
            self.assertIsNone(funding.paypal_from_env())
        with Env(PAYPAL_CLIENT_ID="a", PAYPAL_CLIENT_SECRET="b", PAYPAL_BASE_URL=None):
            self.assertEqual(funding.paypal_from_env().base_url, funding.SANDBOX_URL)

    def test_owner_mapping_from_env(self):
        with Env(GUILD_AGENT_OWNERS="sourcer=a@x.com, doer=bad, web_operator=w@x.com,=z@x.com"):
            self.assertEqual(funding.owners_from_env(), {"sourcer": "a@x.com", "web_operator": "w@x.com"})


class PreviewTests(unittest.TestCase):
    def test_preview_numbers(self):
        p = funding.preview(Decimal("3.00"), Decimal("2.00"),
                            {"sourcer@o.example": Decimal("0.80"), "doer@o.example": Decimal("0.20"),
                             "x@o.example": Decimal("0")})
        self.assertEqual((p["mode"], p["authorized"], p["captured"], p["released"]), ("preview", "3.00", "2.00", "1.00"))
        self.assertEqual(p["payouts"], [{"to": "d***@o.example", "amount": "0.20"}, {"to": "s***@o.example", "amount": "0.80"}])


class FundedRunTests(unittest.TestCase):
    def app(self, fake, public=PUBLIC, brain=None):
        return TestClient(create_app(brain=brain or (lambda: (ScriptedLead(), RuleReviewer())), paypal=client(fake),
                                     public_url=public), follow_redirects=False)

    def wait(self, c, run_id):
        for _ in range(300):
            body = c.get(f"/api/runs/{run_id}").json()
            if body["done"]:
                return body["events"]
            time.sleep(0.02)
        self.fail("run did not finish")

    def fund_and_return(self, c, fake, budget="3.00"):
        r = c.post("/api/fund", json={"budget": budget})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["approve_url"].startswith("https://www.sandbox.paypal.com/"))
        order = fake.calls[1][3]
        self.assertEqual(order["intent"], "AUTHORIZE")
        self.assertEqual(order["purchase_units"][0]["amount"], {"currency_code": "USD", "value": budget})
        ctx = order["payment_source"]["paypal"]["experience_context"]
        self.assertTrue(ctx["return_url"].startswith(PUBLIC + "/api/paypal/return?fund="))
        fund_id = urllib.parse.parse_qs(urllib.parse.urlsplit(ctx["return_url"]).query)["fund"][0]
        return fund_id, c.get("/api/paypal/return", params={"fund": fund_id, "token": "ORDER1"})

    def test_approve_run_capture_spent_release_rest_and_pay_owners(self):
        fake = FakePayPal()
        with Env(GUILD_AGENT_OWNERS=OWNERS):
            c = self.app(fake)
            _, r = self.fund_and_return(c, fake)
            self.assertEqual(r.status_code, 303)
            run_id = urllib.parse.parse_qs(urllib.parse.urlsplit(r.headers["location"]).query)["run"][0]
            events = self.wait(c, run_id)
        pay = events[-1]
        self.assertEqual((pay["type"], pay["mode"]), ("payment", "sandbox"))
        self.assertEqual((pay["authorized"], pay["captured"], pay["released"]), ("3.00", "2.00", "1.00"))
        self.assertEqual((pay["capture_id"], pay["payout_batch_id"]), ("CAP1", "BATCH1"))
        self.assertEqual(fake.paths(), ["/v2/checkout/orders", "/v2/checkout/orders/ORDER1/authorize",
                                        "/v2/payments/authorizations/AUTH1/capture", "/v1/payments/payouts"])
        capture = next(d for _, p, _, d in fake.calls if p.endswith("/capture"))
        self.assertEqual(capture, {"amount": {"currency_code": "USD", "value": "2.00"}, "final_capture": True})
        payout = next(d for _, p, _, d in fake.calls if p == "/v1/payments/payouts")
        self.assertEqual(payout["sender_batch_header"]["sender_batch_id"], f"guild-{run_id}")
        paid = {i["receiver"]: i["amount"]["value"] for i in payout["items"]}
        self.assertEqual(paid, {"src@buyer.example": "0.80", "res@buyer.example": "0.50",
                                "web@buyer.example": "0.50", "doer@buyer.example": "0.20"})
        self.assertNotIn("cheap@owners.example", paid)  # CheapBot's junk earned nothing
        self.assertTrue(all("***" in row["to"] for row in pay["payouts"]))  # emails masked on the page

    def test_link_is_single_use_and_token_must_match(self):
        fake = FakePayPal()
        c = self.app(fake)
        c.post("/api/fund", json={"budget": "3.00"})
        ctx = fake.calls[1][3]["payment_source"]["paypal"]["experience_context"]
        fund_id = urllib.parse.parse_qs(urllib.parse.urlsplit(ctx["return_url"]).query)["fund"][0]
        bad = c.get("/api/paypal/return", params={"fund": fund_id, "token": "ORDER999"})
        self.assertIn("payment_error", bad.headers["location"])
        again = c.get("/api/paypal/return", params={"fund": fund_id, "token": "ORDER1"})
        self.assertIn("payment_error", again.headers["location"])  # consumed by the bad attempt
        self.assertNotIn("/v2/checkout/orders/ORDER1/authorize", fake.paths())
        self.assertIn("payment_error", c.get("/api/paypal/return", params={"fund": "nope", "token": "x"}).headers["location"])

    def test_cancel_charges_nothing(self):
        fake = FakePayPal()
        c = self.app(fake)
        c.post("/api/fund", json={"budget": "3.00"})
        r = c.get("/api/paypal/cancel", params={"fund": "whatever"})
        self.assertIn("cancelled", urllib.parse.unquote_plus(r.headers["location"]))
        self.assertEqual(fake.paths(), ["/v2/checkout/orders"])

    def test_nothing_passed_voids_the_authorization(self):
        fake = FakePayPal()
        c = self.app(fake)
        _, r = self.fund_and_return(c, fake, budget="0.05")  # too small for any bid
        run_id = urllib.parse.parse_qs(urllib.parse.urlsplit(r.headers["location"]).query)["run"][0]
        pay = self.wait(c, run_id)[-1]
        self.assertTrue(pay["voided"])
        self.assertEqual(pay["captured"], "0.00")
        self.assertEqual(fake.paths()[-1], "/v2/payments/authorizations/AUTH1/void")

    def test_payout_failure_is_reported_with_debug_id(self):
        fake = FakePayPal(fail={"/v1/payments/payouts": PayPalError(403, {"name": "NOT_AUTHORIZED", "debug_id": "dbg42"})})
        c = self.app(fake)
        _, r = self.fund_and_return(c, fake)
        run_id = urllib.parse.parse_qs(urllib.parse.urlsplit(r.headers["location"]).query)["run"][0]
        pay = self.wait(c, run_id)[-1]
        self.assertEqual((pay["mode"], pay["debug_id"]), ("error", "dbg42"))
        self.assertIn("NOT_AUTHORIZED", pay["message"])

    def test_a_crashed_run_voids_the_hold(self):
        class Broken:
            label = "broken"

            def plan(self, goal):
                raise RuntimeError("down")
        fake = FakePayPal()
        c = self.app(fake, brain=lambda: (Broken(), RuleReviewer()))
        _, r = self.fund_and_return(c, fake)
        run_id = urllib.parse.parse_qs(urllib.parse.urlsplit(r.headers["location"]).query)["run"][0]
        events = self.wait(c, run_id)
        self.assertEqual(events[0]["type"], "error")
        self.assertTrue(events[-1]["voided"])
        self.assertNotIn("/v2/payments/authorizations/AUTH1/capture", fake.paths())

    def test_fund_needs_paypal_and_a_public_url(self):
        no_paypal = TestClient(create_app(brain=lambda: (ScriptedLead(), RuleReviewer()), paypal=None, public_url=PUBLIC))
        self.assertEqual(no_paypal.post("/api/fund", json={}).status_code, 503)
        self.assertEqual(self.app(FakePayPal(), public="").post("/api/fund", json={}).status_code, 503)
        self.assertEqual(self.app(FakePayPal()).post("/api/fund", json={"budget": "99"}).status_code, 400)

    def test_paypal_error_on_create_is_502(self):
        fake = FakePayPal(fail={"/v2/checkout/orders": PayPalError(422, {"name": "UNPROCESSABLE_ENTITY"})})
        self.assertEqual(self.app(fake).post("/api/fund", json={}).status_code, 502)

    def test_healthz_reports_payments(self):
        self.assertEqual(self.app(FakePayPal()).get("/healthz").json()["payments"], "PayPal sandbox")
        self.assertTrue(self.app(FakePayPal()).get("/healthz").json()["funding"])


class HuntFundingTests(unittest.TestCase):
    """Deal bounties: the bounty is held, paid in full to the winning agent's developer, or voided."""

    def app(self, fake, hunters=None):
        from guild.hunters import default_hunters
        return TestClient(create_app(brain=lambda: (ScriptedLead(), RuleReviewer()), paypal=client(fake),
                                     public_url=PUBLIC, hunters=hunters or default_hunters, hunt_speed=0,
                                     registry=Registry()),
                          follow_redirects=False)

    def wait(self, c, run_id):
        for _ in range(300):
            body = c.get(f"/api/runs/{run_id}").json()
            if body["done"]:
                return body["events"]
            time.sleep(0.02)
        self.fail("hunt did not finish")

    def fund(self, c, fake, bounty="2.00", **want):
        r = c.post("/api/fund", json={"kind": "hunt", "bounty": bounty, **want})
        self.assertEqual(r.status_code, 200, r.text)
        order = fake.calls[1][3]
        self.assertEqual(order["purchase_units"][0]["amount"]["value"], bounty)
        ctx = order["payment_source"]["paypal"]["experience_context"]
        fund_id = urllib.parse.parse_qs(urllib.parse.urlsplit(ctx["return_url"]).query)["fund"][0]
        r = c.get("/api/paypal/return", params={"fund": fund_id, "token": "ORDER1"})
        self.assertEqual(r.status_code, 303)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(r.headers["location"]).query)
        self.assertIn("hunt", q)  # the page knows to show a hunt
        return self.wait(c, q["hunt"][0])

    def test_winner_gets_the_whole_bounty(self):
        fake = FakePayPal()
        with Env(GUILD_AGENT_OWNERS="browser=dev-browser@buyer.example"):
            events = self.fund(self.app(fake), fake)
        win = next(e for e in events if e["type"] == "winner")
        pay = events[-1]
        self.assertEqual((win["agent_id"], pay["mode"], pay["captured"], pay["released"]), ("browser", "sandbox", "2.00", "0.00"))
        payout = next(d for _, p, _, d in fake.calls if p == "/v1/payments/payouts")
        self.assertEqual([(i["receiver"], i["amount"]["value"]) for i in payout["items"]], [("dev-browser@buyer.example", "2.00")])

    def test_the_buyers_own_want_survives_the_trip_to_paypal(self):
        fake = FakePayPal()
        events = self.fund(self.app(fake), fake, bounty="3.00", must_have=["Kindle", "Paperwhite"], max_price="150")
        self.assertEqual((events[0]["must_have"], events[0]["max_price"], events[0]["bounty"]),
                         (["Kindle", "Paperwhite"], "150.00", "3.00"))
        self.assertTrue(events[-1]["voided"])  # the demo shops sell no Kindles: nobody is paid

    def test_a_bad_want_is_refused_before_paypal_is_called(self):
        fake = FakePayPal()
        r = self.app(fake).post("/api/fund", json={"kind": "hunt", "bounty": "2.00", "ship_to": "Mars"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("ship_to", r.json()["detail"])
        self.assertEqual(fake.calls, [])

    def test_no_real_deal_means_the_hold_is_voided(self):
        from guild.hunters import default_hunters
        fake = FakePayPal()
        only_quickfind = lambda: {"quickfind": default_hunters()["quickfind"]}
        events = self.fund(self.app(fake, hunters=only_quickfind), fake)
        self.assertTrue(any(e["type"] == "no_winner" for e in events))
        self.assertTrue(events[-1]["voided"])
        self.assertEqual(fake.paths()[-1], "/v2/payments/authorizations/AUTH1/void")
        self.assertNotIn("/v1/payments/payouts", fake.paths())

    def test_unfunded_hunt_ends_with_a_preview(self):
        c = TestClient(create_app(brain=lambda: (ScriptedLead(), RuleReviewer()), paypal=None, hunt_speed=0,
                                  registry=Registry()))
        r = c.post("/api/hunts", json={"bounty": "3.50"})
        self.assertEqual(r.status_code, 200)
        events = self.wait(c, r.json()["run_id"])
        types = [e["type"] for e in events]
        self.assertEqual((types[0], types[-2], types[-1]), ("want", "done", "payment"))
        self.assertEqual((events[-1]["mode"], events[-1]["captured"]), ("preview", "3.50"))
        self.assertEqual(c.post("/api/hunts", json={"bounty": "99"}).status_code, 400)
        self.assertIn("scout", c.get("/healthz").json()["hunters"])


if __name__ == "__main__":
    unittest.main()
