"""Heartbeat data model and shared status vocabulary.

Every agent in the fleet emits a `Heartbeat` (fire-and-forget). The collector
owns the source of truth; this module just defines the wire shape so agents,
collector, and renderer all agree on one vocabulary.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class Status(str, Enum):
    """The tiny, consistent status vocabulary used everywhere in the UI."""

    RUNNING = "RUNNING"   # actively working / streaming tokens
    WAITING = "WAITING"   # blocked on a dependency, lock, or human approval
    IDLE = "IDLE"         # alive, no task assigned
    DONE = "DONE"         # task completed successfully
    FAILED = "FAILED"     # errored / crashed / timed out
    RETRYING = "RETRYING" # transient failure, backing off

    @property
    def glyph(self) -> str:
        return _GLYPHS[self]


_GLYPHS = {
    Status.RUNNING: "●",   # ●
    Status.WAITING: "⏸",   # ⏸
    Status.IDLE: "○",      # ○
    Status.DONE: "✔",      # ✔
    Status.FAILED: "✖",    # ✖
    Status.RETRYING: "◐",  # ◐
}


@dataclass
class Heartbeat:
    """A single point-in-time report from one agent.

    Only `agent_id` and `status` are strictly required; everything else is
    best-effort telemetry. Agents emit these as often as they like — the
    collector treats absence of heartbeats (staleness) as its own signal.
    """

    agent_id: str
    status: Status = Status.IDLE

    # identity / grouping
    team: str = "default"
    role: str = ""
    model: str = ""

    # what it's doing right now
    task: str = ""
    step: str = ""
    progress: float = 0.0          # 0.0 .. 1.0

    # resource pressure
    tokens: int = 0
    context_used: float = 0.0      # 0.0 .. 1.0 of the context window
    spend_usd: float = 0.0         # cumulative spend for this agent

    # failure / dependency triage
    error: str = ""
    retry_count: int = 0
    blocked_on: list[str] = field(default_factory=list)

    ts: float = field(default_factory=time.time)

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "Heartbeat":
        d = dict(d)
        d["status"] = Status(d.get("status", "IDLE"))
        # tolerate older/extra keys without exploding
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in allowed})
