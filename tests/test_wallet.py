import os
import random
import sys
import threading
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from guild import shops
from guild.bounty import Claim, Offer, Trust, Verdict, verify
from guild.fetcher import SafeFetcher
from guild.wallet import (APPROVED, BLOCKED, FAILED, PAID, PENDING, REJECTED, Envelope, Policy, PolicyError,
                          Wallet, WalletError)

WANT = shops.DEMO_WANT
PAYEE = {"email_address": "seller@business.example.com"}
FETCH = SafeFetcher(local=shops.serve)
TRUST = Trust(listings=tuple(shops.catalogue()), seller_of=shops.seller_of)


def policy(budget="400", each="350", ask="250", envelopes=None):
    return Policy(Decimal(budget), Decimal(each), Decimal(ask), WANT, envelopes)


def env(limit, each=None, ask=None):
    each = each or limit
    return Envelope(Decimal(limit), Decimal(each), Decimal(ask or each))


def split():
    """A $400 wallet: shopping 200, marketing 120 (ask above 50), tools 80 (at most 30 each)."""
    return policy(envelopes={"shopping": env("200", "200", "150"), "marketing": env("120", "100", "50"),
                             "tools": env("80", "30")})


def ok(price, url="https://shop.example/p"):
    claim = Claim("c", WANT.id, "agent", url, Decimal(price), "USD", 0.0)
    offer = Offer(name="Sony WH-1000XM5", price=Decimal(price), currency="USD", availability="instock")
    return Verdict(claim, True, ["matches the want"], offer, {"name": True})


def checked(url, price):
    return verify(WANT, Claim("c", WANT.id, "agent", url, Decimal(price), "USD", 0.0), FETCH, TRUST)


class PolicyTests(unittest.TestCase):
    def test_bad_policies_are_refused(self):
        for args in (("0", "1", "0"), ("600", "100", "50"), ("100", "150", "50"), ("100", "50", "60"),
                     ("100", "0", "0"), ("-1", "1", "0")):
            with self.assertRaises(PolicyError, msg=args):
                policy(*args)
        policy("400", "400", "0")  # ask for everything is fine


