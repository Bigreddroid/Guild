"""HTTP API for the dashboard.

    POST /api/runs                 run the market once; returns {"run_id", "events"}
    GET  /api/runs/{id}/events     replay that run as server-sent events, step by step
    GET  /healthz                  liveness plus which parts are live or simulated

A run is computed in full when it is created (it takes milliseconds in simulated
mode) and the stream replays it with a short pause between steps, so the timeline
and the dashboard fill in the way a judge can follow. The built web app in web/dist
is served from "/" when it exists.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from guild.agents import default_agents
from guild.lead import DEMO_GOAL, ScriptedLead
from guild.review import RuleReviewer
from guild.run import run

MAX_RUNS = 50                    # runs kept in memory for replay
MAX_BUDGET = Decimal("10.00")    # sandbox demo cap
DEFAULT_BUDGET = Decimal("3.00")
STEP_DELAY_S = float(os.environ.get("GUILD_STEP_DELAY_S", "0.6"))
WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


class RateLimiter:
    """Fixed window per client: at most `limit` calls every `window_s` seconds."""

    def __init__(self, limit: int = 20, window_s: float = 60.0):
        self.limit, self.window_s = limit, window_s
        self._hits: dict = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            start, count = self._hits.get(key, (now, 0))
            if now - start >= self.window_s:
                start, count = now, 0
            if count >= self.limit:
                return False
            self._hits[key] = (start, count + 1)
            if len(self._hits) > 10_000:  # forget stale clients
                self._hits = {k: v for k, v in self._hits.items() if now - v[0] < self.window_s}
            return True


def _parse_budget(raw) -> Decimal:
    if raw is None:
        return DEFAULT_BUDGET
    try:
        budget = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise HTTPException(400, "budget must be a number")
    if not budget.is_finite() or budget <= 0 or budget > MAX_BUDGET:
        raise HTTPException(400, f"budget must be more than 0 and at most {MAX_BUDGET}")
    return budget.quantize(Decimal("0.01"))


def create_app() -> FastAPI:
    app = FastAPI(title="Guild", docs_url="/api/docs", openapi_url="/api/openapi.json")
    runs: OrderedDict = OrderedDict()
    limiter = RateLimiter()
    lock = threading.Lock()

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "lead": ScriptedLead.label, "reviewer": RuleReviewer.label,
                "agents": "simulated", "payments": "not connected"}

    @app.post("/api/runs")
    async def create_run(request: Request):
        client = request.client.host if request.client else "unknown"
        if not limiter.allow(client):
            raise HTTPException(429, "too many runs; wait a minute")
        body = {}
        if int(request.headers.get("content-length") or 0) > 4096:
            raise HTTPException(413, "body too large")
        raw = await request.body()
        if raw:
            try:
                body = json.loads(raw)
            except ValueError:
                raise HTTPException(400, "body must be JSON")
            if not isinstance(body, dict):
                raise HTTPException(400, "body must be a JSON object")
        budget = _parse_budget(body.get("budget"))
        events: list = []
        run(DEMO_GOAL, budget, default_agents(), ScriptedLead(), RuleReviewer(), events.append)
        events = json.loads(json.dumps(events, default=str))
        run_id = uuid.uuid4().hex[:12]
        with lock:
            runs[run_id] = events
            while len(runs) > MAX_RUNS:
                runs.popitem(last=False)
        return {"run_id": run_id, "events": events}

    @app.get("/api/runs/{run_id}/events")
    async def stream(run_id: str, delay: float = STEP_DELAY_S):
        with lock:
            events = runs.get(run_id)
        if events is None:
            raise HTTPException(404, "unknown run")
        delay = min(max(delay, 0.0), 3.0)

        async def gen():
            for i, event in enumerate(events):
                yield f"id: {i}\nevent: {event['type']}\ndata: {json.dumps(event)}\n\n"
                if delay:
                    await asyncio.sleep(delay)
            yield "event: end\ndata: {}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    if (WEB_DIST / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            target = (WEB_DIST / path).resolve()
            if path and target.is_file() and WEB_DIST in target.parents:
                return FileResponse(target)
            return FileResponse(WEB_DIST / "index.html")

    return app


app = create_app()
