"""Domain model for restaurant reservations.

Small, provider-agnostic vocabulary shared by the booker, the providers, and
the CLI. A *request* is what the guest wants; an *offer* is a concrete bookable
slot a provider returned; a *booking* is a confirmed offer; a *waitlist entry*
is the fallback when nothing is bookable.

Pure stdlib, dataclasses only — same spirit as the rest of house-elf.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, asdict
from datetime import date as _date, datetime, time as _time
from enum import Enum
from typing import Any


class Outcome(str, Enum):
    """How a booking attempt resolved."""

    CONFIRMED = "CONFIRMED"     # a table is reserved
    WAITLISTED = "WAITLISTED"   # no slot; we joined the remote waitlist instead
    UNAVAILABLE = "UNAVAILABLE" # nothing bookable and waitlist closed/declined
    FAILED = "FAILED"           # the provider errored


_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def parse_time(s: str) -> _time:
    """Parse 'HH:MM' (24h) into a time. Raises ValueError on junk."""
    m = _TIME_RE.match(s.strip())
    if not m:
        raise ValueError(f"expected HH:MM (24-hour), got {s!r}")
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ValueError(f"time out of range: {s!r}")
    return _time(hour=h, minute=mi)


def parse_date(s: str) -> _date:
    """Parse 'YYYY-MM-DD' into a date. Raises ValueError on junk."""
    return datetime.strptime(s.strip(), "%Y-%m-%d").date()


@dataclass
class Guest:
    """Who the table is for. Yelp Guest Manager requires name + phone."""

    name: str
    phone: str
    email: str = ""

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("guest name is required")
        digits = re.sub(r"\D", "", self.phone)
        if len(digits) < 10:
            raise ValueError(f"phone must have at least 10 digits, got {self.phone!r}")


@dataclass
class ReservationRequest:
    """What the guest wants.

    `earliest`/`latest` bound the acceptable seating window on `day`; the booker
    picks the offered slot closest to `preferred` (defaults to `earliest`).
    """

    party_size: int
    day: _date
    earliest: _time
    latest: _time
    guest: Guest
    preferred: _time | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if self.party_size < 1:
            raise ValueError("party_size must be >= 1")
        if self.latest < self.earliest:
            raise ValueError("latest seating time is before earliest")
        if self.preferred is None:
            self.preferred = self.earliest
        self.guest.validate()

    def accepts(self, t: _time) -> bool:
        """Is a slot at time `t` within the guest's window?"""
        return self.earliest <= t <= self.latest

    @classmethod
    def of(cls, party_size: int, day: str, earliest: str, latest: str,
           name: str, phone: str, email: str = "", preferred: str | None = None,
           notes: str = "") -> "ReservationRequest":
        """Convenience constructor from CLI-style strings."""
        return cls(
            party_size=party_size,
            day=parse_date(day),
            earliest=parse_time(earliest),
            latest=parse_time(latest),
            preferred=parse_time(preferred) if preferred else None,
            guest=Guest(name=name, phone=phone, email=email),
            notes=notes,
        )


@dataclass(frozen=True)
class Offer:
    """A concrete bookable slot returned by a provider."""

    when: datetime           # local seating date+time
    party_size: int
    provider: str            # which provider surfaced this
    hold_token: str = ""     # opaque token a provider may need to confirm

    @property
    def hhmm(self) -> str:
        return self.when.strftime("%H:%M")


@dataclass
class Booking:
    """A confirmed reservation (or the result of trying)."""

    outcome: Outcome
    provider: str
    when: datetime | None = None
    party_size: int = 0
    confirmation_id: str = ""
    # waitlist-only fields
    quoted_wait_min: int | None = None
    waitlist_position: int | None = None
    # diagnostics
    detail: str = ""
    ts: float = field(default_factory=time.time)

    @property
    def ok(self) -> bool:
        return self.outcome in (Outcome.CONFIRMED, Outcome.WAITLISTED)

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["outcome"] = self.outcome.value
        d["when"] = self.when.isoformat() if self.when else None
        return d

    def summary(self) -> str:
        if self.outcome is Outcome.CONFIRMED and self.when:
            return (f"✔ Reserved {self.party_size} @ {self.when:%a %b %d %H:%M} "
                    f"· {self.provider} · conf {self.confirmation_id}")
        if self.outcome is Outcome.WAITLISTED:
            wait = f"~{self.quoted_wait_min} min" if self.quoted_wait_min else "unknown wait"
            pos = f", #{self.waitlist_position} in line" if self.waitlist_position else ""
            return f"⏸ Waitlisted ({wait}{pos}) · {self.provider} · {self.confirmation_id}"
        if self.outcome is Outcome.UNAVAILABLE:
            return f"○ No table available · {self.provider}" + (f" · {self.detail}" if self.detail else "")
        return f"✖ Booking failed · {self.provider}" + (f" · {self.detail}" if self.detail else "")
