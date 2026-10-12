"""Who gets paid: a seller's own PayPal account, proven by the seller's domain.

A shop that accepts payments from AI agents publishes a small card on its own site:

    https://<shop>/.well-known/agent-pay.json
    {"version": 1, "domain": "<shop host>", "seller": "<name>", "paypal": {"merchant_id": "...", "email": "..."}}

Because the card is served from the shop's own domain, whoever controls the domain
controls where money goes, the same trust model as HTTPS certificates. That proves
*control*, not *honesty*: a fake shop can publish a card too, which is why Guild
also needs an independent listing of the seller (bounty.cross_check) before paying.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass
from typing import Callable, Optional

CARD_PATH = "/.well-known/agent-pay.json"
_EMAIL = re.compile(r"^[^@\s<>\"']{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
_MERCHANT_ID = re.compile(r"^[A-Z0-9]{8,20}$")


class SellerCardError(ValueError):
    pass


@dataclass(frozen=True)
class SellerCard:
    seller: str          # the seller id the card was fetched for (host, or demo shop)
    name: str
    merchant_id: str     # PayPal merchant id (preferred payee)
    email: str           # PayPal account email (payee fallback)
    card_url: str

    def payee(self) -> dict:
        """The Orders v2 purchase_units[].payee for this seller."""
        return {"merchant_id": self.merchant_id} if self.merchant_id else {"email_address": self.email}


def host_of(url: str) -> str:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def read_card(url: str, fetch: Callable[[str], tuple], seller_of: Callable[[str], str] = host_of,
              card_url_of: Optional[Callable[[str], str]] = None) -> SellerCard:
    """Fetch and validate the seller card for the shop that serves `url`."""
    seller = seller_of(url)
    card_url = card_url_of(url) if card_url_of else f"https://{urllib.parse.urlsplit(url).hostname}{CARD_PATH}"
    try:
        status, body = fetch(card_url)
    except Exception as exc:
        raise SellerCardError(f"seller card could not be fetched ({type(exc).__name__})") from exc
    if status != 200:
        raise SellerCardError(f"seller publishes no agent-pay card (HTTP {status})")
    try:
        card = json.loads(body or "")
    except ValueError:
        raise SellerCardError("seller card is not JSON")
    if not isinstance(card, dict) or card.get("version") != 1:
        raise SellerCardError("seller card has an unknown format")
    if str(card.get("domain", "")).lower() != seller:
        raise SellerCardError(f"seller card is for {card.get('domain')!r}, not {seller!r}")
    paypal = card.get("paypal") if isinstance(card.get("paypal"), dict) else {}
    merchant_id, email = str(paypal.get("merchant_id") or ""), str(paypal.get("email") or "")
    if merchant_id and not _MERCHANT_ID.match(merchant_id):
        merchant_id = ""
    if email and not _EMAIL.match(email):
        email = ""
    if not (merchant_id or email):
        raise SellerCardError("seller card names no PayPal account")
    return SellerCard(seller=seller, name=str(card.get("seller") or seller)[:80], merchant_id=merchant_id,
                      email=email, card_url=card_url)
