"""Mission Control — an ASCII dashboard to oversee AI agent teams.

Layers (decoupled on purpose):
  schema     — the heartbeat agents emit
  collector  — append-only log + derived state (staleness, burn, alerts)
  render     — pure state -> ASCII
  dashboard  — live TUI loop
  simulator  — synthetic fleet for demos
"""

from .schema import Heartbeat, Status
from .collector import EventLog, FleetState, derive

__all__ = ["Heartbeat", "Status", "EventLog", "FleetState", "derive"]
