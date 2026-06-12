"""reservations — a house-elf errand: book a table at Din Tai Fung Scottsdale.

A small, stdlib-only booking tool with a provider abstraction so the real
backend (Yelp Guest Manager, which powers DTF Scottsdale's reservations and
waitlist) and an offline mock are interchangeable.

    from reservations import Booker, ReservationRequest, get_provider

    req = ReservationRequest.of(
        party_size=2, day="2026-07-04", earliest="18:00", latest="20:00",
        name="Ada Lovelace", phone="480-555-0100")
    booking = Booker(get_provider("mock")).book(req)
    print(booking.summary())
"""

from __future__ import annotations

from .booker import Booker, DTF_SCOTTSDALE, pick_best
from .model import (
    Booking, Guest, Offer, Outcome, ReservationRequest,
    parse_date, parse_time,
)
from .providers import (
    DTF_SCOTTSDALE_YELP_SLUG, MockProvider, Provider, ProviderError,
    YelpProvider, get_provider,
)
from .notify import (
    ConsoleNotifier, EmailNotifier, Notifier, NotifyError, NullNotifier,
    compose_booking, compose_openings, get_notifier,
)

__all__ = [
    "Booker", "DTF_SCOTTSDALE", "pick_best",
    "Booking", "Guest", "Offer", "Outcome", "ReservationRequest",
    "parse_date", "parse_time",
    "DTF_SCOTTSDALE_YELP_SLUG", "MockProvider", "Provider", "ProviderError",
    "YelpProvider", "get_provider",
    "ConsoleNotifier", "EmailNotifier", "Notifier", "NotifyError", "NullNotifier",
    "compose_booking", "compose_openings", "get_notifier",
]
