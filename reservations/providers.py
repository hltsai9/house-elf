"""Booking providers.

A *provider* knows how to talk to one reservation backend. The booker only
depends on the `Provider` protocol, so swapping backends never touches the
orchestration logic — the same split the dashboard uses between collector and
renderer.

Two providers ship here:

  * `MockProvider`   — deterministic, offline, dependency-free. The default, so
                       the tool (and its tests) run anywhere with no network and
                       no credentials. It models the real-world DTF situation:
                       prime weekend slots are gone, off-peak weekday slots open
                       up, and there's always a waitlist.
  * `YelpProvider`   — a faithful sketch of the real integration. DTF Scottsdale
                       runs on Yelp Guest Manager; both its reservations page
                       (yelp.com/reservations/din-tai-fung-scottsdale-5) and its
                       waitlist (yelp.com/waitlist/...) are Yelp-backed. This
                       provider targets Yelp's partner reservation endpoints with
                       a `YELP_API_KEY`. Partner access is gated by Yelp, so it
                       fails loudly with guidance rather than pretending to book.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from .model import Booking, Offer, Outcome, ReservationRequest


@runtime_checkable
class Provider(Protocol):
    """The only surface the booker depends on."""

    name: str

    def find_openings(self, req: ReservationRequest) -> list[Offer]:
        """Return bookable slots on req.day within [earliest, latest]."""
        ...

    def reserve(self, req: ReservationRequest, offer: Offer) -> Booking:
        """Confirm a specific offer into a Booking."""
        ...

    def join_waitlist(self, req: ReservationRequest) -> Booking:
        """Fallback: join the remote waitlist for req.day."""
        ...


# --------------------------------------------------------------------------- #
# Mock provider — deterministic, offline, the default.
# --------------------------------------------------------------------------- #

# DTF seats on the half hour across lunch + dinner.
_SERVICE_HALF_HOURS = list(range(11 * 2, 14 * 2 + 1)) + list(range(17 * 2, 21 * 2 + 1))


class MockProvider:
    """A believable, seedable stand-in for a real reservation backend.

    Availability is a pure function of (date, time, party_size) via a hash, so
    results are stable across runs and easy to assert on in tests. It mimics the
    DTF reality: weekends and 6–8pm are almost always full; large parties are
    harder; off-peak weekday slots open up.
    """

    name = "mock"

    def __init__(self, seed: str = "dtf-scottsdale", fill_pressure: float = 0.78):
        # fill_pressure in [0,1): higher = fewer open tables (DTF ~ 0.78).
        self.seed = seed
        self.fill_pressure = fill_pressure

    def _roll(self, *parts: object) -> float:
        raw = "|".join([self.seed, *map(str, parts)])
        h = hashlib.sha256(raw.encode()).hexdigest()
        return int(h[:8], 16) / 0xFFFFFFFF  # 0.0 .. 1.0

    def _is_open(self, when: datetime, party_size: int) -> bool:
        pressure = self.fill_pressure
        if when.weekday() >= 4:                 # Fri/Sat/Sun: brutal
            pressure += 0.15
        if 18 <= when.hour <= 20:               # dinner prime time
            pressure += 0.12
        if party_size >= 5:                     # big tables are scarcer
            pressure += 0.08
        return self._roll(when.date(), when.hour, when.minute, party_size) > pressure

    def find_openings(self, req: ReservationRequest) -> list[Offer]:
        offers: list[Offer] = []
        for half in _SERVICE_HALF_HOURS:
            when = datetime.combine(
                req.day, datetime.min.time()).replace(hour=half // 2,
                                                       minute=(half % 2) * 30)
            if not req.accepts(when.time()):
                continue
            if self._is_open(when, req.party_size):
                token = f"mock-hold-{int(self._roll(when, 'h') * 1e6):06d}"
                offers.append(Offer(when=when, party_size=req.party_size,
                                    provider=self.name, hold_token=token))
        return offers

    def reserve(self, req: ReservationRequest, offer: Offer) -> Booking:
        conf = f"DTF-{int(self._roll(offer.when, req.guest.phone) * 1e8):08d}"
        return Booking(
            outcome=Outcome.CONFIRMED,
            provider=self.name,
            when=offer.when,
            party_size=offer.party_size,
            confirmation_id=conf,
            detail=f"held via {offer.hold_token}",
        )

    def join_waitlist(self, req: ReservationRequest) -> Booking:
        # Wait scales with how slammed the service is; shortest right at open.
        base = 15 + int(self._roll(req.day, "wait") * 75)
        if req.preferred and 18 <= req.preferred.hour <= 20:
            base += 30
        position = 1 + int(self._roll(req.day, "pos") * 20)
        conf = f"WL-{int(self._roll(req.guest.phone, 'wl') * 1e7):07d}"
        return Booking(
            outcome=Outcome.WAITLISTED,
            provider=self.name,
            party_size=req.party_size,
            quoted_wait_min=base,
            waitlist_position=position,
            confirmation_id=conf,
            detail="joined remote waitlist; arrive before your quote expires",
        )


# --------------------------------------------------------------------------- #
# Yelp Guest Manager provider — the real integration point.
# --------------------------------------------------------------------------- #

# DTF Scottsdale's Yelp business slug (from its reservations/waitlist URLs).
DTF_SCOTTSDALE_YELP_SLUG = "din-tai-fung-scottsdale-5"
YELP_API_ROOT = "https://api.yelp.com/v3"


class ProviderError(RuntimeError):
    """Raised when a real provider can't proceed (auth, network, gating)."""


