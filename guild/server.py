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
import urllib.parse
import uuid
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from guild import funding, vendor
from guild.agents import agent_modes, default_agents
from guild.lead import DEMO_GOAL
from guild.paypal import PayPalError
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


def client_key(request: Request) -> str:
    """The visitor's IP for rate limiting. Behind Render's proxy every request comes
    from the proxy, so use the last X-Forwarded-For entry: the proxy appends the
    address it saw, and a visitor can only prepend fake ones."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        last = forwarded.split(",")[-1].strip()
        if last:
            return last[:64]
    return request.client.host if request.client else "unknown"


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


_NOT_SET = object()


def create_app(brain=None, paypal=_NOT_SET, public_url: Optional[str] = None) -> FastAPI:
    """`brain` is a callable returning (lead, reviewer); default: Claude when a key is set.
    `paypal` is a PayPalClient (sandbox) or None; default: from the environment."""
    from guild.brain import default_brain

    make_brain = brain or default_brain
    paypal = funding.paypal_from_env() if paypal is _NOT_SET else paypal
    public_url = public_url if public_url is not None else funding.public_base_url()
    funds = funding.Funds(paypal) if paypal else None
    app = FastAPI(title="Guild", docs_url="/api/docs", openapi_url="/api/openapi.json")
    runs: OrderedDict = OrderedDict()
    limiter = RateLimiter()
    lock = threading.Lock()

    def running() -> int:
        return sum(1 for r in runs.values() if not r.done)

    def execute(r: Run, run_id: str, budget: Decimal, authorization_id: Optional[str]) -> None:
        try:
            lead, reviewer = make_brain()
            result = run(DEMO_GOAL, budget, default_agents(), lead, reviewer, r.emit)
            if authorization_id:
                r.emit(funding.settle(paypal, run_id, authorization_id, budget, result["spent"], result["owed"]))
            else:
                r.emit(funding.preview(budget, result["spent"], result["owed"]))
        except Exception as exc:  # report, never crash the server
            r.emit({"type": "error", "message": f"run failed: {type(exc).__name__}"})
            if authorization_id:  # the run broke: release the buyer's hold, take nothing
                try:
                    paypal.void(authorization_id)
                    r.emit({"type": "payment", "mode": "sandbox", "voided": True, "captured": "0.00",
                            "released": f"{budget:.2f}", "authorized": f"{budget:.2f}", "payouts": []})
                except Exception as void_exc:
                    r.emit({"type": "payment", "mode": "error",
                            "message": f"void failed: {type(void_exc).__name__}; the hold expires on its own"})
        finally:
            r.done = True

    def start_run(budget: Decimal, authorization_id: Optional[str] = None) -> str:
        r = Run()
        run_id = uuid.uuid4().hex[:12]
        with lock:
            # Funded runs always start: the buyer already approved and money is on hold.
            if authorization_id is None and running() >= MAX_CONCURRENT:  # each run can spend AI calls
                raise HTTPException(429, "the market is busy; try again in a few seconds")
            runs[run_id] = r
            while len(runs) > MAX_RUNS:
                oldest = next(iter(runs))
                if not runs[oldest].done:
                    break
                runs.pop(oldest)
        threading.Thread(target=execute, args=(r, run_id, budget, authorization_id), daemon=True).start()
        return run_id

    async def read_json(request: Request) -> dict:
        if int(request.headers.get("content-length") or 0) > 4096:
            raise HTTPException(413, "body too large")
        raw = await request.body()
        if not raw:
            return {}
        try:
            body = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "body must be JSON")
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be a JSON object")
        return body

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
                "payments": "PayPal sandbox" if paypal else "preview only (no PayPal keys)",
                "funding": bool(funds and public_url)}

    @app.post("/api/runs")
    async def create_run(request: Request):
        client = client_key(request)
        if not limiter.allow(client):
            raise HTTPException(429, "too many runs; wait a minute")
        budget = _parse_budget((await read_json(request)).get("budget"))
        return {"run_id": start_run(budget)}

    @app.post("/api/fund")
    async def fund(request: Request):
        """Start a PayPal-funded run: returns the sandbox approval link for the budget."""
        if funds is None:
            raise HTTPException(503, "PayPal sandbox is not configured on this server")
        if not public_url:
            raise HTTPException(503, "no public https URL for PayPal to return to")
        client = client_key(request)
        if not limiter.allow(client):
            raise HTTPException(429, "too many runs; wait a minute")
        budget = _parse_budget((await read_json(request)).get("budget"))
        try:
            _, url = funds.start(budget, public_url)
        except (PayPalError, funding.FundingError) as exc:
            raise HTTPException(502, f"PayPal: {exc}")
        return {"approve_url": url}

    def back_home(error: str = "", run_id: str = "") -> RedirectResponse:
        query = urllib.parse.urlencode({"run": run_id} if run_id else {"payment_error": error[:200]})
        return RedirectResponse(f"/?{query}", status_code=303)

    @app.get("/api/paypal/return", include_in_schema=False)
    def paypal_return(fund: str = "", token: str = ""):
        if funds is None:
            return back_home("PayPal is not configured")
        try:
            budget, authorization_id = funds.complete(fund, token)
        except (PayPalError, funding.FundingError) as exc:
            return back_home(f"payment not authorized: {exc}")
        return back_home(run_id=start_run(budget, authorization_id))

    @app.get("/api/paypal/cancel", include_in_schema=False)
    def paypal_cancel(fund: str = ""):
        if funds is not None:
            funds.cancel(fund)
        return back_home("payment cancelled; nothing was charged")

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
        # Gzip only the static bundle (the event stream must not be buffered).
        app.mount("/assets", GZipMiddleware(StaticFiles(directory=WEB_DIST / "assets"), minimum_size=1024),
                  name="assets")

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
