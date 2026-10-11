"""Paying for a run through PayPal (sandbox only).

1. The buyer approves the run's budget on PayPal: an Orders v2 order with intent
   AUTHORIZE. Money is held, not taken.
2. The market runs.
3. settle(): capture only what passed review (final_capture, so PayPal releases
   the rest of the hold), then pay each agent's owner in one Payouts batch. If
   nothing passed, the authorization is voided.

Without PayPal keys a run still ends with a `payment` event in preview mode, showing
exactly what would be captured, released and paid out.
"""
from __future__ import annotations

import os
import secrets
import threading
import time
import urllib.parse
from decimal import Decimal
from typing import Optional

from guild.payments import build_payout, settle_run
from guild.paypal import PayPalClient, PayPalError, approve_link

SANDBOX_URL = "https://api-m.sandbox.paypal.com"
_SANDBOX_HOSTS = ("api-m.sandbox.paypal.com", "api.sandbox.paypal.com")
CURRENCY = "USD"
FUND_TTL_S = 1800  # an approval link is good for half an hour


def is_paypal_sandbox(url: str) -> bool:
    """True only for an https URL whose host is PayPal's sandbox API. A substring
    check would let `https://api-m.paypal.com/?sandbox` through to live money."""
    parts = urllib.parse.urlsplit(url or "")
    return parts.scheme == "https" and (parts.hostname or "") in _SANDBOX_HOSTS


def paypal_from_env() -> Optional[PayPalClient]:
    cid, secret = os.environ.get("PAYPAL_CLIENT_ID", ""), os.environ.get("PAYPAL_CLIENT_SECRET", "")
    base = os.environ.get("PAYPAL_BASE_URL", SANDBOX_URL)
    if not cid or not secret or not is_paypal_sandbox(base):
        return None  # missing keys, or anything that is not the sandbox: never live money
    return PayPalClient(base, cid, secret)


def public_base_url() -> str:
    """This server's public https address (Render sets RENDER_EXTERNAL_URL)."""
    base = os.environ.get("GUILD_PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or ""
    return base.rstrip("/") if base.startswith("https://") else ""


def mask(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:1]}***@{domain}" if domain else "***"


def _payout_rows(owed: dict) -> list:
    return [{"to": mask(e), "amount": f"{a:.2f}"} for e, a in sorted(owed.items()) if a > 0]


def preview(budget: Decimal, spent: Decimal, owed: dict) -> dict:
    return {"type": "payment", "mode": "preview", "currency": CURRENCY,
            "authorized": f"{budget:.2f}", "captured": f"{spent:.2f}", "released": f"{budget - spent:.2f}",
            "payouts": _payout_rows(owed),
            "note": "No PayPal keys: nothing was charged. With keys, this is what PayPal sandbox does."}


def settle(client: PayPalClient, run_id: str, authorization_id: str, budget: Decimal, spent: Decimal,
           owed: dict) -> dict:
    """Move the money for a finished run and describe what happened as an event."""
    base = {"type": "payment", "mode": "sandbox", "currency": CURRENCY, "authorized": f"{budget:.2f}",
            "authorization_id": authorization_id}
    try:
        out = settle_run(client, run_id, authorization_id, spent, owed, CURRENCY)
    except PayPalError as exc:
        return {**base, "mode": "error", "message": f"PayPal {exc.status} {exc.name}".strip(),
                "debug_id": exc.debug_id}
    except Exception as exc:
        return {**base, "mode": "error", "message": f"settlement failed: {type(exc).__name__}"}
    if out["voided"]:
        return {**base, "captured": "0.00", "released": f"{budget:.2f}", "voided": True, "payouts": []}
    return {**base, "captured": f"{spent:.2f}", "released": f"{budget - spent:.2f}",
            "capture_id": out["capture_id"], "capture_status": out["capture_status"],
            "payout_batch_id": out["payout_batch_id"], "payout_status": out["payout_status"],
            "payouts": _payout_rows(owed)}


class FundingError(Exception):
    pass


class Funds:
    """Pending PayPal approvals, keyed by an unguessable id that is single-use."""

    def __init__(self, client: PayPalClient, clock=time.monotonic):
        self.client, self.clock = client, clock
        self._pending: dict = {}
        self._lock = threading.Lock()

    def start(self, budget: Decimal, public_url: str) -> tuple:
        """Create the AUTHORIZE order; returns (fund_id, approve_url)."""
        fund_id = secrets.token_urlsafe(16)
        base = public_url.rstrip("/")
        order = self.client.create_order(budget, CURRENCY, reference_id=f"guild-{fund_id[:12]}",
                                         return_url=f"{base}/api/paypal/return?fund={fund_id}",
                                         cancel_url=f"{base}/api/paypal/cancel?fund={fund_id}")
        url = approve_link(order)
        if not order.get("id") or not url:
            raise FundingError("PayPal did not return an approval link")
        with self._lock:
            now = self.clock()
            self._pending = {k: v for k, v in self._pending.items() if now - v["at"] < FUND_TTL_S}
            self._pending[fund_id] = {"order_id": order["id"], "budget": budget, "at": now}
        return fund_id, url

    def cancel(self, fund_id: str) -> None:
        with self._lock:
            self._pending.pop(fund_id, None)

    def complete(self, fund_id: str, token: str) -> tuple:
        """The buyer approved: authorize the order. Returns (budget, authorization_id)."""
        with self._lock:
            entry = self._pending.pop(fund_id, None)  # single use, even if authorizing fails
        if entry is None or self.clock() - entry["at"] >= FUND_TTL_S:
            raise FundingError("unknown or expired payment link")
        if token != entry["order_id"]:
            raise FundingError("PayPal order does not match this payment link")
        result = self.client.authorize_order(entry["order_id"])
        try:
            auth = result["purchase_units"][0]["payments"]["authorizations"][0]
        except (KeyError, IndexError, TypeError):
            raise FundingError("PayPal did not return an authorization")
        if auth.get("status") not in ("CREATED", "PENDING"):
            raise FundingError(f"authorization status {auth.get('status')}")
        return entry["budget"], auth["id"]


def owners_from_env() -> dict:
    """GUILD_AGENT_OWNERS="sourcer=a@x.com,researcher=b@x.com,..." maps each agent to the
    PayPal (sandbox) account its earnings go to."""
    out = {}
    for pair in os.environ.get("GUILD_AGENT_OWNERS", "").split(","):
        agent, _, email = pair.strip().partition("=")
        if agent and "@" in email and " " not in email and len(email) <= 254:
            out[agent.strip()] = email.strip()
    return out


__all__ = ["Funds", "FundingError", "build_payout", "is_paypal_sandbox", "mask", "owners_from_env",
           "paypal_from_env", "preview", "settle"]
