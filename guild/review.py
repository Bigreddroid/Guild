"""Reviewers return 'pass', 'fail' or 'hold'. The market pays only on an exact
'pass', so a reviewer that crashes, refuses or answers oddly can never move money.

RuleReviewer checks the machine-readable rules attached to each demo job. It is
labelled as rules, not AI, wherever it appears. ClaudeReviewer (added when a key is
available) checks the plain-language acceptance criteria instead.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from guild.lead import ScriptedLead


class RuleReviewer:
    label = "rule checks (not AI)"

    def review(self, job, output: dict) -> dict:
        checks = ScriptedLead.checks(job.id)
        items = output.get("items")
        reasons = []
        if not isinstance(items, list):
            return {"verdict": "fail", "reasons": ["output has no item list"]}
        need = checks.get("min_items", 1)
        if len(items) < need:
            reasons.append(f"{len(items)} items, need at least {need}")
        for i, item in enumerate(items, 1):
            missing = [f for f in checks.get("required_fields", ()) if not item.get(f)]
            if missing:
                reasons.append(f"item {i} missing {', '.join(missing)}")
        if "max_total" in checks:
            try:
                total = sum(Decimal(str(it.get("price", "x"))) * int(it.get("qty", 1)) for it in items)
                if total > checks["max_total"]:
                    reasons.append(f"total {total} over {checks['max_total']}")
            except (InvalidOperation, ValueError, TypeError):
                reasons.append("a price is not a number")
        return {"verdict": "fail" if reasons else "pass", "reasons": reasons or ["all checks passed"]}
