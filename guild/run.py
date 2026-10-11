"""One run of the market: plan → bid → award → work → review → settle, job by job.

Every step is reported through `emit(event)` so the dashboard and timeline can
show it live. A job whose work fails review is re-auctioned once without the agent
that failed it. Jobs whose dependencies did not get done are skipped as blocked.
"""
from __future__ import annotations

from decimal import Decimal

from guild.market import Market, NoEligibleBid


def run(goal: str, budget: Decimal, agents: dict, lead, reviewer, emit) -> dict:
    market = Market({a.card.id: a.card for a in agents.values()}, budget)
    jobs = lead.plan(goal)
    emit({"type": "plan", "goal": goal, "lead": lead.label, "reviewer": reviewer.label,
          "jobs": [{"id": j.id, "title": j.title, "skill": j.skill, "max_price": str(j.max_price),
                    "depends_on": list(j.depends_on)} for j in jobs]})
    done = set()
    for job in jobs:
        if any(dep not in done for dep in job.depends_on):
            emit({"type": "blocked", "job_id": job.id, "waiting_for": [d for d in job.depends_on if d not in done]})
            continue
        excluded = set()
        for attempt in (1, 2):
            bids = [b for a in agents.values() if a.card.id not in excluded and (b := a.bid(job))]
            ranked = market.rank(job, bids)
            emit({"type": "bids", "job_id": job.id, "attempt": attempt, "bids": [
                {"agent_id": r.bid.agent_id, "price": str(r.bid.price), "eta_s": r.bid.eta_s,
                 "stars": market.agents[r.bid.agent_id].stars, "value": round(r.value, 3),
                 "eligible": r.eligible, "reason": r.reason} for r in ranked]})
            try:
                winner = market.award(job, bids)
            except NoEligibleBid as exc:
                emit({"type": "unfunded", "job_id": job.id, "reason": str(exc), "remaining": str(market.remaining)})
                break
            emit({"type": "awarded", "job_id": job.id, "agent_id": winner.agent_id, "price": str(winner.price)})
            output = agents[winner.agent_id].work(job)
            emit({"type": "work", "job_id": job.id, "agent_id": winner.agent_id,
                  "mode": output.get("mode", "live"), "tool": output.get("tool", ""), "items": output.get("items"),
                  **{k: output[k] for k in ("note", "live_view_url") if output.get(k)}})
            try:
                review = reviewer.review(job, output)
                verdict = review.get("verdict")
                reasons = list(review.get("reasons", []))
            except Exception as exc:  # a broken reviewer must never move money
                verdict, reasons = "hold", [f"reviewer error: {type(exc).__name__}"]
            outcome = market.settle(job, winner, verdict)
            emit({"type": "settled", "job_id": job.id, "agent_id": winner.agent_id, "verdict": verdict,
                  "outcome": outcome, "reasons": reasons, "price": str(winner.price),
                  "stars_after": market.agents[winner.agent_id].stars, "spent": str(market.spent)})
            if outcome == "paid":
                done.add(job.id)
                break
            if outcome == "held":
                break
            excluded.add(winner.agent_id)
    result = {
        "goal": goal,
        "budget": budget,
        "spent": market.spent,
        "owed": market.owed(),
        "jobs": [{"id": j.id, "title": j.title} for j in jobs],
        "agents": {c.id: {"name": c.name, "stars": c.stars, "passes": c.passes, "fails": c.fails}
                   for c in market.agents.values()},
    }
    emit({"type": "done", "spent": str(market.spent), "budget": str(budget),
          "owed": {k: str(v) for k, v in market.owed().items()}})
    return result
