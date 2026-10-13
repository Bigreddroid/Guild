"""The budget: what a wallet's money did, what to watch, and what to change.

Everything here is plain arithmetic over the wallet's own ledger, so every number can
be checked by hand:

    numbers     loaded, spent, available, by spending limit, what was stopped
    pace        dollars a minute since the wallet opened, and how long the rest lasts
                (only after 10 minutes and 2 payments: before that it is noise)
    watch       spending limits nearly used up, repeated blocked attempts (and the
                check that failed most), a payment far above the usual, a creator
                booking about to run out of time
    proposals   moving room between spending limits, never above the wallet's limit;
                the owner applies them with one tap

A short plain-English summary is written from these numbers: by Claude when the server
has an Anthropic key (it sees only the numbers, never page text or emails), otherwise
by a fixed template. Either way the summary says which wrote it.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from decimal import Decimal
from typing import Optional

from guild.wallet import BLOCKED, HELD, PAID, PENDING, Wallet

NEAR_FULL = Decimal("0.8")
BLOCKED_ALERT = 3
SPIKE = Decimal("2")
DEADLINE_WARN_S = 60
PACE_MIN_S, PACE_MIN_PAYMENTS = 600, 2   # below this a pace is noise: don't forecast
MODEL = os.environ.get("GUILD_BUDGET_MODEL", "claude-opus-5-5")
CHECK_WORDS = {
    "seller": "the seller isn't listed anywhere independent", "name": "it wasn't the item you asked for",
    "claimed_price": "the price didn't match the page", "sanity": "the price was implausible for the item",
    "max_price": "it was over your max price", "stock": "it was out of stock", "shipping": "it doesn't ship to you",
    "currency": "the currency was wrong",
}


def _usd(d) -> str:
    return f"${Decimal(d):,.2f}"


def _round5(d: Decimal) -> Decimal:
    return (d / 5).to_integral_value(rounding="ROUND_FLOOR") * 5


def analyze(wallet: Wallet, elapsed_s: float) -> dict:
    with wallet._lock:
        ps = list(wallet.purchases)
        paid = [p for p in ps if p.status == PAID]
        blocked = [p for p in ps if p.status == BLOCKED]
        spent, available, funded = wallet.spent(), wallet.remaining(), wallet.funded
        limits = []
        for name, env in wallet.policy.envelopes.items():
            used = wallet.spent(name) + wallet.reserved(name)
            limits.append({"name": name, "limit": env.limit, "spent": wallet.spent(name), "used": used,
                           "free": env.limit - used, "used_pct": float(used / env.limit) if env.limit else 0.0})

    minutes = max(elapsed_s / 60, 0.5)
    per_min = spent / Decimal(str(round(minutes, 2))) if spent else Decimal("0")
    pace_known = elapsed_s >= PACE_MIN_S and len(paid) >= PACE_MIN_PAYMENTS
    lasts_min = float(available / per_min) if pace_known and per_min > 0 and available > 0 else None
    avg_paid = (sum((p.price for p in paid), Decimal("0")) / len(paid)) if paid else Decimal("0")

    watch = []
    for l in limits:
        if l["limit"] > 0 and Decimal(str(l["used_pct"])) >= NEAR_FULL:
            watch.append({"level": "warn", "key": f"limit:{l['name']}",
                          "text": f"Your {l['name']} limit is {round(l['used_pct'] * 100)}% used "
                                  f"({_usd(l['used'])} of {_usd(l['limit'])})."})
    if len(blocked) >= BLOCKED_ALERT:
        failed = Counter(k for p in blocked for k, ok in p.checks.items() if not ok).most_common(2)
        # Name a reason only when one clearly leads; a tie would single one out unfairly.
        top = failed[0][0] if failed and failed[0][1] > 1 and (len(failed) == 1 or failed[0][1] > failed[1][1]) else ""
        why = f" Most often, {CHECK_WORDS.get(top, top)}." if top else ""
        watch.append({"level": "alert", "key": "blocked",
                      "text": f"Your AI tried {len(blocked)} purchases that failed Guild's checks "
                              f"({_usd(sum((p.price for p in blocked), Decimal('0')))}). Nothing was paid.{why}"})
    for p in paid:
        others = [q.price for q in paid if q is not p]
        if len(others) >= 2:
            usual = sum(others, Decimal("0")) / len(others)
            if p.price > SPIKE * usual:
                watch.append({"level": "warn", "key": f"spike:{p.id}",
                              "text": f"{p.item} ({_usd(p.price)}) cost more than twice your usual payment "
                                      f"({_usd(usual)})."})
    for p in ps:
        if p.status == HELD and p.kind == "promotion":
            left = p.extra.get("deadline_at", 0) - elapsed_s
            if 0 < left <= DEADLINE_WARN_S:
                watch.append({"level": "warn", "key": f"deadline:{p.id}",
                              "text": f"{p.item}: about {int(left)}s left for a good proof, then the "
                                      f"{_usd(p.price)} is released."})

    proposals = []
    full = [l for l in limits if l["limit"] > 0 and Decimal(str(l["used_pct"])) >= NEAR_FULL]
    for l in full:
        donors = sorted((d for d in limits if d is not l and d["free"] >= 10), key=lambda d: -d["free"])
        if not donors:
            continue
        d = donors[0]
        move = _round5(min(d["free"] / 2, max(l["limit"] / 2, Decimal("10"))))
        if move < 5:
            continue
        proposals.append({
            "id": f"move:{d['name']}:{l['name']}:{move}",
            "text": f"Move {_usd(move)} of unused room from {d['name']} to {l['name']}.",
            "why": f"{l['name']} is {round(l['used_pct'] * 100)}% used; {d['name']} has {_usd(d['free'])} unused.",
            "limits": {l["name"]: f"{l['limit'] + move:.2f}", d["name"]: f"{d['limit'] - move:.2f}"}})

    by_cat = sorted(({"name": l["name"], "spent": f"{l['spent']:.2f}", "limit": f"{l['limit']:.2f}",
                      "used_pct": round(l["used_pct"] * 100)} for l in limits), key=lambda c: -Decimal(c["spent"]))
    return {
        "numbers": {"loaded": f"{funded:.2f}", "spent": f"{spent:.2f}", "available": f"{available:.2f}",
                    "paid_count": len(paid), "average_payment": f"{avg_paid:.2f}",
                    "blocked_count": len(blocked),
                    "blocked_total": f"{sum((p.price for p in blocked), Decimal('0')):.2f}",
                    "waiting_count": sum(p.status == PENDING for p in ps),
                    "on_hold": f"{wallet.reserved():.2f}", "by_limit": by_cat},
        "pace": {"minutes_open": round(elapsed_s / 60, 1), "per_minute": f"{per_min:.2f}", "known": pace_known,
                 "lasts_minutes": None if lasts_min is None else round(lasts_min, 1)},
        "watch": watch, "proposals": proposals,
    }


def template_summary(a: dict) -> str:
    n, pace = a["numbers"], a["pace"]
    parts = []
    if Decimal(n["spent"]) > 0:
        spent_on = ", ".join(f"{c['name']} {_usd(c['spent'])}" for c in n["by_limit"] if Decimal(c["spent"]) > 0)
        parts.append(f"Your AI spent {_usd(n['spent'])} of the {_usd(n['loaded'])} you loaded ({spent_on}).")
    elif Decimal(n["loaded"]) > 0:
        parts.append(f"You loaded {_usd(n['loaded'])}; nothing has been paid yet.")
    else:
        parts.append("The wallet is empty: add money to let your AI start.")
    if n["blocked_count"]:
        parts.append(f"Guild stopped {n['blocked_count']} bad purchase attempt"
                     f"{'s' if n['blocked_count'] != 1 else ''} worth {_usd(n['blocked_total'])} before any money moved.")
    if Decimal(n["available"]) > 0:
        tail = (f" At this pace that lasts about {pace['lasts_minutes']:g} more minutes."
                if pace["lasts_minutes"] is not None else "")
        parts.append(f"{_usd(n['available'])} is still available.{tail}")
    return " ".join(parts)


def summarize(a: dict, client=None, model: str = MODEL) -> dict:
    """A 2-3 sentence summary. Claude when a client is given, else the template."""
    if client is None:
        return {"text": template_summary(a), "by": "Guild (rule-based)"}
    try:
        response = client.beta.messages.create(
            model=model, max_tokens=400, output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            system=("You explain an AI agent's prepaid PayPal wallet to its owner. Use only the numbers given; "
                    "never invent amounts. Two or three short sentences, plain words, US dollars. "
                    "Mention anything under 'watch' first if there is one."),
            messages=[{"role": "user", "content": json.dumps(a)}])
        if getattr(response, "stop_reason", "") == "refusal":
            raise ValueError("refused")
        text = " ".join(b.text for b in response.content if getattr(b, "type", "") == "text").strip()
        if not text:
            raise ValueError("empty")
        return {"text": text[:600], "by": "Claude"}
    except Exception:  # noqa: BLE001 - the numbers stand on their own; fall back quietly
        return {"text": template_summary(a), "by": "Guild (rule-based)"}


def client_from_env():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    return anthropic.Anthropic()
