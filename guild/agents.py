"""Specialist agents. Each one bids on jobs that match its skills and does the
work with a sponsor tool:

    sourcer       Channel3 product search
    researcher    Elastic search over venue and job documents
    web_operator  KERNEL cloud browser
    doer          Zapier actions (email, calendar)
    cheapbot      a deliberately careless agent, so the demo shows unpaid bad work

Until a tool's key is configured the agent returns fixed sample output and every
event it produces says mode="simulated".
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from guild.market import AgentCard, Bid, Job

D = Decimal


class Agent:
    tool = ""
    price = D("0")
    eta_s = 0

    def __init__(self, card: AgentCard):
        self.card = card

    def bid(self, job: Job) -> Optional[Bid]:
        if job.skill not in self.card.skills:
            return None
        return Bid(self.card.id, job.id, self.price, self.eta_s)

    def work(self, job: Job) -> dict:
        return {"mode": "simulated", "tool": self.tool, "items": self.sample(job)}

    def sample(self, job: Job) -> list:
        raise NotImplementedError


class Sourcer(Agent):
    tool, price, eta_s = "Channel3", D("0.40"), 120

    def sample(self, job):
        if job.id == "swag":
            return [{"title": "Stainless bottle 750 ml, custom logo", "price": "18.50", "qty": 6,
                     "url": "https://shop.example/bottle-750"}]
        return [{"title": "A5 dotted notebook", "price": "6.90", "qty": 6, "url": "https://shop.example/a5"},
                {"title": "Gel pen, black", "price": "1.40", "qty": 6, "url": "https://shop.example/pen"}]


class Researcher(Agent):
    tool, price, eta_s = "Elastic", D("0.50"), 90

    def sample(self, job):
        return [{"name": "LX Factory Loft", "capacity": 12, "price_per_day": "420"},
                {"name": "Alfama Studio", "capacity": 8, "price_per_day": "310"},
                {"name": "Chiado Meeting Room", "capacity": 10, "price_per_day": "380"}]


class WebOperator(Agent):
    tool, price, eta_s = "KERNEL", D("0.50"), 150

    def sample(self, job):
        return [{"provider": "Lisbon Transfers", "price": "96", "pickup": "09:30"},
                {"provider": "Airport Shuttle Co", "price": "72", "pickup": "10:15"}]


class Doer(Agent):
    tool, price, eta_s = "Zapier", D("0.20"), 30

    def sample(self, job):
        return [{"to": f"teammate{i}@team.example", "subject": "Lisbon offsite plan"} for i in range(1, 7)]


class CheapBot(Agent):
    tool, price, eta_s = "none", D("0.10"), 20

    def sample(self, job):
        # Fast and cheap, and wrong: no links, no prices, too few results.
        return [{"title": "notebook"}]


def default_agents() -> dict:
    cards = [
        (Sourcer, AgentCard("sourcer", "Sourcer", "sourcer@owners.example", {"source"}, 0.9, passes=12)),
        (Researcher, AgentCard("researcher", "Researcher", "researcher@owners.example", {"research"}, 0.9, passes=9)),
        (WebOperator, AgentCard("web_operator", "Web Operator", "webop@owners.example", {"browse"}, 0.85, passes=6)),
        (Doer, AgentCard("doer", "Doer", "doer@owners.example", {"email"}, 0.95, passes=20)),
        (CheapBot, AgentCard("cheapbot", "CheapBot", "cheap@owners.example", {"source", "research", "browse"}, 0.6)),
    ]
    return {card.id: cls(card) for cls, card in cards}
