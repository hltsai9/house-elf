"""Notifications for booking events — email (SMTP) with a console fallback.

Same shape as the providers: the booker/CLI depend only on a tiny `Notifier`
surface, so the real channel (SMTP email) and an offline `ConsoleNotifier` are
interchangeable. Nothing here needs a third-party package.

Email config comes from the environment (so secrets stay out of argv):

    SMTP_HOST        smtp.gmail.com           (required for email)
    SMTP_PORT        587                       (465 if SMTP_SSL=1)
    SMTP_USER        you@gmail.com
    SMTP_PASSWORD    app-password
    SMTP_FROM        you@gmail.com             (defaults to SMTP_USER)
    SMTP_SSL         0/1                        (use SMTP_SSL on port 465)
    SMTP_STARTTLS    1/0                        (STARTTLS on 587; default on)
    RESERVATIONS_EMAIL_TO   where alerts go    (or pass --email-to)
"""

from __future__ import annotations

import os
import smtplib
import ssl
import sys
from dataclasses import dataclass
from email.message import EmailMessage
from typing import IO, Protocol, runtime_checkable

from .model import Booking, Offer, Outcome, ReservationRequest


class NotifyError(RuntimeError):
    """Raised when a notifier can't deliver (missing config, SMTP error)."""


@runtime_checkable
class Notifier(Protocol):
    """The only surface the booker/CLI depend on."""

    def send(self, subject: str, body: str) -> bool:
        """Deliver one message. Returns True if sent, False if a no-op."""
        ...


# --------------------------------------------------------------------------- #
# Message composition — shared by every notifier so the wording is consistent.
# --------------------------------------------------------------------------- #

def _venue_block(venue: dict) -> str:
    return (f"{venue['name']}\n"
            f"{venue['address']} ({venue['inside']})\n"
            f"☎ {venue['phone']}\n"
            f"Reserve:  {venue['yelp_reservations']}\n"
            f"Waitlist: {venue['yelp_waitlist']}")


def compose_openings(offers: list[Offer], req: ReservationRequest,
                     venue: dict) -> tuple[str, str]:
    """A 'slots just opened' alert — what the user asked for."""
    times = ", ".join(o.hhmm for o in offers)
    subject = (f"🍜 {len(offers)} table(s) open at {venue['name']} "
               f"— {req.day:%a %b %d}")
    body = (
        f"A reservation slot just opened up.\n\n"
        f"  Date:   {req.day:%A, %B %d %Y}\n"
        f"  Party:  {req.party_size}\n"
        f"  Times:  {times}\n"
        f"  Window: {req.earliest:%H:%M}–{req.latest:%H:%M}\n\n"
        f"Grab it fast — these go in seconds:\n  {venue['yelp_reservations']}\n\n"
        f"{_venue_block(venue)}\n"
    )
    return subject, body


def compose_booking(b: Booking, req: ReservationRequest,
                    venue: dict) -> tuple[str, str]:
    """A confirmation/waitlist/failure notice for a completed attempt."""
    if b.outcome is Outcome.CONFIRMED and b.when:
        subject = f"✅ Reserved {b.party_size} at {venue['name']} — {b.when:%a %b %d %H:%M}"
        head = (f"Your table is booked!\n\n"
                f"  When:  {b.when:%A, %B %d %Y at %H:%M}\n"
                f"  Party: {b.party_size}\n"
                f"  Conf#: {b.confirmation_id}\n")
    elif b.outcome is Outcome.WAITLISTED:
        wait = f"~{b.quoted_wait_min} min" if b.quoted_wait_min else "unknown"
        pos = f"#{b.waitlist_position} in line" if b.waitlist_position else "position n/a"
        subject = f"⏳ Waitlisted at {venue['name']} — {req.day:%a %b %d}"
        head = (f"No open table — you're on the waitlist.\n\n"
                f"  Date:  {req.day:%A, %B %d %Y}\n"
                f"  Party: {req.party_size}\n"
                f"  Quote: {wait} ({pos})\n"
                f"  Ref#:  {b.confirmation_id}\n"
                f"  Note:  the whole party must be present to be seated.\n")
    else:
        subject = f"⚠ Couldn't book {venue['name']} — {req.day:%a %b %d}"
        head = (f"The booking attempt didn't land.\n\n"
                f"  Outcome: {b.outcome.value}\n"
                f"  Detail:  {b.detail or 'n/a'}\n")
    return subject, head + "\n" + _venue_block(venue) + "\n"


# --------------------------------------------------------------------------- #
# Notifiers
# --------------------------------------------------------------------------- #

class NullNotifier:
    """Does nothing — the default when no channel is requested."""

    def send(self, subject: str, body: str) -> bool:
        return False


@dataclass
class ConsoleNotifier:
    """Prints the message instead of sending it — offline dry-run channel."""

    stream: IO[str] | None = None

    def send(self, subject: str, body: str) -> bool:
        out = self.stream or sys.stderr
        print(f"\n📧 [notification] {subject}\n{body}", file=out)
        return True


def _envbool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class EmailNotifier:
    """Sends real email over SMTP (stdlib `smtplib`)."""

    to: str
    host: str = ""
    port: int = 587
    user: str = ""
    password: str = ""
    sender: str = ""
    use_ssl: bool = False
    use_starttls: bool = True
    timeout: float = 15.0

    @classmethod
    def from_env(cls, to: str = "") -> "EmailNotifier":
        return cls(
            to=to or os.environ.get("RESERVATIONS_EMAIL_TO", ""),
            host=os.environ.get("SMTP_HOST", ""),
            port=int(os.environ.get("SMTP_PORT", "587")),
            user=os.environ.get("SMTP_USER", ""),
            password=os.environ.get("SMTP_PASSWORD", ""),
            sender=os.environ.get("SMTP_FROM", "") or os.environ.get("SMTP_USER", ""),
            use_ssl=_envbool("SMTP_SSL", False),
            use_starttls=_envbool("SMTP_STARTTLS", True),
        )

    def _require(self) -> None:
        missing = [name for name, val in
                   (("SMTP_HOST", self.host), ("a recipient (--email-to / RESERVATIONS_EMAIL_TO)", self.to))
                   if not val]
        if missing:
            raise NotifyError(
                "email notifications need " + " and ".join(missing) +
                ". Set the SMTP_* env vars, or use --notify console to print "
                "alerts instead of emailing them.")

    def _build(self, subject: str, body: str) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.sender or self.user or self.to
        msg["To"] = self.to
        msg.set_content(body)
        return msg

    def send(self, subject: str, body: str) -> bool:
        self._require()
        msg = self._build(subject, body)
        try:
            if self.use_ssl:
                ctx = ssl.create_default_context()
                with smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout,
                                      context=ctx) as s:
                    if self.user:
                        s.login(self.user, self.password)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as s:
                    if self.use_starttls:
                        s.starttls(context=ssl.create_default_context())
                    if self.user:
                        s.login(self.user, self.password)
                    s.send_message(msg)
        except (smtplib.SMTPException, OSError) as e:
            raise NotifyError(f"SMTP send failed via {self.host}:{self.port}: {e}") from e
        return True


def get_notifier(kind: str, to: str = "") -> Notifier:
    """Factory used by the CLI: 'none' (default), 'console', or 'email'."""
    kind = (kind or "none").lower()
    if kind in ("none", "off"):
        return NullNotifier()
    if kind == "console":
        return ConsoleNotifier()
    if kind == "email":
        return EmailNotifier.from_env(to=to)
    raise ValueError(f"unknown notifier {kind!r} (expected none|console|email)")
