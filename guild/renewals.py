"""Subscriptions an AI keeps for its owner: tools it pays for again every period.

A buyer's existing PayPal subscriptions can't be listed or changed over PayPal's REST
APIs, so Guild runs renewals itself, from the prepaid wallet. Each period the tool's page
is checked again exactly like a first purchase (real seller, right item, price still
the same) and paid to the seller from the loaded money. Nothing renews by itself into a
higher price, an unlisted seller or an empty wallet: that charge is blocked and the
renewal pauses, with the reason.

    waiting    the AI started it; the first charge waits for the owner's OK
    active     paid for this period; charged again at next_at
    paused     a charge was blocked or failed; the owner resumes or cancels
    cancelled  never charged again

The AI reports when it actually uses a tool. One unused for two whole periods is
flagged, so the owner can cancel what nobody needs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

WAITING, ACTIVE, PAUSED, CANCELLED = "waiting", "active", "paused", "cancelled"
MIN_EVERY_S, MAX_EVERY_S = 60, 31 * 24 * 3600
MAX_RENEWALS = 10
UNUSED_PERIODS = 2


class RenewalError(ValueError):
    pass


@dataclass
class Renewal:
    id: str
    tool: str
    url: str
    price: Decimal
    every_s: float
    must_have: tuple
    category: str
    booked_by: str
    started_at: float
    status: str = WAITING
    next_at: float = 0.0
    last_used_at: Optional[float] = None
    reason: str = ""
    charges: list = field(default_factory=list)   # purchase ids, one per period

    def unused(self, now: float) -> bool:
        """No reported use for UNUSED_PERIODS whole periods while it was being paid for."""
        since = self.last_used_at if self.last_used_at is not None else self.started_at
        return self.status in (ACTIVE, PAUSED) and now - since >= UNUSED_PERIODS * self.every_s

    def due(self, now: float) -> bool:
        return self.status == ACTIVE and now >= self.next_at

    def public(self, now: float) -> dict:
        return {"id": self.id, "tool": self.tool, "url": self.url, "price": f"{self.price:.2f}",
                "every_minutes": round(self.every_s / 60, 2), "category": self.category, "booked_by": self.booked_by,
                "status": self.status, "reason": self.reason, "charges": len(self.charges),
                "next_in_s": max(0, round(self.next_at - now)) if self.status == ACTIVE else None,
                "last_used_s_ago": None if self.last_used_at is None else round(now - self.last_used_at),
                "unused": self.unused(now)}


def parse(body: dict) -> dict:
    """API body -> the fields for a Renewal. Raises RenewalError with a message for people."""
    url = body.get("url")
    if not isinstance(url, str) or not url.startswith("https://") or len(url) > 500:
        raise RenewalError("url must be the tool's https page (max 500 chars)")
    tool = " ".join(str(body.get("item") or "").split())[:80]
    if not tool:
        raise RenewalError("item must name the tool, e.g. ScribeAI Pro")
    words = body.get("must_have") or tool.split()
    if isinstance(words, str):
        words = [w.strip() for w in words.split(",")]
    if not isinstance(words, (list, tuple)) or not 1 <= len(words) <= 6:
        raise RenewalError("must_have: 1 to 6 words the tool's name must contain")
    try:
        price = Decimal(str(body.get("price")).strip()).quantize(Decimal("0.01"))
    except Exception:  # noqa: BLE001
        raise RenewalError("price must be an amount")
    if not price.is_finite() or price <= 0:
        raise RenewalError("price must be more than 0")
    try:
        every_s = float(body.get("every_minutes", 30 * 24 * 60)) * 60
    except (TypeError, ValueError):
        raise RenewalError("every_minutes must be a number")
    if not MIN_EVERY_S <= every_s <= MAX_EVERY_S:
        raise RenewalError("every_minutes must be between 1 and 44640 (31 days)")
    category = body.get("category") or "tools"
    if not isinstance(category, str) or len(category) > 24:
        raise RenewalError("category must be one of the wallet's spending limits")
    return {"tool": tool, "url": url, "price": price, "every_s": every_s, "category": category,
            "must_have": tuple(str(w).strip()[:40] for w in words if str(w).strip())}
