"""The booker: orchestration over a provider.

This is the house-elf that actually runs the errand. Given a request and a
provider, it:

  1. finds openings in the guest's window,
  2. picks the slot closest to their preferred time,
  3. reserves it — and if nothing is bookable, falls back to the waitlist.

It also handles the *defining* DTF problem: prime reservations don't exist until
they drop (~30 days out, at midnight) and then vanish in seconds. `watch()` polls
openings with backoff and grabs the first acceptable slot the instant it appears.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .model import Booking, Offer, Outcome, ReservationRequest
from .providers import Provider, ProviderError

# DTF Scottsdale — the concrete target this tool was built for.
DTF_SCOTTSDALE = {
    "name": "Din Tai Fung — Scottsdale",
    "address": "7014 E Camelback Rd, Suite 608, Scottsdale, AZ 85251",
    "inside": "Scottsdale Fashion Square",
    "phone": "+1-480-256-8683",
    "yelp_reservations": "https://www.yelp.com/reservations/din-tai-fung-scottsdale-5",
    "yelp_waitlist": "https://www.yelp.com/waitlist/din-tai-fung-scottsdale-5",
    "notes": ("Reservations release ~30 days ahead at midnight local and sell out "
              "within seconds; the remote waitlist is the realistic walk-up path "
              "and is shortest right at open."),
}


def pick_best(req: ReservationRequest, offers: list[Offer]) -> Offer | None:
    """Choose the acceptable offer closest to the guest's preferred time."""
    acceptable = [o for o in offers if req.accepts(o.when.time())]
    if not acceptable:
        return None
    target = req.preferred or req.earliest
    def distance(o: Offer) -> int:
        t = o.when.time()
        return abs((t.hour * 60 + t.minute) - (target.hour * 60 + target.minute))
    return min(acceptable, key=distance)


@dataclass
class Booker:
    """Wraps a provider with the booking errand + waitlist fallback."""

    provider: Provider
    fallback_to_waitlist: bool = True

    def book(self, req: ReservationRequest) -> Booking:
        """One attempt: reserve the best open slot, else join the waitlist."""
        try:
            offers = self.provider.find_openings(req)
        except ProviderError as e:
            return Booking(outcome=Outcome.FAILED, provider=self.provider.name,
                           detail=str(e))
        best = pick_best(req, offers)
        if best is not None:
            try:
                return self.provider.reserve(req, best)
            except ProviderError as e:
                # Lost the slot mid-confirm (classic at midnight drops) — fall through.
                last = Booking(outcome=Outcome.FAILED, provider=self.provider.name,
                               detail=f"reserve failed: {e}")
        else:
            last = Booking(outcome=Outcome.UNAVAILABLE, provider=self.provider.name,
                           detail="no slot in window")
        if self.fallback_to_waitlist:
            try:
                return self.provider.join_waitlist(req)
            except ProviderError as e:
                return Booking(outcome=Outcome.FAILED, provider=self.provider.name,
                               detail=f"waitlist failed: {e}")
        return last

    def alert_when_open(self, req: ReservationRequest, *, attempts: int = 20,
                        interval_s: float = 2.0, max_interval_s: float = 30.0,
                        on_poll=None) -> list[Offer]:
        """Poll for openings and return the first acceptable batch — no booking.

        For "just tell me when a slot exists": the CLI hands these offers to a
        notifier so the user can grab one themselves. Same backoff as `watch`.
        """
        delay = interval_s
        for i in range(1, attempts + 1):
            try:
                offers = self.provider.find_openings(req)
            except ProviderError as e:
                if on_poll:
                    on_poll(i, attempts, f"provider error: {e}")
                offers = []
            acceptable = [o for o in offers if req.accepts(o.when.time())]
            if acceptable:
                return acceptable
            if on_poll:
                on_poll(i, attempts, "no openings yet")
            if i < attempts:
                time.sleep(delay)
                delay = min(delay * 1.5, max_interval_s)
        return []

    def watch(self, req: ReservationRequest, *, attempts: int = 20,
              interval_s: float = 2.0, max_interval_s: float = 30.0,
              on_poll=None) -> Booking:
        """Poll for openings (e.g. a midnight drop) and grab the first slot.

        Backs off from `interval_s` toward `max_interval_s`. Only falls back to
        the waitlist on the final attempt, so we don't give up a hunt early.
        Returns as soon as a reservation is CONFIRMED.
        """
        delay = interval_s
        for i in range(1, attempts + 1):
            try:
                offers = self.provider.find_openings(req)
            except ProviderError as e:
                if on_poll:
                    on_poll(i, attempts, f"provider error: {e}")
                offers = []
            best = pick_best(req, offers)
            if best is not None:
                try:
                    booking = self.provider.reserve(req, best)
                    if booking.outcome is Outcome.CONFIRMED:
                        return booking
                except ProviderError as e:
                    if on_poll:
                        on_poll(i, attempts, f"lost slot: {e}")
            elif on_poll:
                on_poll(i, attempts, "no openings yet")
            if i < attempts:
                time.sleep(delay)
                delay = min(delay * 1.5, max_interval_s)
        # Hunt exhausted — try the waitlist as a last resort.
        if self.fallback_to_waitlist:
            try:
                return self.provider.join_waitlist(req)
            except ProviderError as e:
                return Booking(outcome=Outcome.FAILED, provider=self.provider.name,
                               detail=f"waitlist failed after hunt: {e}")
        return Booking(outcome=Outcome.UNAVAILABLE, provider=self.provider.name,
                       detail=f"no opening after {attempts} attempts")
