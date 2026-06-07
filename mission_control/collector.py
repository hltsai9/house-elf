"""Collector: append-only event log + derived fleet state.

Agents emit heartbeats; the collector is the single source of truth. It does
two jobs:

  1. Persist every heartbeat to an append-only JSONL log (durable, tail-able,
     replayable).
  2. Derive *intelligence* on top of raw heartbeats — the thing that separates
     a monitor from a pretty log viewer: staleness, burn rate, status
     roll-ups, and alerts.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Iterable

from .schema import Heartbeat, Status

# A "running" agent that hasn't checked in for this long is probably hung.
STALE_AFTER_S = 20.0
# Burn rate (USD/min) above this is flagged — usually a tool-call loop.
HIGH_BURN_USD_PER_MIN = 1.5


class EventLog:
    """Append-only JSONL store. One heartbeat per line."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)

    def emit(self, hb: Heartbeat) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps(hb.to_json()) + "\n")
            f.flush()

    def read_all(self) -> list[Heartbeat]:
        if not os.path.exists(self.path):
            return []
        out: list[Heartbeat] = []
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(Heartbeat.from_json(json.loads(line)))
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue  # tolerate partial writes at the tail
        return out


@dataclass
class AgentState:
    """Latest known state of one agent, plus collector-derived fields."""

    hb: Heartbeat
    age_s: float = 0.0            # seconds since last heartbeat
    stale: bool = False           # claims RUNNING but has gone quiet
    burn_usd_per_min: float = 0.0


@dataclass
class FleetState:
    agents: list[AgentState] = field(default_factory=list)
    counts: dict[Status, int] = field(default_factory=dict)
    total_spend: float = 0.0
    burn_usd_per_min: float = 0.0
    alerts: list[str] = field(default_factory=list)
    now: float = 0.0

    def by_team(self) -> dict[str, list[AgentState]]:
        teams: dict[str, list[AgentState]] = {}
        for a in self.agents:
            teams.setdefault(a.hb.team, []).append(a)
        return teams


def _burn_for(agent_id: str, history: list[Heartbeat], now: float,
              window_s: float = 60.0) -> float:
    """Estimate USD/min from spend deltas within a recent time window."""
    pts = [h for h in history if h.agent_id == agent_id and h.ts >= now - window_s]
    if len(pts) < 2:
        return 0.0
    pts.sort(key=lambda h: h.ts)
    dt = pts[-1].ts - pts[0].ts
    if dt < 1.0:
        return 0.0  # too little time spread to estimate a rate meaningfully
    d_spend = pts[-1].spend_usd - pts[0].spend_usd
    return max(0.0, d_spend) / dt * 60.0


def derive(history: Iterable[Heartbeat], now: float | None = None,
           budget_usd: float = 0.0) -> FleetState:
    """Fold the full event history into current fleet state + alerts."""
    now = time.time() if now is None else now
    history = list(history)

    # latest heartbeat wins, per agent
    latest: dict[str, Heartbeat] = {}
    for hb in history:
        prev = latest.get(hb.agent_id)
        if prev is None or hb.ts >= prev.ts:
            latest[hb.agent_id] = hb

    agents: list[AgentState] = []
    for aid, hb in latest.items():
        age = now - hb.ts
        stale = hb.status == Status.RUNNING and age > STALE_AFTER_S
        agents.append(AgentState(
            hb=hb,
            age_s=age,
            stale=stale,
            burn_usd_per_min=_burn_for(aid, history, now),
        ))
    agents.sort(key=lambda a: (a.hb.team, a.hb.agent_id))

    counts = {s: 0 for s in Status}
    for a in agents:
        counts[a.hb.status] += 1

    total_spend = sum(a.hb.spend_usd for a in agents)
    fleet_burn = sum(a.burn_usd_per_min for a in agents)

    state = FleetState(
        agents=agents,
        counts=counts,
        total_spend=total_spend,
        burn_usd_per_min=fleet_burn,
        now=now,
    )
    state.alerts = _alerts(state, budget_usd)
    return state


def _alerts(state: FleetState, budget_usd: float) -> list[str]:
    alerts: list[str] = []
    for a in state.agents:
        hb = a.hb
        if hb.status == Status.FAILED:
            why = f" ({hb.error})" if hb.error else ""
            alerts.append(f"✖ {hb.agent_id} failed{why}")
        elif a.stale:
            alerts.append(f"⚠ {hb.agent_id} stale — RUNNING but silent {a.age_s:.0f}s")
        elif hb.status == Status.WAITING and a.age_s > STALE_AFTER_S:
            blk = f" on {', '.join(hb.blocked_on)}" if hb.blocked_on else ""
            alerts.append(f"⚠ {hb.agent_id} blocked {a.age_s:.0f}s{blk}")
    if state.burn_usd_per_min > HIGH_BURN_USD_PER_MIN:
        alerts.append(f"🔥 high burn {state.burn_usd_per_min:.2f}$/min")
    if budget_usd and state.total_spend > budget_usd:
        alerts.append(f"💸 over budget ${state.total_spend:.2f} / ${budget_usd:.2f}")
    return alerts
