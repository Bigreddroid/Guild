"""End-of-run money movement.

The human's budget is authorized up front (Orders v2, intent AUTHORIZE). When the
run ends, only what was earned by passed work is captured; final_capture releases
the rest of the authorization. Agent owners are then paid in one Payouts batch.
Nothing is paid if the capture fails, and the batch id is fixed per run so a retry
can never pay anyone twice.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from guild.paypal import PayPalClient


def build_payout(run_id: str, owed: dict, currency: str) -> Optional[dict]:
    items = [
        {
            "recipient_type": "EMAIL",
            "amount": {"value": f"{amount:.2f}", "currency": currency},
            "receiver": receiver,
            "note": "Guild: payment for reviewed and accepted work",
            "sender_item_id": f"{run_id}-{i}",
        }
        for i, (receiver, amount) in enumerate(sorted(owed.items()))
        if amount > 0
    ]
    if not items:
        return None
    return {
        "sender_batch_header": {
            "sender_batch_id": f"guild-{run_id}",
            "email_subject": "You were paid for work your agent did on Guild",
        },
        "items": items,
    }


def settle_run(client: PayPalClient, run_id: str, authorization_id: str, spent: Decimal,
               owed: dict, currency: str) -> dict:
    total_owed = sum(owed.values(), Decimal("0"))
    if total_owed != spent:
        raise ValueError(f"owed {total_owed} does not match spent {spent}")
    if spent <= 0:
        client.void(authorization_id)
        return {"voided": True, "capture_id": None, "payout_batch_id": None}
    capture = client.capture(authorization_id, spent, currency)
    payout = client.create_payout(build_payout(run_id, owed, currency))
    return {
        "voided": False,
        "capture_id": capture.get("id"),
        "capture_status": capture.get("status"),
        "payout_batch_id": payout.get("batch_header", {}).get("payout_batch_id"),
        "payout_status": payout.get("batch_header", {}).get("batch_status"),
    }
