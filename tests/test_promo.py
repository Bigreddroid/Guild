import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from guild import funding, shops
from guild.fetcher import SafeFetcher
from guild.paypal import PayPalClient
from guild.promo import Brief, BriefError, check_proof, parse_brief, visible_text
from guild.registry import Registry
from guild.wallet import (APPROVED, BLOCKED, HELD, PAID, PENDING, VOIDED, Envelope, Policy, Wallet,
                          WalletError)
from guild.walletpay import WalletPay
from tests.test_wallet_api import app
from tests.test_walletpay import PUBLIC, FakeSandbox, link_of

FETCH = SafeFetcher(local=shops.serve)
DEMO = shops.demo_brief()
PROOFS = DEMO["proofs"]
BRIEF = parse_brief(DEMO)[0]


def page(html, status=200):
    return lambda url: (status, html)


def split_policy():
    return Policy(Decimal("400"), Decimal("350"), Decimal("250"), shops.DEMO_WANT,
                  {"shopping": Envelope(Decimal("300"), Decimal("300"), Decimal("250")),
                   "marketing": Envelope(Decimal("100"), Decimal("60"), Decimal("60"))})


class ProofTests(unittest.TestCase):
    def test_the_demo_posts(self):
        verdicts = {k: check_proof(BRIEF, u, FETCH) for k, u in PROOFS.items()}
        self.assertTrue(verdicts["good"].valid)
        self.assertEqual(verdicts["hidden_ad"].reasons, ["no visible ad disclosure (#ad, #sponsored, paid partnership)"])
        self.assertFalse(verdicts["no_link"].checks["link"])
        self.assertIn("does not mention Harbor Audio", verdicts["no_link"].reasons)
        self.assertEqual(verdicts["copycat"].checks["channel"], False)
        self.assertTrue(all(not v.valid for k, v in verdicts.items() if k != "good"))

    def test_what_counts_as_proof(self):
        url = shops.CREATOR_CHANNEL + "/post"
        link = f"<a href='{shops.PROMOTED}?utm_campaign=x'>deal</a>"
        good = f"<p>#sponsored Harbor Audio {link}</p>"
        self.assertTrue(check_proof(BRIEF, url, page(good)).valid)
        for bad, why in ((f"<p>#adventure Harbor Audio {link}</p>", "disclosure"),            # not a disclosure
                         (f"<p>#ad Harbor Audio</p><!-- {link} -->", "link"),                  # link only in a comment
                         (f"<p>#ad Harbor Audio</p>{shops.PROMOTED}", "link"),                 # text, not a link
                         (f"<p>#ad Harbor Audio</p><a style='display:none' href='{shops.PROMOTED}'>x</a>", "link"),
                         (f"<p>#ad Harbor Audio</p><div hidden><a href='{shops.PROMOTED}'>x</a></div>", "link"),
                         (f"<p>#ad {link}</p><span hidden>Harbor Audio</span>", "mentions"),   # hidden mention
                         (f"<p>#ad Harbor Audio <a href='{shops.PROMOTED}-case'>x</a></p>", "link")):
            v = check_proof(BRIEF, url, page(bad))
            self.assertFalse(v.checks[why], (bad, v.checks))
        self.assertEqual(check_proof(BRIEF, url, page(good, 404)).reasons, ["the post is not live (HTTP 404)"])
        self.assertFalse(check_proof(BRIEF, "http://x.example", FETCH).valid)
        boom = check_proof(BRIEF, url, lambda u: (_ for _ in ()).throw(OSError()))
        self.assertEqual((boom.valid, boom.checks["live"]), (False, False))
        # the channel is a path prefix, not a string prefix
        self.assertFalse(check_proof(BRIEF, shops.CREATOR_CHANNEL + "-fake/post", page(good)).checks["channel"])
        self.assertEqual(visible_text("<div style='display: none'><b>x</b></div>y<script>z</script>"), "y")
        # real blogs wrap posts in overflow: hidden or classes named *hidden*; that text is still visible
        wrapped = f"<div style='overflow: hidden' class='post not-hidden'>{good}</div>"
        self.assertTrue(check_proof(BRIEF, url, page(wrapped)).valid)
        self.assertTrue(check_proof(BRIEF, url, page(f"<div style='font-size:0.9em'>{good}</div>")).valid)

    def test_the_demo_creator_account_comes_from_the_environment(self):
        old = os.environ.get("GUILD_SELLER_ACCOUNTS")
        os.environ["GUILD_SELLER_ACCOUNTS"] = "harbor-audio=h@biz.example.com,maya-reviews=maya@personal.example.com"
        try:
            self.assertEqual(shops.demo_brief()["paypal_email"], "maya@personal.example.com")
            self.assertNotIn("maya-reviews", shops.seller_accounts())   # a creator is not a shop
        finally:
            if old is None:
                os.environ.pop("GUILD_SELLER_ACCOUNTS")
            else:
                os.environ["GUILD_SELLER_ACCOUNTS"] = old

    def test_bad_briefs(self):
        for change in ({"creator": ""}, {"channel": "http://x.example"}, {"link": "nope"}, {"paypal_email": "x"},
                       {"amount": "0"}, {"amount": "lots"}, {"deadline_minutes": 0}, {"deadline_minutes": 5000},
                       {"words": ["a"] * 7}, {"words": 5}):
            with self.assertRaises(BriefError, msg=change):
                parse_brief({**DEMO, **change})
        self.assertEqual(parse_brief({**DEMO, "words": "Harbor Audio, XM5"})[0].words, ("Harbor Audio", "XM5"))
        with self.assertRaises(BriefError):
            Brief("x", "https://", "https://a.example")


