"""The job market: agents bid, one wins, the work is reviewed, and money is owed
only for work that passed. Everything here is deterministic; the AI parts (lead,
reviewer, specialist agents) live elsewhere and can only hand this module a bid or
a verdict string.

Reputation is a smoothed pass rate that starts at the agent's prior:
    reputation = (passes + K * prior) / (passes + fails + K)
A bid's value is reputation**2 / price: quality counts twice as much as price, so a
cheap agent with a bad record loses to a reliable one that costs a bit more.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

K = 2  # how many results it takes before the record outweighs the prior


def reputation(passes: int, fails: int, prior: float) -> float:
    return (passes + K * prior) / (passes + fails + K)


class NoEligibleBid(Exception):
    """No bid can be accepted: wrong skill, over the job cap, or over the budget."""


@dataclass
class AgentCard:
    id: str
    name: str
    owner_email: str  # PayPal receiver for this agent's earnings
    skills: set
    prior: float = 0.6
    passes: int = 0
    fails: int = 0

    @property
    def reputation(self) -> float:
        return reputation(self.passes, self.fails, self.prior)

    @property
    def stars(self) -> float:
        return round(1 + 4 * self.reputation, 1)


@dataclass(frozen=True)
class Job:
    id: str
    title: str
    skill: str
    acceptance: tuple  # criteria the reviewer checks
    max_price: Decimal
    depends_on: tuple = ()


@dataclass(frozen=True)
class Bid:
    agent_id: str
    job_id: str
    price: Decimal
    eta_s: int


@dataclass
class Ranked:
    bid: Bid
    value: float
    eligible: bool
    reason: str


@dataclass
class Market:
    agents: dict
    budget: Decimal
    spent: Decimal = Decimal("0")
    _owed: dict = field(default_factory=dict)
    _paid: set = field(default_factory=set)       # job ids that have been paid (final)
    _attempts: set = field(default_factory=set)   # (job id, agent id) pairs already judged

    @property
    def remaining(self) -> Decimal:
        return self.budget - self.spent

    def rank(self, job: Job, bids: list) -> list:
        """Every bid with its value and why it is (in)eligible; best first."""
        out = []
        for b in bids:
            agent = self.agents.get(b.agent_id)
            reason = ""
            if agent is None:
                reason = "unknown agent"
            elif b.job_id != job.id:
                reason = "bid is for another job"
            elif job.skill not in agent.skills:
                reason = f"agent lacks skill '{job.skill}'"
            elif not isinstance(b.price, Decimal) or not b.price.is_finite() or b.price <= 0:
                reason = "price must be a positive amount"
            elif b.price > job.max_price:
                reason = f"over job cap {job.max_price}"
            elif b.price > self.remaining:
                reason = f"over remaining budget {self.remaining}"
            value = agent.reputation ** 2 / float(b.price) if not reason else 0.0
            out.append(Ranked(b, value, not reason, reason or "eligible"))
        # Best value first; ties go to the faster bid, then to agent id so the
        # result never depends on the order bids arrived in.
        out.sort(key=lambda r: (not r.eligible, -round(r.value, 9), r.bid.eta_s, r.bid.agent_id))
        return out

    def award(self, job: Job, bids: list) -> Bid:
        ranked = self.rank(job, bids)
        if not ranked or not ranked[0].eligible:
            raise NoEligibleBid(f"no eligible bid for {job.id}")
        return ranked[0].bid

    def record_review(self, agent_id: str, passed: bool) -> None:
        agent = self.agents[agent_id]
        if passed:
            agent.passes += 1
        else:
            agent.fails += 1

    def settle(self, job: Job, winner: Bid, verdict: Optional[str]) -> str:
        """Apply the reviewer's verdict. Only an exact 'pass' moves money; 'fail'
        costs the agent reputation; anything else holds the job unchanged."""
        if job.id in self._paid:
            raise ValueError(f"job {job.id} is already paid")
        if (job.id, winner.agent_id) in self._attempts:
            raise ValueError(f"{winner.agent_id} was already judged on job {job.id}")
        if verdict == "pass":
            if winner.price > self.remaining:
                raise ValueError("payment would exceed the budget")
            agent = self.agents[winner.agent_id]
            self.spent += winner.price
            self._owed[agent.owner_email] = self._owed.get(agent.owner_email, Decimal("0")) + winner.price
            self.record_review(winner.agent_id, passed=True)
            self._paid.add(job.id)
            self._attempts.add((job.id, winner.agent_id))
            return "paid"
        if verdict == "fail":
            self.record_review(winner.agent_id, passed=False)
            self._attempts.add((job.id, winner.agent_id))  # this job can be re-auctioned to others
            return "unpaid"
        return "held"

    def owed(self) -> dict:
        return dict(self._owed)
