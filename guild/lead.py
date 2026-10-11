"""The lead agent turns a goal into jobs. ScriptedLead returns a fixed plan for the
demo goal so the run is reproducible without an API key; ClaudeLead (added when a
key is available) produces the same shape from any goal."""
from __future__ import annotations

from decimal import Decimal

from guild.market import Job

DEMO_GOAL = "Plan my team offsite in Lisbon: 6 people, 2 days."

# Machine-checkable acceptance rules per job, used by RuleReviewer. The Claude
# reviewer reads the plain-language criteria instead.
DEMO_JOBS = [
    (Job("supplies", "Source notebooks and pens for 6 people", "source",
         ("6 notebooks and 6 pens", "every item has a price and a buy link", "total under $120"),
         Decimal("0.60")),
     {"min_items": 2, "required_fields": ("title", "price", "url"), "max_total": 120}),
    (Job("swag", "Source 6 branded water bottles", "source",
         ("6 bottles", "every item has a price and a buy link", "total under $150"),
         Decimal("0.60")),
     {"min_items": 1, "required_fields": ("title", "price", "url"), "max_total": 150}),
    (Job("venue", "Shortlist 3 meeting venues in Lisbon for 6 people", "research",
         ("3 venues", "each has capacity >= 6 and a daily price"),
         Decimal("0.80")),
     {"min_items": 3, "required_fields": ("name", "capacity", "price_per_day")}),
    (Job("transport", "Check airport transfer options for 6 people", "browse",
         ("at least 2 options", "each has price and pickup time"),
         Decimal("0.80")),
     {"min_items": 2, "required_fields": ("provider", "price", "pickup")}),
    (Job("email", "Email the team the plan", "email",
         ("sent to 6 recipients", "subject mentions Lisbon offsite"),
         Decimal("0.40"), depends_on=("supplies", "swag", "venue", "transport")),
     {"min_items": 6, "required_fields": ("to",)}),
]


class ScriptedLead:
    label = "scripted plan for the demo goal"

    def plan(self, goal: str) -> list:
        if goal != DEMO_GOAL:
            raise ValueError("the scripted lead only knows the demo goal; set ANTHROPIC_API_KEY for others")
        return [job for job, _ in DEMO_JOBS]

    @staticmethod
    def checks(job_id: str) -> dict:
        return dict(next(c for j, c in DEMO_JOBS if j.id == job_id))
