"""The wallet's rules: what an AI agent may buy with its owner's money.

The wallet is prepaid. The owner loads money into it with PayPal (a top-up) and
the agent can only ever spend what is loaded: there is no card on file to run up.
Whatever is not spent goes back to the owner (withdraw, or automatically when the
wallet closes). The policy's budget is the most the wallet may hold.

The owner sets a Policy once: a budget, the most one purchase may cost, the amount
above which they want to approve each purchase, and the want itself (what to buy,
what not to buy, max price, ship-to). The agent can only ask to buy; the wallet
decides, in code:

    approved   the purchase is verified and within every rule
    pending    verified and within budget, but above the owner's "ask me" amount
    blocked    a rule failed; the reasons say which

The budget can be split into envelopes (shopping, marketing, tools, people, ...),
each with its own limit, max per purchase and "ask me" amount. Every purchase is
tagged with one envelope and must fit both it and the whole budget. A wallet
without envelopes has one, "shopping", made from the policy's own amounts.

Purchases waiting for approval reserve their price, so neither the budget nor an
envelope can be overspent, whatever order approvals come in. Everything here is pure (no network,
no PayPal); the server pays approved purchases and records the result.
"""
from __future__ import annotations

import itertools
import re
import threading
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from guild.bounty import Verdict, Want, canonical_url

WALLET_MAX_BUDGET = Decimal("500.00")   # sandbox demo cap
CENT = Decimal("0.01")

APPROVED, PENDING, BLOCKED, REJECTED, PAID, FAILED, REFUNDED, HELD, VOIDED = (
    "approved", "pending", "blocked", "rejected", "paid", "failed", "refunded", "held", "voided")
COMMITTED = (APPROVED, PAID)             # counts as spent
OPEN = (PENDING, HELD)                   # counts as reserved (held: authorized in PayPal, paid on proof)


DEFAULT_ENVELOPE = "shopping"
MAX_ENVELOPES = 8
_ENVELOPE_NAME = re.compile(r"[a-z][a-z0-9_-]{0,23}")


class PolicyError(ValueError):
    pass


class WalletError(ValueError):
    pass


def _check_amounts(obj, names, prefix=""):
    for name in names:
        v = getattr(obj, name)
        if not isinstance(v, Decimal) or not v.is_finite() or v < 0:
            raise PolicyError(f"{prefix}{name} must be a non-negative amount")


@dataclass(frozen=True)
class Envelope:
    """One slice of the budget: e.g. marketing, at most 150, 60 per purchase, ask above 40."""
    limit: Decimal
    max_per_purchase: Decimal
    approve_above: Decimal

    def check(self, name: str) -> None:
        _check_amounts(self, ("limit", "max_per_purchase", "approve_above"), f"{name}: ")
        if self.limit <= 0:
            raise PolicyError(f"{name}: limit must be more than 0")
        if not (Decimal("0") < self.max_per_purchase <= self.limit):
            raise PolicyError(f"{name}: max per purchase must be more than 0 and at most its limit")
        if self.approve_above > self.max_per_purchase:
            raise PolicyError(f"{name}: the approval amount can't be above its max per purchase")


@dataclass(frozen=True)
class Policy:
    budget: Decimal
    max_per_purchase: Decimal
    approve_above: Decimal
    want: Want
    envelopes: Optional[dict] = None     # name -> Envelope; None means one "shopping" envelope

    def __post_init__(self):
        _check_amounts(self, ("budget", "max_per_purchase", "approve_above"))
        if not (Decimal("0") < self.budget <= WALLET_MAX_BUDGET):
            raise PolicyError(f"budget must be more than 0 and at most {WALLET_MAX_BUDGET}")
        if not (Decimal("0") < self.max_per_purchase <= self.budget):
            raise PolicyError("max per purchase must be more than 0 and at most the budget")
        if self.approve_above > self.max_per_purchase:
            raise PolicyError("the approval amount can't be above the max per purchase")
        envelopes = self.envelopes
        if envelopes is None:
            envelopes = {DEFAULT_ENVELOPE: Envelope(self.budget, self.max_per_purchase, self.approve_above)}
        if not isinstance(envelopes, dict) or not 1 <= len(envelopes) <= MAX_ENVELOPES:
            raise PolicyError(f"give 1 to {MAX_ENVELOPES} envelopes")
        for name, env in envelopes.items():
            if not isinstance(name, str) or not _ENVELOPE_NAME.fullmatch(name):
                raise PolicyError("envelope names are lowercase words, e.g. marketing")
            if not isinstance(env, Envelope):
                raise PolicyError(f"{name}: not an envelope")
            env.check(name)
        if sum((e.limit for e in envelopes.values()), Decimal("0")) > self.budget:
            raise PolicyError("the envelopes add up to more than the budget")
        object.__setattr__(self, "envelopes", dict(envelopes))


