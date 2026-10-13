import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from guild import funding, renewals as rn, shops
from guild.paypal import PayPalClient
from guild.registry import Registry
from tests.test_wallet_api import app, open_wallet
from tests.test_walletpay import FakeSandbox

WALLET = {"budget": "400", "max_per_purchase": "350", "approve_above": "250",
          "envelopes": {"shopping": {"limit": "300", "max_per_purchase": "300", "approve_above": "250"},
                        "marketing": {"limit": "60"}, "tools": {"limit": "40"}}}
TOOL = shops.demo_renewal()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class ParseTests(unittest.TestCase):
    def test_bad_subscriptions_are_refused(self):
        for change in ({"url": "http://x.example"}, {"item": ""}, {"price": "0"}, {"price": "lots"},
                       {"every_minutes": 0.5}, {"every_minutes": 50000}, {"must_have": ["a"] * 7}, {"category": 3}):
            with self.assertRaises(rn.RenewalError, msg=change):
                rn.parse({**TOOL, **change})
        f = rn.parse({**TOOL, "must_have": "ScribeAI, Pro"})
        self.assertEqual((f["must_have"], f["every_s"], f["price"]), (("ScribeAI", "Pro"), 120.0, Decimal("20.00")))

    def test_unused_needs_two_whole_periods(self):
        r = rn.Renewal("r", "T", "https://t.example", Decimal("1"), 100, ("T",), "tools", "owner", 0, status=rn.ACTIVE)
        self.assertFalse(r.unused(199))
        self.assertTrue(r.unused(200))
        r.last_used_at = 150
        self.assertFalse(r.unused(300))
        r.status = rn.CANCELLED
        self.assertFalse(r.unused(10_000))                       # nothing to flag once it is cancelled


