"""What happens after Guild verifies a deal: it is kept in the deal history
(Elasticsearch when configured, memory otherwise) and the buyer gets a "deal
found" alert through Zapier. Neither can change who is paid: both run after the
winner is decided, and a failure is reported, never raised.
"""
from __future__ import annotations

import collections
import datetime
import threading
from decimal import Decimal
from typing import Optional

from guild import shops

MAX_FIELD = 200


def _clean(text: str, n: int = MAX_FIELD) -> str:
    return " ".join(str(text).split())[:n]


class DealLog:
    def __init__(self, elastic=None, zapier=None, keep: int = 50, clock=None):
        self.elastic, self.zapier = elastic, zapier
        self._memory: collections.deque = collections.deque(maxlen=keep)
        self._lock = threading.Lock()
        self._clock = clock or (lambda: datetime.datetime.now(datetime.timezone.utc))

    @property
    def store(self) -> str:
        return "Elastic" if self.elastic else "memory"

    def deal(self, hunt_id: str, want, winner, agent_name: str) -> dict:
        offer, price = winner.offer, winner.offer.price
        return {"hunt_id": hunt_id, "found_at": self._clock().isoformat(timespec="seconds"),
                "title": _clean(want.title), "shop": _clean(_shop(winner.claim.url), 80),
                "product": _clean(offer.name), "url": winner.claim.url[:500], "agent": _clean(agent_name, 40),
                "currency": want.currency, "ship_to": want.ship_to, "price": float(price),
                "max_price": float(want.max_price), "saved": float(want.max_price - price),
                "bounty": float(want.bounty), "demo": shops.serve(winner.claim.url) is not None}

    def on_winner(self, hunt_id: str):
        """A run_hunt hook: record the deal and send the alert; returns events for the stream."""
        def hook(want, winner, card) -> list:
            d = self.deal(hunt_id, want, winner, card.name)
            return [self.record(d), self.alert(d)]
        return hook

    def record(self, d: dict) -> dict:
        with self._lock:
            self._memory.appendleft(d)
        if self.elastic is None:
            return {"type": "recorded", "store": "memory", "note": "no Elastic keys"}
        try:
            self.elastic.put_deal(d["hunt_id"], d)
            return {"type": "recorded", "store": "Elastic", "index": "guild-deals"}
        except Exception as exc:
            return {"type": "recorded", "store": "memory", "note": f"Elastic failed ({type(exc).__name__})"}

    def alert(self, d: dict) -> dict:
        if self.zapier is None:
            return {"type": "alert", "via": "Zapier", "sent": False, "note": "no Zapier hook set"}
        money = f"${d['price']:.2f}"
        payload = {"source": "Guild", "event": "deal_found",
                   "subject": _clean(f"Deal found: {d['title']} for {money}", 150),
                   "recipients_text": "the buyer (Guild deal alerts)",
                   "body": (f"Guild verified a deal for \"{d['title']}\": {money} at {d['shop']}, "
                            f"${d['saved']:.2f} under the max. Link: {d['url']} . Found by {d['agent']}; "
                            f"the ${d['bounty']:.2f} bounty goes to its developer through PayPal."),
                   **{k: d[k] for k in ("title", "url", "shop", "price", "saved", "agent", "bounty", "demo")}}
        try:
            request_id = self.zapier.send(payload)
        except Exception as exc:  # throttled or failed: the deal stands, only the alert is skipped
            return {"type": "alert", "via": "Zapier", "sent": False, "note": _clean(str(exc), 120)}
        return {"type": "alert", "via": "Zapier", "sent": True, "zapier_request": request_id}

    def recent(self, size: int = 10) -> dict:
        note = ""
        if self.elastic is not None:
            try:
                return {**self.elastic.recent_deals(size), "store": "Elastic"}
            except Exception as exc:
                note = f"Elastic failed ({type(exc).__name__}); showing this server's memory"
        with self._lock:
            deals = list(self._memory)
        return {"deals": deals[:size], "count": len(deals), "saved": round(sum(d["saved"] for d in deals), 2),
                "store": "memory", **({"note": note} if note else {})}


def _shop(url: str) -> str:
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host == shops.HOST:
        return parts.path.strip("/").split("/")[0] + " (demo)"
    return host[4:] if host.startswith("www.") else host
