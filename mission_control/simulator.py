"""Synthetic agent fleet — drives the dashboard with lifelike telemetry.

This stands in for real agents so the dashboard is demoable on its own. It
models a small multi-team fleet with a task DAG: planners finish, builders
progress, one agent hits a transient error and retries, a deployer fails on a
rate-limit, and a reviewer stays blocked on its upstream. The point is to
exercise every status, the burn meter, staleness, and the alert paths.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from .collector import EventLog
from .schema import Heartbeat, Status


@dataclass
class _Agent:
    agent_id: str
    team: str
    role: str
    task: str
    model: str = "claude-opus-4-8"
    status: Status = Status.IDLE
    progress: float = 0.0
    tokens: int = 0
    spend: float = 0.0
    retry_count: int = 0
    error: str = ""
    blocked_on: list[str] = field(default_factory=list)
    # scripted behaviour
    rate: float = 0.05          # progress per tick when running
    fails_at: float | None = None   # progress fraction where it errors
    recovers: bool = True       # whether a failure recovers (retry) or sticks
    go_stale: bool = False      # simulate a hung "running" agent
    _hung: bool = False         # once hung, run() stops emitting its heartbeats

    def beat(self) -> Heartbeat:
        return Heartbeat(
            agent_id=self.agent_id, team=self.team, role=self.role,
            model=self.model, status=self.status, task=self.task,
            progress=round(self.progress, 3), tokens=self.tokens,
            context_used=min(0.95, self.tokens / 200_000),
            spend_usd=round(self.spend, 4), retry_count=self.retry_count,
            error=self.error, blocked_on=list(self.blocked_on),
        )


def _fleet() -> list[_Agent]:
    return [
        _Agent("planner", "Recon", "planner", "scope-feature",
               status=Status.RUNNING, rate=0.18),
        _Agent("scan-deps", "Recon", "scanner", "scan-deps",
               status=Status.RUNNING, rate=0.10),
        _Agent("api-schema", "Builder", "coder", "design-api",
               status=Status.RUNNING, rate=0.07),
        _Agent("db-migrate", "Builder", "coder", "db-migrate",
               status=Status.RUNNING, rate=0.06, fails_at=0.4, recovers=True),
        _Agent("integ-test", "Builder", "tester", "integration-tests",
               status=Status.WAITING, blocked_on=["db-migrate"]),
        _Agent("reviewer", "Reviewer", "reviewer", "review-pr",
               status=Status.WAITING, blocked_on=["api-schema"]),
        _Agent("deployer", "Deployer", "ops", "push-prod",
               status=Status.RUNNING, rate=0.08, fails_at=0.5, recovers=False,
               error="rate-limit 429"),
        _Agent("watcher", "Deployer", "ops", "watch-metrics",
               status=Status.RUNNING, rate=0.04, go_stale=True),
    ]


def _advance(a: _Agent, rng: random.Random) -> None:
    if a.status in (Status.DONE, Status.FAILED):
        return

    if a.status == Status.WAITING:
        return  # unblocked externally by _unblock()

    if a.go_stale and a.progress > 0.3:
        a._hung = True  # marked so run() stops emitting -> collector flags stale
        return

    if a.status == Status.RETRYING:
        if rng.random() < 0.5:
            a.status = Status.RUNNING
            a.error = ""
        return

    if a.status == Status.RUNNING:
        a.tokens += rng.randint(2000, 9000)
        a.spend += rng.uniform(0.02, 0.18)
        a.progress += a.rate * rng.uniform(0.6, 1.4)

        if a.fails_at is not None and a.progress >= a.fails_at:
            if a.recovers:
                a.status = Status.RETRYING
                a.retry_count += 1
                a.error = "lock timeout"
                a.fails_at = a.progress + 0.5  # don't fail again immediately
            else:
                a.status = Status.FAILED
            return

        if a.progress >= 1.0:
            a.progress = 1.0
            a.status = Status.DONE


def _unblock(agents: list[_Agent]) -> None:
    done = {a.agent_id for a in agents if a.status == Status.DONE}
    for a in agents:
        if a.status == Status.WAITING and set(a.blocked_on) <= done:
            a.status = Status.RUNNING
            a.blocked_on = []


def run(log_path: str, ticks: int = 0, interval: float = 0.8,
        seed: int = 7) -> None:
    """Drive the synthetic fleet, emitting heartbeats to `log_path`.

    `ticks=0` runs until interrupted. Designed to be launched in a thread.
    """
    rng = random.Random(seed)
    log = EventLog(log_path)
    agents = _fleet()

    n = 0
    while ticks == 0 or n < ticks:
        for a in agents:
            _advance(a, rng)
        _unblock(agents)
        for a in agents:
            if a._hung:
                continue  # hung agent: heartbeats stop -> collector sees staleness
            log.emit(a.beat())
        n += 1
        time.sleep(interval)
