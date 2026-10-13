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
import dataclasses
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

from guild import funding, readiness, shops, vendor
from guild.agents import agent_modes, default_agents
from guild.bounty import Trust, Want, WantError, parse_want
from guild.deals import DealLog
from guild.fetcher import SafeFetcher
from guild.hunt import OpenHunt, run_hunt
from guild.hunters import default_hunters
from guild.registry import Registry, RegistryError, default_registry
from guild.lead import DEMO_GOAL
from guild.paypal import PayPalError
from guild.run import run
from guild.wallet_api import WalletService, mount as mount_wallets
from guild.walletpay import WalletPay

MAX_RUNS = 50                    # runs kept in memory for replay
MAX_BUDGET = Decimal("10.00")    # sandbox demo cap
DEFAULT_BUDGET = Decimal("3.00")
STEP_DELAY_S = float(os.environ.get("GUILD_STEP_DELAY_S", "0.6"))
MAX_CONCURRENT = 3              # runs executing at once (each may call Claude)
STREAM_TIMEOUT_S = 300
HUNT_DEADLINE_S = float(os.environ.get("GUILD_HUNT_DEADLINE_S", "45"))
HUNT_SPEED = float(os.environ.get("GUILD_HUNT_SPEED", "1.0"))  # scales simulated agents' hunting time
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


def trust_from_env(want) -> Trust:
    """Independent evidence for a want: Channel3's index when a key is set, otherwise
    the labelled demo index. A failed search gives no listings, so claims fail closed."""
    from guild.agents import _env_channel3
    channel3 = _env_channel3(os.environ.get("CHANNEL3_API_KEY", ""))
    listings = shops.catalogue() + shops.tool_catalogue()
    if channel3:
        try:
            listings = channel3.search(" ".join(want.must_have), limit=20, max_price=float(want.max_price) * 2)
        except Exception:
            listings = []
    return Trust(listings=tuple(listings), seller_of=shops.seller_of)


def default_hunters_from_env() -> dict:
    """Deal-hunting agents, live wherever their keys are set."""
    from guild.agents import _env_channel3, _env_kernel
    claude = None
    if os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic
        claude = anthropic.Anthropic()
    return default_hunters(channel3=_env_channel3(os.environ.get("CHANNEL3_API_KEY", "")),
                           kernel=_env_kernel(os.environ.get("KERNEL_API_KEY", "")), claude=claude)


def default_deal_log() -> DealLog:
    from guild.agents import _env_elastic, _env_zapier
    return DealLog(elastic=_env_elastic(os.environ.get("ELASTIC_URL", ""), os.environ.get("ELASTIC_API_KEY", "")),
                   zapier=_env_zapier(os.environ.get("ZAPIER_HOOK_URL", "")))


