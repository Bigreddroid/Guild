"""Marketing that pays on proof: a creator is paid only when their post is really live.

The owner (or their agent, with the owner's approval) books a promotion: who the
creator is, the channel they publish on, their PayPal account, the amount, the link
the post must carry and the words it must mention. Guild holds the amount in PayPal
(an authorization paid to the creator's account). When someone sends the post's URL,
Guild opens it and checks, in code:

    live         the page opens (HTTP 200)
    channel      it is on the creator's own channel, not someone else's site
    link         it links to the owner's page (a real <a href>, tracking params ignored)
    disclosure   it says it is an ad (#ad, #sponsored, "paid partnership", ...) in visible text
    mentions     it names every required word, in visible text

All pass: the hold is captured and the creator is paid. Anything fails: the money
stays held, with the reasons, until a good proof arrives or the deadline voids it.
Pure: no PayPal, no network (the fetch is passed in).
"""
from __future__ import annotations

import html as htmllib
import re
import urllib.parse
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from guild.bounty import canonical_url

DISCLOSURES = ("#ad", "#ads", "#sponsored", "#paidpartner", "#partner", "sponsored", "paid partnership",
               "advertisement", "ad:", "in partnership with")
MAX_WORDS = 6


class BriefError(ValueError):
    pass


@dataclass(frozen=True)
class Brief:
    creator: str          # who is being paid, e.g. "Maya Reviews"
    channel: str          # their channel: proof must be under this URL
    link: str             # the owner's page the post must link to
    words: tuple = ()     # the post must mention each of these
    disclosure: bool = True

    def __post_init__(self):
        if not self.creator.strip() or len(self.creator) > 80:
            raise BriefError("creator must be a name (max 80 chars)")
        for name in ("channel", "link"):
            url = getattr(self, name)
            if not isinstance(url, str) or not url.startswith("https://") or len(url) > 300:
                raise BriefError(f"{name} must be an https URL (max 300 chars)")
            if not urllib.parse.urlsplit(url).hostname:
                raise BriefError(f"{name} must be an https URL with a host")
        if len(self.words) > MAX_WORDS or any(not w.strip() or len(w) > 40 for w in self.words):
            raise BriefError(f"give at most {MAX_WORDS} words to mention, each up to 40 chars")


@dataclass
class ProofVerdict:
    url: str
    valid: bool
    reasons: list
    checks: dict = field(default_factory=dict)


def _shown(page: str) -> str:
    """The page's HTML without comments, scripts, styles or hidden elements."""
    text = re.sub(r"(?is)<!--.*?-->", " ", page or "")
    text = re.sub(r"(?is)<(script|style|template|noscript)\b[^>]*>.*?</\1\s*>", " ", text)
    # the hidden attribute (not a class name or "overflow: hidden"), aria-hidden, or hiding styles
    hidden = r"""(?:(?<!:)\shidden(?=[\s>/=])|aria-hidden\s*=\s*["']?true|display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?![.\d]))"""
    for _ in range(3):  # nested hidden blocks
        text = re.sub(rf"(?is)<(\w+)\b[^>]*{hidden}[^>]*>.*?</\1\s*>", " ", text)
    return text


def visible_text(page: str) -> str:
    """What a reader actually sees."""
    return " ".join(htmllib.unescape(re.sub(r"(?s)<[^>]+>", " ", _shown(page))).split())


def links(page: str, base: str) -> set:
    """Every visible <a href> on the page, canonical (tracking params dropped)."""
    out = set()
    for href in re.findall(r"""(?is)<a\b[^>]*\bhref\s*=\s*["']([^"']+)["']""", _shown(page)):
        url = urllib.parse.urljoin(base, htmllib.unescape(href.strip()))
        if url.startswith("https://"):
            out.add(canonical_url(url))
    return out


def _has(text: str, phrase: str) -> bool:
    """Whole-word, case-insensitive: '#ad' must not match '#adventure', 'ad:' not 'lead:'."""
    p = phrase.lower()
    return re.search(r"(?<![\w#])" + re.escape(p) + (r"(?!\w)" if p[-1].isalnum() else ""), text.lower()) is not None


def on_channel(url: str, channel: str) -> bool:
    u, c = urllib.parse.urlsplit(canonical_url(url)), urllib.parse.urlsplit(canonical_url(channel))
    if u.hostname != c.hostname:
        return False
    prefix = c.path.rstrip("/")
    return not prefix or u.path == prefix or u.path.startswith(prefix + "/")


def check_proof(brief: Brief, url: str, fetch: Callable) -> ProofVerdict:
    """Open the post and check it against the brief."""
    checks, reasons = {}, []
    if not isinstance(url, str) or not url.startswith("https://") or len(url) > 500:
        return ProofVerdict(str(url)[:500], False, ["the proof must be an https URL"], {"live": False})
    checks["channel"] = on_channel(url, brief.channel)
    if not checks["channel"]:
        reasons.append(f"not on {brief.creator}'s channel ({brief.channel})")
    try:
        status, page = fetch(url)
    except Exception as exc:  # noqa: BLE001 - any failure to open is "not live"
        status, page = 0, ""
        reasons.append(f"could not open the post ({type(exc).__name__})")
    checks["live"] = status == 200
    if status and status != 200:
        reasons.append(f"the post is not live (HTTP {status})")
    if checks["live"]:
        text = visible_text(page)
        checks["link"] = canonical_url(brief.link) in links(page, url)
        if not checks["link"]:
            reasons.append(f"no link to {brief.link}")
        if brief.disclosure:
            checks["disclosure"] = any(_has(text, d) for d in DISCLOSURES)
            if not checks["disclosure"]:
                reasons.append("no visible ad disclosure (#ad, #sponsored, paid partnership)")
        missing = [w for w in brief.words if not _has(text, w)]
        checks["mentions"] = not missing
        if missing:
            reasons.append("does not mention " + ", ".join(missing))
    valid = all(checks.values()) and not reasons
    return ProofVerdict(url, valid, reasons or ["live, on the creator's channel, links to you and discloses the ad"],
                        checks)


def parse_brief(body: dict) -> tuple:
    """API body -> (Brief, amount, payee email, deadline minutes)."""
    words = body.get("words") or ()
    if isinstance(words, str):
        words = [w.strip() for w in words.split(",")]
    if not isinstance(words, (list, tuple)):
        raise BriefError("words must be a list")
    brief = Brief(str(body.get("creator") or "").strip(), str(body.get("channel") or ""), str(body.get("link") or ""),
                  tuple(str(w).strip() for w in words if str(w).strip()), body.get("disclosure", True) is not False)
    email = str(body.get("paypal_email") or "").strip()
    if not re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}", email):
        raise BriefError("paypal_email must be the creator's PayPal email")
    try:
        minutes = int(body.get("deadline_minutes", 10))
    except (TypeError, ValueError):
        raise BriefError("deadline_minutes must be a whole number")
    if not 1 <= minutes <= 3 * 24 * 60:   # a PayPal authorization is honoured for 3 days
        raise BriefError("deadline_minutes must be between 1 and 4320 (3 days)")
    try:
        amount = Decimal(str(body.get("amount")).strip())
    except Exception:  # noqa: BLE001
        raise BriefError("amount must be an amount")
    if not amount.is_finite() or amount <= 0:
        raise BriefError("amount must be more than 0")
    return brief, amount.quantize(Decimal("0.01")), email, minutes
