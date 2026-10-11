"""Specialist agents. Each one bids on jobs that match its skills and does the
work with a sponsor tool:

    sourcer       Channel3 product search
    researcher    Elasticsearch over Guild's demo venue catalogue
    web_operator  KERNEL cloud browser on Guild's demo vendor page
    doer          Zapier actions (email, calendar)
    cheapbot      a deliberately careless agent, so the demo shows unpaid bad work

Until a tool's key is configured the agent returns fixed sample output and every
event it produces says mode="simulated". The Sourcer is live on Channel3 when
CHANNEL3_API_KEY is set; if a live call fails it falls back to sample data and
says so in the event.
"""
from __future__ import annotations

import functools
import os
import re
from decimal import Decimal, InvalidOperation
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

    # What to search for on each demo job: (query, quantity, max unit price).
    # Other jobs search their title, one of each.
    SHOPPING = {
        "supplies": [("A5 notebook", 6, 15.00), ("black gel pen", 6, 5.00)],
        "swag": [("custom logo stainless steel water bottle", 6, 24.00)],
    }

    def __init__(self, card: AgentCard, channel3=None):
        super().__init__(card)
        self.channel3 = channel3

    def work(self, job: Job) -> dict:
        if self.channel3 is None:
            return super().work(job)
        try:
            items = []
            for query, qty, max_price in self.SHOPPING.get(job.id, [(job.title, 1, None)]):
                hits = self.channel3.search(query, limit=10, max_price=max_price)
                if hits:  # cheapest in-stock match; a miss leaves the list short and review fails it
                    items.append({**hits[0], "qty": qty})
            return {"mode": "live", "tool": self.tool, "items": items}
        except Exception as exc:  # never crash a run; say plainly that this is sample data
            out = super().work(job)
            out["note"] = f"Channel3 unavailable ({exc}); sample data"
            return out

    def sample(self, job):
        if job.id == "swag":
            return [{"title": "Stainless bottle 750 ml, custom logo", "price": "18.50", "qty": 6,
                     "url": "https://shop.example/bottle-750"}]
        return [{"title": "A5 dotted notebook", "price": "6.90", "qty": 6, "url": "https://shop.example/a5"},
                {"title": "Gel pen, black", "price": "1.40", "qty": 6, "url": "https://shop.example/pen"}]


class Researcher(Agent):
    """Shortlists venues from Guild's demo catalogue indexed in Elasticsearch."""
    tool, price, eta_s = "Elastic", D("0.50"), 90

    # What each demo job searches for: (city, minimum seats).
    SEARCHES = {"venue": ("lisbon", 6)}

    def __init__(self, card: AgentCard, elastic=None):
        super().__init__(card)
        self.elastic = elastic

    def work(self, job: Job) -> dict:
        if self.elastic is None or job.id not in self.SEARCHES:
            return super().work(job)
        try:
            self.elastic.seed()
            city, seats = self.SEARCHES[job.id]
            return {"mode": "live", "tool": self.tool, "items": self.elastic.shortlist(city, seats, size=3)}
        except Exception as exc:  # never crash a run; say plainly that this is sample data
            out = super().work(job)
            out["note"] = f"Elastic unavailable ({exc}); sample data"
            return out

    def sample(self, job):
        return [{"name": "LX Factory Loft", "capacity": 12, "price_per_day": "420"},
                {"name": "Alfama Studio", "capacity": 8, "price_per_day": "310"},
                {"name": "Chiado Meeting Room", "capacity": 10, "price_per_day": "380"}]


class WebOperator(Agent):
    """Drives a KERNEL cloud browser through Guild's demo vendor page."""
    tool, price, eta_s = "KERNEL", D("0.50"), 150

    def __init__(self, card: AgentCard, kernel=None, vendor_url: str = ""):
        super().__init__(card)
        self.kernel, self.vendor_url = kernel, vendor_url

    def work(self, job: Job) -> dict:
        if self.kernel is None or not self.vendor_url:
            return super().work(job)
        from guild.vendor import script
        session = None
        try:
            session = self.kernel.create_browser()
            result = self.kernel.execute(session["session_id"], script(self.vendor_url), timeout_sec=60)
            items = _clean_quotes(result)
            out = {"mode": "live", "tool": self.tool, "items": items}
            if isinstance(session.get("browser_live_view_url"), str) and session["browser_live_view_url"].startswith("https://"):
                out["live_view_url"] = session["browser_live_view_url"][:500]
            return out
        except Exception as exc:  # never crash a run; say plainly that this is sample data
            out = super().work(job)
            out["note"] = f"KERNEL unavailable ({exc}); sample data"
            return out
        finally:
            if session:
                try:
                    self.kernel.delete_browser(session["session_id"])
                except Exception:
                    pass  # the session also ends itself after its inactivity timeout

    def sample(self, job):
        return [{"provider": "Lisbon Transfers", "price": "96", "pickup": "09:30"},
                {"provider": "Airport Shuttle Co", "price": "72", "pickup": "10:15"}]


_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _clean_quotes(result) -> list:
    """Keep only well-formed quotes from the browser; text is third-party, so trim it."""
    rows = []
    for r in result if isinstance(result, list) else []:
        if not isinstance(r, dict):
            continue
        provider, price, pickup = r.get("provider"), r.get("price"), r.get("pickup")
        if not isinstance(provider, str) or not provider.strip() or not isinstance(pickup, str):
            continue
        try:
            amount = Decimal(str(price))
        except (InvalidOperation, ValueError):
            continue
        if not amount.is_finite() or amount <= 0 or not _TIME.match(pickup.strip()):
            continue
        rows.append({"provider": provider.strip()[:100], "price": str(amount), "pickup": pickup.strip()})
    return rows[:10]


