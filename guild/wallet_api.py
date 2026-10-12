"""Guild Pay over HTTP: prepaid wallets that owners top up with PayPal and agents spend, within rules.

    POST /api/wallets                               owner creates a wallet (returns an owner key once);
                                                    optional envelopes {name: {limit, max_per_purchase, approve_above}}
    GET  /api/wallets/{id}                          policy, totals, purchases
    POST /api/wallets/{id}/top-up                   owner: {amount}; PayPal checkout (credited at once in preview)
    POST /api/wallets/{id}/withdraw                 owner: every unspent, unreserved dollar back to PayPal
    POST /api/wallets/{id}/purchases                agent (Bearer token): ask to buy {url, item, price, category?}
    POST /api/wallets/{id}/purchases/{pid}/approve  owner: approve one waiting purchase
    POST /api/wallets/{id}/purchases/{pid}/reject   owner
    POST /api/wallets/{id}/stop                     owner: freeze; waiting purchases dropped, unspent money returned
    POST /api/wallets/{id}/promotions               owner (X-Owner-Key) or the wallet's agent (Bearer): book a
                                                    creator, paid on proof {creator, channel, paypal_email, amount,
                                                    link, words?, deadline_minutes?, category?}
    POST /api/wallets/{id}/promotions/{pid}/proof   anyone: {url} of the live post; paid only if it checks out
    POST /api/wallets/{id}/purchases/{pid}/cancel   owner: release a held promotion
    GET  /api/wallets/paypal/{topup,cancel}         PayPal sends the owner back here

Every step is an event on /api/runs/{wallet_id}/events, so the page can follow along.
The owner key is shown once and only its hash is kept; it travels in the
X-Owner-Key header, never in a URL.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from guild import shops
from guild.bounty import Claim, WantError, parse_want, verify
from guild.promo import Brief, BriefError, check_proof, parse_brief
from guild.sellers import SellerCardError, read_card
from guild.wallet import (APPROVED, DEFAULT_ENVELOPE, HELD, MAX_ENVELOPES, Envelope, Policy, PolicyError, Wallet,
                          WalletError)
from guild.walletpay import WalletPay

WALLET_LIFETIME_S = 15 * 60
MAX_WALLETS = 50
MAX_PROOFS = 10            # per promotion
HOUSE_AGENT = "shopperbot"


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _money(raw, name: str) -> Decimal:
    try:
        v = Decimal(str(raw).strip()) if isinstance(raw, (str, int, float)) and not isinstance(raw, bool) else None
    except InvalidOperation:
        v = None
    if v is None or not v.is_finite():
        raise HTTPException(400, f"{name} must be an amount")
    return v.quantize(Decimal("0.01"))


def _envelopes(raw) -> Optional[dict]:
    """{"marketing": {"limit": 150, "max_per_purchase": 60, "approve_above": 40}, ...} -> Envelopes."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or not 1 <= len(raw) <= MAX_ENVELOPES:
        raise HTTPException(400, f"envelopes must be an object with 1 to {MAX_ENVELOPES} entries")
    out = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            raise HTTPException(400, "each envelope needs limit, max_per_purchase and approve_above")
        label = str(name)[:24]
        limit = _money(spec.get("limit"), f"{label} limit")
        most = _money(spec.get("max_per_purchase", str(limit)), f"{label} max_per_purchase")
        out[str(name)] = Envelope(limit, most, _money(spec.get("approve_above", str(most)), f"{label} approve_above"))
    return out


