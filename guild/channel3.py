"""Channel3 product search, used by the Sourcer agent.

Matches the official SDK (channel3-sdk 4.1.0): POST {base}/v1/search with an
`x-api-key` header and body {query, limit, filters, config}. Each product has
offers (url, domain, price {price, currency}, availability); we keep the
cheapest in-stock offer in the requested currency.

Product titles and URLs come from third-party shops, so they are trimmed and
only https links are kept; the reviewer treats all agent output as data anyway.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Optional

import httpx

BASE_URL = "https://api.trychannel3.com"
MAX_LIMIT = 30  # API maximum


class Channel3Error(Exception):
    pass


class Channel3:
    def __init__(self, api_key: str, base_url: str = BASE_URL, timeout_s: float = 15.0,
                 transport: Optional[httpx.BaseTransport] = None):
        if not api_key:
            raise ValueError("Channel3 needs an API key")
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport,
                                  headers={"x-api-key": api_key, "content-type": "application/json"})

    def search(self, query: str, limit: int = 10, max_price: Optional[float] = None,
               currency: str = "USD") -> list:
        """Cheapest in-stock offer per matching product, cheapest first."""
        filters: dict = {"availability": ["InStock"]}
        if max_price is not None:
            filters["price"] = {"max_price": float(max_price)}
        body = {"query": query[:300], "limit": max(1, min(int(limit), MAX_LIMIT)), "filters": filters,
                "config": {"currency": currency, "country": "US"}}
        try:
            r = self._http.post("/v1/search", json=body)
        except httpx.HTTPError as exc:
            raise Channel3Error(f"network: {type(exc).__name__}") from exc
        if r.status_code != 200:
            raise Channel3Error(f"HTTP {r.status_code}")
        try:
            products = r.json().get("products") or []
        except ValueError as exc:
            raise Channel3Error("response was not JSON") from exc
        found = [o for p in products if isinstance(p, dict) and (o := _best_offer(p, currency))]
        return sorted(found, key=lambda o: (Decimal(o["price"]), o["title"]))

    def close(self) -> None:
        self._http.close()


def _best_offer(product: dict, currency: str) -> Optional[dict]:
    best = None
    for offer in product.get("offers") or []:
        if not isinstance(offer, dict) or offer.get("availability") != "InStock":
            continue
        url, price = offer.get("url"), offer.get("price") or {}
        if not isinstance(url, str) or not url.startswith("https://") or len(url) > 500:
            continue
        if price.get("currency") != currency:
            continue
        try:
            amount = Decimal(str(price.get("price"))).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            continue
        if not amount.is_finite() or amount <= 0:
            continue
        if best is None or amount < best[0]:
            best = (amount, url, offer.get("domain"))
    if best is None:
        return None
    brands = product.get("brands") or []
    brand = brands[0].get("name") if brands and isinstance(brands[0], dict) else None
    return {"title": str(product.get("title", ""))[:200], "price": str(best[0]), "url": best[1],
            "shop": str(best[2] or "")[:100], "brand": brand, "channel3_id": str(product.get("id", ""))[:100]}
