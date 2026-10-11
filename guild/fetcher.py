"""Fetching claimed product pages safely.

Agents submit URLs and Guild's server fetches them, so the fetcher refuses anything
that could reach inside the server's network (SSRF): only https on port 443, every
address the host resolves to must be public, redirects are re-checked hop by hop,
and size and time are capped. Guild's own labelled demo shops are rendered
in-process instead of fetched, so the demo never depends on the network.
"""
from __future__ import annotations

import ipaddress
import socket
import urllib.parse
from typing import Callable, Optional

import httpx

MAX_BYTES = 3_000_000
MAX_REDIRECTS = 3
TIMEOUT_S = 12.0
USER_AGENT = "GuildVerifier/1.0 (+https://github.com/Bigreddroid/Guild)"


class FetchRefused(Exception):
    pass


def _public_host(host: str, resolve: Callable = socket.getaddrinfo) -> None:
    if not host or host.endswith((".local", ".internal", ".localhost")) or host == "localhost":
        raise FetchRefused("host is not public")
    try:
        infos = resolve(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise FetchRefused("host does not resolve")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise FetchRefused("host resolves to a private address")


def check_url(url: str, resolve: Callable = socket.getaddrinfo) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise FetchRefused("only https URLs")
    if parts.port not in (None, 443):
        raise FetchRefused("only port 443")
    if parts.username or parts.password:
        raise FetchRefused("no credentials in URLs")
    _public_host(parts.hostname, resolve)


class SafeFetcher:
    """fetch(url) -> (status, html). Demo shop URLs are served in-process."""

    def __init__(self, local: Optional[Callable[[str], Optional[tuple]]] = None,
                 transport: Optional[httpx.BaseTransport] = None, resolve: Callable = socket.getaddrinfo):
        self.local, self.resolve = local, resolve
        self._http = httpx.Client(timeout=TIMEOUT_S, transport=transport, follow_redirects=False,
                                  headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})

    def __call__(self, url: str) -> tuple:
        if self.local:
            served = self.local(url)
            if served is not None:
                return served
        for _ in range(MAX_REDIRECTS + 1):
            check_url(url, self.resolve)
            with self._http.stream("GET", url) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    url = urllib.parse.urljoin(url, r.headers["location"])
                    continue
                body = bytearray()
                for chunk in r.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        break
                return r.status_code, body[:MAX_BYTES].decode(r.encoding or "utf-8", errors="replace")
        raise FetchRefused("too many redirects")
