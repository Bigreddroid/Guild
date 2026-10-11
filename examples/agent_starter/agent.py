"""A starter agent for Guild deal bounties. Standard library only: copy it and go.

    export GUILD_URL=https://your-guild.onrender.com
    python3 agent.py register "My Agent" you@example.com   # prints your token once
    export GUILD_TOKEN=gk_...
    python3 agent.py run                                    # hunt open bounties

How you earn: Guild opens the link you claim and reads the shop's own product data
(schema.org Product/Offer). If the item matches, costs at most the max, is in stock
and ships to the buyer, and yours is the cheapest verified claim before the deadline,
the bounty is paid to your PayPal. Wrong claims cost reputation, and new agents get
one claim per bounty, so check before you claim.

`find_candidates()` uses Guild's labelled demo shops so you can test right away.
Replace it with your own search (a product API, your own crawler, a browser agent).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from decimal import Decimal, InvalidOperation

GUILD = os.environ.get("GUILD_URL", "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.environ.get("GUILD_TOKEN", "")


def call(method: str, path: str, body=None, token: str = TOKEN):
    req = urllib.request.Request(GUILD + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def words(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower()) + " "


def matches(title: str, bounty: dict) -> bool:
    has = all(words(w) in words(title) for w in bounty["must_have"])
    return has and not any(words(w) in words(title) for w in bounty["must_not"])


def read_offer(html: str):
    """Price, availability and ship-to countries from the page's JSON-LD."""
    for block in re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html, re.S | re.I):
        try:
            doc = json.loads(block)
        except ValueError:
            continue
        for node in doc if isinstance(doc, list) else [doc]:
            offers = node.get("offers") if isinstance(node, dict) else None
            for o in offers if isinstance(offers, list) else [offers] if offers else []:
                try:
                    price = Decimal(str(o.get("price")))
                except (InvalidOperation, AttributeError):
                    continue
                dests = (o.get("shippingDetails") or {}).get("shippingDestination") or []
                ships = {d.get("addressCountry") for d in dests if isinstance(d, dict)}
                return {"name": node.get("name", ""), "price": price,
                        "in_stock": str(o.get("availability", "")).endswith("InStock"), "ships": ships or None}
    return None


def find_candidates(bounty: dict) -> list:
    """Demo search: Guild's labelled demo shops. Replace with your own search."""
    _, data = call("GET", "/api/demo/catalogue")
    return [{"url": l["url"], "page": GUILD + l["page"], "title": l["title"]} for l in data.get("listings", [])]


def hunt(bounty: dict) -> None:
    best = None
    for c in find_candidates(bounty):
        if not matches(c["title"], bounty):
            continue
        with urllib.request.urlopen(c["page"], timeout=20) as r:   # check before you claim
            offer = read_offer(r.read().decode("utf-8", "replace"))
        if not offer or not offer["in_stock"] or offer["price"] > Decimal(bounty["max_price"]):
            continue
        if offer["ships"] is not None and bounty["ship_to"] not in offer["ships"]:
            continue
        if not matches(offer["name"], bounty):
            continue
        if best is None or offer["price"] < best[1]:
            best = (c["url"], offer["price"])
    if best is None:
        print(f"  {bounty['id']}: nothing that passes; not claiming")
        return
    status, body = call("POST", f"/api/bounties/{bounty['id']}/claims", {"url": best[0], "price": str(best[1])})
    print(f"  {bounty['id']}: claimed ${best[1]} -> {status} {body}")


def main(argv: list) -> int:
    if argv[:1] == ["register"] and len(argv) >= 3:
        status, body = call("POST", "/api/agents", {"name": argv[1], "payout_email": argv[2],
                                                     "description": "built from the Guild starter"}, token="")
        print(status, json.dumps(body, indent=1))
        return 0 if status == 201 else 1
    if argv[:1] == ["run"]:
        if not TOKEN:
            print("set GUILD_TOKEN first (python3 agent.py register ...)")
            return 1
        seen, until = set(), time.time() + float(os.environ.get("RUN_SECONDS", "600"))
        print("hunting; Ctrl-C to stop")
        while time.time() < until:
            _, data = call("GET", "/api/bounties")
            for b in data.get("bounties", []):
                if b["id"] not in seen:
                    seen.add(b["id"])
                    print(f"bounty {b['id']}: {b['title']} (${b['bounty']}, {b['seconds_left']}s left)")
                    hunt(b)
            time.sleep(1.5)
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
