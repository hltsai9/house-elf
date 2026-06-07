# Mission Control

An ASCII dashboard to oversee teams of AI agents — fleet health at a glance,
drill in only when something blinks red. Pure Python stdlib, zero installs.

```
╔══════════════════════════════════════════════════════════════════════════════╗
║ MISSION CONTROL · house-elf                          ⏱ 19:48:42   ▲ 2 alerts ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ FLEET  ● 2 run   ⏸ 1 wait   ○ 0 idle   ✔ 3 done   ✖ 1 fail   ◐ 1 retry       ║
║ SPEND  $ 6.11 / $20.00  ███████░░░░░░░░░░░░░░░░░                🔥 39.14$/min ║
╚══════════════════════════════════════════════════════════════════════════════╝
◢ Builder   (3 agents)
  ✔ api-schema     DONE     design-api     ██████ 81k    now
  ◐ db-migrate     RETRYING db-migrate     ██████ 73k    now        ↻ 2
  ⏸ integ-test     WAITING  integration-t… ░░░░░░ 0      now        ⤷ db-migrate
◢ Deployer   (2 agents)
  ✖ deployer       FAILED   push-prod      ███░░░ 34k    now        rate-limit 429
  ● watcher        RUNNING  watch-metrics  ██░░░░ 46k    26s ago    ⚠ stale
...
▲ ALERTS
  ✖ deployer failed (rate-limit 429)
  ⚠ watcher stale — RUNNING but silent 26s
  🔥 high burn 39.14$/min
```

## Try it

**In your browser** (recommended):

```bash
python -m mission_control web --demo     # opens http://127.0.0.1:8000
```

**In the terminal:**

```bash
python -m mission_control demo           # synthetic fleet + live ASCII dashboard
```

`Ctrl-C` to exit either one. The demo runs a simulated 8-agent / 4-team fleet that
exercises every state: a task DAG that unblocks as upstreams finish, a transient
failure that retries, a hard failure (rate-limit), a hung agent that goes stale,
and a high burn-rate alert.

## Status vocabulary

| glyph | status   | meaning                                            |
|-------|----------|----------------------------------------------------|
| `●`   | RUNNING  | actively working / streaming tokens                |
| `⏸`   | WAITING  | blocked on a dependency, lock, or human approval   |
| `○`   | IDLE     | alive, no task assigned                            |
| `✔`   | DONE     | completed successfully                             |
| `✖`   | FAILED   | errored / crashed / timed out                      |
| `◐`   | RETRYING | transient failure, backing off                     |

## Architecture

Three decoupled layers. Agents only *emit*; the collector owns truth; the
renderer is a pure function of state (so a web UI could reuse it untouched).

```
  agents ──heartbeat──▶ collector (append-only JSONL + derived state) ──▶ renderer (ASCII)
```

| module         | role                                                            |
|----------------|-----------------------------------------------------------------|
| `schema.py`    | the `Heartbeat` agents emit + the status enum                   |
| `collector.py` | event log + derived intelligence: staleness, burn, rollups, alerts |
| `render.py`    | pure `FleetState -> str` (fleet table, spend bar, alerts)       |
| `dashboard.py` | live TUI refresh loop (ANSI, stdlib)                            |
| `simulator.py` | synthetic fleet for the demo                                   |

**The intelligence is in the collector, not the renderer.** What makes this a
monitor and not a pretty log viewer:

- **Staleness** — an agent that claims `RUNNING` but hasn't checked in for
  `STALE_AFTER_S` is flagged. A heartbeat that *stops* is more alarming than an
  explicit error (it means the agent hung).
- **Burn rate** — USD/min from spend deltas; spikes catch tool-call loops before
  they drain the budget.
- **Dependency alerts** — long-blocked waiters surface their `blocked_on`, so the
  bottleneck on the critical path is obvious.

## Wiring in real agents

Agents are fire-and-forget producers. Anything that can write a line of JSON can
feed the dashboard:

```python
from mission_control import EventLog, Heartbeat, Status

log = EventLog("fleet.jsonl")     # shared path the dashboard reads

# call this whenever your agent changes state or makes a tool call
log.emit(Heartbeat(
    agent_id="db-migrate",
    team="Builder",
    status=Status.RETRYING,
    task="db-migrate",
    progress=0.6,
    tokens=88_000,
    spend_usd=1.12,
    retry_count=2,
    error="lock timeout",
    blocked_on=[],
))
```

Then point the dashboard at that log:

```bash
python -m mission_control view fleet.jsonl --budget 20
```

The log is plain JSONL, so a non-Python agent can just append
`{"agent_id": "...", "status": "RUNNING", ...}` lines directly — no dependency on
this package.

## CLI

```
python -m mission_control web  [LOG] [--demo --port 8000 --budget N --no-open]
python -m mission_control demo                 # simulate + watch (terminal)
python -m mission_control view LOG  [--budget N --interval S --no-color]
python -m mission_control snapshot LOG         # render one frame and exit (pipe-able)
python -m mission_control simulate LOG         # only emit synthetic heartbeats
```

The web dashboard (`web.py`) reuses the same collector — the browser polls
`/api/state` (derived `FleetState` as JSON) once a second and draws it. It shows
an animated **floor** where each agent is an avatar (by role): it wanders while
`RUNNING`, eases to a *waiting* bench when blocked, shakes while `RETRYING`,
lines up in the *done* column when finished, and drops to the *failed* strip on
error. To watch real agents: `python -m mission_control web fleet.jsonl`.

## Tests

```bash
python -m unittest mission_control.tests.test_collector
```

## Roadmap

The leap from *dashboard* to *mission control* is the ability to **act** on what
you see. Natural next steps:

1. **Team / DAG view** — render the per-team dependency graph (who blocks whom).
2. **Agent cockpit** — drill into one agent's live transcript + recent tool calls.
3. **Control actions** — pause / resume / kill / re-prompt an agent from the UI.
4. **Transports** — swap the JSONL log for a Redis stream or SQLite for multi-host
   fleets; the collector interface stays the same.
