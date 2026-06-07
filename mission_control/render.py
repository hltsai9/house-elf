"""Pure state -> ASCII rendering. No I/O, no timers — just strings.

Keeping this a pure function of `FleetState` means the same renderer feeds a
live TUI today and could feed a web view or a Slack snapshot tomorrow.
"""

from __future__ import annotations

import time

from .collector import AgentState, FleetState
from .schema import Status

# ── ANSI colors (graceful: pass color=False for plain text / pipes) ──────────
_C = {
    "reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m",
    "green": "\033[32m", "yellow": "\033[33m", "red": "\033[31m",
    "cyan": "\033[36m", "grey": "\033[90m", "magenta": "\033[35m",
}

_STATUS_COLOR = {
    Status.RUNNING: "green",
    Status.WAITING: "yellow",
    Status.IDLE: "grey",
    Status.DONE: "cyan",
    Status.FAILED: "red",
    Status.RETRYING: "magenta",
}

WIDTH = 80


def _paint(s: str, color: str, on: bool) -> str:
    if not on or color not in _C:
        return s
    return f"{_C[color]}{s}{_C['reset']}"


def _fit(s: str, w: int) -> str:
    """Pad or truncate to exactly `w` visible chars (ANSI-free input)."""
    if len(s) > w:
        return s[: max(0, w - 1)] + "…"
    return s.ljust(w)


def _bar(frac: float, w: int, fill: str = "█", empty: str = "░") -> str:
    frac = max(0.0, min(1.0, frac))
    n = round(frac * w)
    return fill * n + empty * (w - n)


def _fmt_age(s: float) -> str:
    if s < 1:
        return "now"
    if s < 60:
        return f"{s:.0f}s ago"
    if s < 3600:
        return f"{s / 60:.0f}m ago"
    return f"{s / 3600:.1f}h ago"


def render(state: FleetState, color: bool = True, budget_usd: float = 0.0) -> str:
    lines: list[str] = []
    lines += _header(state, color, budget_usd)
    for team, agents in state.by_team().items():
        lines += _team_block(team, agents, color)
    lines += _alerts_block(state, color)
    return "\n".join(lines)


def _header(state: FleetState, color: bool, budget: float) -> list[str]:
    c = state.counts
    clock = time.strftime("%H:%M:%S", time.localtime(state.now))
    title = _paint("MISSION CONTROL · house-elf", "bold", color)
    n_alerts = len(state.alerts)
    alert_tag = _paint(f"▲ {n_alerts} alerts", "red" if n_alerts else "grey", color)

    top = "╔" + "═" * (WIDTH - 2) + "╗"
    bot = "╚" + "═" * (WIDTH - 2) + "╝"
    sep = "╠" + "═" * (WIDTH - 2) + "╣"

    def row(left: str, right: str = "") -> str:
        inner = WIDTH - 4
        pad = inner - _vis_len(left) - _vis_len(right)
        return "║ " + left + " " * max(1, pad) + right + " ║"

    fleet = (
        f"{_paint('●', 'green', color)} {c[Status.RUNNING]} run   "
        f"{_paint('⏸', 'yellow', color)} {c[Status.WAITING]} wait   "
        f"{_paint('○', 'grey', color)} {c[Status.IDLE]} idle   "
        f"{_paint('✔', 'cyan', color)} {c[Status.DONE]} done   "
        f"{_paint('✖', 'red', color)} {c[Status.FAILED]} fail   "
        f"{_paint('◐', 'magenta', color)} {c[Status.RETRYING]} retry"
    )

    spend_line = []
    if budget:
        frac = state.total_spend / budget if budget else 0.0
        bar = _bar(frac, 24)
        bcolor = "red" if frac > 0.9 else "yellow" if frac > 0.6 else "green"
        spend_line = [row(
            f"SPEND  ${state.total_spend:5.2f} / ${budget:5.2f}  {_paint(bar, bcolor, color)}",
            f"🔥 {state.burn_usd_per_min:.2f}$/min",
        )]
    else:
        spend_line = [row(
            f"SPEND  ${state.total_spend:5.2f}",
            f"🔥 {state.burn_usd_per_min:.2f}$/min",
        )]

    return [
        top,
        row(title, f"⏱ {clock}   {alert_tag}"),
        sep,
        row("FLEET  " + fleet),
        *spend_line,
        bot,
    ]


def _team_block(team: str, agents: list[AgentState], color: bool) -> list[str]:
    lines = [_paint(f"◢ {team}", "bold", color) + _paint(
        f"   ({len(agents)} agents)", "dim", color)]
    for a in agents:
        lines.append(_agent_row(a, color))
    lines.append("")
    return lines


def _agent_row(a: AgentState, color: bool) -> str:
    hb = a.hb
    col = _STATUS_COLOR[hb.status]
    glyph = _paint(hb.status.glyph, col, color)
    name = _fit(hb.agent_id, 14)
    status = _fit(hb.status.value, 8)
    task = _fit(hb.task or hb.step or "—", 14)
    bar = _bar(hb.progress, 6)
    toks = _fit(_fmt_tokens(hb.tokens), 6)
    age = _fit(_fmt_age(a.age_s), 9)

    tail = ""
    if a.stale:
        tail = _paint("  ⚠ stale", "red", color)
    elif hb.status == Status.RETRYING:
        tail = _paint(f"  ↻ {hb.retry_count}", "magenta", color)
    elif hb.status == Status.WAITING and hb.blocked_on:
        tail = _paint("  ⤷ " + ",".join(hb.blocked_on)[:18], "yellow", color)
    elif hb.status == Status.FAILED and hb.error:
        tail = _paint("  " + hb.error[:22], "red", color)

    return (f"  {glyph} {name} {_paint(status, col, color)} "
            f"{task} {bar} {toks} {_paint(age, 'dim', color)}{tail}")


def _alerts_block(state: FleetState, color: bool) -> list[str]:
    if not state.alerts:
        return [_paint("▲ no alerts — fleet nominal", "green", color)]
    out = [_paint("▲ ALERTS", "bold", color)]
    for al in state.alerts:
        c = "red" if al[0] in "✖🔥💸" else "yellow"
        out.append("  " + _paint(al, c, color))
    return out


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M"
    if n >= 1_000:
        return f"{n / 1e3:.0f}k"
    return str(n)


def _vis_len(s: str) -> int:
    """Visible length, ignoring ANSI escapes."""
    out, i = 0, 0
    while i < len(s):
        if s[i] == "\033":
            j = s.find("m", i)
            i = (j + 1) if j != -1 else i + 1
            continue
        out += 1
        i += 1
    return out