class YelpProvider:
    """Talks to Yelp's partner reservation/waitlist endpoints.

    Flow mirrors Yelp Guest Manager's API:
        GET  /bookings/{biz}/openings        -> available slots
        POST /bookings/{biz}/holds           -> hold a slot (returns hold_id)
        POST /bookings/{biz}/reservations    -> confirm the hold
        POST /waitlist_partner/{biz}/visit   -> join the waitlist (fallback)

    Requires a Yelp partner API key (env YELP_API_KEY). Yelp gates reservation
    write-access per partner, so without it this raises a clear ProviderError
    instead of silently failing — call `MockProvider` to dry-run the flow.
    """

    name = "yelp"

    def __init__(self, business_id: str = DTF_SCOTTSDALE_YELP_SLUG,
                 api_key: str | None = None, timeout: float = 10.0):
        self.business_id = business_id
        self.api_key = api_key or os.environ.get("YELP_API_KEY", "")
        self.timeout = timeout

    # -- low-level HTTP ----------------------------------------------------- #

    def _require_key(self) -> str:
        if not self.api_key:
            raise ProviderError(
                "no YELP_API_KEY set. Yelp gates reservation write-access to "
                "approved partners. Set YELP_API_KEY, or run with --provider mock "
                "to dry-run the booking flow offline.")
        return self.api_key

    def _request(self, method: str, path: str, *, params: dict | None = None,
                 body: dict | None = None) -> dict:
        key = self._require_key()
        url = f"{YELP_API_ROOT}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {key}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:400]
            raise ProviderError(f"Yelp {method} {path} -> HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise ProviderError(f"Yelp {method} {path} unreachable: {e.reason}") from e

    # -- Provider protocol -------------------------------------------------- #

    def find_openings(self, req: ReservationRequest) -> list[Offer]:
        data = self._request("GET", f"/bookings/{self.business_id}/openings",
                             params={
                                 "covers": req.party_size,
                                 "date": req.day.isoformat(),
                                 "start": req.earliest.strftime("%H:%M"),
                                 "end": req.latest.strftime("%H:%M"),
                             })
        offers: list[Offer] = []
        for slot in data.get("reservation_times", []):
            try:
                when = datetime.fromisoformat(slot["time"])
            except (KeyError, ValueError):
                continue
            if not req.accepts(when.time()):
                continue
            offers.append(Offer(when=when, party_size=req.party_size,
                                provider=self.name, hold_token=slot.get("hold_token", "")))
        return offers

    def reserve(self, req: ReservationRequest, offer: Offer) -> Booking:
        hold = self._request("POST", f"/bookings/{self.business_id}/holds",
                             body={"covers": offer.party_size,
                                   "time": offer.when.isoformat(),
                                   "hold_token": offer.hold_token})
        res = self._request("POST", f"/bookings/{self.business_id}/reservations",
                           body={
                               "hold_id": hold.get("hold_id"),
                               "covers": offer.party_size,
                               "time": offer.when.isoformat(),
                               "first_name": req.guest.name.split(" ")[0],
                               "last_name": " ".join(req.guest.name.split(" ")[1:]),
                               "phone": req.guest.phone,
                               "email": req.guest.email,
                               "notes": req.notes,
                           })
        return Booking(
            outcome=Outcome.CONFIRMED,
            provider=self.name,
            when=offer.when,
            party_size=offer.party_size,
            confirmation_id=res.get("reservation_id", ""),
            detail="confirmed via Yelp Guest Manager",
        )

    def join_waitlist(self, req: ReservationRequest) -> Booking:
        visit = self._request("POST", f"/waitlist_partner/{self.business_id}/visit",
                            body={"party_size": req.party_size,
                                  "first_name": req.guest.name.split(" ")[0],
                                  "last_name": " ".join(req.guest.name.split(" ")[1:]),
                                  "phone": req.guest.phone,
                                  "notes": req.notes})
        return Booking(
            outcome=Outcome.WAITLISTED,
            provider=self.name,
            party_size=req.party_size,
            quoted_wait_min=visit.get("wait_minutes"),
            waitlist_position=visit.get("position"),
            confirmation_id=visit.get("visit_id", ""),
            detail="joined Yelp waitlist",
        )


def get_provider(name: str, **kwargs) -> Provider:
    """Factory used by the CLI: 'mock' (default) or 'yelp'."""
    name = (name or "mock").lower()
    if name == "mock":
        return MockProvider(**{k: v for k, v in kwargs.items()
                               if k in ("seed", "fill_pressure")})
    if name == "yelp":
        return YelpProvider(**{k: v for k, v in kwargs.items()
                               if k in ("business_id", "api_key", "timeout")})
    raise ValueError(f"unknown provider {name!r} (expected 'mock' or 'yelp')")
