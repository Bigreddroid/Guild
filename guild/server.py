"""HTTP API for the dashboard.

    POST /api/runs                 start a market run; returns {"run_id"}
    GET  /api/runs/{id}            the run's events so far and whether it is done
    GET  /api/runs/{id}/events     the run as server-sent events, live, step by step
    GET  /healthz                  liveness plus which parts are live or simulated

A run executes in a worker thread (the Claude reviewer takes seconds per job) and
the stream sends each event as it happens, at least a short pause apart, so the
timeline and the dashboard fill in at a pace a judge can follow. The built web app in web/dist
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
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from guild import vendor
from guild.agents import agent_modes, default_agents
from guild.lead import DEMO_GOAL
from guild.run import run

MAX_RUNS = 50                    # runs kept in memory for replay
MAX_BUDGET = Decimal("10.00")    # sandbox demo cap
DEFAULT_BUDGET = Decimal("3.00")
STEP_DELAY_S = float(os.environ.get("GUILD_STEP_DELAY_S", "0.6"))
MAX_CONCURRENT = 3              # runs executing at once (each may call Claude)
STREAM_TIMEOUT_S = 300
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


def _agents_summary(modes: dict) -> str:
    live = [f"{k} {v}" for k, v in modes.items() if v != "simulated"]
    return "; ".join(live + ["others simulated"]) if live else "simulated"


class Run:
    """One market run executing in a worker thread; events are appended as they happen."""

    def __init__(self):
        self.events: list = []
        self.done = False
        self.started = time.monotonic()

    def emit(self, event: dict) -> None:
        self.events.append(json.loads(json.dumps(event, default=str)))


def create_app(brain=None) -> FastAPI:
    """`brain` is a callable returning (lead, reviewer); default: Claude when a key is set."""
    from guild.brain import default_brain

    make_brain = brain or default_brain
    app = FastAPI(title="Guild", docs_url="/api/docs", openapi_url="/api/openapi.json")
    runs: OrderedDict = OrderedDict()
    limiter = RateLimiter()
    lock = threading.Lock()

    def running() -> int:
        return sum(1 for r in runs.values() if not r.done)

    def execute(r: Run, budget: Decimal) -> None:
        try:
            lead, reviewer = make_brain()
            run(DEMO_GOAL, budget, default_agents(), lead, reviewer, r.emit)
        except Exception as exc:  # report, never crash the server; no money moves on error
            r.emit({"type": "error", "message": f"run failed: {type(exc).__name__}"})
        finally:
            r.done = True

    @app.get(vendor.PATH, include_in_schema=False)
    def vendor_page(passengers: Optional[str] = None, date: Optional[str] = None):
        try:
            return HTMLResponse(vendor.page(vendor.parse(passengers, date)))
        except vendor.VendorInputError as exc:
            return HTMLResponse(vendor.page(error=str(exc)), status_code=400)

    @app.get("/healthz")
    def healthz():
        lead, reviewer = make_brain()
        return {"ok": True, "lead": lead.label, "reviewer": reviewer.label,
                "agents": _agents_summary(agent_modes()), "agent_modes": agent_modes(),
                "payments": "not connected"}

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
        r = Run()
        run_id = uuid.uuid4().hex[:12]
        with lock:
            if running() >= MAX_CONCURRENT:  # each run can spend AI calls
                raise HTTPException(429, "the market is busy; try again in a few seconds")
            runs[run_id] = r
            while len(runs) > MAX_RUNS:
                oldest = next(iter(runs))
                if not runs[oldest].done:
                    break
                runs.pop(oldest)
        threading.Thread(target=execute, args=(r, budget), daemon=True).start()
        return {"run_id": run_id}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        r = runs.get(run_id)
        if r is None:
            raise HTTPException(404, "unknown run")
        return {"run_id": run_id, "done": r.done, "events": list(r.events)}

    @app.get("/api/runs/{run_id}/events")
    async def stream(run_id: str, delay: float = STEP_DELAY_S):
        r = runs.get(run_id)
        if r is None:
            raise HTTPException(404, "unknown run")
        delay = min(max(delay, 0.0), 3.0)

        async def gen():
            i, deadline = 0, time.monotonic() + STREAM_TIMEOUT_S
            while time.monotonic() < deadline:
                if i < len(r.events):
                    event = r.events[i]
                    yield f"id: {i}\nevent: {event['type']}\ndata: {json.dumps(event)}\n\n"
                    i += 1
                    if delay:
                        await asyncio.sleep(delay)
                elif r.done:
                    break
                else:
                    await asyncio.sleep(0.1)
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