class RenewalApiTests(unittest.TestCase):
    def setUp(self, paypal=None):
        self.c = app(shopper=None, paypal=paypal)
        self.clock = Clock()
        self.service = self.c.app.state.wallets
        self.service.clock = self.clock
        r = open_wallet(self.c, WALLET)
        self.wid, self.owner = r["wallet_id"], {"X-Owner-Key": r["owner_key"]}

    def subscribe(self, headers=None, **change):
        body = {k: v for k, v in TOOL.items()}
        return self.c.post(f"/api/wallets/{self.wid}/renewals", json={**body, **change},
                           headers=self.owner if headers is None else headers)

    def view(self):
        return self.c.get(f"/api/wallets/{self.wid}").json()

    def tick(self, seconds):
        self.clock.t += seconds
        self.service.expire()

    def test_charged_once_per_period_and_never_after_cancel(self):
        r = self.subscribe()
        self.assertEqual(r.status_code, 201, r.text)
        r = r.json()
        self.assertEqual((r["status"], r["charges"], r["next_in_s"]), ("active", 1, 120))
        first = self.view()["purchases"][-1]
        self.assertEqual((first["status"], first["category"], first["renewal"], first["period"]),
                         ("paid", "tools", r["id"], 1))
        self.tick(60)
        self.assertEqual(self.view()["renewals"][0]["charges"], 1)         # not due yet
        self.tick(60)
        self.tick(1)                                                        # a second sweep in the same period
        self.assertEqual(self.view()["renewals"][0]["charges"], 2)
        self.assertEqual([e["spent"] for e in self.view()["envelopes"] if e["name"] == "tools"], ["40.00"])
        self.tick(120)                                                      # third period: tools limit is used up
        ren = self.view()["renewals"][0]
        self.assertEqual((ren["status"], ren["charges"]), ("paused", 3))
        self.assertEqual(ren["reason"], "20.00 is over the tools envelope left (0.00)")
        self.assertEqual(self.view()["purchases"][-1]["status"], "blocked")
        c = self.c.post(f"/api/wallets/{self.wid}/renewals/{r['id']}/cancel", headers=self.owner).json()
        self.assertEqual(c["status"], "cancelled")
        self.tick(1000)
        self.assertEqual(self.view()["renewals"][0]["charges"], 3)          # never charged again
        self.assertEqual(self.c.post(f"/api/wallets/{self.wid}/renewals/{r['id']}/resume",
                                     headers=self.owner).status_code, 409)

    def test_a_price_rise_or_a_fake_page_is_not_paid(self):
        r = self.subscribe(price="15.00").json()                            # the page says 20.00
        self.assertEqual(r["status"], "paused")
        self.assertEqual(r["reason"], "page price 20.00 is over the max 15.00")   # the price went up: not paid
        r2 = self.subscribe(item="ScribeAI Pro", must_have=["ScribeAI", "Pro"],
                            url=shops.url_for("harbor-audio/sony-wh-1000xm5-black")).json()
        self.assertEqual(r2["status"], "paused")                             # wrong item on that page

    def test_unused_tools_are_flagged_and_usage_clears_it(self):
        r = self.subscribe(every_minutes=10).json()
        self.tick(1200)
        ren = self.view()["renewals"][0]
        self.assertTrue(ren["unused"])
        self.c.post(f"/api/wallets/{self.wid}/renewals/{r['id']}/used", headers=self.owner)
        self.assertFalse(self.view()["renewals"][0]["unused"])
        self.assertEqual(self.c.post(f"/api/wallets/{self.wid}/renewals/nope/used", headers=self.owner).status_code, 404)

    def test_an_agents_subscription_waits_for_the_owner_then_renews(self):
        reg = Registry()
        self.c = app(shopper=None, registry=reg)
        self.service = self.c.app.state.wallets
        self.service.clock = self.clock
        bot = self.c.post("/api/agents", json={"name": "Tool Bot", "payout_email": "dev@example.com"}).json()
        r = open_wallet(self.c, {**WALLET, "agent": bot["agent_id"]})
        self.wid, self.owner = r["wallet_id"], {"X-Owner-Key": r["owner_key"]}
        agent = {"Authorization": f"Bearer {bot['token']}"}
        sub = self.subscribe(headers=agent).json()
        self.assertEqual((sub["status"], sub["booked_by"]), ("waiting", "agent"))      # even $20 waits
        self.assertIn("your AI started this subscription", sub["reason"])
        pid = self.view()["purchases"][-1]["id"]
        self.c.post(f"/api/wallets/{self.wid}/purchases/{pid}/approve", headers=self.owner)
        self.assertEqual(self.view()["renewals"][0]["status"], "active")
        self.tick(120)
        self.assertEqual(self.view()["purchases"][-1]["status"], "paid")              # later periods don't ask
        self.assertEqual(self.c.post(f"/api/wallets/{self.wid}/renewals/{sub['id']}/used",
                                     headers=agent).status_code, 200)
        self.assertEqual(self.c.post(f"/api/wallets/{self.wid}/renewals/{sub['id']}/cancel",
                                     headers=agent).status_code, 403)                 # only the owner cancels

    def test_declining_the_first_payment_cancels_it(self):
        reg = Registry()
        self.c = app(shopper=None, registry=reg)
        self.service = self.c.app.state.wallets
        bot = self.c.post("/api/agents", json={"name": "Tool Bot", "payout_email": "dev@example.com"}).json()
        r = open_wallet(self.c, {**WALLET, "agent": bot["agent_id"]})
        self.wid, self.owner = r["wallet_id"], {"X-Owner-Key": r["owner_key"]}
        self.subscribe(headers={"Authorization": f"Bearer {bot['token']}"})
        pid = self.view()["purchases"][-1]["id"]
        self.c.post(f"/api/wallets/{self.wid}/purchases/{pid}/reject", headers=self.owner)
        self.assertEqual(self.view()["renewals"][0]["status"], "cancelled")

    def test_a_stopped_wallet_charges_nothing(self):
        self.subscribe()
        self.c.post(f"/api/wallets/{self.wid}/stop", headers=self.owner)
        spent = self.view()["spent"]
        self.tick(600)
        self.assertEqual(self.view()["spent"], spent)


class SandboxRenewalTests(unittest.TestCase):
    def test_each_period_is_its_own_payout_to_the_tool_seller(self):
        fake = FakeSandbox()
        os.environ["GUILD_SELLER_ACCOUNTS"] = "scribe-labs=sb-scribe@business.example.com"
        try:
            c = app(shopper=None, paypal=PayPalClient(funding.SANDBOX_URL, "id", "secret", transport=fake))
            service = c.app.state.wallets
            clock = Clock()
            service.clock = clock
            r = c.post("/api/wallets", json=WALLET).json()
            wid, owner = r["wallet_id"], {"X-Owner-Key": r["owner_key"]}
            service.get(wid)["wallet"].top_up(Decimal("100"), order_id="O", capture_id="CAP")   # as if paid in PayPal
            c.post(f"/api/wallets/{wid}/renewals", json=TOOL, headers=owner)
            clock.t += 120
            service.expire()
        finally:
            del os.environ["GUILD_SELLER_ACCOUNTS"]
        items = [p["items"][0] for p in fake.payouts()]
        self.assertEqual([(i["receiver"], i["amount"]["value"]) for i in items],
                         [("sb-scribe@business.example.com", "20.00")] * 2)
        self.assertEqual(len({i["sender_item_id"] for i in items}), 2)       # one id per period: no double pay


if __name__ == "__main__":
    unittest.main()