class WalletHoldTests(unittest.TestCase):
    def test_holds_reserve_their_envelope_until_paid_or_voided(self):
        w = Wallet("w", "a", split_policy())
        payee = {"email_address": "maya@example.com"}
        a = w.book("Promo A", Decimal("50"), "marketing", payee, shops.CREATOR_CHANNEL, {"creator": "A"}, True)
        b = w.book("Promo B", Decimal("40"), "marketing", payee, shops.CREATOR_CHANNEL, {"creator": "B"}, False)
        too_big = w.book("Promo C", Decimal("61"), "marketing", payee, shops.CREATOR_CHANNEL, {}, True)
        self.assertEqual((a.status, b.status, too_big.status), (APPROVED, PENDING, BLOCKED))
        self.assertIn("waiting for the owner", b.reasons[0])           # an agent's booking always waits
        w.record_hold(a.id, "O1", "AUTH1")
        self.assertEqual((a.status, w.reserved("marketing"), w.remaining("marketing")), (HELD, Decimal("90"), Decimal("10")))
        self.assertEqual(w.book("D", Decimal("11"), "marketing", payee, "https://x.example", {}, True).status, BLOCKED)
        w.record_capture(a.id, "CAP1", "proof checked")
        self.assertEqual((a.status, w.spent("marketing")), (PAID, Decimal("50")))
        w.approve(b.id)
        w.record_hold(b.id, "O2", "AUTH2")
        w.record_void(b.id, "deadline")
        self.assertEqual((b.status, w.remaining("marketing")), (VOIDED, Decimal("50")))
        for bad in (lambda: w.record_capture(b.id, "x", ""), lambda: w.record_void(a.id, ""),
                    lambda: w.record_hold(a.id)):
            with self.assertRaises(WalletError):
                bad()