class DecisionTests(unittest.TestCase):
    def test_the_demo_story(self):
        w = Wallet("w1", "shopper", policy())
        fake = w.decide(shops.TRAP_URL, "headphones", checked(shops.TRAP_URL, "149.00"), payee=PAYEE)
        pads = w.decide(shops.url_for("harbor-audio/sony-wh-1000xm5-ear-pads"), "ear pads",
                        checked(shops.url_for("harbor-audio/sony-wh-1000xm5-ear-pads"), "19.00"), payee=PAYEE)
        misquote = w.decide(shops.url_for("metro-tech/sony-wh-1000xm5-midnight"), "headphones",
                            checked(shops.url_for("metro-tech/sony-wh-1000xm5-midnight"), "309.00"), payee=PAYEE)
        harbor_url = shops.url_for("harbor-audio/sony-wh-1000xm5-black")
        harbor = w.decide(harbor_url, "headphones", checked(harbor_url, "299.00"), payee=PAYEE)
        case_url = shops.url_for("harbor-audio/sony-wh-1000xm5-case")
        case = w.decide(case_url, "case", checked(case_url, "29.00"), payee=PAYEE)

        self.assertEqual([p.status for p in (fake, pads, misquote, harbor, case)],
                         [BLOCKED, BLOCKED, BLOCKED, PENDING, BLOCKED])
        self.assertIn("no independent source lists this seller", fake.reasons[0])
        self.assertTrue(any("usual" in r for r in pads.reasons))
        self.assertIn("claimed 309.00, page says 318.00", misquote.reasons)
        self.assertTrue(any("has case" in r for r in case.reasons))
        totals = w.totals()
        self.assertEqual({k: totals[k] for k in ("budget", "spent", "reserved", "remaining")},
                         {"budget": "400.00", "spent": "0.00", "reserved": "299.00", "remaining": "101.00"})
        self.assertEqual(totals["envelopes"], [{"name": "shopping", "limit": "400.00", "spent": "0.00",
                                                "reserved": "299.00", "remaining": "101.00",
                                                "max_per_purchase": "350.00", "approve_above": "250.00"}])
        w.approve(harbor.id)
        self.assertEqual(w.totals()["spent"], "299.00")
        w.record_payment(harbor.id, order_id="ORDER-1")
        self.assertEqual((harbor.status, harbor.order_id), (PAID, "ORDER-1"))
        self.assertIsNone(fake.payee)  # a blocked purchase never carries a payee

    def test_small_purchases_go_through_and_big_ones_wait(self):
        w = Wallet("w", "a", policy())
        self.assertEqual(w.decide("https://a.example/1", "x", ok("250.00"), payee=PAYEE).status, APPROVED)
        self.assertEqual(w.decide("https://a.example/2", "x", ok("151.00"), payee=PAYEE).status, BLOCKED)  # 150 left
        self.assertEqual(w.decide("https://a.example/3", "x", ok("150.00"), payee=PAYEE).status, APPROVED)
        w2 = Wallet("w2", "a", policy())
        self.assertEqual(w2.decide("https://a.example/1", "x", ok("250.01"), payee=PAYEE).status, PENDING)
        self.assertEqual(w2.decide("https://a.example/2", "x", ok("351"), payee=PAYEE).reasons[0],
                         "351.00 is over the 350 limit per purchase")

    def test_pending_purchases_reserve_budget(self):
        w = Wallet("w", "a", policy(budget="400", each="350", ask="100"))
        first = w.decide("https://a.example/1", "x", ok("300"), payee=PAYEE)
        second = w.decide("https://a.example/2", "x", ok("150"), payee=PAYEE)
        self.assertEqual((first.status, second.status), (PENDING, BLOCKED))
        w.reject(first.id)
        self.assertEqual(w.decide("https://a.example/2", "x", ok("150"), payee=PAYEE).status, PENDING)

    def test_no_payee_no_duplicates_and_the_page_price_is_what_counts(self):
        w = Wallet("w", "a", policy())
        self.assertIn("publishes no PayPal account", w.decide("https://a.example/1", "x", ok("10")).reasons[0])
        self.assertEqual(w.decide("https://a.example/1", "x", ok("10"), seller_problem="card is for x.example").reasons,
                         ["card is for x.example"])
        w.decide("https://a.example/2?utm_source=x", "x", ok("10"), payee=PAYEE)
        self.assertIn("already bought", w.decide("https://a.example/2", "x", ok("10"), payee=PAYEE).reasons[0])
        v = ok("10.40")
        v.claim = Claim("c", WANT.id, "agent", "https://a.example/3", Decimal("10"), "USD", 0.0)
        self.assertEqual(w.decide("https://a.example/3", "x", v, payee=PAYEE).price, Decimal("10.40"))

    def test_approvals_and_payments_only_move_forward(self):
        w = Wallet("w", "a", policy())
        blocked = w.decide("https://a.example/1", "x", ok("999"), payee=PAYEE)
        pending = w.decide("https://a.example/2", "x", ok("300"), payee=PAYEE)
        approved = w.decide("https://a.example/3", "x", ok("10"), payee=PAYEE)
        for bad in (lambda: w.approve(blocked.id), lambda: w.reject(approved.id),
                    lambda: w.record_payment(pending.id), lambda: w.approve("nope")):
            with self.assertRaises(WalletError):
                bad()
        w.record_payment(approved.id, error="INSTRUMENT_DECLINED")
        self.assertEqual(approved.status, FAILED)
        self.assertEqual(w.totals()["spent"], "0.00")  # a failed payment releases its budget

    def test_stop_and_close_drop_waiting_purchases(self):
        w = Wallet("w", "a", policy())
        pending = w.decide("https://a.example/1", "x", ok("300"), payee=PAYEE)
        w.stop()
        self.assertEqual(pending.status, REJECTED)
        self.assertEqual(w.decide("https://a.example/2", "x", ok("10"), payee=PAYEE).reasons[0],
                         "the owner stopped this wallet")
        w2 = Wallet("w2", "a", policy())
        p2 = w2.decide("https://a.example/1", "x", ok("300"), payee=PAYEE)
        self.assertEqual(w2.close()["reserved"], "0.00")
        with self.assertRaises(WalletError):
            w2.approve(p2.id)
        self.assertEqual(w2.decide("https://a.example/2", "x", ok("1"), payee=PAYEE).status, BLOCKED)


