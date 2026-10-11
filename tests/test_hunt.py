import json
import os
import socket
import sys
import time
import unittest
from decimal import Decimal
from types import SimpleNamespace

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from guild import shops
from guild.fetcher import FetchRefused, SafeFetcher, check_url
from guild.hunt import run_hunt
from guild.hunters import Browser, QuickFind, Scout, default_hunters
from guild.market import AgentCard

PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


def resolver(table):
    def resolve(host, port, proto=0):
        if host not in table:
            raise socket.gaierror("nope")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in table[host]]
    return resolve


def fetcher(**kw):
    return SafeFetcher(local=shops.serve, resolve=resolver({}), **kw)


def card(i="x", rep=0.8):
    return AgentCard(i, i.title(), f"{i}@devs.example", {"hunt"}, rep)


class FetcherTests(unittest.TestCase):
    def test_refuses_anything_that_could_reach_inside(self):
        r = resolver({"private.example": ["10.0.0.5"], "loop.example": ["127.0.0.1"], "meta.example": ["169.254.169.254"],
                      "v6.example": ["::1"], "mixed.example": ["93.184.216.34", "192.168.1.1"], "ok.example": ["93.184.216.34"]})
        for url in ("http://ok.example/p", "https://ok.example:8443/p", "https://user:pw@ok.example/p",
                    "https://localhost/p", "https://private.example/p", "https://loop.example/p",
                    "https://meta.example/latest/meta-data", "https://v6.example/p", "https://mixed.example/p",
                    "https://nowhere.example/p", "ftp://ok.example/p"):
            with self.assertRaises(FetchRefused, msg=url):
                check_url(url, r)
        check_url("https://ok.example/p", r)  # public is fine

    def test_redirects_are_rechecked_each_hop(self):
        def handler(request):
            return httpx.Response(302, headers={"location": "https://private.example/admin"})
        f = SafeFetcher(transport=httpx.MockTransport(handler),
                        resolve=resolver({"ok.example": ["93.184.216.34"], "private.example": ["10.0.0.5"]}))
        with self.assertRaises(FetchRefused):
            f("https://ok.example/deal")

    def test_body_is_capped_and_demo_shops_never_touch_the_network(self):
        f = SafeFetcher(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 4_000_000)),
                        resolve=resolver({"ok.example": ["93.184.216.34"]}))
        status, body = f("https://ok.example/big")
        self.assertEqual((status, len(body)), (200, 3_000_000))
        status, body = fetcher()(shops.url_for("harbor-audio/sony-wh-1000xm5-black"))
        self.assertEqual(status, 200)
        self.assertIn("application/ld+json", body)
        self.assertEqual(fetcher()(shops.url_for("nope/missing"))[0], 404)


class HunterTests(unittest.TestCase):
    def test_scout_trusts_listings_and_claims_the_three_cheapest(self):
        claims = Scout(card("scout")).hunt(shops.DEMO_WANT, 5)
        self.assertEqual([c["price"] for c in claims], ["219.00", "279.00", "289.00"])
        self.assertEqual(len(Scout(card("scout")).hunt(shops.DEMO_WANT, 1)), 1)  # respects its claim limit

    def test_scout_live_uses_channel3(self):
        c3 = SimpleNamespace(search=lambda q, limit, max_price: [
            {"title": "Sony WH-1000XM5", "price": "301.00", "url": "https://real.example/xm5", "shop": "R"},
            {"title": "Sony WH-1000XM4", "price": "199.00", "url": "https://real.example/xm4", "shop": "R"}])
        s = Scout(card("scout"), channel3=c3)
        self.assertEqual(s.mode, "live")
        self.assertEqual(s.hunt(shops.DEMO_WANT, 5), [{"url": "https://real.example/xm5", "price": "301.00"}])

    def test_browser_claims_only_what_the_page_confirms(self):
        claims = Browser(card("browser")).hunt(shops.DEMO_WANT, 5)
        self.assertEqual(claims, [{"url": shops.url_for("harbor-audio/sony-wh-1000xm5-black"), "price": "299.00"},
                                  {"url": shops.url_for("metro-tech/sony-wh-1000xm5-midnight"), "price": "318.00"}])

    def test_browser_live_reads_pages_in_kernel_and_closes_them(self):
        calls = []
        kernel = SimpleNamespace(
            create_browser=lambda: calls.append("create") or {"session_id": "s1"},
            execute=lambda sid, code, timeout_sec: calls.append("execute") or shops.page("harbor-audio/sony-wh-1000xm5-black"),
            delete_browser=lambda sid: calls.append("delete"))
        c3 = SimpleNamespace(search=lambda q, limit, max_price: [
            {"title": "Sony WH-1000XM5", "price": "290.00", "url": "https://real.example/xm5", "shop": "R"}])
        b = Browser(card("browser"), channel3=c3, kernel=kernel)
        self.assertEqual(b.hunt(shops.DEMO_WANT, 5), [{"url": "https://real.example/xm5", "price": "299.00"}])
        self.assertEqual(calls, ["create", "execute", "delete"])

    def test_quickfind_simulated_and_live(self):
        self.assertEqual(len(QuickFind(card("quickfind")).hunt(shops.DEMO_WANT, 5)), 2)
        sent = []

        def create(**kw):
            sent.append(kw)
            text = json.dumps({"deals": [{"url": "https://made-up.example/xm5", "price": "199"}]})
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])
        q = QuickFind(card("quickfind"), client=SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create))))
        self.assertEqual(q.hunt(shops.DEMO_WANT, 1), [{"url": "https://made-up.example/xm5", "price": "199"}])
        self.assertNotIn("tools", sent[0])  # answers from memory, by design
        bad = QuickFind(card("q"), client=SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(
            create=lambda **kw: SimpleNamespace(content=[SimpleNamespace(type="text", text="not json")])))))
        self.assertEqual(bad.hunt(shops.DEMO_WANT, 3), [])


