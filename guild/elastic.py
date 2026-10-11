"""Elasticsearch: the Researcher agent's venue shortlist, and the history of deals
Guild verified (see deals.py).

Plain Elasticsearch REST, as the official client sends it:
    Authorization: ApiKey <encoded key>      (Elastic Cloud / Serverless API key)
    PUT  /{index}                            create the index with mappings (400 if it exists)
    POST /_bulk?refresh=wait_for             NDJSON index actions with fixed _ids (idempotent)
    POST /{index}/_search                    bool filter + sort, or sort + sum aggregation
    PUT  /{index}/_doc/{id}?refresh=wait_for one deal, keyed by its bounty (idempotent)
"""
from __future__ import annotations

import json
from typing import Optional

import httpx

from guild import venues

DEALS_INDEX = "guild-deals"
DEALS_MAPPINGS = {"properties": {
    "found_at": {"type": "date"}, "title": {"type": "text"}, "shop": {"type": "keyword"},
    "url": {"type": "keyword"}, "agent": {"type": "keyword"}, "currency": {"type": "keyword"},
    "ship_to": {"type": "keyword"}, "price": {"type": "double"}, "max_price": {"type": "double"},
    "saved": {"type": "double"}, "bounty": {"type": "double"}, "demo": {"type": "boolean"}}}


class ElasticError(Exception):
    pass


class Elastic:
    def __init__(self, url: str, api_key: str, timeout_s: float = 20.0,
                 transport: Optional[httpx.BaseTransport] = None):
        if not url.startswith("https://") or not api_key:
            raise ValueError("Elastic needs an https URL and an API key")
        self._http = httpx.Client(base_url=url.rstrip("/"), timeout=timeout_s, transport=transport,
                                  headers={"Authorization": f"ApiKey {api_key}"})
        self._seeded = False
        self._deals_ready = False

    def _req(self, method: str, path: str, *, body=None, content: Optional[bytes] = None,
             content_type: str = "application/json", ok=(200, 201)) -> httpx.Response:
        try:
            r = self._http.request(method, path, json=body if content is None else None, content=content,
                                   headers={"content-type": content_type})
        except httpx.HTTPError as exc:
            raise ElasticError(f"network: {type(exc).__name__}") from exc
        if r.status_code not in ok:
            raise ElasticError(f"HTTP {r.status_code} on {method} {path.split('?')[0]}")
        return r

    def seed(self) -> None:
        """Create the venue index and load the demo catalogue once per process."""
        if self._seeded:
            return
        r = self._req("PUT", f"/{venues.INDEX}", body={"mappings": venues.MAPPINGS}, ok=(200, 201, 400))
        if r.status_code == 400 and "resource_already_exists_exception" not in r.text:
            raise ElasticError("could not create the venue index")
        lines = []
        for v in venues.VENUES:
            lines.append(json.dumps({"index": {"_index": venues.INDEX, "_id": v["id"]}}))
            lines.append(json.dumps({k: v[k] for k in v if k != "id"}))
        r = self._req("POST", "/_bulk?refresh=wait_for", content=("\n".join(lines) + "\n").encode(),
                      content_type="application/x-ndjson")
        if _json(r).get("errors"):
            raise ElasticError("bulk load reported errors")
        self._seeded = True

    def shortlist(self, city: str, min_capacity: int, size: int = 3) -> list:
        """Cheapest venues in a city that seat at least `min_capacity`."""
        body = {"size": size,
                "query": {"bool": {"filter": [{"term": {"city": city.lower()}},
                                              {"range": {"capacity": {"gte": int(min_capacity)}}}]}},
                "sort": [{"price_per_day": "asc"}, {"capacity": "desc"}]}
        hits = (_json(self._req("POST", f"/{venues.INDEX}/_search", body=body)).get("hits") or {}).get("hits") or []
        out = []
        for h in hits:
            src = h.get("_source") if isinstance(h, dict) else None
            if not isinstance(src, dict):
                continue
            try:
                out.append({"name": str(src["name"])[:120], "capacity": int(src["capacity"]),
                            "price_per_day": str(int(src["price_per_day"])),
                            "neighbourhood": str(src.get("neighbourhood", ""))[:60]})
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def _deals_index(self) -> None:
        if self._deals_ready:
            return
        r = self._req("PUT", f"/{DEALS_INDEX}", body={"mappings": DEALS_MAPPINGS}, ok=(200, 201, 400))
        if r.status_code == 400 and "resource_already_exists_exception" not in r.text:
            raise ElasticError("could not create the deals index")
        self._deals_ready = True

    def put_deal(self, deal_id: str, doc: dict) -> None:
        """Store one verified deal. The bounty id is the document id, so a retry overwrites."""
        self._deals_index()
        self._req("PUT", f"/{DEALS_INDEX}/_doc/{deal_id}?refresh=wait_for", body=doc)

    def recent_deals(self, size: int = 10) -> dict:
        """Newest deals first, plus how many there are and what buyers saved in total."""
        self._deals_index()
        body = {"size": size, "sort": [{"found_at": "desc"}], "track_total_hits": True,
                "aggs": {"saved": {"sum": {"field": "saved"}}}}
        data = _json(self._req("POST", f"/{DEALS_INDEX}/_search", body=body))
        hits = data.get("hits") or {}
        total = hits.get("total")
        count = total.get("value") if isinstance(total, dict) else total
        saved = ((data.get("aggregations") or {}).get("saved") or {}).get("value")
        docs = [h["_source"] for h in hits.get("hits") or [] if isinstance(h, dict) and isinstance(h.get("_source"), dict)]
        return {"deals": docs, "count": int(count or 0), "saved": float(saved or 0)}


def _json(r: httpx.Response) -> dict:
    try:
        data = r.json()
    except ValueError as exc:
        raise ElasticError("response was not JSON") from exc
    if not isinstance(data, dict):
        raise ElasticError("response was not an object")
    return data
