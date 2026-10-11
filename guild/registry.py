"""Outside developers' agents: registration, tokens and reputation that persists.

A developer registers an agent with a name and the PayPal account its bounties are
paid to, and gets a secret token once. Only a SHA-256 of the token is stored. New
agents start below the trust line (one claim per bounty) and earn more claims as
their verified record grows. Records live in a small JSON file (GUILD_DATA_DIR);
on hosts with ephemeral disks they reset on redeploy.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Optional

from guild.market import AgentCard

NEW_AGENT_PRIOR = 0.45          # below 0.5: one claim per bounty until it earns more
MAX_AGENTS = 500
_EMAIL = re.compile(r"^[^@\s<>\"']{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{1,38}[A-Za-z0-9]$")
RESERVED = {"scout", "browser", "quickfind", "guild"}


class RegistryError(ValueError):
    pass


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Registry:
    def __init__(self, path: Optional[Path] = None):
        self.path = path
        self._lock = threading.Lock()
        self._agents: dict = {}     # agent_id -> record
        self._by_token: dict = {}   # token hash -> agent_id
        self.cards: dict = {}       # agent_id -> AgentCard (live reputation)
        if path and path.exists():
            for rec in json.loads(path.read_text()).get("agents", []):
                self._add(rec)

    def _add(self, rec: dict) -> None:
        self._agents[rec["id"]] = rec
        self._by_token[rec["token_sha256"]] = rec["id"]
        self.cards[rec["id"]] = AgentCard(rec["id"], rec["name"], rec["payout_email"], {"hunt"},
                                          NEW_AGENT_PRIOR, passes=rec.get("passes", 0), fails=rec.get("fails", 0))

    def _save(self) -> None:
        if not self.path:
            return
        for aid, card in self.cards.items():
            self._agents[aid]["passes"], self._agents[aid]["fails"] = card.passes, card.fails
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"agents": list(self._agents.values())}, indent=1))
        tmp.replace(self.path)

    def register(self, name: str, payout_email: str, description: str = "") -> tuple:
        """Returns (agent_id, token). The token is shown once and never stored."""
        name, payout_email = (name or "").strip(), (payout_email or "").strip()
        if not _NAME.match(name):
            raise RegistryError("name: 3-40 letters, digits, spaces, dots, dashes or underscores")
        if not _EMAIL.match(payout_email):
            raise RegistryError("payout_email must be an email address (the PayPal account bounties go to)")
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if slug in RESERVED:
            raise RegistryError("that name is reserved")
        token = "gk_" + secrets.token_urlsafe(32)
        with self._lock:
            if len(self._agents) >= MAX_AGENTS:
                raise RegistryError("registry is full")
            if any(r["name"].lower() == name.lower() for r in self._agents.values()):
                raise RegistryError("an agent with that name exists")
            agent_id = f"{slug[:24]}-{secrets.token_hex(3)}"
            self._add({"id": agent_id, "name": name, "payout_email": payout_email,
                       "description": (description or "")[:200], "token_sha256": _hash(token), "passes": 0, "fails": 0})
            self._save()
        return agent_id, token

    def authenticate(self, token: str) -> Optional[AgentCard]:
        if not token or not token.startswith("gk_") or len(token) > 100:
            return None
        aid = self._by_token.get(_hash(token))
        return self.cards.get(aid) if aid else None

    def public(self, agent_id: str) -> Optional[dict]:
        card = self.cards.get(agent_id)
        if card is None:
            return None
        return {"id": card.id, "name": card.name, "stars": card.stars, "passes": card.passes, "fails": card.fails,
                "description": self._agents[agent_id].get("description", "")}

    def leaderboard(self) -> list:
        return sorted((self.public(a) for a in self.cards), key=lambda r: (-r["stars"], r["name"]))

    def save(self) -> None:
        with self._lock:
            self._save()

    def __len__(self) -> int:
        return len(self._agents)


def default_registry() -> Registry:
    root = os.environ.get("GUILD_DATA_DIR", "data")
    return Registry(Path(root) / "agents.json")