class RunHuntTests(unittest.TestCase):
    def run_demo(self, hunters=None, **kw):
        events = []
        result = run_hunt(shops.DEMO_WANT, hunters or default_hunters(), fetcher(), events.append,
                          deadline_s=kw.pop("deadline_s", 30), speed=kw.pop("speed", 0), **kw)
        return result, events

    def test_the_demo_story(self):
        result, events = self.run_demo()
        claims = [e for e in events if e["type"] == "claim"]
        by_agent = {a: [c["valid"] for c in claims if c["agent_id"] == a] for a in ("scout", "browser", "quickfind")}
        self.assertEqual(by_agent, {"scout": [False] * 3, "browser": [True, True], "quickfind": [False, False]})
        reasons = " | ".join(r for c in claims for r in c["reasons"])
        for want in ("refurbished", "not in stock", "does not ship to US", "host does not resolve",
                     "claimed 309.00, page says 318.00"):
            self.assertIn(want, reasons)
        win = next(e for e in events if e["type"] == "winner")
        self.assertEqual((win["agent_id"], win["price"], win["saved"]), ("browser", "299.00", "31.00"))
        self.assertEqual(result["owed"], {"browser@devs.example": Decimal("2.00")})
        self.assertEqual(events[0]["type"], "want")
        self.assertEqual(events[-1]["type"], "done")

    def test_reputation_moves_with_each_claim(self):
        hunters = default_hunters()
        before = {k: h.card.stars for k, h in hunters.items()}
        self.run_demo(hunters)
        after = {k: h.card.stars for k, h in hunters.items()}
        self.assertGreater(after["browser"], before["browser"])
        self.assertLess(after["scout"], before["scout"])
        self.assertLess(after["quickfind"], before["quickfind"])

    def test_no_valid_claim_means_no_bounty(self):
        result, events = self.run_demo({"quickfind": default_hunters()["quickfind"]})
        self.assertIsNone(result["winner"])
        self.assertEqual(result["owed"], {})
        self.assertTrue(any(e["type"] == "no_winner" for e in events))

    def test_slow_agents_miss_the_deadline(self):
        hunters = default_hunters()
        hunters["browser"].eta_s = 5.0  # simulated: takes 5 s
        start = time.monotonic()
        result, events = self.run_demo(hunters, deadline_s=0.3, speed=1.0)
        self.assertLess(time.monotonic() - start, 3)
        self.assertNotIn("browser", {e["agent_id"] for e in events if e["type"] == "claim"})

    def test_bad_agents_cannot_break_the_run(self):
        class Crash(Scout):
            def hunt(self, want, n):
                raise RuntimeError("boom")

        class Junk(Scout):
            def hunt(self, want, n):
                return [{"url": "https://x.example/p", "price": "free"}, "nonsense", {"url": 5, "price": None}] * 3

        hunters = {"crash": Crash(card("crash")), "junk": Junk(card("junk", rep=0.3))}
        result, events = self.run_demo(hunters)
        self.assertTrue(any(e["type"] == "agent_error" and e["agent_id"] == "crash" for e in events))
        junk = [e for e in events if e["type"] == "claim"]
        self.assertEqual(len(junk), 1)  # low reputation: one claim allowed
        self.assertFalse(junk[0]["valid"])
        self.assertIsNone(result["winner"])


if __name__ == "__main__":
    unittest.main()
