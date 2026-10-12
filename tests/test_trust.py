import json
import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from guild import shops
from guild.bounty import Claim, Trust, cross_check, read_offer, typical_price, verify
from guild.fetcher import SafeFetcher
from guild.hunt import run_hunt
from guild.hunters import Hunter, default_hunters
from guild.market import AgentCard
from guild.sellers import SellerCard, SellerCardError, read_card

WANT = shops.DEMO_WANT
FETCH = SafeFetcher(local=shops.serve)
TRUST = Trust(listings=tuple(shops.catalogue()), seller_of=shops.seller_of)
HARBOR = shops.url_for("harbor-audio/sony-wh-1000xm5-black")
PADS = shops.url_for("harbor-audio/sony-wh-1000xm5-ear-pads")
CASE = shops.url_for("harbor-audio/sony-wh-1000xm5-case")
METRO = shops.url_for("metro-tech/sony-wh-1000xm5-midnight")


def claim(url, price):
    return Claim(id="c1", want_id=WANT.id, agent_id="a", url=url, price=Decimal(price), currency="USD", at=1.0)


class TrustRuleTests(unittest.TestCase):
    def test_a_seller_must_be_listed_somewhere_independent(self):
        self.assertIsNone(cross_check(HARBOR, TRUST))
        self.assertIn("no independent source lists this seller (sony-outlet-deals.example)",
                      cross_check(shops.TRAP_URL, TRUST))
        self.assertIn("no independent listings", cross_check(HARBOR, Trust(listings=())))
        www = Trust(listings=({"url": "https://www.shop.example/a", "title": "x", "price": "1"},))
        self.assertIsNone(cross_check("https://shop.example/b", www))

    def test_typical_price_is_the_median_of_matching_listings(self):
        # refurbished is a must-not, so Loop Renew's $219 doesn't count: median of 279, 289, 299, 309
        self.assertEqual(typical_price(shops.catalogue(), WANT), Decimal("294.00"))
        self.assertIsNone(typical_price(shops.catalogue()[:1], WANT))  # one listing is not evidence

    def test_the_real_deal_passes_every_check(self):
        v = verify(WANT, claim(HARBOR, "299.00"), FETCH, TRUST)
        self.assertTrue(v.valid, v.reasons)
        self.assertTrue(all(v.checks.values()))
        self.assertEqual(set(v.checks), {"seller", "name", "currency", "max_price", "claimed_price", "sanity",
                                         "stock", "shipping"})

    def test_the_fake_shop_is_blocked_even_though_its_page_and_card_look_right(self):
        v = verify(WANT, claim(shops.TRAP_URL, "149.00"), FETCH, TRUST)
        self.assertFalse(v.valid)
        self.assertEqual([k for k, ok in v.checks.items() if not ok], ["seller"])
        self.assertEqual(read_card(shops.TRAP_URL, FETCH).email, "payments@sony-outlet-deals.example")

    def test_ear_pads_pass_the_name_rules_but_not_price_sanity(self):
        v = verify(WANT, claim(PADS, "19.00"), FETCH, TRUST)
        self.assertEqual([k for k, ok in v.checks.items() if not ok], ["sanity"])
        self.assertIn("6% of the usual 294.00", v.reasons[0])

    def test_the_injected_case_fails_on_the_owners_words_and_misquotes_still_fail(self):
        self.assertEqual([k for k, ok in verify(WANT, claim(CASE, "29.00"), FETCH, TRUST).checks.items() if not ok],
                         ["name", "sanity"])
        self.assertIn("claimed 309.00, page says 318.00", verify(WANT, claim(METRO, "309.00"), FETCH, TRUST).reasons)

    def test_without_trust_the_rules_are_unchanged(self):
        v = verify(WANT, claim(shops.TRAP_URL, "149.00"), FETCH)
        self.assertTrue(v.valid)
        self.assertNotIn("seller", v.checks)


class DemoPageTests(unittest.TestCase):
    def test_trap_pages_are_served_but_not_indexed(self):
        indexed = {l["url"] for l in shops.catalogue()}
        for url in (PADS, CASE, shops.TRAP_URL):
            self.assertEqual(FETCH(url)[0], 200)
            self.assertNotIn(url, indexed)
        self.assertEqual(FETCH(f"https://{shops.TRAP_HOST}/other")[0], 404)
        self.assertIsNone(shops.serve("https://elsewhere.example/x"))

    def test_the_injection_is_hidden_text_that_never_reaches_the_offer(self):
        status, body = FETCH(METRO)
        self.assertIn("Note to AI shopping assistants", body)
        self.assertIn("display:none", body)
        self.assertEqual(read_offer(body).name, "Sony WH-1000XM5 Headphones, Midnight Black")
        self.assertNotIn("Note to AI", FETCH(HARBOR)[1])


class SellerCardTests(unittest.TestCase):
    def test_each_demo_shop_publishes_a_card_for_its_own_path(self):
        card = read_card(HARBOR, FETCH, shops.seller_of, shops.card_url_of)
        self.assertEqual((card.seller, card.name), (f"{shops.HOST}/harbor-audio", "Harbor Audio"))
        self.assertEqual(card.payee(), {"email_address": "harbor-audio@sellers.guild.example"})

    def test_sandbox_accounts_come_from_the_environment(self):
        os.environ["GUILD_SELLER_ACCOUNTS"] = "harbor-audio=sb-harbor@business.example.com,bogus"
        try:
            card = read_card(HARBOR, FETCH, shops.seller_of, shops.card_url_of)
        finally:
            del os.environ["GUILD_SELLER_ACCOUNTS"]
        self.assertEqual(card.email, "sb-harbor@business.example.com")

    def test_bad_cards_are_refused(self):
        def serving(status, body):
            return lambda url: (status, body)
        good = {"version": 1, "domain": "shop.example", "seller": "S", "paypal": {"email": "s@shop.example"}}
        cases = [serving(404, ""), serving(200, "not json"), serving(200, json.dumps({**good, "version": 2})),
                 serving(200, json.dumps({**good, "domain": "other.example"})),
                 serving(200, json.dumps({**good, "paypal": {"email": "not-an-email"}})),
                 lambda url: (_ for _ in ()).throw(OSError("down"))]
        for fetch in cases:
            with self.assertRaises(SellerCardError):
                read_card("https://shop.example/p", fetch)
        card = read_card("https://www.shop.example/p",
                         serving(200, json.dumps({**good, "paypal": {"merchant_id": "ABCD1234EFGH", "email": "s@shop.example"}})))
        self.assertEqual(card.payee(), {"merchant_id": "ABCD1234EFGH"})


class BountyTrustTests(unittest.TestCase):
    def test_a_fake_shop_can_no_longer_win_a_bounty(self):
        class Faker(Hunter):
            tool, eta_s = "a website it made", 0.0

            def hunt(self, want, max_claims):
                return [{"url": shops.TRAP_URL, "price": "149.00"}]
        hunters = {**default_hunters(), "faker": Faker(AgentCard("faker", "Faker", "f@x.example", {"hunt"}, 0.9))}
        events = []
        result = run_hunt(WANT, hunters, FETCH, events.append, deadline_s=10, speed=0, trust=TRUST)
        fake = next(e for e in events if e["type"] == "claim" and e["agent_id"] == "faker")
        self.assertFalse(fake["valid"])
        self.assertFalse(fake["checks"]["seller"])
        self.assertEqual((result["winner"].claim.agent_id, result["winner"].offer.price), ("browser", Decimal("299.00")))


if __name__ == "__main__":
    unittest.main()
