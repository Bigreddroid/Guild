"""KERNEL cloud browsers, used by the Web Operator agent.

Matches the official SDK (kernel-python-sdk 0.123.0): base https://api.onkernel.com,
`Authorization: Bearer <key>`.
    POST   /browsers                               -> {session_id, browser_live_view_url, ...}
    POST   /browsers/{id}/playwright/execute       -> {success, result, error, ...}
    DELETE /browsers/{id}
Playwright code runs on KERNEL's side, so the Guild server needs no browser.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote

import httpx

BASE_URL = "https://api.onkernel.com"


class KernelError(Exception):
    pass


class Kernel:
    def __init__(self, api_key: str, base_url: str = BASE_URL, timeout_s: float = 90.0,
                 transport: Optional[httpx.BaseTransport] = None):
        if not api_key:
            raise ValueError("KERNEL needs an API key")
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport,
                                  headers={"Authorization": f"Bearer {api_key}"})

    def _call(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        try:
            r = self._http.request(method, path, json=body)
        except httpx.HTTPError as exc:
            raise KernelError(f"network: {type(exc).__name__}") from exc
        if not 200 <= r.status_code < 300:
            raise KernelError(f"HTTP {r.status_code}")
        if not r.content:
            return {}
        try:
            data = r.json()
        except ValueError as exc:
            raise KernelError("response was not JSON") from exc
        if not isinstance(data, dict):
            raise KernelError("response was not an object")
        return data

    def create_browser(self, timeout_seconds: int = 60) -> dict:
        # headful so the live view works; the session ends itself after inactivity
        data = self._call("POST", "/browsers", {"headless": False, "stealth": False,
                                                "timeout_seconds": timeout_seconds})
        if not isinstance(data.get("session_id"), str) or not data["session_id"]:
            raise KernelError("no session id")
        return data

    def execute(self, session_id: str, code: str, timeout_sec: int = 60) -> object:
        data = self._call("POST", f"/browsers/{quote(session_id, safe='')}/playwright/execute",
                          {"code": code, "timeout_sec": timeout_sec})
        if not data.get("success"):
            raise KernelError(f"script failed: {str(data.get('error') or 'unknown')[:200]}")
        return data.get("result")

    def delete_browser(self, session_id: str) -> None:
        self._call("DELETE", f"/browsers/{quote(session_id, safe='')}")
