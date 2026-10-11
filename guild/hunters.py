"""Deal-hunting agents. Each one has a different strategy, so their results differ
for real reasons, not because a script says who fails:

    scout       Channel3 product search. Fast: claims the cheapest listings that
                match the model, without opening the pages. Catalogues go stale and
                listings hide conditions, so some of its claims fail.
    browser     Channel3 for candidates, then reads every live page in a KERNEL
                cloud browser and claims only what the page itself confirms. Slower.
    quickfind   An LLM answering from memory with no tools. Cheap and instant, and
                it hallucinates links and prices, which the verifier catches.

Without keys each agent runs on Guild's labelled demo shops (guild/shops.py) and
says so (mode="simulated"); the verifier treats their claims exactly the same.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal, InvalidOperation
from typing import Optional

from guild import shops
from guild.bounty import Want, _has, read_offer
from guild.market import AgentCard

FAST_MODEL = os.environ.get("GUILD_FAST_MODEL", "claude-haiku-5-5")


def _words_ok(want: Want, title: str, check_banned: bool) -> bool:
    """Same whole-term matching the verifier uses."""
    if not all(_has(title, w) for w in want.must_have):
        return False
    return not (check_banned and any(_has(title, w) for w in want.must_not))


def _dec(v) -> Optional[Decimal]:
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() and d > 0 else None


class Hunter:
    tool = ""
    eta_s = 0.0          # how long its hunt takes (simulated mode only)

    def __init__(self, card: AgentCard):
        self.card = card

    @property
    def mode(self) -> str:
        return "simulated"

    def hunt(self, want: Want, max_claims: int) -> list:
        """Return [{'url', 'price'}...] at most max_claims long."""
        raise NotImplementedError


class Scout(Hunter):
    tool, eta_s = "Channel3", 2.0

    def __init__(self, card, channel3=None):
        super().__init__(card)
        self.channel3 = channel3

    @property
    def mode(self):
        return "live" if self.channel3 else "simulated"

    def _listings(self, want):
        if self.channel3:
            return self.channel3.search(" ".join(want.must_have), limit=20, max_price=float(want.max_price))
        return shops.catalogue()

    def hunt(self, want, max_claims):
        # Strategy: trust the listing. Match the model, take the cheapest few under the
        # max, don't open the pages. (It skips the banned-word check too: listings are
        # short and the claims are cheap.)
        hits = [h for h in self._listings(want)
                if _words_ok(want, h["title"], check_banned=False)
                and (p := _dec(h["price"])) is not None and p <= want.max_price]
        hits.sort(key=lambda h: Decimal(str(h["price"])))
        return [{"url": h["url"], "price": str(h["price"])} for h in hits[:min(3, max_claims)]]


class Browser(Hunter):
    tool, eta_s = "KERNEL", 6.0

    def __init__(self, card, channel3=None, kernel=None, fetch=None):
        super().__init__(card)
        self.channel3, self.kernel, self.fetch = channel3, kernel, fetch

    @property
    def mode(self):
        return "live" if (self.channel3 and self.kernel) else "simulated"

    def _read(self, url: str) -> Optional[str]:
        if self.kernel:
            session = self.kernel.create_browser()
            try:
                return self.kernel.execute(session["session_id"],
                                           f"await page.goto({json.dumps(url)}, {{ waitUntil: 'domcontentloaded' }});"
                                           " return (await page.content()).slice(0, 2000000);", timeout_sec=45)
            finally:
                try:
                    self.kernel.delete_browser(session["session_id"])
                except Exception:
                    pass
        status, body = (self.fetch or shops.serve)(url) or (404, "")
        return body if status == 200 else None

    def hunt(self, want, max_claims):
        listings = (self.channel3.search(" ".join(want.must_have), limit=20, max_price=float(want.max_price))
                    if self.channel3 and self.kernel else shops.catalogue())
        listings = [h for h in listings if _words_ok(want, h["title"], check_banned=True)]
        listings.sort(key=lambda h: Decimal(str(h["price"])))
        claims = []
        for h in listings[:6]:  # open at most six pages
            if len(claims) >= max_claims:
                break
            try:
                page = self._read(h["url"])
            except Exception:
                continue
            offer = read_offer(page or "")
            if not offer or offer.price is None or offer.price > want.max_price:
                continue
            if offer.availability not in ("instock", "limitedavailability", "onlineonly"):
                continue
            if offer.ship_countries is not None and want.ship_to not in offer.ship_countries:
                continue
            if not _words_ok(want, offer.name, check_banned=True):
                continue
            claims.append({"url": h["url"], "price": str(offer.price)})  # the price the page shows now
        return claims


QUICKFIND_SCHEMA = {
    "type": "object",
    "properties": {"deals": {"type": "array", "items": {
        "type": "object", "properties": {"url": {"type": "string"}, "price": {"type": "string"}},
        "required": ["url", "price"], "additionalProperties": False}}},
    "required": ["deals"], "additionalProperties": False,
}


class QuickFind(Hunter):
    tool, eta_s = "memory (no tools)", 0.5

    # What it "remembers" when no model is configured: a domain that doesn't exist,
    # and a real demo listing at the price it had last month.
    SIMULATED = [{"url": "https://www.sonydeals-outlet.example/wh-1000xm5", "price": "189.99"},
                 {"url": shops.url_for("metro-tech/sony-wh-1000xm5-midnight"), "price": "309.00"}]

    def __init__(self, card, client=None, model: str = FAST_MODEL):
        super().__init__(card)
        self.client, self.model = client, model

    @property
    def mode(self):
        return "live" if self.client else "simulated"

    def hunt(self, want, max_claims):
        if not self.client:
            return self.SIMULATED[:max_claims]
        prompt = (f"Find where to buy: {want.title}. Must be under {want.max_price} {want.currency}, new, "
                  f"in stock, shipping to {want.ship_to}. Answer from what you know: give product page URLs "
                  f"and prices. At most {max_claims}.")
        r = self.client.beta.messages.create(
            model=self.model, max_tokens=800, messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": QUICKFIND_SCHEMA}},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        text = next((b.text for b in r.content if getattr(b, "type", "") == "text"), "{}")
        try:
            deals = json.loads(text).get("deals", [])
        except (ValueError, AttributeError):
            return []
        return [{"url": str(d.get("url", ""))[:500], "price": str(d.get("price", ""))[:20]}
                for d in deals if isinstance(d, dict)][:max_claims]


def default_hunters(channel3=None, kernel=None, claude=None, fetch=None) -> dict:
    cards = [
        (Scout, AgentCard("scout", "Scout", "scout@devs.example", {"hunt"}, 0.8, passes=8, fails=2), {"channel3": channel3}),
        (Browser, AgentCard("browser", "Browser", "browser@devs.example", {"hunt"}, 0.8, passes=6, fails=1),
         {"channel3": channel3, "kernel": kernel, "fetch": fetch}),
        (QuickFind, AgentCard("quickfind", "QuickFind", "quickfind@devs.example", {"hunt"}, 0.6, passes=1, fails=1),
         {"client": claude}),
    ]
    return {card.id: cls(card, **kw) for cls, card, kw in cards}
