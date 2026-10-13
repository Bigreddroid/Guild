"""Live check: run the whole Guild Pay story against a deployed server with PayPal sandbox keys.

    python3 -m scripts.live_check --url https://<your-service>.onrender.com

1. Reads /api/readiness and stops with the exact fix if something is missing.
2. Opens a wallet and starts a $400 top-up. You open the printed PayPal link and pay
   as the sandbox buyer (the only manual step); the script waits for the money.
3. Waits for ShopperBot: four bad purchases blocked, the $299 one waiting. Approves it
   and checks it was paid out to the seller (a PayPal Payouts batch id).
4. Books the demo creator ($40), sends the real post as proof, checks the creator was paid.
5. Returns the unspent money ($61) as a refund to the buyer.
6. Writes evidence/live-run.json: every step, its ids and statuses. No keys; emails masked.

Exits non-zero at the first step that fails, with the server's (and PayPal's) error.
Use --allow-preview to dry-run against a server without keys (nothing real moves).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Optional

TOP_UP = "400.00"
# The same wallet the page opens for its demo: $400, shopping $300 (ask above $250), marketing $60, tools $40.
DEMO_WALLET = {"budget": "400", "max_per_purchase": "350", "approve_above": "250",
               "envelopes": {"shopping": {"limit": "300", "max_per_purchase": "300", "approve_above": "250"},
                             "marketing": {"limit": "60"}, "tools": {"limit": "40"}}}


class CheckFailed(Exception):
    pass


def http_call(base: str) -> Callable:
    def call(method: str, path: str, body=None, headers: Optional[dict] = None):
        req = urllib.request.Request(base.rstrip("/") + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}
    return call


def mask(email: str) -> str:
    name, _, domain = str(email).partition("@")
    return f"{name[:1]}***@{domain}" if domain else "***"


def ask_human(url: str) -> None:
    print("\n  Open this link, log in as the sandbox BUYER and pay:\n\n    " + url + "\n", flush=True)


class LiveCheck:
    def __init__(self, call: Callable, approve: Callable[[str], None] = ask_human, sleep: Callable = time.sleep,
                 wait_s: float = 600, allow_preview: bool = False, say: Callable = print):
        self.call, self.approve, self.sleep, self.wait_s = call, approve, sleep, wait_s
        self.allow_preview, self.say = allow_preview, say
        self.steps: list = []
        self.wid = self.key = ""

    # ------------------------------------------------------------------ helpers

    def step(self, name: str, ok: bool, detail: str, **ids) -> None:
        self.steps.append({"step": name, "ok": ok, "detail": detail, **ids,
                           "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        self.say(f"{'OK  ' if ok else 'FAIL'} {name}: {detail}")
        if not ok:
            raise CheckFailed(f"{name}: {detail}")

    def owner(self, method: str, path: str, body=None):
        return self.call(method, path, body, {"X-Owner-Key": self.key})

    def view(self) -> dict:
        status, body = self.call("GET", f"/api/wallets/{self.wid}")
        if status != 200:
            raise CheckFailed(f"wallet view: HTTP {status} {body.get('detail', '')}")
        return body

    def until(self, what: str, test: Callable[[dict], bool]) -> dict:
        waited = 0.0
        while True:
            v = self.view()
            if test(v):
                return v
            if waited >= self.wait_s:
                raise CheckFailed(f"timed out after {int(waited)}s waiting for {what}")
            self.sleep(2)
            waited += 2

    # ------------------------------------------------------------------ the run

    def run(self) -> dict:
        status, ready = self.call("GET", "/api/readiness")
        if status != 200:
            self.step("readiness", False, f"GET /api/readiness answered HTTP {status}")
        missing = [i for i in ready["items"] if i["required"] and i["ok"] is False]
        for i in missing:
            self.say(f"  missing: {i['item']}\n           {i['how']}")
        if missing and not self.allow_preview:
            self.step("readiness", False, f"{len(missing)} required item(s) missing: "
                      + "; ".join(i["item"] for i in missing))
        self.step("readiness", True, f"mode {ready['mode']}" + (f", {len(missing)} item(s) missing (preview dry run)"
                                                                 if missing else ", everything required is set"))
        if ready["mode"] != "sandbox" and not self.allow_preview:
            self.step("mode", False, "the server is in preview mode; set the PayPal keys first")

        status, body = self.call("POST", "/api/wallets", DEMO_WALLET)
        if status != 201:
            self.step("open wallet", False, f"HTTP {status} {body.get('detail', '')}")
        self.wid, self.key = body["wallet_id"], body["owner_key"]
        self.step("open wallet", True, f"wallet {self.wid}, empty, mode {body['mode']}", wallet_id=self.wid)

        status, top = self.owner("POST", f"/api/wallets/{self.wid}/top-up", {"amount": TOP_UP})
        if status != 200:
            self.step("top up", False, f"HTTP {status} {top.get('detail', '')}")
        if top.get("approve_url"):
            self.approve(top["approve_url"])
        v = self.until("the top-up to arrive", lambda v: float(v["funded"]) >= float(TOP_UP))
        self.step("top up", True, f"${v['funded']} loaded ({top.get('mode')})")

        v = self.until("ShopperBot's purchases",
                       lambda v: any(p["status"] == "pending" for p in v["purchases"]) or v["closed"])
        blocked = [p for p in v["purchases"] if p["status"] == "blocked"]
        pending = [p for p in v["purchases"] if p["status"] == "pending"]
        self.step("agent shops", bool(pending), f"{len(blocked)} blocked (${sum(float(p['price']) for p in blocked):.2f}), "
                  f"{len(pending)} waiting for the owner")

        p = pending[0]
        status, paid = self.owner("POST", f"/api/wallets/{self.wid}/purchases/{p['id']}/approve")
        ok = status == 200 and paid.get("type") == "paid"
        self.step("pay the seller", ok, f"${p['price']} to {paid.get('purchase', {}).get('seller') or p['seller']}"
                  if ok else f"{paid.get('type') or status}: {paid.get('purchase', {}).get('reasons') or paid}",
                  payout_batch_id=paid.get("purchase", {}).get("order_id", ""))

        status, demo = self.call("GET", "/api/demo/promotion")
        brief = {k: v for k, v in demo.items() if k not in ("proofs", "demo")}
        status, promo = self.owner("POST", f"/api/wallets/{self.wid}/promotions", brief)
        if status != 201 or promo.get("status") != "held":
            self.step("book creator", False, f"HTTP {status} {promo.get('status')} {promo.get('reasons') or promo.get('detail')}")
        self.step("book creator", True, f"${promo['price']} held for {promo['creator']} ({mask(brief['paypal_email'])})")
        status, proof = self.call("POST", f"/api/wallets/{self.wid}/promotions/{promo['id']}/proof",
                                  {"url": demo["proofs"]["good"]})
        ok = status == 200 and proof.get("type") == "paid"
        self.step("pay the creator", ok, "the post checked out and the creator was paid" if ok
                  else f"{proof.get('type') or status}: {proof.get('error') or proof.get('reasons') or proof}",
                  payout_batch_id=proof.get("purchase", {}).get("order_id", ""))

        status, back = self.owner("POST", f"/api/wallets/{self.wid}/withdraw")
        ok = status == 200 and back.get("type") == "withdrawn"
        self.step("return unspent", ok, f"${back.get('amount')} refunded to the buyer" if ok
                  else f"{back.get('type') or status}: {back.get('error') or back.get('detail')}")

        self.owner("POST", f"/api/wallets/{self.wid}/stop")
        return {"ok": True, "wallet_id": self.wid, "mode": ready["mode"], "steps": self.steps}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", required=True, help="the deployed Guild server, e.g. https://guild.onrender.com")
    ap.add_argument("--out", default="evidence/live-run.json")
    ap.add_argument("--wait", type=float, default=600, help="seconds to wait at each step (default 600)")
    ap.add_argument("--allow-preview", action="store_true", help="dry run against a server without PayPal keys")
    args = ap.parse_args(argv)
    check = LiveCheck(http_call(args.url), wait_s=args.wait, allow_preview=args.allow_preview)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        result = check.run()
    except CheckFailed as exc:
        result = {"ok": False, "error": str(exc), "wallet_id": check.wid, "steps": check.steps}
    result = {"server": args.url, "started": started, **result}
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\n{'PASSED' if result['ok'] else 'FAILED'}: evidence written to {args.out}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
