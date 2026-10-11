import json
import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from guild.bounty import (Claim, Want, canonical_url, claim_limit, pick_winner, read_offer, verify)

D = Decimal
WANT = Want(id="w1", title="Sony WH-1000XM5 headphones, black, new", must_have=("WH-1000XM5", "Sony"),
            must_not=("refurbished", "used", "case"), max_price=D("330"), currency="USD", ship_to="US",
            bounty=D("2.00"))


def jsonld_page(name="Sony WH-1000XM5 Wireless Headphones - Black", price="299.99", currency="USD",
                availability="https://schema.org/InStock", ships=None, offer_type="Offer", graph=False, extra=None):
    offer = {"@type": offer_type, "priceCurrency": currency, "availability": availability}
    offer["lowPrice" if offer_type == "AggregateOffer" else "price"] = price
    if ships is not None:
        offer["shippingDetails"] = {"@type": "OfferShippingDetails",
                                    "shippingDestination": [{"@type": "DefinedRegion", "addressCountry": c} for c in ships]}
    product = {"@context": "https://schema.org", "@type": "Product", "name": name, "offers": [offer]}
    if extra:
        product.update(extra)
    doc = {"@context": "https://schema.org", "@graph": [{"@type": "WebSite", "name": "Shop"}, product]} if graph else product
    return f"<html><head><title>x</title><script type='application/ld+json'>{json.dumps(doc)}</script></head><body>Buy</body></html>"


MICRODATA = """<div itemscope itemtype="https://schema.org/Product">
  <h1 itemprop="name">Sony <b>WH-1000XM5</b> Headphones</h1>
  <div itemprop="offers" itemscope itemtype="https://schema.org/Offer">
    <span itemprop="price" content="289.00">$289.00</span>
    <meta itemprop="priceCurrency" content="USD">
    <link itemprop="availability" href="https://schema.org/InStock">
  </div></div>"""

OPENGRAPH = """<html><head><meta property="og:title" content="Sony WH-1000XM5 &amp; carry pouch">
<meta property="product:price:amount" content="305.00"><meta property="product:price:currency" content="USD">
<meta property="product:availability" content="in stock"></head></html>"""


def claim(url="https://shop.example/p/xm5", price="299.99", agent="scout", at=10.0, cid="c1", want="w1"):
    return Claim(id=cid, want_id=want, agent_id=agent, url=url, price=D(price), currency="USD", at=at)


def pages(mapping):
    def fetch(url):
        if url not in mapping:
            return 404, "not found"
        return 200, mapping[url]
    return fetch


class ReadOfferTests(unittest.TestCase):
    def test_jsonld_product_offer(self):
        o = read_offer(jsonld_page())
        self.assertEqual((o.price, o.currency, o.availability, o.source), (D("299.99"), "USD", "instock", "json-ld"))

    def test_jsonld_graph_aggregate_and_price_specification(self):
        self.assertEqual(read_offer(jsonld_page(graph=True)).price, D("299.99"))
        self.assertEqual(read_offer(jsonld_page(offer_type="AggregateOffer", price="279")).price, D("279"))
        spec = ("<script type='application/ld+json'>" + json.dumps({"@type": "Product", "name": "Sony WH-1000XM5",
                "offers": {"@type": "Offer", "priceSpecification": {"@type": "UnitPriceSpecification", "price": "310.50",
                           "priceCurrency": "USD"}, "priceCurrency": "USD", "availability": "InStock"}}) + "</script>")
        self.assertEqual(read_offer(spec).price, D("310.50"))

    def test_microdata_and_opengraph_fallbacks(self):
        m = read_offer(MICRODATA)
        self.assertEqual((m.name, m.price, m.availability, m.source), ("Sony WH-1000XM5 Headphones", D("289.00"), "instock", "microdata"))
        g = read_offer(OPENGRAPH)
        self.assertEqual((g.name, g.price, g.availability, g.source), ("Sony WH-1000XM5 & carry pouch", D("305.00"), "instock", "opengraph"))

    def test_shipping_countries(self):
        self.assertEqual(read_offer(jsonld_page(ships=["US", "CA"])).ship_countries, {"US", "CA"})
        self.assertIsNone(read_offer(jsonld_page()).ship_countries)

    def test_pages_without_an_offer_or_with_junk(self):
        for page in ("<html>no data</html>", "<script type='application/ld+json'>{broken</script>",
                     "<script type='application/ld+json'>" + json.dumps({"@type": "Product", "name": "x"}) + "</script>",
                     jsonld_page(price="free"), jsonld_page(price="-5"), "", "<<<>>>"):
            self.assertIsNone(read_offer(page), page[:60])


