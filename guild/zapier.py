"""Zapier, used by the Doer agent to send the team email.

Guild posts JSON to a "Webhooks by Zapier -> Catch Hook" URL; the Zap behind it
(set up in Zapier, e.g. Catch Hook -> Gmail "Send Email") does the sending. The
hook URL is the only secret. Catch Hook answers {"status": "success", "id": ...}.

Guard rails, because the demo site is public:
- recipients and wording are fixed by Guild, never taken from visitors;
- only https://hooks.zapier.com/hooks/catch/ URLs are accepted;
- at most one live send per `min_interval_s` per process; runs in between say so
  and use sample output, so re-running the demo can't flood an inbox or burn tasks.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

import httpx

HOOK_PREFIX = "https://hooks.zapier.com/hooks/catch/"


class ZapierError(Exception):
    pass


class ZapierThrottled(ZapierError):
    pass


class Zapier:
    def __init__(self, hook_url: str, min_interval_s: float = 600.0, timeout_s: float = 15.0,
                 transport: Optional[httpx.BaseTransport] = None, clock=time.monotonic):
        if not hook_url.startswith(HOOK_PREFIX):
            raise ValueError("Zapier hook URL must start with " + HOOK_PREFIX)
        self._url, self._min_interval, self._clock = hook_url, min_interval_s, clock
        self._http = httpx.Client(timeout=timeout_s, transport=transport)
        self._last: Optional[float] = None
        self._lock = threading.Lock()

    def send(self, payload: dict) -> str:
        """Post one message to the Zap; returns Zapier's request id."""
        with self._lock:
            now = self._clock()
            if self._last is not None and now - self._last < self._min_interval:
                wait = int(self._min_interval - (now - self._last))
                raise ZapierThrottled(f"live email sent recently; next allowed in {wait}s")
            self._last = now  # count attempts, so a failing Zap isn't hammered either
        try:
            r = self._http.post(self._url, json=payload)
        except httpx.HTTPError as exc:
            raise ZapierError(f"network: {type(exc).__name__}") from exc
        if r.status_code != 200:
            raise ZapierError(f"HTTP {r.status_code}")
        try:
            data = r.json()
        except ValueError as exc:
            raise ZapierError("response was not JSON") from exc
        if not isinstance(data, dict) or data.get("status") != "success":
            raise ZapierError("Zapier did not accept the message")
        return str(data.get("request_id") or data.get("id") or "")[:100]