class PromotionApiTests(unittest.TestCase):
    def setUp(self):
        self.c = app(shopper=None, registry=Registry())
        r = self.c.post("/api/wallets", json={"envelopes": {"shopping": {"limit": "300"},
                                                            "marketing": {"limit": "100", "max_per_purchase": "60"}}})
        self.wid, self.key = r.json()["wallet_id"], r.json()["owner_key"]
        self.owner = {"X-Owner-Key": self.key}

    def book(self, **change):
        body = {k: v for k, v in DEMO.items() if k != "proofs"}
        return self.c.post(f"/api/wallets/{self.wid}/promotions", json={**body, **change}, headers=self.owner)

    def proof(self, pid, url):
        return self.c.post(f"/api/wallets/{self.wid}/promotions/{pid}/proof", json={"url": url})

    def test_pay_on_proof(self):
        demo = self.c.get("/api/demo/promotion").json()
        self.assertEqual((demo["demo"], demo["creator"]), (True, "Maya Reviews"))
        r = self.book()
        self.assertEqual(r.status_code, 201, r.text)
        pid = r.json()["id"]
        view = self.c.get(f"/api/wallets/{self.wid}").json()
        promo = view["purchases"][0]
        self.assertEqual((promo["status"], promo["kind"], promo["category"]), ("held", "promotion", "marketing"))
        self.assertEqual([e["reserved"] for e in view["envelopes"]], ["0.00", "40.00"])
        for name in ("hidden_ad", "no_link", "copycat"):
            bad = self.proof(pid, PROOFS[name]).json()
            self.assertFalse(bad["valid"], name)
            self.assertEqual(bad["purchase"]["status"], "held")       # still held: the creator can fix it
        paid = self.proof(pid, PROOFS["good"]).json()
        self.assertEqual((paid["type"], paid["via"], paid["purchase"]["status"], paid["spent"]),
                         ("paid", "proof", "paid", "40.00"))
        self.assertEqual(len(paid["purchase"]["proofs"]), 4)
        self.assertEqual(self.proof(pid, PROOFS["good"]).status_code, 409)   # paid once
        types = [e["type"] for e in self.c.get(f"/api/runs/{self.wid}").json()["events"]]
        self.assertEqual(types[1:], ["promotion", "held", "proof", "proof", "proof", "proof", "paid"])

    def test_limits_cancel_and_deadline(self):
        self.assertEqual(self.book(amount="61").json()["status"], "blocked")   # over the marketing max of 60
        self.assertEqual(self.book(paypal_email="nope").status_code, 400)
        self.assertEqual(self.c.post(f"/api/wallets/{self.wid}/promotions", json=DEMO).status_code, 401)  # no auth
        self.assertEqual(self.c.post(f"/api/wallets/{self.wid}/promotions", json=DEMO,
                                     headers={"X-Owner-Key": "ok_wrong"}).status_code, 403)
        first = self.book().json()["id"]
        cancel = self.c.post(f"/api/wallets/{self.wid}/purchases/{first}/cancel", headers=self.owner).json()
        self.assertEqual((cancel["type"], cancel["purchase"]["status"], cancel["reserved"]), ("voided", "voided", "0.00"))
        self.assertEqual(self.proof(first, PROOFS["good"]).status_code, 409)
        second = self.book(deadline_minutes=1).json()["id"]
        service = self.c.app.state.wallets
        service.get(self.wid)["created"] -= 61    # a minute passes with no good proof
        service.expire()
        p = self.c.get(f"/api/wallets/{self.wid}").json()["purchases"][-1]
        self.assertEqual((p["id"], p["status"]), (second, "voided"))
        self.assertIn("deadline", p["reasons"][0])
        third = self.book().json()["id"]
        self.c.post(f"/api/wallets/{self.wid}/stop", headers=self.owner)      # stopping releases every hold
        self.assertEqual(self.c.get(f"/api/wallets/{self.wid}").json()["purchases"][-1]["status"], "voided")
        self.assertEqual(self.proof(third, PROOFS["good"]).status_code, 409)
        self.assertEqual(self.proof("nope", PROOFS["good"]).status_code, 404)

    def test_an_agent_proposes_and_the_owner_decides(self):
        reg = Registry()
        c = app(shopper=None, registry=reg)
        bot = c.post("/api/agents", json={"name": "Promo Bot", "payout_email": "dev@example.com"}).json()
        r = c.post("/api/wallets", json={"agent": bot["agent_id"], "envelopes": {"shopping": {"limit": "300"},
                                                                                "marketing": {"limit": "100"}}}).json()
        wid, owner = r["wallet_id"], {"X-Owner-Key": r["owner_key"]}
        body = {k: v for k, v in DEMO.items() if k != "proofs"}
        p = c.post(f"/api/wallets/{wid}/promotions", json={**body, "amount": "5"},
                   headers={"Authorization": f"Bearer {bot['token']}"}).json()
        self.assertEqual((p["status"], p["booked_by"]), ("pending", "agent"))   # even $5 waits: the agent picked the payee
        held = c.post(f"/api/wallets/{wid}/purchases/{p['id']}/approve", headers=owner).json()
        self.assertEqual((held["type"], held["purchase"]["status"]), ("held", "held"))


