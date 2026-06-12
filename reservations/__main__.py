"""CLI for the reservations tool.

  # what's open in my window? (offline mock by default)
  python -m reservations check --party 2 --day 2026-07-04 --from 18:00 --to 20:00

  # book the best slot, fall back to the waitlist if nothing's open
  python -m reservations book  --party 2 --day 2026-07-04 --from 18:00 --to 20:00 \
      --name "Ada Lovelace" --phone 480-555-0100

  # camp on a midnight drop and grab the first slot that appears
  python -m reservations watch --party 4 --day 2026-07-04 --from 18:00 --to 19:30 \
      --name "Ada Lovelace" --phone 480-555-0100 --attempts 30 --interval 1

  # join the remote waitlist directly
  python -m reservations waitlist --party 2 --day 2026-07-04 --from 17:00 --to 21:00 \
      --name "Ada Lovelace" --phone 480-555-0100

Use --provider yelp (with YELP_API_KEY) to hit the real Yelp Guest Manager
backend that powers DTF Scottsdale; the default 'mock' provider runs offline.
"""

from __future__ import annotations

import argparse
import sys

from .booker import Booker, DTF_SCOTTSDALE
from .model import Outcome, ReservationRequest
from .notify import (
    NotifyError, NullNotifier, compose_booking, compose_openings, get_notifier,
)
from .providers import get_provider


def _add_request_args(p: argparse.ArgumentParser, *, need_guest: bool) -> None:
    p.add_argument("--party", type=int, required=True, help="party size")
    p.add_argument("--day", required=True, help="seating date YYYY-MM-DD")
    p.add_argument("--from", dest="earliest", required=True, help="earliest time HH:MM")
    p.add_argument("--to", dest="latest", required=True, help="latest time HH:MM")
    p.add_argument("--at", dest="preferred", help="preferred time HH:MM (default: --from)")
    p.add_argument("--notes", default="", help="reservation notes")
    p.add_argument("--name", required=need_guest, help="guest name")
    p.add_argument("--phone", required=need_guest, help="guest phone")
    p.add_argument("--email", default="", help="guest email")
    p.add_argument("--provider", default="mock", choices=["mock", "yelp"],
                   help="booking backend (default: mock, offline)")
    p.add_argument("--notify", default="none", choices=["none", "console", "email"],
                   help="notify on a found/booked slot (email needs SMTP_* env)")
    p.add_argument("--email-to", default="",
                   help="recipient for --notify email (or set RESERVATIONS_EMAIL_TO)")


def _build_request(args) -> ReservationRequest:
    return ReservationRequest.of(
        party_size=args.party, day=args.day, earliest=args.earliest,
        latest=args.latest, preferred=getattr(args, "preferred", None),
        name=getattr(args, "name", None) or "Walk-in Guest",
        phone=getattr(args, "phone", None) or "480-000-0000",
        email=getattr(args, "email", ""), notes=args.notes)


def _build_notifier(args):
    return get_notifier(getattr(args, "notify", "none"),
                        to=getattr(args, "email_to", ""))


def _deliver(notifier, subject_body) -> None:
    """Send a composed (subject, body), turning config errors into a warning."""
    if isinstance(notifier, NullNotifier):
        return
    try:
        if notifier.send(*subject_body):
            print(f"  📧 notified: {subject_body[0]}", file=sys.stderr)
    except NotifyError as e:
        print(f"  ⚠ notification skipped: {e}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="reservations",
                                description="Book a table at Din Tai Fung Scottsdale.")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="list open slots in your window")
    _add_request_args(c, need_guest=False)

    b = sub.add_parser("book", help="reserve best slot, else join the waitlist")
    _add_request_args(b, need_guest=True)
    b.add_argument("--no-waitlist", action="store_true",
                   help="don't fall back to the waitlist if nothing's open")

    w = sub.add_parser("watch", help="poll for a drop and grab the first slot")
    _add_request_args(w, need_guest=True)
    w.add_argument("--attempts", type=int, default=20)
    w.add_argument("--interval", type=float, default=2.0, help="initial poll seconds")
    w.add_argument("--no-waitlist", action="store_true")
    w.add_argument("--alert-only", action="store_true",
                   help="notify when a slot appears but don't book it")

    wl = sub.add_parser("waitlist", help="join the remote waitlist directly")
    _add_request_args(wl, need_guest=True)

    info = sub.add_parser("info", help="print DTF Scottsdale booking facts")

    args = p.parse_args(argv)

    if args.cmd == "info":
        for k, v in DTF_SCOTTSDALE.items():
            print(f"{k:18} {v}")
        return 0

    provider = get_provider(args.provider)
    notifier = _build_notifier(args)
    try:
        req = _build_request(args)
    except ValueError as e:
        print(f"✖ bad request: {e}", file=sys.stderr)
        return 2

    if args.cmd == "check":
        offers = provider.find_openings(req)
        if not offers:
            print(f"○ No open slots for {req.party_size} on {req.day} "
                  f"between {args.earliest} and {args.latest} ({provider.name}).")
            print(f"  Tip: {DTF_SCOTTSDALE['notes']}")
            return 1
        print(f"● {len(offers)} open slot(s) for {req.party_size} on {req.day} "
              f"({provider.name}):")
        for o in offers:
            print(f"    {o.hhmm}  ({o.party_size} seats)")
        _deliver(notifier, compose_openings(offers, req, DTF_SCOTTSDALE))
        return 0

    if args.cmd == "book":
        booking = Booker(provider, fallback_to_waitlist=not args.no_waitlist).book(req)
        print(booking.summary())
        if booking.ok:
            _deliver(notifier, compose_booking(booking, req, DTF_SCOTTSDALE))
        return 0 if booking.ok else 1

    if args.cmd == "watch":
        def on_poll(i, n, msg):
            print(f"  [{i}/{n}] {msg}", file=sys.stderr)
        booker = Booker(provider, fallback_to_waitlist=not args.no_waitlist)
        if args.alert_only:
            offers = booker.alert_when_open(
                req, attempts=args.attempts, interval_s=args.interval, on_poll=on_poll)
            if not offers:
                print(f"○ No slot appeared after {args.attempts} checks ({provider.name}).")
                return 1
            print(f"● {len(offers)} slot(s) opened: "
                  f"{', '.join(o.hhmm for o in offers)} ({provider.name})")
            _deliver(notifier, compose_openings(offers, req, DTF_SCOTTSDALE))
            return 0
        booking = booker.watch(req, attempts=args.attempts, interval_s=args.interval,
                               on_poll=on_poll)
        print(booking.summary())
        if booking.ok:
            _deliver(notifier, compose_booking(booking, req, DTF_SCOTTSDALE))
        return 0 if booking.ok else 1

    if args.cmd == "waitlist":
        booking = provider.join_waitlist(req)
        print(booking.summary())
        if booking.ok:
            _deliver(notifier, compose_booking(booking, req, DTF_SCOTTSDALE))
        return 0 if booking.outcome is Outcome.WAITLISTED else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
