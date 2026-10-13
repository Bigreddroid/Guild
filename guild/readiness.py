"""What this server still needs before a live (PayPal sandbox) run, in plain words.

Each item says whether it is ready and, if not, exactly what to set and where. It
never echoes a key, a secret or an email: only whether one is there.
"""
from __future__ import annotations

import os
from typing import Optional

from guild import funding, shops

PLACEHOLDER_DOMAIN = "@sellers.guild.example"


def _item(key: str, label: str, ok: Optional[bool], how: str, required: bool = True) -> dict:
    return {"key": key, "item": label, "ok": ok, "required": required, "how": "" if ok else how}


def check(paypal, public_url: str, env=os.environ) -> dict:
    keys_set = bool(env.get("PAYPAL_CLIENT_ID") and env.get("PAYPAL_CLIENT_SECRET"))
    base = env.get("PAYPAL_BASE_URL", funding.SANDBOX_URL)
    sellers = shops.seller_accounts()
    creator = shops.creator_accounts()[shops.CREATOR]
    items = [
        _item("paypal_keys", "PayPal sandbox app keys", bool(paypal),
              "In Render → your service → Environment, set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET from "
              "developer.paypal.com → Apps & Credentials (Sandbox)." if not keys_set else
              f"PAYPAL_BASE_URL must be {funding.SANDBOX_URL} (Guild never uses live PayPal)."),
        _item("public_url", "Public https address (for PayPal to send the owner back)", bool(public_url),
              "Render sets RENDER_EXTERNAL_URL by itself; anywhere else, set GUILD_PUBLIC_URL=https://<your host>."),
        _item("payouts", "Payouts turned on for the sandbox app", None,
              "In the sandbox app's features, turn on Payouts. Guild can only tell on the first payout: "
              "if it is off, that purchase fails with PayPal's error and the money stays in the wallet."),
        _item("seller_account", "Demo seller (Harbor Audio) is a real sandbox account",
              not sellers["harbor-audio"].endswith(PLACEHOLDER_DOMAIN),
              "Create a sandbox Business account and add it to GUILD_SELLER_ACCOUNTS as harbor-audio=<email>."),
        _item("creator_account", "Demo creator (Maya Reviews) is a real sandbox account",
              not creator.endswith(PLACEHOLDER_DOMAIN),
              "Create a sandbox Personal account and add it to GUILD_SELLER_ACCOUNTS as maya-reviews=<email>."),
        _item("anthropic", "Claude for ShopperBot (optional)", bool(env.get("ANTHROPIC_API_KEY")),
              "Optional: set ANTHROPIC_API_KEY, and GUILD_SHOPPER_LIVE=1 to let ShopperBot shop with Claude.",
              required=False),
        _item("channel3", "Channel3 product index (optional)", bool(env.get("CHANNEL3_API_KEY")),
              "Optional: set CHANNEL3_API_KEY so 'seller listed' checks a real index instead of the demo one.",
              required=False),
    ]
    ready = all(i["ok"] is not False for i in items if i["required"])
    return {"ready_for_live": ready and bool(paypal) and bool(public_url),
            "mode": "sandbox" if paypal and public_url else "preview", "items": items}
