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


def run_hunt(want: Want, hunters: dict, fetch: Callable, emit: Callable, deadline_s: float = 60.0,
             clock: Callable = time.monotonic, sleep: Callable = time.sleep, speed: float = 1.0) -> dict:
    """`speed` scales the simulated agents' hunting time (0 = instant, for tests)."""
    t0 = clock()
    emit({"type": "want", "want_id": want.id, "title": want.title, "must_have": list(want.must_have),
          "must_not": list(want.must_not), "max_price": str(want.max_price), "currency": want.currency,
          "ship_to": want.ship_to, "bounty": str(want.bounty), "deadline_s": deadline_s,
          "agents": [{"id": h.card.id, "name": h.card.name, "tool": h.tool, "mode": h.mode,
                      "stars": h.card.stars, "max_claims": claim_limit(h.card.reputation)} for h in hunters.values()]})
    inbox: queue.Queue = queue.Queue()

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

    verdicts, pending, n = [], len(hunters), 0
    while pending:
        remaining = deadline_s - (clock() - t0)
        if remaining <= 0:
            break
        try:
            kind, agent_id, payload = inbox.get(timeout=remaining)
        except queue.Empty:
            break
        pending -= 1
        if kind == "error":
            emit({"type": "agent_error", "agent_id": agent_id, "error": payload})
            continue
        card = hunters[agent_id].card
        at = round(clock() - t0, 2)
        emit({"type": "searched", "agent_id": agent_id, "claims": len(payload), "at": at})
        for raw in payload[:claim_limit(card.reputation)]:
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
            emit({"type": "claim", "claim_id": c.id, "agent_id": agent_id, "url": url, "claimed": str(c.price),
                  "page_price": str(v.offer.price) if v.offer and v.offer.price else None,
                  "page_name": v.offer.name[:160] if v.offer else None, "valid": v.valid, "reasons": v.reasons,
                  "stars_after": card.stars, "at": at})

    winner = pick_winner(verdicts, deadline_s)
    if winner:
        card = hunters[winner.claim.agent_id].card
        emit({"type": "winner", "agent_id": card.id, "url": winner.claim.url, "price": str(winner.offer.price),
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
