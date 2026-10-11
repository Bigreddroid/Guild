"""PayPal sandbox client for the AUTHORIZE -> capture/void flow.

Endpoints (Orders v2 / Payments v2):
  POST /v1/oauth2/token
  POST /v2/checkout/orders                       (intent AUTHORIZE)
  POST /v2/checkout/orders/{id}/authorize        (after the buyer approves)
  POST /v2/payments/authorizations/{id}/capture
  POST /v2/payments/authorizations/{id}/void
  POST /v1/payments/payouts                      (pay agent owners)
  GET  /v1/payments/payouts/{batch_id}
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
import uuid
from decimal import Decimal
from typing import Callable, Optional

# Refresh this many seconds before PayPal's stated expiry.
_TOKEN_SKEW = 60
# Lifetime to assume if a token response ever omits expires_in.
_DEFAULT_TOKEN_TTL = 300


class PayPalError(Exception):
    """A non-2xx PayPal response. debug_id is what PayPal support asks for."""

    def __init__(self, status: int, body: dict):
        self.status = status
        self.body = body or {}
        self.name = self.body.get("name", "")
        self.debug_id = self.body.get("debug_id", "")
        super().__init__(f"{status} {self.name}: {self.body.get('message', '')} (debug_id={self.debug_id})")


def _urllib_transport(method: str, url: str, headers: dict, body: Optional[bytes],
                      opener: Callable = urllib.request.urlopen) -> dict:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with opener(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw) if raw else {}
        except ValueError:
            parsed = {"message": raw.decode(errors="replace")[:200]}
        raise PayPalError(exc.code, parsed) from None


def approve_link(order: dict) -> Optional[str]:
    """URL the buyer opens to approve. Orders with payment_source return
    'payer-action'; older flows return 'approve'."""
    links = {l.get("rel"): l.get("href") for l in order.get("links", [])}
    return links.get("payer-action") or links.get("approve")


class PayPalClient:
    def __init__(self, base_url: str, client_id: str, client_secret: str,
                 transport: Callable = _urllib_transport, clock: Callable = time.monotonic):
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.transport = transport
        self.clock = clock
        self._token: Optional[str] = None
        self._token_expires_at = 0.0

    def _auth(self) -> str:
        if not self._token or self.clock() >= self._token_expires_at:
            basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
            data = self.transport(
                "POST",
                f"{self.base_url}/v1/oauth2/token",
                {"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
                b"grant_type=client_credentials",
            )
            self._token = data["access_token"]
            self._token_expires_at = self.clock() + int(data.get("expires_in", _DEFAULT_TOKEN_TTL)) - _TOKEN_SKEW
        return self._token

    def _call(self, method: str, path: str, payload: Optional[dict] = None, request_id: Optional[str] = None) -> dict:
        data = None if method == "GET" else json.dumps(payload if payload is not None else {}).encode()
        request_id = request_id or uuid.uuid4().hex
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self._auth()}", "PayPal-Request-Id": request_id}
            if data is not None:
                headers["Content-Type"] = "application/json"
            try:
                return self.transport(method, f"{self.base_url}{path}", headers, data)
            except PayPalError as exc:
                if exc.status == 401 and attempt == 1:
                    self._token = None  # revoked or expired early: fetch a new one, retry once
                    continue
                raise

    def create_order(self, amount: Decimal, currency: str, reference_id: str = "default",
                     return_url: Optional[str] = None, cancel_url: Optional[str] = None) -> dict:
        payload = {
            "intent": "AUTHORIZE",
            "purchase_units": [{
                "reference_id": reference_id,
                "amount": {"currency_code": currency, "value": f"{amount:.2f}"},
            }],
        }
        if return_url or cancel_url:
            # CONTINUE: the buyer only authorizes; Guardian decides whether to capture.
            ctx = {"user_action": "CONTINUE"}
            if return_url:
                ctx["return_url"] = return_url
            if cancel_url:
                ctx["cancel_url"] = cancel_url
            payload["payment_source"] = {"paypal": {"experience_context": ctx}}
        return self._call("POST", "/v2/checkout/orders", payload)

    def get_order(self, order_id: str) -> dict:
        return self._call("GET", f"/v2/checkout/orders/{order_id}")

    def get_authorization(self, authorization_id: str) -> dict:
        return self._call("GET", f"/v2/payments/authorizations/{authorization_id}")

    def authorize_order(self, order_id: str) -> dict:
        return self._call("POST", f"/v2/checkout/orders/{order_id}/authorize",
                          request_id=f"{order_id}-authorize")

    def capture(self, authorization_id: str, amount: Decimal, currency: str) -> dict:
        # Request id is tied to the authorization so a retry can never capture twice.
        return self._call("POST", f"/v2/payments/authorizations/{authorization_id}/capture", {
            "amount": {"currency_code": currency, "value": f"{amount:.2f}"},
            "final_capture": True,
        }, request_id=f"{authorization_id}-capture")

    def void(self, authorization_id: str) -> dict:
        return self._call("POST", f"/v2/payments/authorizations/{authorization_id}/void",
                          request_id=f"{authorization_id}-void")

    def create_payout(self, payload: dict) -> dict:
        # The batch id doubles as the request id, so a retried payout is a no-op.
        return self._call("POST", "/v1/payments/payouts", payload,
                          request_id=payload["sender_batch_header"]["sender_batch_id"])

    def get_payout_batch(self, payout_batch_id: str) -> dict:
        return self._call("GET", f"/v1/payments/payouts/{payout_batch_id}")
