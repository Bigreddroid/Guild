"""Moving the owner's money for a wallet, on PayPal (sandbox only).

1. Mandate: the owner approves once in PayPal (Vault v3 setup token), and Guild keeps
   only the resulting payment token. The owner can revoke it at any time.
2. Pay: an approved purchase is a PayPal order whose payee is the seller's own
   account (from its agent-pay card): either charged to the vaulted mandate, or, if
   there is no mandate, approved by the owner in PayPal checkout.
3. Refund: a paid purchase can be refunded to the owner in full.

Without PayPal keys every step is a labelled preview: the same decisions, with
order ids that say PREVIEW and no money moving.
"""
from __future__ import annotations

import secrets
import threading
import time
import urllib.parse
from typing import Optional

from guild.paypal import PayPalClient, PayPalError, approve_link, capture_of
from guild.wallet import APPROVED, PAID, Purchase, Wallet, WalletError

LINK_TTL_S = 30 * 60


def _err(exc: Exception) -> str:
    if isinstance(exc, PayPalError):
        return f"{exc.name or exc.status}" + (f" (debug id {exc.debug_id})" if exc.debug_id else "")
    return type(exc).__name__


class WalletPay:
    def __init__(self, paypal: Optional[PayPalClient], public_url: str = "", clock=time.monotonic):
        self.paypal, self.public_url, self.clock = paypal, public_url.rstrip("/"), clock
        self._links: dict = {}   # one-time link id -> {"kind", "wallet", "purchase", "paypal_id", "at"}
        self._lock = threading.Lock()

    @property
    def mode(self) -> str:
        return "sandbox" if self.paypal and self.public_url else "preview"

    # ------------------------------------------------------------ one-time return links

    def _link(self, **entry) -> str:
        link_id = secrets.token_urlsafe(16)
        with self._lock:
            now = self.clock()
            self._links = {k: v for k, v in self._links.items() if now - v["at"] < LINK_TTL_S}
            self._links[link_id] = {**entry, "at": now}
        return link_id

    def _take(self, link_id: str, kind: str) -> dict:
        with self._lock:
            entry = self._links.pop(link_id, None)   # single use, even if the next step fails
        if entry is None or entry["kind"] != kind or self.clock() - entry["at"] >= LINK_TTL_S:
            raise WalletError("unknown or expired link")
        return entry

    def _urls(self, link_id: str, what: str) -> tuple:
        q = urllib.parse.urlencode({"link": link_id})
        return f"{self.public_url}/api/wallets/paypal/{what}?{q}", f"{self.public_url}/api/wallets/paypal/cancel?{q}"

    # ------------------------------------------------------------ mandate

    def start_mandate(self, wallet: Wallet) -> Optional[str]:
        """The PayPal page where the owner approves the mandate (None in preview)."""
        if self.mode == "preview":
            return None
        link = self._link(kind="mandate", wallet=wallet.id, purchase="", paypal_id="")
        ok, cancel = self._urls(link, "mandate")
        token = self.paypal.create_setup_token(ok, cancel, request_id=f"{wallet.id}-{link[:8]}-setup")
        with self._lock:
            self._links[link]["paypal_id"] = token.get("id", "")
        url = approve_link(token)
        if not token.get("id") or not url:
            raise WalletError("PayPal did not return an approval link for the mandate")
        return url

    def finish_mandate(self, link_id: str, wallets: dict) -> Wallet:
        entry = self._take(link_id, "mandate")
        wallet = wallets.get(entry["wallet"])
        if wallet is None:
            raise WalletError("wallet not found")
        token = self.paypal.create_payment_token(entry["paypal_id"])
        if not token.get("id"):
            raise WalletError("PayPal did not return a payment token")
        wallet.mandate_id = token["id"]
        return wallet

    def revoke(self, wallet: Wallet) -> dict:
        if wallet.mandate_id and self.mode == "sandbox":
            try:
                self.paypal.delete_payment_token(wallet.mandate_id)
            except PayPalError as exc:
                if exc.status != 404:
                    return {"type": "mandate", "revoked": False, "error": _err(exc)}
        wallet.mandate_id = ""
        return {"type": "mandate", "revoked": True}

    # ------------------------------------------------------------ paying

    def _describe(self, wallet: Wallet, p: Purchase) -> tuple:
        return wallet.policy.want.currency, f"Guild Pay: {p.item} (agent purchase)"

    def pay(self, wallet: Wallet, p: Purchase) -> dict:
        """Pay an approved purchase. Returns an event: paid, failed, or needs the owner in PayPal."""
        if p.status != APPROVED:
            raise WalletError(f"purchase is {p.status}, not approved")
        currency, description = self._describe(wallet, p)
        if self.mode == "preview":
            wallet.record_payment(p.id, order_id=f"PREVIEW-{p.id}", capture_id=f"PREVIEW-CAP-{p.id}")
            return {"type": "paid", "purchase": p.public(), "mode": "preview"}
        if wallet.mandate_id:
            try:
                order = self.paypal.pay_with_vault(p.price, currency, p.payee, wallet.mandate_id, p.id, description)
            except Exception as exc:
                wallet.record_payment(p.id, error=_err(exc))
                return {"type": "failed", "purchase": p.public(), "mode": "sandbox"}
            cap = capture_of(order)
            if cap is None:
                wallet.record_payment(p.id, error=f"order {order.get('status', 'not completed')}")
                return {"type": "failed", "purchase": p.public(), "mode": "sandbox"}
            wallet.record_payment(p.id, order_id=order.get("id", ""), capture_id=cap.get("id", ""))
            return {"type": "paid", "purchase": p.public(), "mode": "sandbox", "via": "mandate"}
        # No mandate: the owner pays this one in PayPal checkout, straight to the seller.
        link = self._link(kind="checkout", wallet=wallet.id, purchase=p.id, paypal_id="")
        ok, cancel = self._urls(link, "checkout")
        try:
            order = self.paypal.checkout_for_seller(p.price, currency, p.payee, p.id, description, ok, cancel)
        except Exception as exc:
            wallet.record_payment(p.id, error=_err(exc))
            return {"type": "failed", "purchase": p.public(), "mode": "sandbox"}
        with self._lock:
            self._links[link]["paypal_id"] = order.get("id", "")
        return {"type": "checkout", "purchase": p.public(), "mode": "sandbox", "approve_url": approve_link(order)}

    def finish_checkout(self, link_id: str, token: str, wallets: dict) -> tuple:
        """The owner approved in PayPal: capture the order to the seller."""
        entry = self._take(link_id, "checkout")
        if token != entry["paypal_id"]:
            raise WalletError("PayPal order does not match this link")
        wallet = wallets.get(entry["wallet"])
        if wallet is None:
            raise WalletError("wallet not found")
        p = wallet._get(entry["purchase"])
        try:
            order = self.paypal.capture_order(entry["paypal_id"])
            cap = capture_of(order)
            if cap is None:
                raise WalletError(f"order {order.get('status', 'not completed')}")
        except Exception as exc:
            wallet.record_payment(p.id, error=_err(exc))
            return wallet, {"type": "failed", "purchase": p.public(), "mode": "sandbox"}
        wallet.record_payment(p.id, order_id=entry["paypal_id"], capture_id=cap.get("id", ""))
        return wallet, {"type": "paid", "purchase": p.public(), "mode": "sandbox", "via": "checkout"}

    def cancel_link(self, link_id: str) -> None:
        with self._lock:
            self._links.pop(link_id, None)

    def refund(self, wallet: Wallet, p: Purchase) -> dict:
        if p.status != PAID:
            raise WalletError(f"purchase is {p.status}, not paid")
        if self.mode == "sandbox":
            try:
                result = self.paypal.refund_capture(p.capture_id, p.price, wallet.policy.want.currency,
                                                    note="Refunded by Guild Pay at the owner's request")
            except Exception as exc:
                return {"type": "refund_failed", "purchase": p.public(), "error": _err(exc)}
            if result.get("status") not in ("COMPLETED", "PENDING"):
                return {"type": "refund_failed", "purchase": p.public(), "error": f"refund {result.get('status')}"}
        wallet.record_refund(p.id)
        return {"type": "refunded", "purchase": p.public(), "mode": self.mode}