class SandboxHoldTests(unittest.TestCase):
    def wallet_and_promo(self, pay):
        w = Wallet("w1", "a", split_policy())
        p = w.book("Promo", Decimal("40"), "marketing", {"email_address": "maya@personal.example.com"},
                   shops.CREATOR_CHANNEL, {"creator": "Maya"}, True)
        return w, p

    def test_hold_from_the_mandate_then_capture_to_the_creator(self):
        fake = FakeSandbox()
        pay = WalletPay(PayPalClient(funding.SANDBOX_URL, "id", "secret", transport=fake), PUBLIC)
        w, p = self.wallet_and_promo(pay)
        w.mandate_id = "PT1"
        self.assertEqual(pay.hold(w, p)["type"], "held")
        order = fake.calls[-1][3]
        self.assertEqual((order["intent"], order["purchase_units"][0]["payee"], order["payment_source"]["paypal"]),
                         ("AUTHORIZE", {"email_address": "maya@personal.example.com"}, {"vault_id": "PT1"}))
        event = pay.capture(w, p, "proof checked")
        self.assertEqual((event["type"], p.status, p.capture_id), ("paid", PAID, "CAP-AUTH-ORDER1"))
        self.assertEqual(fake.calls[-1][2], "AUTH-ORDER1-capture")          # idempotent per hold

    def test_checkout_hold_and_void(self):
        fake = FakeSandbox()
        pay = WalletPay(PayPalClient(funding.SANDBOX_URL, "id", "secret", transport=fake), PUBLIC)
        w, p = self.wallet_and_promo(pay)
        event = pay.hold(w, p)
        self.assertEqual(event["type"], "checkout")
        ret = fake.calls[-1][3]["payment_source"]["paypal"]["experience_context"]
        self.assertEqual(ret["user_action"], "CONTINUE")
        self.assertTrue(ret["return_url"].startswith(f"{PUBLIC}/api/wallets/paypal/hold?link="))
        with self.assertRaises(WalletError):
            pay.finish_hold(link_of(ret["return_url"]), "WRONG", {"w1": w})
        # links are single-use, so the mismatch spent that one: hold a second promotion
        p2 = w.book("Promo 2", Decimal("10"), "marketing", {"email_address": "m@x.example.com"},
                    shops.CREATOR_CHANNEL, {}, True)
        pay.hold(w, p2)
        ret2 = fake.calls[-1][3]["payment_source"]["paypal"]["experience_context"]["return_url"]
        _, held = pay.finish_hold(link_of(ret2), "ORDER2", {"w1": w})
        self.assertEqual((held["type"], p2.status, p2.authorization_id), ("held", HELD, "AUTH-ORDER2"))
        voided = pay.void(w, p2, "deadline")
        self.assertEqual((voided["type"], p2.status, fake.holds["AUTH-ORDER2"]), ("voided", VOIDED, "VOIDED"))

    def test_a_void_paypal_refuses_only_counts_if_the_hold_is_gone(self):
        from guild.paypal import PayPalError
        fake = FakeSandbox(fail={"/void": PayPalError(422, {"name": "UNPROCESSABLE_ENTITY"})})
        pay = WalletPay(PayPalClient(funding.SANDBOX_URL, "id", "secret", transport=fake), PUBLIC)
        w, p = self.wallet_and_promo(pay)
        w.mandate_id = "PT1"
        pay.hold(w, p)
        self.assertEqual(pay.void(w, p, "x")["type"], "void_failed")      # still CREATED at PayPal
        self.assertEqual(p.status, HELD)
        fake.holds[p.authorization_id] = "EXPIRED"
        self.assertEqual(pay.void(w, p, "x")["type"], "voided")


if __name__ == "__main__":
    unittest.main()
