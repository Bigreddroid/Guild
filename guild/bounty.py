"""Deal bounties: the rules that decide whether a claimed deal is real and who wins.

A buyer posts a *want* (what, max price, ship-to, deadline) with a bounty. Agents
submit *claims* (a product URL and the price they saw). Guild fetches the page and
reads the shop's own structured data (schema.org Product/Offer as JSON-LD, with
microdata and Open Graph as fallbacks). A claim passes only if the page itself says
the item matches, costs at most the max, is in stock and can ship. The cheapest
verified claim before the deadline wins the bounty.

Everything here is deterministic and offline: fetching is passed in, so the rules
are tested on saved pages. No AI decides who gets paid.
"""
from __future__ import annotations

import html as htmllib
import json
import re
import urllib.parse
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Callable, Optional

IN_STOCK = {"instock", "limitedavailability", "onlineonly", "instoreonly"}
PRICE_TOLERANCE = Decimal("0.02")    # claimed vs page: 2% ...
PRICE_TOLERANCE_MIN = Decimal("0.50")  # ... or 50 cents, whichever is larger
MAX_PAGE_BYTES = 3_000_000


# ------------------------------------------------------------------ inputs

@dataclass(frozen=True)
class Want:
    id: str
    title: str                       # what the buyer typed, for people
    must_have: tuple                 # words the product name must contain
    must_not: tuple = ()             # words it must not contain (e.g. "refurbished")
    max_price: Decimal = Decimal("0")
    currency: str = "USD"
    ship_to: str = "US"              # ISO country code
    allow_unknown_shipping: bool = True
    bounty: Decimal = Decimal("0")


@dataclass(frozen=True)
class Claim:
    id: str
    want_id: str
    agent_id: str
    url: str
    price: Decimal                   # what the agent says the page shows
    currency: str
    at: float                        # seconds since the want opened


@dataclass
class Offer:
    """What the page itself says, read from its structured data."""
    name: str = ""
    price: Optional[Decimal] = None
    currency: str = ""
    availability: str = ""           # normalised, e.g. "instock"
    ship_countries: Optional[set] = None   # None = page doesn't say
    source: str = ""                 # json-ld | microdata | opengraph


@dataclass
class Verdict:
    claim: Claim
    valid: bool
    reasons: list = field(default_factory=list)
    offer: Optional[Offer] = None


# ---------------------------------------------------------- page reading

class _Collector(HTMLParser):
    """Collects JSON-LD blocks, microdata itemprops and meta tags."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.jsonld: list = []
        self._in_ld = False
        self._buf: list = []
        self.props: dict = {}
        self.meta: dict = {}
        self._prop_stack: list = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script" and a.get("type", "").lower().strip() == "application/ld+json":
            self._in_ld, self._buf = True, []
            return
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key:
                self.meta.setdefault(key, a.get("content", ""))
            if a.get("itemprop"):
                self.props.setdefault(a["itemprop"].lower(), a.get("content", ""))
            return
        if tag == "link" and a.get("itemprop"):
            self.props.setdefault(a["itemprop"].lower(), a.get("href", ""))
            return
        if a.get("itemprop"):
            prop = a["itemprop"].lower()
            if "content" in a:
                self.props.setdefault(prop, a["content"])
            else:
                self._prop_stack.append([prop, [], tag])

    def handle_endtag(self, tag):
        if tag == "script" and self._in_ld:
            self.jsonld.append("".join(self._buf))
            self._in_ld = False
        elif self._prop_stack and self._prop_stack[-1][2] == tag:
            prop, text, _ = self._prop_stack.pop()
            self.props.setdefault(prop, " ".join("".join(text).split()))

    def handle_data(self, data):
        if self._in_ld:
            self._buf.append(data)
        for entry in self._prop_stack:
            entry[1].append(data)


def _dec(value) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "")
    text = re.sub(r"[^\d.\-]", "", text)
    try:
        d = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() and d > 0 else None


def _norm_availability(value) -> str:
    text = str(value or "").strip().lower()
    return text.rsplit("/", 1)[-1].replace(" ", "").replace("_", "")


def _walk(node):
    """Yield every dict inside a JSON-LD document (handles @graph and nesting)."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _types(node: dict) -> set:
    t = node.get("@type", [])
    return {str(x).lower() for x in (t if isinstance(t, list) else [t])}


def _ship_countries(offer: dict) -> Optional[set]:
    details = offer.get("shippingDetails")
    if not details:
        return None
    countries = set()
    for d in _walk(details):
        dest = d.get("shippingDestination") if isinstance(d, dict) else None
        for x in _walk(dest) if dest else []:
            c = x.get("addressCountry")
            for code in (c if isinstance(c, list) else [c]):
                if isinstance(code, str) and code.strip():
                    countries.add(code.strip().upper())
    return countries or None


def _from_jsonld(blocks: list) -> Optional[Offer]:
    for raw in blocks:
        try:
            doc = json.loads(raw.strip())
        except ValueError:
            continue
        for node in _walk(doc):
            if "product" not in _types(node):
                continue
            offers = node.get("offers")
            candidates = [o for o in _walk(offers) if {"offer", "aggregateoffer"} & _types(o)] if offers else []
            for o in candidates:
                price = _dec(o.get("price")) or _dec(o.get("lowPrice"))
                if price is None:
                    spec = o.get("priceSpecification")
                    for s in _walk(spec) if spec else []:
                        price = _dec(s.get("price"))
                        if price is not None:
                            break
                if price is None:
                    continue
                currency = str(o.get("priceCurrency") or "").upper()
                return Offer(name=" ".join(str(node.get("name", "")).split()), price=price, currency=currency,
                             availability=_norm_availability(o.get("availability")),
                             ship_countries=_ship_countries(o), source="json-ld")
    return None


