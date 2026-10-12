"""Moving money for a prepaid wallet, on PayPal (sandbox only).

1. Top up: the owner loads money with a normal PayPal checkout, paid to Guild's own
   sandbox account. The agent can only spend what was loaded.
2. Pay: an approved purchase (or a promotion whose proof checked out) is sent from
   the loaded money to the seller's or creator's own PayPal account with Payouts.
3. Withdraw: whatever is loaded but not spent or reserved goes back to the owner,
   as refunds of their top-ups (newest first). Closing a wallet does this too.

Promotions are held inside the wallet (the money is reserved, not sent) until a
proof checks out; voiding one just releases the reservation.

Without PayPal keys every step is a labelled preview: the same decisions, with
ids that say PREVIEW and no money moving.
"""
from __future__ import annotations

import secrets
import threading
import time
import urllib.parse
from decimal import Decimal
from typing import Optional

from guild.paypal import PayPalClient, PayPalError, approve_link, capture_of
from guild.wallet import APPROVED, HELD, Purchase, Wallet, WalletError

LINK_TTL_S = 30 * 60
SENT = ("SUCCESS", "PENDING", "PROCESSING")


def _err(exc: Exception) -> str:
    if isinstance(exc, PayPalError):
        return f"{exc.name or exc.status}" + (f" (debug id {exc.debug_id})" if exc.debug_id else "")
    if isinstance(exc, WalletError):
        return str(exc)
    return type(exc).__name__