@dataclass
class Purchase:
    id: str
    url: str
    item: str
    price: Decimal               # what will be paid: the price the page shows
    status: str
    reasons: list
    checks: dict = field(default_factory=dict)
    seller: str = ""
    payee: Optional[dict] = None
    order_id: str = ""
    capture_id: str = ""
    category: str = DEFAULT_ENVELOPE
    kind: str = "buy"                # "buy": paid now; "promotion": held, paid on proof
    authorization_id: str = ""
    extra: dict = field(default_factory=dict)   # promotion: creator, channel, link, words, deadline, proofs

    def public(self) -> dict:
        return {"id": self.id, "url": self.url, "item": self.item, "price": f"{self.price:.2f}",
                "category": self.category, "kind": self.kind, "status": self.status,
                "reasons": list(self.reasons), "checks": dict(self.checks), "seller": self.seller,
                "order_id": self.order_id, "capture_id": self.capture_id, **self.extra}


class Wallet:
    def __init__(self, wallet_id: str, agent_id: str, policy: Policy, funded: Optional[Decimal] = None):
        self.id, self.agent_id, self.policy = wallet_id, agent_id, policy
        self.purchases: list = []
        self.closed = self.stopped = False
        # Money loaded and not yet withdrawn. None: pre-loaded with the whole budget (demo money).
        self.funded = policy.budget if funded is None else Decimal(funded).quantize(CENT)
        self.top_ups: list = []     # {"id", "amount", "order_id", "capture_id", "returned"}
        self.withdrawn = Decimal("0")
        self._lock = threading.RLock()
        self._ids = itertools.count(1)

    # ---------------------------------------------------------------- loading and returning money

    def balance(self) -> Decimal:
        """Loaded money not yet paid out (some may be reserved)."""
        return self.funded - self.spent()

    def top_up(self, amount: Decimal, order_id: str = "", capture_id: str = "") -> dict:
        with self._lock:
            amount = Decimal(amount).quantize(CENT)
            if self.closed or self.stopped:
                raise WalletError("this wallet is closed")
            if amount <= 0:
                raise WalletError("a top-up must be more than 0")
            if self.balance() + amount > self.policy.budget:
                raise WalletError(f"the wallet can hold at most {self.policy.budget:.2f}; "
                                  f"you can add up to {self.policy.budget - self.balance():.2f}")
            entry = {"id": f"{self.id}-t{len(self.top_ups) + 1}", "amount": amount, "order_id": order_id,
                     "capture_id": capture_id, "returned": Decimal("0")}
            self.top_ups.append(entry)
            self.funded += amount
            return entry

    def returnable(self) -> Decimal:
        """What can go back to the owner now: loaded, not spent, not reserved."""
        return max(self.remaining(), Decimal("0"))

    def record_withdraw(self, amount: Decimal) -> None:
        with self._lock:
            amount = Decimal(amount).quantize(CENT)
            if amount <= 0 or amount > self.returnable():
                raise WalletError("nothing to return")
            self.funded -= amount
            self.withdrawn += amount

    # ---------------------------------------------------------------- money totals

    def _sum(self, statuses, category: Optional[str] = None) -> Decimal:
        return sum((p.price for p in self.purchases
                    if p.status in statuses and (category is None or p.category == category)), Decimal("0"))

    def spent(self, category: Optional[str] = None) -> Decimal:
        return self._sum(COMMITTED, category)

    def reserved(self, category: Optional[str] = None) -> Decimal:
        return self._sum(OPEN, category)

    def remaining(self, category: Optional[str] = None) -> Decimal:
        """Left to spend: loaded money not spent or reserved, or that within one envelope."""
        left = self.funded - self.spent() - self.reserved()
        if category is None:
            return left
        env = self.policy.envelopes[category]
        return min(left, env.limit - self.spent(category) - self.reserved(category))

    def totals(self) -> dict:
        with self._lock:
            return {"budget": f"{self.policy.budget:.2f}", "funded": f"{self.funded:.2f}",
                    "balance": f"{self.balance():.2f}", "withdrawn": f"{self.withdrawn:.2f}",
                    "spent": f"{self.spent():.2f}",
                    "reserved": f"{self.reserved():.2f}", "remaining": f"{self.remaining():.2f}",
                    "envelopes": [{"name": name, "limit": f"{env.limit:.2f}", "spent": f"{self.spent(name):.2f}",
                                   "reserved": f"{self.reserved(name):.2f}", "remaining": f"{self.remaining(name):.2f}",
                                   "max_per_purchase": f"{env.max_per_purchase:.2f}",
                                   "approve_above": f"{env.approve_above:.2f}"}
                                  for name, env in self.policy.envelopes.items()]}

    # ---------------------------------------------------------------- decisions

    def _limits(self, price: Decimal, category: str) -> tuple:
        """The rules every spend obeys: open wallet, its envelope, the limits, the money left.
        Returns (reasons it breaks, the ask-me amount that applies, the category as recorded)."""
        reasons = []
        if self.stopped:
            reasons.append("the owner stopped this wallet")
        elif self.closed:
            reasons.append("this wallet is closed")
        env = self.policy.envelopes.get(category)
        approve_above = self.policy.approve_above
        if env is None:
            reasons.append(f"there is no {category[:24]!r} envelope in this wallet")
            category = category[:24]
            if price > self.remaining():
                reasons.append(f"{price} is over the money left in the wallet ({self.remaining():.2f})"
                               + ("; top it up first" if self.funded <= 0 else ""))
            return reasons, approve_above, category
        approve_above = min(approve_above, env.approve_above)
        if price > self.policy.max_per_purchase:
            reasons.append(f"{price} is over the {self.policy.max_per_purchase} limit per purchase")
        elif price > env.max_per_purchase:
            reasons.append(f"{price} is over the {env.max_per_purchase} limit per {category} purchase")
        if price > self.remaining():
            reasons.append(f"{price} is over the money left in the wallet ({self.remaining():.2f})"
                               + ("; top it up first" if self.funded <= 0 else ""))
        elif price > self.remaining(category):
            reasons.append(f"{price} is over the {category} envelope left ({self.remaining(category):.2f})")
        return reasons, approve_above, category

    def decide(self, url: str, item: str, verdict: Verdict, seller: str = "", payee: Optional[dict] = None,
               seller_problem: str = "", category: str = DEFAULT_ENVELOPE) -> Purchase:
        """The agent asks to buy `url` from the `category` envelope; `verdict` is the
        verifier's check of that page against the owner's want. Returns the recorded
        purchase with its decision."""
        with self._lock:
            price = (verdict.offer.price if verdict.offer and verdict.offer.price else verdict.claim.price)
            price = Decimal(price).quantize(CENT)
            reasons = [] if verdict.valid else list(verdict.reasons)
            more, approve_above, category = self._limits(price, category)
            if self.stopped or self.closed:     # say that first
                reasons.insert(0, more.pop(0))
            reasons += more
            key = canonical_url(url)
            if any(canonical_url(p.url) == key and p.status in COMMITTED + OPEN for p in self.purchases):
                reasons.append("this item was already bought or is waiting for approval")
            if payee is None:
                reasons.append(seller_problem or "the seller publishes no PayPal account to pay")
            status = BLOCKED if reasons else (PENDING if price > approve_above else APPROVED)
            if status == PENDING:
                reasons = [f"{price} is above {approve_above}: waiting for the owner"]
            elif status == APPROVED:
                reasons = ["verified and within every rule"]
            purchase = Purchase(id=f"{self.id}-p{next(self._ids)}", url=url[:500], item=item[:160], price=price,
                                status=status, reasons=reasons, checks=dict(verdict.checks), seller=seller,
                                payee=payee if status != BLOCKED else None, category=category)
            self.purchases.append(purchase)
            return purchase

    def book(self, item: str, price: Decimal, category: str, payee: dict, url: str, extra: dict,
             by_owner: bool) -> Purchase:
        """A promotion: money held for a creator, paid only on proof. The owner's own
        bookings are approved (they still obey every limit); an agent's always wait
        for the owner, because the agent chose who gets paid."""
        with self._lock:
            price = Decimal(price).quantize(CENT)
            reasons, _, category = self._limits(price, category)
            status = BLOCKED if reasons else (APPROVED if by_owner else PENDING)
            if status == PENDING:
                reasons = ["an agent booked this promotion: waiting for the owner"]
            elif status == APPROVED:
                reasons = ["booked by the owner: holding the money in PayPal"]
            p = Purchase(id=f"{self.id}-p{next(self._ids)}", url=url[:500], item=item[:160], price=price,
                         status=status, reasons=reasons, seller=extra.get("creator", ""),
                         payee=payee if status != BLOCKED else None, category=category, kind="promotion",
                         extra=dict(extra))
            self.purchases.append(p)
            return p

    def record_hold(self, purchase_id: str, order_id: str = "", authorization_id: str = "", error: str = "") -> Purchase:
        """After PayPal authorizes: an approved promotion is held (still reserved), or failed."""
        with self._lock:
            p = self._get(purchase_id)
            if p.kind != "promotion" or p.status != APPROVED:
                raise WalletError(f"purchase is {p.status}, not an approved promotion")
            if error:
                p.status, p.reasons = FAILED, [f"PayPal hold failed: {error}"]
            else:
                p.status, p.order_id, p.authorization_id = HELD, order_id, authorization_id
                p.reasons = ["held in PayPal: paid to the creator when their post checks out"]
            return p

    def record_capture(self, purchase_id: str, capture_id: str, why: str) -> Purchase:
        with self._lock:
            p = self._get(purchase_id)
            if p.status != HELD:
                raise WalletError(f"purchase is {p.status}, not held")
            p.status, p.capture_id, p.reasons = PAID, capture_id, [why]
            return p

    def record_void(self, purchase_id: str, why: str) -> Purchase:
        with self._lock:
            p = self._get(purchase_id)
            if p.status != HELD:
                raise WalletError(f"purchase is {p.status}, not held")
            p.status, p.reasons = VOIDED, [why]
            return p

    def held(self) -> list:
        return [p for p in self.purchases if p.status == HELD]

    def _get(self, purchase_id: str) -> Purchase:
        for p in self.purchases:
            if p.id == purchase_id:
                return p
        raise WalletError("no such purchase")

    def approve(self, purchase_id: str) -> Purchase:
        with self._lock:
            p = self._get(purchase_id)
            if self.closed or self.stopped:
                raise WalletError("this wallet is closed")
            if p.status != PENDING:
                raise WalletError(f"purchase is {p.status}, not waiting for approval")
            p.status, p.reasons = APPROVED, ["approved by the owner"]   # its price was already reserved
            return p

    def reject(self, purchase_id: str) -> Purchase:
        with self._lock:
            p = self._get(purchase_id)
            if p.status != PENDING:
                raise WalletError(f"purchase is {p.status}, not waiting for approval")
            p.status, p.reasons = REJECTED, ["rejected by the owner"]
            return p

    def record_payment(self, purchase_id: str, order_id: str = "", error: str = "", capture_id: str = "") -> Purchase:
        """After PayPal: an approved purchase becomes paid, or failed (money released)."""
        with self._lock:
            p = self._get(purchase_id)
            if p.status != APPROVED:
                raise WalletError(f"purchase is {p.status}, not approved")
            if error:
                p.status, p.reasons = FAILED, [f"payment failed: {error}"]
            else:
                p.status, p.order_id, p.capture_id = PAID, order_id, capture_id
            return p

    def record_refund(self, purchase_id: str) -> Purchase:
        with self._lock:
            p = self._get(purchase_id)
            if p.status != PAID:
                raise WalletError(f"purchase is {p.status}, not paid")
            p.status, p.reasons = REFUNDED, ["refunded to the owner"]
            return p

    def stop(self) -> list:
        """The owner's stop switch: no new purchases; anything waiting is rejected."""
        with self._lock:
            self.stopped = True
            return self._drop_pending("the owner stopped this wallet")

    def close(self) -> dict:
        """End of the task: no new purchases; anything still waiting is dropped, unpaid."""
        with self._lock:
            self.closed = True
            self._drop_pending("the wallet closed before the owner approved")
            return self.totals()

    def _drop_pending(self, why: str) -> list:
        dropped = [p for p in self.purchases if p.status == PENDING]
        for p in dropped:
            p.status, p.reasons = REJECTED, [why]
        return dropped