class WalletService:
    """Wallets in memory, each with its event run. Pure enough to test without HTTP."""

    def __init__(self, fetch: Callable, trust_for: Callable, pay: WalletPay, new_run: Callable,
                 clock=time.monotonic):
        self.fetch, self.trust_for, self.pay, self.new_run, self.clock = fetch, trust_for, pay, new_run, clock
        self.entries: dict = {}     # wallet id -> {"wallet", "run", "key", "created"}
        self._lock = threading.Lock()
        self._money_lock = threading.RLock()   # one capture, void or withdrawal at a time
        self.house_agent: Optional[Callable] = None   # starts ShopperBot once the wallet has money

    # ------------------------------------------------------------- lifecycle

    def wallets(self) -> dict:
        return {wid: e["wallet"] for wid, e in self.entries.items()}

    def create(self, body: dict, agent_id: str) -> tuple:
        try:
            want = parse_want(body, Decimal("0"), shops.DEMO_WANT, "want-" + secrets.token_hex(4))
            policy = Policy(_money(body.get("budget", "400"), "budget"),
                            _money(body.get("max_per_purchase", "350"), "max_per_purchase"),
                            _money(body.get("approve_above", "250"), "approve_above"), want,
                            _envelopes(body.get("envelopes")))
        except (WantError, PolicyError) as exc:
            raise HTTPException(400, str(exc))
        self.expire()
        with self._lock:
            if sum(not e["wallet"].closed for e in self.entries.values()) >= MAX_WALLETS:
                raise HTTPException(429, "too many open wallets; try again soon")
            run_id, run = self.new_run()
            # Every wallet starts empty: the owner loads it (demo money in preview, PayPal in sandbox).
            wallet = Wallet(run_id, agent_id, policy, Decimal("0"))
            key = "ok_" + secrets.token_urlsafe(24)
            self.entries[run_id] = {"wallet": wallet, "run": run, "key": _hash(key), "created": self.clock(),
                                    "agent_started": False}
        run.emit({"type": "wallet", "wallet_id": wallet.id, "agent": agent_id, "mode": self.pay.mode,
                  "want": want.title, "must_have": list(want.must_have), "must_not": list(want.must_not),
                  "max_price": str(want.max_price), "ship_to": want.ship_to, **wallet.totals(),
                  "max_per_purchase": f"{policy.max_per_purchase:.2f}", "approve_above": f"{policy.approve_above:.2f}",
                  "lifetime_s": WALLET_LIFETIME_S})
        return wallet, key, run

    def start_sweeper(self, every_s: float = 5.0) -> threading.Thread:
        """Release holds whose deadline passed (and close old wallets) even if nobody is looking."""
        def loop():
            while True:
                time.sleep(every_s)
                try:
                    self.expire()
                except Exception:  # noqa: BLE001 - keep sweeping
                    pass
        t = threading.Thread(target=loop, name="wallet-sweeper", daemon=True)
        t.start()
        return t

    def get(self, wallet_id: str) -> dict:
        entry = self.entries.get(wallet_id)
        if entry is None:
            raise HTTPException(404, "no such wallet")
        return entry

    def owner(self, wallet_id: str, key: str) -> dict:
        entry = self.get(wallet_id)
        if not key or not hmac.compare_digest(_hash(key), entry["key"]):
            raise HTTPException(403, "owner key required (X-Owner-Key)")
        return entry

    def expire(self) -> None:
        now = self.clock()
        for e in list(self.entries.values()):
            if e["wallet"].closed:
                continue
            for p in e["wallet"].held():
                if now - e["created"] >= p.extra.get("deadline_at", 0):
                    self._void(e, p, "no good proof before the deadline: the hold was released, nothing paid")
            if now - e["created"] >= WALLET_LIFETIME_S:
                self.close(e, "the wallet's time ran out")

    def close(self, entry: dict, why: str) -> None:
        wallet, run = entry["wallet"], entry["run"]
        if wallet.closed:
            return
        for p in wallet.held():
            self._void(entry, p, f"the hold was released: {why}")
        wallet.close()
        if wallet.returnable() > 0:     # unspent money always goes back to the owner
            self._withdraw(entry)
        run.emit({"type": "closed", "reason": why, **wallet.totals()})
        run.done = True

    def view(self, entry: dict) -> dict:
        w = entry["wallet"]
        return {"wallet_id": w.id, "agent": w.agent_id, "closed": w.closed, "stopped": w.stopped,
                "mode": self.pay.mode, "want": w.policy.want.title,
                "max_per_purchase": f"{w.policy.max_per_purchase:.2f}", "approve_above": f"{w.policy.approve_above:.2f}",
                **w.totals(), "purchases": [p.public() for p in w.purchases]}

    # ------------------------------------------------------------- purchases

    def request(self, wallet_id: str, agent_id: str, url: str, item: str, price,
                category=DEFAULT_ENVELOPE) -> dict:
        """An agent asks to buy. Verified, decided by the rules, and paid if approved."""
        entry = self.get(wallet_id)
        wallet, run = entry["wallet"], entry["run"]
        if agent_id != wallet.agent_id:
            raise HTTPException(403, "this wallet belongs to another agent")
        if not isinstance(url, str) or not url.startswith("https://") or len(url) > 500:
            raise HTTPException(400, "url must be an https URL (max 500 chars)")
        price = _money(price, "price")
        if not isinstance(category, str) or not category or len(category) > 24:
            raise HTTPException(400, "category must be the name of one of the wallet's envelopes")
        item = " ".join(str(item or "").split())[:160] or "item"
        self.expire()
        want = wallet.policy.want
        claim = Claim(id=f"{wallet.id}-c{len(wallet.purchases) + 1}", want_id=want.id, agent_id=agent_id, url=url,
                      price=price, currency=want.currency, at=round(self.clock() - entry["created"], 2))
        verdict = verify(want, claim, self.fetch, self.trust_for(want))
        payee, seller, problem = None, "", ""
        try:
            card = read_card(url, self.fetch, shops.seller_of, shops.card_url_of)
            payee, seller = card.payee(), card.name
        except SellerCardError as exc:
            problem = str(exc)
        purchase = wallet.decide(url, item, verdict, seller=seller, payee=payee, seller_problem=problem,
                                 category=category)
        run.emit({"type": "purchase", "purchase": purchase.public(), "at": claim.at, "page_price": str(verdict.offer.price)
                  if verdict.offer and verdict.offer.price else None, "claimed": f"{price:.2f}", **wallet.totals()})
        if purchase.status == APPROVED:
            self._pay(entry, purchase)
        return purchase.public()

    def _pay(self, entry: dict, purchase) -> dict:
        try:
            event = self.pay.pay(entry["wallet"], purchase)
        except WalletError as exc:
            event = {"type": "failed", "purchase": purchase.public(), "error": str(exc)}
        event = {**event, **entry["wallet"].totals()}
        entry["run"].emit(event)
        return event

    def approve(self, entry: dict, purchase_id: str) -> dict:
        try:
            p = entry["wallet"].approve(purchase_id)
        except WalletError as exc:
            raise HTTPException(409, str(exc))
        entry["run"].emit({"type": "approved", "purchase": p.public(), **entry["wallet"].totals()})
        return self._hold(entry, p) if p.kind == "promotion" else self._pay(entry, p)

    # ------------------------------------------------------------- promotions (pay on proof)

    def promote(self, wallet_id: str, body: dict, by_owner: bool, agent_id: str = "") -> dict:
        """Book a creator: the amount is held in PayPal and paid only when their post checks out."""
        entry = self.get(wallet_id)
        wallet, run = entry["wallet"], entry["run"]
        if not by_owner and agent_id != wallet.agent_id:
            raise HTTPException(403, "this wallet belongs to another agent")
        try:
            brief, amount, email, minutes = parse_brief(body)
        except BriefError as exc:
            raise HTTPException(400, str(exc))
        category = body.get("category") or "marketing"
        if not isinstance(category, str) or len(category) > 24:
            raise HTTPException(400, "category must be the name of one of the wallet's envelopes")
        self.expire()
        now = round(self.clock() - entry["created"], 2)
        extra = {"creator": brief.creator, "channel": brief.channel, "link": brief.link, "words": list(brief.words),
                 "disclosure": brief.disclosure, "booked_by": "owner" if by_owner else "agent",
                 "deadline_at": round(now + minutes * 60, 2), "proofs": []}
        p = wallet.book(f"Promotion by {brief.creator}", amount, category, {"email_address": email}, brief.channel,
                        extra, by_owner)
        run.emit({"type": "promotion", "purchase": p.public(), "at": now, **wallet.totals()})
        if p.status == APPROVED:
            self._hold(entry, p)
        return p.public()

    def _hold(self, entry: dict, p) -> dict:
        try:
            event = self.pay.hold(entry["wallet"], p)
        except WalletError as exc:
            event = {"type": "failed", "purchase": p.public(), "error": str(exc)}
        event = {**event, **entry["wallet"].totals()}
        entry["run"].emit(event)
        return event

    def proof(self, wallet_id: str, purchase_id: str, url) -> dict:
        """Someone says the post is live. Check it; capture the hold if it passes."""
        entry = self.get(wallet_id)
        wallet, run = entry["wallet"], entry["run"]
        self.expire()
        with self._money_lock:
            try:
                p = wallet._get(purchase_id)
            except WalletError as exc:
                raise HTTPException(404, str(exc))
            if p.kind != "promotion" or p.status != HELD:
                raise HTTPException(409, f"this promotion is {p.status}, not waiting for proof")
            if len(p.extra["proofs"]) >= MAX_PROOFS:
                raise HTTPException(429, "too many proofs for this promotion")
            brief = Brief(p.extra["creator"], p.extra["channel"], p.extra["link"], tuple(p.extra["words"]),
                          p.extra["disclosure"])
            verdict = check_proof(brief, url, self.fetch)
            at = round(self.clock() - entry["created"], 2)
            p.extra["proofs"].append({"url": verdict.url, "valid": verdict.valid, "reasons": verdict.reasons,
                                      "checks": verdict.checks, "at": at})
            p.checks = dict(verdict.checks)
            run.emit({"type": "proof", "purchase": p.public(), "url": verdict.url, "valid": verdict.valid,
                      "reasons": verdict.reasons, "checks": verdict.checks, "at": at})
            if not verdict.valid:
                return {"type": "proof", "valid": False, "reasons": verdict.reasons, "checks": verdict.checks,
                        "purchase": p.public()}
            event = {**self.pay.capture(wallet, p, "the post checked out: paid to the creator"), **wallet.totals()}
            run.emit(event)
            return event

    def _void(self, entry: dict, p, why: str) -> dict:
        with self._money_lock:
            if p.status != HELD:
                return {}
            event = {**self.pay.void(entry["wallet"], p, why), **entry["wallet"].totals()}
            entry["run"].emit(event)
            return event

    def cancel(self, entry: dict, purchase_id: str) -> dict:
        try:
            p = entry["wallet"]._get(purchase_id)
        except WalletError as exc:
            raise HTTPException(404, str(exc))
        if p.status != HELD:
            raise HTTPException(409, f"purchase is {p.status}, not held")
        return self._void(entry, p, "cancelled by the owner: the hold was released, nothing paid")

    def reject(self, entry: dict, purchase_id: str) -> dict:
        try:
            p = entry["wallet"].reject(purchase_id)
        except WalletError as exc:
            raise HTTPException(409, str(exc))
        event = {"type": "rejected", "purchase": p.public(), **entry["wallet"].totals()}
        entry["run"].emit(event)
        return event

    # ------------------------------------------------------------- loading and returning money

    def top_up(self, entry: dict, amount) -> dict:
        amount = _money(amount, "amount")
        if entry["wallet"].closed:
            raise HTTPException(409, "this wallet is closed")
        try:
            event = self.pay.start_top_up(entry["wallet"], amount)
        except WalletError as exc:
            raise HTTPException(409, str(exc))
        except Exception as exc:  # noqa: BLE001 - PayPal down or refused
            raise HTTPException(502, f"PayPal: {type(exc).__name__}")
        if event["type"] == "topped_up":
            self.credited(entry, event)
        return event

    def credited(self, entry: dict, event: dict) -> None:
        """Money arrived: tell the page, and let the house agent start shopping."""
        entry["run"].emit({**event, **entry["wallet"].totals()})
        self.start_agent(entry)

    def start_agent(self, entry: dict) -> None:
        wallet = entry["wallet"]
        if (self.house_agent and wallet.agent_id == HOUSE_AGENT and not entry["agent_started"]
                and wallet.remaining() > 0 and not wallet.closed):
            entry["agent_started"] = True
            self.house_agent(self, wallet.id)

    def _withdraw(self, entry: dict) -> dict:
        with self._money_lock:
            event = {**self.pay.withdraw(entry["wallet"]), **entry["wallet"].totals()}
        entry["run"].emit(event)
        return event

    def withdraw(self, entry: dict) -> dict:
        if entry["wallet"].returnable() <= 0:
            raise HTTPException(409, "nothing to return: the rest is spent or waiting")
        return self._withdraw(entry)

    def stop(self, entry: dict) -> dict:
        dropped = entry["wallet"].stop()
        event = {"type": "stopped", "dropped": [p.id for p in dropped], **entry["wallet"].totals()}
        entry["run"].emit(event)
        self.close(entry, "the owner stopped this wallet")
        return event


