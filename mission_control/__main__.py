"""CLI entry point.

  python -m mission_control demo            # synthetic fleet + live dashboard
  python -m mission_control view LOG        # watch a real heartbeat log
  python -m mission_control snapshot LOG    # render one frame and exit
  python -m mission_control simulate LOG    # only emit synthetic heartbeats
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading

from . import dashboard, simulator


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mission_control")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo", help="run synthetic fleet + live dashboard")
    d.add_argument("--interval", type=float, default=1.0)
    d.add_argument("--budget", type=float, default=20.0)
    d.add_argument("--no-color", action="store_true")

    v = sub.add_parser("view", help="watch a real heartbeat log")
    v.add_argument("log")
    v.add_argument("--interval", type=float, default=1.0)
    v.add_argument("--budget", type=float, default=20.0)
    v.add_argument("--no-color", action="store_true")

    s = sub.add_parser("snapshot", help="render one frame and exit")
    s.add_argument("log")
    s.add_argument("--budget", type=float, default=20.0)
    s.add_argument("--no-color", action="store_true")

    sim = sub.add_parser("simulate", help="emit synthetic heartbeats to a log")
    sim.add_argument("log")
    sim.add_argument("--ticks", type=int, default=0)
    sim.add_argument("--interval", type=float, default=0.8)

    w = sub.add_parser("web", help="serve the dashboard as a web page")
    w.add_argument("log", nargs="?", help="heartbeat log to watch (omit with --demo)")
    w.add_argument("--demo", action="store_true",
                   help="run the synthetic fleet too (no real agents needed)")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8000)
    w.add_argument("--budget", type=float, default=20.0)
    w.add_argument("--no-open", action="store_true",
                   help="don't auto-open a browser")

    args = p.parse_args(argv)
    color = None if not getattr(args, "no_color", False) else False

    if args.cmd == "demo":
        log_path = os.path.join(tempfile.mkdtemp(prefix="mc-"), "fleet.jsonl")
        t = threading.Thread(
            target=simulator.run, args=(log_path,),
            kwargs={"interval": 0.8}, daemon=True)
        t.start()
        dashboard.watch(log_path, interval=args.interval,
                        budget_usd=args.budget, color=color)
        return 0

    if args.cmd == "view":
        dashboard.watch(args.log, interval=args.interval,
                        budget_usd=args.budget, color=color)
        return 0

    if args.cmd == "snapshot":
        dashboard.watch(args.log, budget_usd=args.budget, once=True, color=color)
        return 0

    if args.cmd == "simulate":
        simulator.run(args.log, ticks=args.ticks, interval=args.interval)
        return 0

    if args.cmd == "web":
        from . import web
        log_path = args.log
        if args.demo or not log_path:
            log_path = os.path.join(tempfile.mkdtemp(prefix="mc-"), "fleet.jsonl")
            threading.Thread(
                target=simulator.run, args=(log_path,),
                kwargs={"interval": 0.8}, daemon=True).start()
        web.serve(log_path, host=args.host, port=args.port,
                  budget_usd=args.budget, open_browser=not args.no_open)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
