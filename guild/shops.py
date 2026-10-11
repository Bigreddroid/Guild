"""Guild's labelled demo shops: the deterministic stage for the demo when agents run
without keys. Each page publishes schema.org Product/Offer JSON-LD exactly like a
real Shopify store, and the verifier reads it the same way it reads a real shop.

The catalogue is built to exercise every rule: one real winner, a cheaper one that
is out of stock, a refurbished one, one that only ships to the UK, and a price that
changed after a catalogue was indexed (so a careless agent misreports it).
"""
from __future__ import annotations

import html
import json
import urllib.parse
from decimal import Decimal
from typing import Optional

from guild.bounty import Want

HOST = "demo-shops.guild.example"   # never resolves; served in-process only
BASE = f"https://{HOST}"

DEMO_WANT = Want(
    id="want-xm5", title="Sony WH-1000XM5 headphones, black, new, under $330, ship to US",
    must_have=("Sony", "WH-1000XM5"), must_not=("refurbished", "renewed", "used", "case"),
    max_price=Decimal("330"), currency="USD", ship_to="US", allow_unknown_shipping=True,
    bounty=Decimal("2.00"))

# slug -> product facts. "listed" is what a stale catalogue still shows.
PRODUCTS = {
    "harbor-audio/sony-wh-1000xm5-black": dict(shop="Harbor Audio", name="Sony WH-1000XM5 Wireless Headphones, Black",
                                               price="299.00", listed="299.00", stock="InStock", ships=["US", "CA"]),
    "northline/sony-wh1000xm5": dict(shop="Northline Electronics", name="Sony WH-1000XM5 Noise Cancelling Headphones",
                                     price="279.00", listed="279.00", stock="OutOfStock", ships=["US"]),
    "loop-renew/sony-wh-1000xm5-refurb": dict(shop="Loop Renew", name="Sony WH-1000XM5 Headphones (Refurbished)",
                                              price="219.00", listed="219.00", stock="InStock", ships=["US"]),
    "albion-sound/sony-wh-1000xm5": dict(shop="Albion Sound", name="Sony WH-1000XM5 Headphones, Black",
                                         price="289.00", listed="289.00", stock="InStock", ships=["GB"]),
    "metro-tech/sony-wh-1000xm5-midnight": dict(shop="Metro Tech", name="Sony WH-1000XM5 Headphones, Midnight Black",
                                                price="318.00", listed="309.00", stock="InStock", ships=["US"]),
}


def url_for(slug: str) -> str:
    return f"{BASE}/{slug}"


def catalogue() -> list:
    """What a product search index returns for the demo want (with stale 'listed' prices)."""
    return [{"title": p["name"], "price": p["listed"], "url": url_for(slug), "shop": p["shop"]}
            for slug, p in PRODUCTS.items()]


def page(slug: str) -> Optional[str]:
    p = PRODUCTS.get(slug)
    if p is None:
        return None
    offer = {"@type": "Offer", "price": p["price"], "priceCurrency": "USD",
             "availability": f"https://schema.org/{p['stock']}", "url": url_for(slug),
             "shippingDetails": {"@type": "OfferShippingDetails", "shippingDestination": [
                 {"@type": "DefinedRegion", "addressCountry": c} for c in p["ships"]]}}
    doc = {"@context": "https://schema.org", "@type": "Product", "name": p["name"],
           "brand": {"@type": "Brand", "name": "Sony"}, "offers": offer}
    name = html.escape(p["name"])
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{name} | {html.escape(p['shop'])}</title>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<script type='application/ld+json'>{json.dumps(doc)}</script></head>"
            f"<body style='font-family:system-ui;max-width:640px;margin:24px auto;padding:0 16px'>"
            f"<p style='background:#222;color:#fff;padding:6px 10px'>Demo shop for Guild. Not a real store.</p>"
            f"<h1>{name}</h1><p><b>${p['price']}</b> · {'In stock' if p['stock'] == 'InStock' else 'Out of stock'}"
            f" · Ships to {', '.join(p['ships'])}</p><p>Sold by {html.escape(p['shop'])}</p></body></html>")


def serve(url: str) -> Optional[tuple]:
    """In-process fetch for demo shop URLs; None for any other URL."""
    parts = urllib.parse.urlsplit(url)
    if (parts.hostname or "").lower() != HOST:
        return None
    body = page(parts.path.strip("/"))
    return (200, body) if body else (404, "<html><body>Not found</body></html>")