def mount(app, service: WalletService, read_json: Callable, client_key: Callable, bearer: Callable,
          limiter, start_house_agent: Optional[Callable] = None) -> None:
    """Add the wallet routes to the FastAPI app."""
    service.house_agent = start_house_agent

    def owner(wallet_id: str, request: Request) -> dict:
        return service.owner(wallet_id, request.headers.get("x-owner-key", ""))

    @app.post("/api/wallets", status_code=201)
    async def create_wallet(request: Request):
        if not limiter.allow(client_key(request)):
            raise HTTPException(429, "too many wallets; wait a minute")
        body = await read_json(request)
        agent = str(body.get("agent") or HOUSE_AGENT)
        wallet, key, run = service.create(body, agent)
        service.start_agent(service.get(wallet.id))   # at once if it has money; otherwise after the first top-up
        return {"wallet_id": wallet.id, "owner_key": key, "agent": agent, "mode": service.pay.mode,
                "note": "Keep the owner key; Guild stores only its hash. Send it as X-Owner-Key to top up, "
                        "approve or stop."}

    @app.get("/api/wallets/paypal/{what}", include_in_schema=False)
    def paypal_return(what: str, link: str = "", token: str = ""):
        wallets = service.wallets()
        try:
            if what == "topup":
                wallet, event = service.pay.finish_top_up(link, token, wallets)
                service.credited(service.get(wallet.id), event)
            else:
                service.pay.cancel_link(link)
                return RedirectResponse("/?wallet_error=cancelled", status_code=303)
        except (WalletError, Exception) as exc:  # noqa: BLE001 - report, never crash the redirect
            msg = str(exc) if isinstance(exc, WalletError) else type(exc).__name__
            return RedirectResponse(f"/?wallet_error={msg[:120]}", status_code=303)
        return RedirectResponse(f"/?wallet={wallet.id}", status_code=303)

    @app.get("/api/wallets/{wallet_id}")
    def get_wallet(wallet_id: str):
        service.expire()
        return service.view(service.get(wallet_id))

    @app.post("/api/wallets/{wallet_id}/top-up")
    async def top_up(wallet_id: str, request: Request):
        entry = owner(wallet_id, request)
        if not limiter.allow(f"topup:{wallet_id}"):
            raise HTTPException(429, "too many top-ups; wait a minute")
        return service.top_up(entry, (await read_json(request)).get("amount"))

    @app.post("/api/wallets/{wallet_id}/withdraw")
    def withdraw(wallet_id: str, request: Request):
        return service.withdraw(owner(wallet_id, request))

    @app.post("/api/wallets/{wallet_id}/purchases")
    async def buy(wallet_id: str, request: Request):
        card = bearer(request)
        if not limiter.allow(f"buy:{card.id}"):
            raise HTTPException(429, "too many purchase requests; slow down")
        body = await read_json(request)
        return service.request(wallet_id, card.id, body.get("url"), body.get("item"), body.get("price"),
                               body.get("category") or DEFAULT_ENVELOPE)

    @app.post("/api/wallets/{wallet_id}/purchases/{purchase_id}/{action}")
    def owner_action(wallet_id: str, purchase_id: str, action: str, request: Request):
        entry = owner(wallet_id, request)
        if action == "approve":
            return service.approve(entry, purchase_id)
        if action == "reject":
            return service.reject(entry, purchase_id)
        if action == "cancel":
            return service.cancel(entry, purchase_id)
        raise HTTPException(404, "unknown action")

    @app.post("/api/wallets/{wallet_id}/promotions", status_code=201)
    async def promote(wallet_id: str, request: Request):
        if request.headers.get("x-owner-key"):
            owner(wallet_id, request)
            by_owner, agent_id = True, ""
        else:
            by_owner, agent_id = False, bearer(request).id
        if not limiter.allow(f"promote:{client_key(request)}"):
            raise HTTPException(429, "too many promotions; wait a minute")
        return service.promote(wallet_id, await read_json(request), by_owner, agent_id)

    @app.post("/api/wallets/{wallet_id}/promotions/{purchase_id}/proof")
    async def proof(wallet_id: str, purchase_id: str, request: Request):
        if not limiter.allow(f"proof:{client_key(request)}"):
            raise HTTPException(429, "too many proofs; wait a minute")
        body = await read_json(request)
        return service.proof(wallet_id, purchase_id, body.get("url"))

    @app.post("/api/wallets/{wallet_id}/stop")
    def stop(wallet_id: str, request: Request):
        return service.stop(owner(wallet_id, request))
