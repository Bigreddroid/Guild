"""One bounty: agents hunt in parallel, every claim is verified as it arrives, and the
cheapest verified deal before the deadline wins. Every step is reported through
emit(event) for the page and the stream.
"""
from __future__ import annotations

import queue
import threading
import time
from decimal import Decimal, InvalidOperation
from typing import Callable

from guild.bounty import Claim, Verdict, Want, claim_limit, pick_winner, verify


class OpenHunt:
    """Lets outside agents submit claims into a running hunt (see server.py)."""

    def __init__(self):
        self.inbox: queue.Queue = queue.Queue()
        self.closed = threading.Event()

    def submit(self, card, raw: dict) -> bool:
        if self.closed.is_set():
            return False
        self.inbox.put(("external", card, [raw]))
        return True


def run_hunt(want: Want, hunters: dict, fetch: Callable, emit: Callable, deadline_s: float = 60.0,
             clock: Callable = time.monotonic, sleep: Callable = time.sleep, speed: float = 1.0,
             door: "OpenHunt | None" = None, close_early: bool = True) -> dict:
    """`speed` scales the simulated agents' hunting time (0 = instant, for tests).
    `door` lets outside agents claim while the hunt is open. With close_early the
    hunt ends once Guild's own agents are done; otherwise it stays open to the
    deadline so outside agents get the full window."""
    t0 = clock()
    cards = {h.card.id: h.card for h in hunters.values()}
    emit({"type": "want", "want_id": want.id, "title": want.title, "must_have": list(want.must_have),
          "must_not": list(want.must_not), "max_price": str(want.max_price), "currency": want.currency,
          "ship_to": want.ship_to, "bounty": str(want.bounty), "deadline_s": deadline_s,
          "agents": [{"id": h.card.id, "name": h.card.name, "tool": h.tool, "mode": h.mode,
                      "stars": h.card.stars, "max_claims": claim_limit(h.card.reputation)} for h in hunters.values()]})
    inbox: queue.Queue = door.inbox if door else queue.Queue()

    def work(h):
        try:
            if h.mode == "simulated" and speed:
                sleep(h.eta_s * speed)
            found = h.hunt(want, claim_limit(h.card.reputation))
            inbox.put(("claims", h.card.id, found if isinstance(found, list) else []))
        except Exception as exc:  # an agent crashing just means no claims from it
            inbox.put(("error", h.card.id, f"{type(exc).__name__}"))

    for h in hunters.values():
        threading.Thread(target=work, args=(h,), daemon=True).start()

    verdicts, pending, n, used = [], len(hunters), 0, {}
    while pending or not close_early:
        remaining = deadline_s - (clock() - t0)
        if remaining <= 0:
            break
        try:
            kind, who, payload = inbox.get(timeout=remaining)
        except queue.Empty:
            break
        if kind == "external":
            card = who
            cards.setdefault(card.id, card)
        else:
            pending -= 1
            if kind == "error":
                emit({"type": "agent_error", "agent_id": who, "error": payload})
                continue
            card = hunters[who].card
        agent_id = card.id
        at = round(clock() - t0, 2)
        allowed = max(0, claim_limit(card.reputation) - used.get(agent_id, 0))
        if kind != "external":
            emit({"type": "searched", "agent_id": agent_id, "claims": len(payload), "at": at})
        elif not allowed:
            emit({"type": "claim_refused", "agent_id": agent_id, "agent_name": card.name, "reason": "claim limit for this bounty reached"})
            continue
        for raw in payload[:allowed]:
            used[agent_id] = used.get(agent_id, 0) + 1
            n += 1
            url = str(raw.get("url", ""))[:500] if isinstance(raw, dict) else ""
            try:
                price = Decimal(str(raw.get("price"))).quantize(Decimal("0.01"))
                if not price.is_finite() or price <= 0:
                    raise InvalidOperation
            except (InvalidOperation, ValueError, AttributeError, TypeError):
                price = None
            c = Claim(id=f"{want.id}-c{n}", want_id=want.id, agent_id=agent_id, url=url,
                      price=price or Decimal("0"), currency=want.currency, at=at)
            v = verify(want, c, fetch) if price is not None else Verdict(c, False, ["claimed price is not a number"])
            verdicts.append(v)
            if v.valid:
                card.passes += 1
            else:
                card.fails += 1
            emit({"type": "claim", "claim_id": c.id, "agent_id": agent_id, "agent_name": card.name, "url": url, "claimed": str(c.price),
                  "page_price": str(v.offer.price) if v.offer and v.offer.price else None,
                  "page_name": v.offer.name[:160] if v.offer else None, "valid": v.valid, "reasons": v.reasons,
                  "stars_after": card.stars, "at": at, "outside": kind == "external"})

    if door:
        door.closed.set()
    winner = pick_winner(verdicts, deadline_s)
    if winner:
        card = cards[winner.claim.agent_id]
        emit({"type": "winner", "agent_id": card.id, "agent_name": card.name, "url": winner.claim.url, "price": str(winner.offer.price),
              "page_name": winner.offer.name[:160], "bounty": str(want.bounty), "at": winner.claim.at,
              "saved": str(want.max_price - winner.offer.price)})
        owed = {card.owner_email: want.bounty}
    else:
        emit({"type": "no_winner", "reason": "no claim passed verification before the deadline"})
        owed = {}
    spent = want.bounty if winner else Decimal("0")
    emit({"type": "done", "spent": str(spent), "budget": str(want.bounty),
          "owed": {k: str(v) for k, v in owed.items()}, "claims": len(verdicts),
          "valid": sum(v.valid for v in verdicts)})
    return {"winner": winner, "spent": spent, "budget": want.bounty, "owed": owed, "verdicts": verdicts}