class Doer(Agent):
    """Sends the team email through a Zapier Catch Hook."""
    tool, price, eta_s = "Zapier", D("0.20"), 30

    # Fixed by Guild, never taken from visitors. `.example` addresses can't receive
    # mail; set the Zap's "To" to your own inbox to watch the demo arrive.
    RECIPIENTS = [f"teammate{i}@team.example" for i in range(1, 7)]
    SUBJECT = "Lisbon offsite plan"

    def __init__(self, card: AgentCard, zapier=None):
        super().__init__(card)
        self.zapier = zapier

    def work(self, job: Job) -> dict:
        if self.zapier is None or job.id != "email":
            return super().work(job)
        payload = {"source": "Guild demo", "subject": self.SUBJECT, "recipients": self.RECIPIENTS,
                   "recipients_text": ", ".join(self.RECIPIENTS),
                   "body": ("Hi team, the Lisbon offsite is planned: venue shortlisted, notebooks, pens and "
                            "bottles sourced, airport transfers quoted. Sent by Guild's Doer agent.")}
        try:
            request_id = self.zapier.send(payload)
        except Exception as exc:  # throttled or failed: say plainly that this is sample data
            out = super().work(job)
            out["note"] = f"Zapier not used ({exc}); sample data"
            return out
        return {"mode": "live", "tool": self.tool,
                "items": [{"to": r, "subject": self.SUBJECT, "via": "Zapier", "zapier_request": request_id}
                          for r in self.RECIPIENTS]}

    def sample(self, job):
        return [{"to": r, "subject": self.SUBJECT} for r in self.RECIPIENTS]


class CheapBot(Agent):
    tool, price, eta_s = "none", D("0.10"), 20

    def sample(self, job):
        # Fast and cheap, and wrong: no links, no prices, too few results.
        return [{"title": "notebook"}]


def default_agents(channel3=None, kernel=None, vendor_url=None, elastic=None, zapier=None) -> dict:
    """The demo roster. The Sourcer searches Channel3 live when given a client
    (default: from CHANNEL3_API_KEY), otherwise it returns labelled sample data."""
    if channel3 is None:
        channel3 = _env_channel3(os.environ.get("CHANNEL3_API_KEY", ""))
    cards = [
        (Sourcer, AgentCard("sourcer", "Sourcer", "sourcer@owners.example", {"source"}, 0.9, passes=12)),
        (Researcher, AgentCard("researcher", "Researcher", "researcher@owners.example", {"research"}, 0.9, passes=9)),
        (WebOperator, AgentCard("web_operator", "Web Operator", "webop@owners.example", {"browse"}, 0.85, passes=6)),
        (Doer, AgentCard("doer", "Doer", "doer@owners.example", {"email"}, 0.95, passes=20)),
        (CheapBot, AgentCard("cheapbot", "CheapBot", "cheap@owners.example", {"source", "research", "browse"}, 0.6)),
    ]
    agents = {card.id: cls(card) for cls, card in cards}
    agents["sourcer"].channel3 = channel3
    agents["doer"].zapier = zapier if zapier is not None else _env_zapier(os.environ.get("ZAPIER_HOOK_URL", ""))
    if elastic is None:
        elastic = _env_elastic(os.environ.get("ELASTIC_URL", ""), os.environ.get("ELASTIC_API_KEY", ""))
    agents["researcher"].elastic = elastic
    if kernel is None:
        kernel = _env_kernel(os.environ.get("KERNEL_API_KEY", ""))
    agents["web_operator"].kernel = kernel
    agents["web_operator"].vendor_url = vendor_url if vendor_url is not None else public_vendor_url()
    return agents


def public_vendor_url() -> str:
    """The demo vendor page as the cloud browser must reach it. Render sets
    RENDER_EXTERNAL_URL; a cloud browser can't reach localhost, so no URL, no live run."""
    from guild.vendor import PATH
    base = os.environ.get("GUILD_PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or ""
    return base.rstrip("/") + PATH if base.startswith("https://") else ""


@functools.lru_cache(maxsize=2)
def _env_zapier(url: str):
    """One shared client per hook URL: its throttle must span runs."""
    from guild.zapier import HOOK_PREFIX, Zapier
    return Zapier(url) if url.startswith(HOOK_PREFIX) else None


@functools.lru_cache(maxsize=2)
def _env_elastic(url: str, key: str):
    from guild.elastic import Elastic
    return Elastic(url, key) if url.startswith("https://") and key else None


@functools.lru_cache(maxsize=2)
def _env_kernel(key: str):
    from guild.kernel import BASE_URL, Kernel
    return Kernel(key, os.environ.get("KERNEL_BASE_URL", BASE_URL)) if key else None


@functools.lru_cache(maxsize=2)
def _env_channel3(key: str):
    """One shared client per key (it holds a connection pool); None without a key."""
    from guild.channel3 import BASE_URL, Channel3
    return Channel3(key, os.environ.get("CHANNEL3_BASE_URL", BASE_URL)) if key else None


def agent_modes() -> dict:
    """Which agents use their real tool right now (for /healthz and the page)."""
    return {"sourcer": "live (Channel3)" if os.environ.get("CHANNEL3_API_KEY") else "simulated",
            "researcher": "live (Elastic)" if os.environ.get("ELASTIC_URL", "").startswith("https://")
            and os.environ.get("ELASTIC_API_KEY") else "simulated",
            "web_operator": "live (KERNEL)" if os.environ.get("KERNEL_API_KEY") and public_vendor_url() else "simulated",
            "doer": "live (Zapier, max 1 email per 10 min)"
            if os.environ.get("ZAPIER_HOOK_URL", "").startswith("https://hooks.zapier.com/hooks/catch/") else "simulated"}