class VerifyTests(unittest.TestCase):
    URL = "https://shop.example/p/xm5"

    def check(self, page, **kw):
        return verify(WANT, claim(url=kw.pop("url", self.URL), **kw), pages({self.URL: page}))

    def test_a_real_deal_passes(self):
        v = self.check(jsonld_page())
        self.assertTrue(v.valid, v.reasons)
        self.assertEqual(v.reasons, ["matches the want"])

    def test_each_rule_fails_on_its_own(self):
        cases = {
            "lacks WH-1000XM5": jsonld_page(name="Sony WH-1000XM4 Headphones"),
            "has refurbished": jsonld_page(name="Sony WH-1000XM5 (Refurbished)"),
            "over the max": jsonld_page(price="349.00"),
            "priced in EUR": jsonld_page(currency="EUR"),
            "not in stock (outofstock)": jsonld_page(availability="https://schema.org/OutOfStock"),
            "does not ship to US": jsonld_page(ships=["GB"]),
            "no schema.org offer": "<html>hello</html>",
        }
        for want_reason, page in cases.items():
            v = self.check(page)
            self.assertFalse(v.valid, want_reason)
            self.assertTrue(any(want_reason in r for r in v.reasons), (want_reason, v.reasons))

    def test_the_claimed_price_must_match_the_page(self):
        v = self.check(jsonld_page(price="319.99"), price="249.99")  # hallucinated a cheaper price
        self.assertFalse(v.valid)
        self.assertIn("claimed 249.99, page says 319.99", v.reasons)
        self.assertTrue(self.check(jsonld_page(price="300.40"), price="299.99").valid)  # within 50 cents

    def test_link_and_fetch_failures(self):
        self.assertIn("https", verify(WANT, claim(url="http://shop.example/p"), pages({})).reasons[0])
        self.assertIn("HTTP 404", verify(WANT, claim(url="https://shop.example/gone"), pages({})).reasons[0])

        def boom(url):
            raise TimeoutError()
        self.assertIn("TimeoutError", verify(WANT, claim(), boom).reasons[0])
        self.assertIn("another want", verify(WANT, claim(want="w2"), pages({})).reasons[0])

    def test_unknown_shipping_only_if_the_want_allows_it(self):
        strict = Want(**{**WANT.__dict__, "allow_unknown_shipping": False})
        v = verify(strict, claim(), pages({self.URL: jsonld_page()}))
        self.assertIn("page does not say where it ships", v.reasons)

    def test_words_match_whole_terms_not_fragments(self):
        self.assertFalse(self.check(jsonld_page(name="Sony WH-1000XM5S Clone")).valid)   # XM5S is not XM5
        self.assertTrue(self.check(jsonld_page(name="SONY wh 1000xm5 headphones")).valid)  # case and punctuation
        self.assertFalse(self.check(jsonld_page(name="Sony WH-1000XM5 carry case")).valid)   # banned word


class WinnerTests(unittest.TestCase):
    def verdicts(self, specs):
        out = []
        for i, (url, price, agent, at, page_price) in enumerate(specs):
            page = jsonld_page(price=page_price) if page_price else "<html></html>"
            out.append(verify(WANT, claim(url=url, price=price, agent=agent, at=at, cid=f"c{i}"), pages({url: page})))
        return out

    def test_cheapest_valid_claim_wins(self):
        vs = self.verdicts([("https://a.example/1", "310", "scout", 5, "310"),
                            ("https://b.example/1", "250", "quickfind", 3, None),      # hallucinated page
                            ("https://c.example/1", "289", "browser", 20, "289")])
        self.assertEqual(pick_winner(vs, deadline_s=60).claim.agent_id, "browser")

    def test_deadline_and_ties(self):
        vs = self.verdicts([("https://a.example/1", "289", "scout", 70, "289"),
                            ("https://b.example/1", "295", "browser", 30, "295")])
        self.assertEqual(pick_winner(vs, deadline_s=60).claim.agent_id, "browser")  # cheaper one was late
        tie = self.verdicts([("https://a.example/1", "295", "scout", 9, "295"),
                             ("https://b.example/2", "295", "browser", 4, "295")])
        self.assertEqual(pick_winner(tie, deadline_s=60).claim.agent_id, "browser")  # earlier wins a tie

    def test_same_url_goes_to_the_first_correct_claim(self):
        vs = self.verdicts([("https://a.example/1?utm_source=x", "289", "copycat", 8, "289"),
                            ("https://A.example/1/", "289", "scout", 2, "289")])
        self.assertEqual(pick_winner(vs, deadline_s=60).claim.agent_id, "scout")
        # a wrong price can't squat on the URL
        page = {"https://a.example/1": jsonld_page(price="289")}
        squat = verify(WANT, claim(url="https://a.example/1", price="199", agent="squatter", at=1, cid="s"), pages(page))
        honest = verify(WANT, claim(url="https://a.example/1", price="289", agent="scout", at=5, cid="h"), pages(page))
        self.assertEqual(pick_winner([squat, honest], deadline_s=60).claim.agent_id, "scout")

    def test_no_valid_claim_means_no_winner(self):
        self.assertIsNone(pick_winner(self.verdicts([("https://b.example/1", "250", "quickfind", 3, None)]), 60))
        self.assertIsNone(pick_winner([], 60))

    def test_canonical_url_and_claim_limits(self):
        self.assertEqual(canonical_url("https://Shop.example/p/1/?utm_source=a&color=b&ref=x"),
                         canonical_url("https://shop.example/p/1?color=b"))
        self.assertEqual([claim_limit(r) for r in (0.9, 0.6, 0.3)], [5, 3, 1])


if __name__ == "__main__":
    unittest.main()