class WalletPay:
    def __init__(self, paypal: Optional[PayPalClient], public_url: str = "", clock=time.monotonic):
        self.paypal, self.public_url, self.clock = paypal, public_url.rstrip("/"), clock
        self._links: dict = {}   # one-time link id -> {"wallet", "amount", "paypal_id", "at"}
        self._lock = threading.Lock()

    @property
    def mode(self) -> str:
        return "sandbox" if self.paypal and self.public_url else "preview"

    # ------------------------------------------------------------ top up

    def start_top_up(self, wallet: Wallet, amount: Decimal) -> dict:
        """Preview: credited at once. Sandbox: the PayPal page where the owner pays."""
        amount = Decimal(amount).quantize(Decimal("0.01"))
        room = wallet.policy.budget - wallet.balance()
        if amount <= 0 or amount > room:
            raise WalletError(f"you can add between 0.01 and {room:.2f}")
        if self.mode == "preview":
            entry = wallet.top_up(amount, order_id=f"PREVIEW-{wallet.id}-{len(wallet.top_ups) + 1}")
            return {"type": "topped_up", "amount": f"{entry['amount']:.2f}", "mode": "preview"}
        link_id = secrets.token_urlsafe(16)
        q = urllib.parse.urlencode({"link": link_id})
        ok = f"{self.public_url}/api/wallets/paypal/topup?{q}"
        cancel = f"{self.public_url}/api/wallets/paypal/cancel?{q}"
        order = self.paypal.create_top_up(amount, wallet.policy.want.currency, f"{wallet.id}-{link_id[:8]}", ok, cancel)
        url = approve_link(order)
        if not order.get("id") or not url:
            raise WalletError("PayPal did not return a checkout link")
        with self._lock:
            now = self.clock()
            self._links = {k: v for k, v in self._links.items() if now - v["at"] < LINK_TTL_S}
            self._links[link_id] = {"wallet": wallet.id, "amount": amount, "paypal_id": order["id"], "at": now}
        return {"type": "top_up_started", "amount": f"{amount:.2f}", "mode": "sandbox", "approve_url": url}

    def finish_top_up(self, link_id: str, token: str, wallets: dict) -> tuple:
        """The owner paid in PayPal: capture it and credit the wallet."""
        with self._lock:
            entry = self._links.pop(link_id, None)   # single use, even if the next step fails
        if entry is None or self.clock() - entry["at"] >= LINK_TTL_S:
            raise WalletError("unknown or expired link")
        if token != entry["paypal_id"]:
            raise WalletError("PayPal order does not match this link")
        wallet = wallets.get(entry["wallet"])
        if wallet is None:
            raise WalletError("wallet not found")
        order = self.paypal.capture_order(entry["paypal_id"])
        cap = capture_of(order)
        if cap is None:
            raise WalletError(f"PayPal order {order.get('status', 'not completed')}")
        try:
            wallet.top_up(entry["amount"], order_id=entry["paypal_id"], capture_id=cap.get("id", ""))
        except WalletError:
            # The money moved but the wallet can't take it (closed, or now over its limit): send it back.
            self.paypal.refund_capture(cap.get("id", ""), entry["amount"], wallet.policy.want.currency,
                                       note="Guild Pay could not add this to your wallet")
            raise WalletError("the wallet could not take this top-up, so it was refunded")
        return wallet, {"type": "topped_up", "amount": f"{entry['amount']:.2f}", "mode": "sandbox",
                        "order_id": entry["paypal_id"]}

    def cancel_link(self, link_id: str) -> None:
        with self._lock:
            self._links.pop(link_id, None)

    # ------------------------------------------------------------ paying out of the wallet

    def _send(self, wallet: Wallet, p: Purchase, note: str) -> tuple:
        """Payout to p.payee. Returns (batch id, error)."""
        if self.mode == "preview":
            return f"PREVIEW-{p.id}", ""
        try:
            out = self.paypal.send_payout(p.payee, p.price, wallet.policy.want.currency, p.id, note)
        except Exception as exc:
            return "", _err(exc)
        header = out.get("batch_header", {})
        if header.get("batch_status") not in SENT:
            return "", f"payout {header.get('batch_status', 'not accepted')}"
        return header.get("payout_batch_id", ""), ""

    def pay(self, wallet: Wallet, p: Purchase) -> dict:
        """Send an approved purchase's price to the seller. Returns a paid or failed event."""
        if p.status != APPROVED:
            raise WalletError(f"purchase is {p.status}, not approved")
        batch, error = self._send(wallet, p, f"Guild Pay: {p.item}, bought by an AI agent for its owner")
        wallet.record_payment(p.id, order_id=batch, error=error)
        return {"type": "failed" if error else "paid", "purchase": p.public(), "mode": self.mode}

    def hold(self, wallet: Wallet, p: Purchase) -> dict:
        """Reserve an approved promotion's amount inside the wallet until its proof arrives."""
        if p.kind != "promotion" or p.status != APPROVED:
            raise WalletError(f"purchase is {p.status}, not an approved promotion")
        wallet.record_hold(p.id, authorization_id=f"HOLD-{p.id}")
        return {"type": "held", "purchase": p.public(), "mode": self.mode}

    def capture(self, wallet: Wallet, p: Purchase, why: str) -> dict:
        """The proof checked out: pay the creator from the held money."""
        if p.status != HELD:
            raise WalletError(f"purchase is {p.status}, not held")
        batch, error = self._send(wallet, p, f"Guild Pay: {p.item}. Your post checked out.")
        if error:
            return {"type": "capture_failed", "purchase": p.public(), "mode": self.mode, "error": error}
        p.order_id = batch
        wallet.record_capture(p.id, "", why)
        return {"type": "paid", "purchase": p.public(), "mode": self.mode, "via": "proof"}

    def void(self, wallet: Wallet, p: Purchase, why: str) -> dict:
        """No proof in time (or cancelled): release the reservation. Nothing was sent."""
        wallet.record_void(p.id, why)
        return {"type": "voided", "purchase": p.public(), "mode": self.mode}

    # ------------------------------------------------------------ giving back what is not spent

    def withdraw(self, wallet: Wallet) -> dict:
        """Return every unspent, unreserved dollar to the owner (refunds of their top-ups)."""
        amount = wallet.returnable()
        if amount <= 0:
            return {"type": "withdrawn", "amount": "0.00", "mode": self.mode}
        if self.mode == "preview":
            wallet.record_withdraw(amount)
            return {"type": "withdrawn", "amount": f"{amount:.2f}", "mode": "preview"}
        left, returned, error = amount, Decimal("0"), ""
        for t in reversed(wallet.top_ups):
            part = min(left, t["amount"] - t["returned"])
            if part <= 0 or not t["capture_id"]:
                continue
            try:
                r = self.paypal.refund_capture(t["capture_id"], part, wallet.policy.want.currency,
                                               note="Unspent money from your Guild Pay wallet",
                                               request_id=f"{t['capture_id']}-return-{t['returned']}")
                if r.get("status") not in ("COMPLETED", "PENDING"):
                    raise WalletError(f"refund {r.get('status')}")
            except Exception as exc:
                error = _err(exc)
                break
            t["returned"] += part
            returned += part
            left -= part
            if left <= 0:
                break
        if returned > 0:
            wallet.record_withdraw(returned)
        event = {"type": "withdraw_failed" if error else "withdrawn", "amount": f"{returned:.2f}", "mode": "sandbox"}
        if error:
            event["error"] = error
        return event