def read_offer(page: str) -> Optional[Offer]:
    """The product offer a page states about itself, or None if it states none."""
    c = _Collector()
    try:
        c.feed(page[:MAX_PAGE_BYTES])
    except Exception:
        return None
    offer = _from_jsonld(c.jsonld)
    if offer:
        return offer
    if _dec(c.props.get("price")) is not None:
        return Offer(name=c.props.get("name", ""), price=_dec(c.props.get("price")),
                     currency=(c.props.get("pricecurrency") or "").upper(),
                     availability=_norm_availability(c.props.get("availability")), source="microdata")
    og_price = c.meta.get("product:price:amount") or c.meta.get("og:price:amount")
    if _dec(og_price) is not None:
        return Offer(name=htmllib.unescape(c.meta.get("og:title", "")), price=_dec(og_price),
                     currency=(c.meta.get("product:price:currency") or c.meta.get("og:price:currency") or "").upper(),
                     availability=_norm_availability(c.meta.get("product:availability") or c.meta.get("og:availability")),
                     source="opengraph")
    return None


# ------------------------------------------------------------- the rules

def _words(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower()) + " "


def _has(name: str, term: str) -> bool:
    return _words(term).strip() != "" and _words(term) in _words(name)


def verify(want: Want, claim: Claim, fetch: Callable[[str], tuple]) -> Verdict:
    """Check one claim against the page. `fetch(url)` returns (status, html)."""
    reasons = []
    parts = urllib.parse.urlsplit(claim.url)
    if parts.scheme != "https" or not parts.hostname:
        return Verdict(claim, False, ["link must be an https URL"])
    if claim.want_id != want.id:
        return Verdict(claim, False, ["claim is for another want"])
    try:
        status, page = fetch(claim.url)
    except Exception as exc:
        detail = str(exc) if exc.__class__.__name__ == "FetchRefused" and str(exc) else type(exc).__name__
        return Verdict(claim, False, [f"page could not be fetched ({detail})"])
    if status != 200:
        return Verdict(claim, False, [f"page returned HTTP {status}"])
    offer = read_offer(page or "")
    if offer is None:
        return Verdict(claim, False, ["page states no product price (no schema.org offer)"])

    missing = [t for t in want.must_have if not _has(offer.name, t)]
    if missing:
        reasons.append(f"product name lacks {', '.join(missing)}")
    banned = [t for t in want.must_not if _has(offer.name, t)]
    if banned:
        reasons.append(f"product name has {', '.join(banned)}")
    if offer.currency and offer.currency != want.currency:
        reasons.append(f"priced in {offer.currency}, want {want.currency}")
    if offer.price > want.max_price:
        reasons.append(f"page price {offer.price} is over the max {want.max_price}")
    tolerance = max(PRICE_TOLERANCE_MIN, (claim.price * PRICE_TOLERANCE).quantize(Decimal("0.01")))
    if abs(offer.price - claim.price) > tolerance:
        reasons.append(f"claimed {claim.price}, page says {offer.price}")
    if offer.availability not in IN_STOCK:
        reasons.append(f"not in stock ({offer.availability or 'not stated'})")
    if offer.ship_countries is not None and want.ship_to.upper() not in offer.ship_countries:
        reasons.append(f"does not ship to {want.ship_to}")
    if offer.ship_countries is None and not want.allow_unknown_shipping:
        reasons.append("page does not say where it ships")
    return Verdict(claim, not reasons, reasons or ["matches the want"], offer)


def canonical_url(url: str) -> str:
    """Same product page regardless of tracking params, case or trailing slash."""
    p = urllib.parse.urlsplit(url.strip())
    query = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if not k.lower().startswith(("utm_", "ref", "fbclid", "gclid"))]
    return urllib.parse.urlunsplit(("https", (p.hostname or "").lower(), p.path.rstrip("/") or "/",
                                    urllib.parse.urlencode(sorted(query)), ""))


def pick_winner(verdicts: list, deadline_s: float) -> Optional[Verdict]:
    """Cheapest valid claim made before the deadline; ties go to the earliest.
    A URL belongs to the first agent that claimed it *correctly*, so a wrong claim
    can't squat on a real deal that another agent then reports properly."""
    seen, eligible = set(), []
    for v in sorted(verdicts, key=lambda v: (v.claim.at, v.claim.id)):
        if not v.valid or v.claim.at > deadline_s:
            continue
        key = canonical_url(v.claim.url)
        if key in seen:
            continue
        seen.add(key)
        eligible.append(v)
    if not eligible:
        return None
    return min(eligible, key=lambda v: (v.offer.price, v.claim.at, v.claim.agent_id))


def claim_limit(reputation: float) -> int:
    """How many claims an agent may make on one want: trust is earned."""
    if reputation >= 0.75:
        return 5
    if reputation >= 0.5:
        return 3
    return 1
