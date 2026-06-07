"""Live TUI refresh loop. Reads the event log, derives state, redraws.

Pure stdlib: ANSI to clear the screen and hide the cursor, a periodic
read-derive-render tick. The renderer is a pure function, so this loop stays
dumb on purpose — all the intelligence lives in the collector.
"""

from __future__ import annotations

import os
import sys
import time

from .collector import EventLog, derive
from .render import render

_CLEAR = "\033[2J\033[H"     # clear screen + home cursor
_HIDE = "\033[?25l"
_SHOW = "\033[?25h"


def watch(log_path: str, interval: float = 1.0, budget_usd: float = 20.0,
          once: bool = False, color: bool | None = None) -> None:
    """Render the dashboard from `log_path` every `interval` seconds."""
    if color is None:
        color = sys.stdout.isatty()
    log = EventLog(log_path)

    if once:
        state = derive(log.read_all(), budget_usd=budget_usd)
        print(render(state, color=color, budget_usd=budget_usd))
        return

    if color:
        sys.stdout.write(_HIDE)
    try:
        while True:
            state = derive(log.read_all(), budget_usd=budget_usd)
            frame = render(state, color=color, budget_usd=budget_usd)
            sys.stdout.write(_CLEAR + frame + "\n")
            sys.stdout.write(
                f"\n  refresh {interval:.1f}s · {len(state.agents)} agents · "
                f"Ctrl-C to exit\n")
            sys.stdout.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        if color:
            sys.stdout.write(_SHOW + "\n")
            sys.stdout.flush()