class EnvelopeTests(unittest.TestCase):
    def test_bad_envelopes_are_refused(self):
        for envelopes in ({}, {"Marketing!": env("10")}, {"a": env("300"), "b": env("200")},  # adds up past 400
                          {"a": env("0", "1")}, {"a": Envelope(Decimal("50"), Decimal("60"), Decimal("0"))},
                          {"a": Envelope(Decimal("50"), Decimal("40"), Decimal("45"))}, {"a": "50"},
                          {f"e{i}": env("10") for i in range(9)}):
            with self.assertRaises(PolicyError, msg=envelopes):
                policy(envelopes=envelopes)
        self.assertEqual(list(split().envelopes), ["shopping", "marketing", "tools"])

    def test_each_purchase_fits_its_envelope_and_the_budget(self):
        w = Wallet("w", "a", split())
        ad = w.decide("https://a.example/ad", "ad", ok("45"), payee=PAYEE, category="marketing")
        post = w.decide("https://a.example/post", "post", ok("60"), payee=PAYEE, category="marketing")
        self.assertEqual((ad.status, post.status, ad.category), (APPROVED, PENDING, "marketing"))
        self.assertEqual(post.reasons, ["60.00 is above 50: waiting for the owner"])  # the envelope's own ask amount
        over = w.decide("https://a.example/more", "more", ok("20"), payee=PAYEE, category="marketing")
        self.assertEqual(over.reasons, ["20.00 is over the marketing envelope left (15.00)"])  # 120 - 45 - 60
        self.assertEqual(w.decide("https://a.example/t", "t", ok("31"), payee=PAYEE, category="tools").reasons,
                         ["31.00 is over the 30 limit per tools purchase"])
        self.assertEqual(w.decide("https://a.example/s", "s", ok("20"), payee=PAYEE).status, APPROVED)  # shopping
        self.assertEqual(w.decide("https://a.example/x", "x", ok("5"), payee=PAYEE, category="travel").reasons,
                         ["there is no 'travel' envelope in this wallet"])
        envs = {e["name"]: e for e in w.totals()["envelopes"]}
        self.assertEqual((envs["marketing"]["spent"], envs["marketing"]["reserved"], envs["marketing"]["remaining"]),
                         ("45.00", "60.00", "15.00"))
        self.assertEqual((envs["shopping"]["spent"], envs["tools"]["remaining"]), ("20.00", "80.00"))
        self.assertEqual(w.totals()["remaining"], "275.00")
        w.reject(post.id)  # a rejected purchase frees its envelope
        self.assertEqual(w.remaining("marketing"), Decimal("75.00"))

    def test_random_sequences_never_overspend_any_envelope(self):
        rng = random.Random(11)
        names = ("shopping", "marketing", "tools", "nope")
        for trial in range(100):
            w = Wallet("w", "a", split())
            for i in range(40):
                if rng.random() < 0.65:
                    w.decide(f"https://a.example/{rng.randint(1, 30)}", "x", ok(str(rng.randint(1, 210))),
                             payee=PAYEE, category=rng.choice(names))
                else:
                    pend = [p for p in w.purchases if p.status == PENDING]
                    if pend:
                        (w.approve if rng.random() < 0.7 else w.reject)(rng.choice(pend).id)
                for name, e in w.policy.envelopes.items():
                    self.assertLessEqual(w.spent(name) + w.reserved(name), e.limit, (trial, i, name))
                self.assertLessEqual(w.spent() + w.reserved(), Decimal("400"))
                self.assertFalse(any(p.category == "nope" and p.status != BLOCKED for p in w.purchases))


class BudgetInvariantTests(unittest.TestCase):
    def test_random_sequences_never_overspend(self):
        rng = random.Random(7)
        for trial in range(200):
            w = Wallet("w", "a", policy(budget="400", each="350", ask=str(rng.choice([0, 50, 250]))))
            for i in range(30):
                op = rng.random()
                if op < 0.6:
                    w.decide(f"https://a.example/{rng.randint(1, 12)}", "x", ok(str(rng.randint(1, 420))), payee=PAYEE)
                else:
                    pend = [p for p in w.purchases if p.status == PENDING]
                    if pend:
                        p = rng.choice(pend)
                        (w.approve if op < 0.85 else w.reject)(p.id)
                spent, reserved = w.spent(), w.reserved()
                self.assertLessEqual(spent + reserved, Decimal("400"), (trial, i))
                self.assertGreaterEqual(w.remaining(), 0)

    def test_concurrent_requests_cannot_overspend(self):
        w = Wallet("w", "a", policy(budget="400", each="100", ask="100"))

        def buy(n):
            w.decide(f"https://a.example/{n}", "x", ok("90"), payee=PAYEE)
        threads = [threading.Thread(target=buy, args=(n,)) for n in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(p.status == APPROVED for p in w.purchases), 4)  # 4 × 90 = 360 ≤ 400 < 5 × 90
        self.assertEqual(w.totals()["remaining"], "40.00")


if __name__ == "__main__":
    unittest.main()