def create_app(brain=None, paypal=_NOT_SET, public_url: Optional[str] = None, hunters=None,
               hunt_speed: float = HUNT_SPEED, registry: Optional[Registry] = None,
               hunt_deadline_s: float = HUNT_DEADLINE_S, deals: Optional[DealLog] = None,
               trust=None, shopper=_NOT_SET) -> FastAPI:
    """`brain` is a callable returning (lead, reviewer); default: Claude when a key is set.
    `paypal` is a PayPalClient (sandbox) or None; default: from the environment."""
    from guild.brain import default_brain

    make_brain = brain or default_brain
    make_hunters = hunters or default_hunters_from_env
    make_trust = trust or trust_from_env
    agents_registry = registry if registry is not None else default_registry()
    deal_log = deals if deals is not None else default_deal_log()
    open_hunts: dict = {}       # run id -> {"door", "want", "opened"}
    signup_limiter = RateLimiter(limit=5, window_s=3600)
    claim_limiter = RateLimiter(limit=60, window_s=60)
    hunt_fetch = SafeFetcher(local=shops.serve)
    paypal = funding.paypal_from_env() if paypal is _NOT_SET else paypal
    public_url = public_url if public_url is not None else funding.public_base_url()
    funds = funding.Funds(paypal) if paypal else None
    app = FastAPI(title="Guild", docs_url="/api/docs", openapi_url="/api/openapi.json")
    runs: OrderedDict = OrderedDict()
    limiter = RateLimiter()
    lock = threading.Lock()

    def running() -> int:
        return sum(1 for r in runs.values() if not r.done)

    def execute(r: Run, run_id: str, budget: Decimal, authorization_id: Optional[str], kind: str = "market",
                want=None) -> None:
        try:
            if kind == "hunt":
                # The budget is the bounty: held on PayPal, paid in full to the winner's developer.
                want = want or dataclasses.replace(shops.DEMO_WANT, bounty=budget)
                door = OpenHunt()
                open_hunts[run_id] = {"door": door, "want": want, "opened": time.monotonic()}
                try:
                    # With outside agents registered, the bounty stays open for its whole window.
                    result = run_hunt(want, agents_registry.adopt(make_hunters()), hunt_fetch, r.emit, deadline_s=hunt_deadline_s,
                                      speed=hunt_speed, door=door, close_early=len(agents_registry) == 0,
                                      on_winner=deal_log.on_winner(run_id), trust=make_trust(want))
                finally:
                    open_hunts.pop(run_id, None)
                    agents_registry.save()  # every agent's reputation persists
            else:
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

    def start_run(budget: Decimal, authorization_id: Optional[str] = None, kind: str = "market", want=None) -> str:
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
        threading.Thread(target=execute, args=(r, run_id, budget, authorization_id, kind, want), daemon=True).start()
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

    # ---------------------------------------------------------------- outside agents

    def bearer(request: Request):
        auth = request.headers.get("authorization", "")
        card = agents_registry.authenticate(auth[7:].strip() if auth.lower().startswith("bearer ") else "")
        if card is None:
            raise HTTPException(401, "missing or unknown agent token")
        return card

    @app.post("/api/agents", status_code=201)
    async def register_agent(request: Request):
        """Register an outside agent. The token is shown once; keep it secret."""
        if not signup_limiter.allow(client_key(request)):
            raise HTTPException(429, "too many registrations from this address; try later")
        body = await read_json(request)
        try:
            agent_id, token = agents_registry.register(str(body.get("name", "")), str(body.get("payout_email", "")),
                                                       str(body.get("description", "")))
        except RegistryError as exc:
            raise HTTPException(400, str(exc))
        return {"agent_id": agent_id, "token": token,
                "note": "Store this token now; Guild keeps only its hash. Send it as 'Authorization: Bearer <token>'."}

    @app.get("/api/agents")
    def list_agents():
        return {"agents": agents_registry.leaderboard()}

    @app.get("/api/agents/me")
    def me(request: Request):
        return agents_registry.public(bearer(request).id)

    @app.get("/api/bounties")
    def open_bounties():
        now = time.monotonic()
        out = []
        for hunt_id, h in list(open_hunts.items()):
            w = h["want"]
            left = hunt_deadline_s - (now - h["opened"])
            if left > 0 and not h["door"].closed.is_set():
                out.append({"id": hunt_id, "title": w.title, "must_have": list(w.must_have),
                            "must_not": list(w.must_not), "max_price": str(w.max_price), "currency": w.currency,
                            "ship_to": w.ship_to, "bounty": str(w.bounty), "seconds_left": round(left, 1)})
        return {"bounties": out}

    @app.post("/api/bounties/{hunt_id}/claims", status_code=202)
    async def submit_claim(hunt_id: str, request: Request):
        card = bearer(request)
        if not claim_limiter.allow(card.id):
            raise HTTPException(429, "too many claims; slow down")
        hunt = open_hunts.get(hunt_id)
        if hunt is None:
            raise HTTPException(404, "no open bounty with that id")
        body = await read_json(request)
        url, price = body.get("url"), body.get("price")
        if not isinstance(url, str) or not url.startswith("https://") or len(url) > 500:
            raise HTTPException(400, "url must be an https URL (max 500 chars)")
        if not isinstance(price, (str, int, float)) or len(str(price)) > 20:
            raise HTTPException(400, "price must be a number or a numeric string")
        if not hunt["door"].submit(card, {"url": url, "price": str(price)}):
            raise HTTPException(409, "this bounty has closed")
        return {"accepted": True, "note": "verified within seconds; watch the bounty's event stream for the result"}

    @app.get("/api/deals")
    def recent_deals(size: int = 10):
        """Deals Guild verified, newest first, with how much buyers saved (Elastic when configured)."""
        return deal_log.recent(min(max(size, 1), 50))

    @app.get("/api/demo/renewal")
    def demo_renewal():
        """A ready-made subscription: a labelled demo AI writing tool, every 2 minutes."""
        return {"demo": True, **shops.demo_renewal()}

    @app.get("/api/demo/promotion")
    def demo_promotion():
        """A ready-made pay-on-proof booking with a labelled demo creator, and four posts
        to try as proof (one good, three that fail for different reasons)."""
        return {"demo": True, **shops.demo_brief()}

    @app.get("/api/demo/catalogue")
    def demo_catalogue():
        """Guild's labelled demo shops, for outside agents testing without a search API.
        Listed prices may be stale: read the page before you claim."""
        return {"demo": True, "listings": [{**item, "page": "/shops/" + item["url"].split("/", 3)[3]}
                                            for item in shops.catalogue()]}

    @app.get("/shops/{slug:path}", include_in_schema=False)
    def demo_shop_page(slug: str):
        body = shops.page(slug)
        if body is None:
            raise HTTPException(404, "no such demo product")
        return HTMLResponse(body)

    @app.get(vendor.PATH, include_in_schema=False)
    def vendor_page(passengers: Optional[str] = None, date: Optional[str] = None):
        try:
            return HTMLResponse(vendor.page(vendor.parse(passengers, date)))
        except vendor.VendorInputError as exc:
            return HTMLResponse(vendor.page(error=str(exc)), status_code=400)

    @app.get("/healthz")
    def healthz():
        lead, reviewer = make_brain()
        return {"ok": True,
                "hunters": {h.card.id: f"{h.mode} ({h.tool})" for h in make_hunters().values()},
                "payments": "PayPal sandbox" if paypal else "preview only (no PayPal keys)",
                "funding": bool(funds and public_url),
                "deal_history": deal_log.store, "deal_alerts": "Zapier" if deal_log.zapier else "off",
                "outside_agents": len(agents_registry),
                "wallets": {"payments": wallet_service.pay.mode,
                            "shopperbot": "live (Claude)" if os.environ.get("ANTHROPIC_API_KEY")
                            and os.environ.get("GUILD_SHOPPER_LIVE") == "1" else "scripted"},
                "prototype": {"lead": lead.label, "reviewer": reviewer.label,
                              "agents": _agents_summary(agent_modes()), "agent_modes": agent_modes()},
                "readiness": readiness.check(paypal, public_url or "")}

    @app.get("/api/readiness")
    def ready():
        """What this server still needs for a live PayPal sandbox run (never shows keys or emails)."""
        return readiness.check(paypal, public_url or "")

    @app.post("/api/runs")
    async def create_run(request: Request):
        client = client_key(request)
        if not limiter.allow(client):
            raise HTTPException(429, "too many runs; wait a minute")
        budget = _parse_budget((await read_json(request)).get("budget"))
        return {"run_id": start_run(budget)}

    def want_from(body: dict) -> "Want":
        bounty = _parse_budget(body.get("bounty", str(shops.DEMO_WANT.bounty)))
        try:
            return parse_want(body, bounty, shops.DEMO_WANT, f"want-{uuid.uuid4().hex[:8]}")
        except WantError as exc:
            raise HTTPException(400, str(exc))

    @app.post("/api/hunts")
    async def create_hunt(request: Request):
        """Start a deal bounty (unfunded: ends with a PayPal preview). Body: {"bounty"} plus,
        optionally, the want: must_have, must_not, max_price, ship_to, title."""
        if not limiter.allow(client_key(request)):
            raise HTTPException(429, "too many runs; wait a minute")
        want = want_from(await read_json(request))
        return {"run_id": start_run(want.bounty, kind="hunt", want=want), "want": want.title}

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
        body = await read_json(request)
        kind = "hunt" if body.get("kind") == "hunt" else "market"
        want = want_from(body) if kind == "hunt" else None
        budget = want.bounty if want else _parse_budget(body.get("budget"))
        try:
            _, url = funds.start(budget, public_url, kind, want)
        except (PayPalError, funding.FundingError) as exc:
            raise HTTPException(502, f"PayPal: {exc}")
        return {"approve_url": url}

    def back_home(error: str = "", run_id: str = "", kind: str = "market") -> RedirectResponse:
        key = "hunt" if kind == "hunt" else "run"
        query = urllib.parse.urlencode({key: run_id} if run_id else {"payment_error": error[:200]})
        return RedirectResponse(f"/?{query}", status_code=303)

    @app.get("/api/paypal/return", include_in_schema=False)
    def paypal_return(fund: str = "", token: str = ""):
        if funds is None:
            return back_home("PayPal is not configured")
        try:
            budget, authorization_id, kind, want = funds.complete(fund, token)
        except (PayPalError, funding.FundingError) as exc:
            return back_home(f"payment not authorized: {exc}")
        return back_home(run_id=start_run(budget, authorization_id, kind, want), kind=kind)

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
    async def stream(run_id: str, request: Request, delay: float = STEP_DELAY_S):
        r = runs.get(run_id)
        if r is None:
            raise HTTPException(404, "unknown run")
        delay = min(max(delay, 0.0), 3.0)
        try:  # a reconnecting browser resumes after the last event it saw
            start = int(request.headers.get("last-event-id", "-1")) + 1
        except ValueError:
            start = 0

        async def gen():
            i, deadline = max(0, start), time.monotonic() + STREAM_TIMEOUT_S
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

    # ---------------------------------------------------------------- Guild Pay wallets

    def new_wallet_run():
        r = Run()
        run_id = uuid.uuid4().hex[:12]
        with lock:
            runs[run_id] = r
            while len(runs) > MAX_RUNS:
                oldest = next(iter(runs))
                if not runs[oldest].done:
                    break
                runs.pop(oldest)
        return run_id, r

    wallet_service = WalletService(hunt_fetch, make_trust, WalletPay(paypal, public_url or ""), new_wallet_run)
    if shopper is _NOT_SET:   # the real server (tests pass their own shopper and drive the clock)
        from guild.shopper import start_shopper_from_env as shopper_fn
        wallet_service.start_sweeper()
    else:
        shopper_fn = shopper
    mount_wallets(app, wallet_service, read_json, client_key, bearer, RateLimiter(limit=30, window_s=60),
                  start_house_agent=shopper_fn)
    if shopper is _NOT_SET:   # the real server: Claude writes the budget summary when a key is set
        from guild.budget import client_from_env as budget_client_from_env
        wallet_service.budget_client = budget_client_from_env()
    app.state.wallets = wallet_service

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
