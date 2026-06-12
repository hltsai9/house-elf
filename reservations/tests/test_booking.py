"""Tests for the booking errand — request validation, slot selection, fallback.

Everything runs against the deterministic MockProvider, so no network or
credentials are needed.
"""

import unittest
from datetime import datetime, time

from reservations import (
    Booker, MockProvider, Outcome, ReservationRequest, pick_best,
)
from reservations.model import Guest, Offer, parse_time
from reservations.providers import YelpProvider, ProviderError, get_provider


def req(party=2, day="2026-07-04", earliest="18:00", latest="20:00",
        preferred=None, name="Ada Lovelace", phone="480-555-0100"):
    return ReservationRequest.of(
        party_size=party, day=day, earliest=earliest, latest=latest,
        preferred=preferred, name=name, phone=phone)


class TestRequestValidation(unittest.TestCase):
    def test_window_must_be_ordered(self):
        with self.assertRaises(ValueError):
            req(earliest="20:00", latest="18:00")

    def test_party_size_positive(self):
        with self.assertRaises(ValueError):
            req(party=0)

    def test_phone_must_have_digits(self):
        with self.assertRaises(ValueError):
            req(phone="call me")

    def test_preferred_defaults_to_earliest(self):
        r = req(earliest="18:00", latest="20:00")
        self.assertEqual(r.preferred, time(18, 0))

    def test_bad_time_format_rejected(self):
        with self.assertRaises(ValueError):
            parse_time("6pm")


class TestPickBest(unittest.TestCase):
    def _offer(self, hh, mm):
        return Offer(when=datetime(2026, 7, 4, hh, mm), party_size=2, provider="mock")

    def test_picks_closest_to_preferred(self):
        r = req(earliest="17:00", latest="21:00", preferred="19:00")
        offers = [self._offer(17, 30), self._offer(19, 30), self._offer(20, 30)]
        best = pick_best(r, offers)
        self.assertEqual(best.when.hour, 19)

    def test_ignores_out_of_window(self):
        r = req(earliest="18:00", latest="19:00")
        offers = [self._offer(12, 0), self._offer(21, 0)]
        self.assertIsNone(pick_best(r, offers))

    def test_none_when_no_offers(self):
        self.assertIsNone(pick_best(req(), []))


class TestMockProvider(unittest.TestCase):
    def test_openings_are_deterministic(self):
        p = MockProvider()
        a = p.find_openings(req())
        b = p.find_openings(req())
        self.assertEqual([o.hhmm for o in a], [o.hhmm for o in b])

    def test_openings_respect_window(self):
        p = MockProvider()
        r = req(earliest="18:00", latest="19:00")
        for o in p.find_openings(r):
            self.assertTrue(r.accepts(o.when.time()))

    def test_more_pressure_means_fewer_slots(self):
        loose = MockProvider(fill_pressure=0.1)
        tight = MockProvider(fill_pressure=0.95)
        r = req(earliest="11:00", latest="21:00")
        self.assertGreater(len(loose.find_openings(r)), len(tight.find_openings(r)))

    def test_reserve_confirms(self):
        p = MockProvider(fill_pressure=0.0)  # everything open
        r = req()
        offer = p.find_openings(r)[0]
        booking = p.reserve(r, offer)
        self.assertIs(booking.outcome, Outcome.CONFIRMED)
        self.assertTrue(booking.confirmation_id.startswith("DTF-"))
        self.assertEqual(booking.when, offer.when)

    def test_waitlist_quotes_a_wait(self):
        booking = MockProvider().join_waitlist(req())
        self.assertIs(booking.outcome, Outcome.WAITLISTED)
        self.assertGreater(booking.quoted_wait_min, 0)


class TestBooker(unittest.TestCase):
    def test_books_when_available(self):
        booker = Booker(MockProvider(fill_pressure=0.0))
        booking = booker.book(req())
        self.assertIs(booking.outcome, Outcome.CONFIRMED)
        self.assertTrue(booking.ok)

    def test_falls_back_to_waitlist_when_full(self):
        booker = Booker(MockProvider(fill_pressure=1.0))  # nothing ever open
        booking = booker.book(req())
        self.assertIs(booking.outcome, Outcome.WAITLISTED)

    def test_no_fallback_reports_unavailable(self):
        booker = Booker(MockProvider(fill_pressure=1.0), fallback_to_waitlist=False)
        booking = booker.book(req())
        self.assertIs(booking.outcome, Outcome.UNAVAILABLE)
        self.assertFalse(booking.ok)

    def test_watch_grabs_a_slot(self):
        booker = Booker(MockProvider(fill_pressure=0.0))
        booking = booker.watch(req(), attempts=1, interval_s=0)
        self.assertIs(booking.outcome, Outcome.CONFIRMED)

    def test_watch_exhausts_to_waitlist(self):
        booker = Booker(MockProvider(fill_pressure=1.0))
        booking = booker.watch(req(), attempts=2, interval_s=0)
        self.assertIs(booking.outcome, Outcome.WAITLISTED)


class TestYelpProvider(unittest.TestCase):
    def test_requires_api_key(self):
        p = YelpProvider(api_key="")
        with self.assertRaises(ProviderError):
            p.find_openings(req())

    def test_yelp_booker_surfaces_failure_not_crash(self):
        # No key -> booker should report FAILED, not raise.
        booking = Booker(YelpProvider(api_key="")).book(req())
        self.assertIs(booking.outcome, Outcome.FAILED)
        self.assertIn("YELP_API_KEY", booking.detail)


class TestFactory(unittest.TestCase):
    def test_get_mock(self):
        self.assertIsInstance(get_provider("mock"), MockProvider)

    def test_get_yelp(self):
        self.assertIsInstance(get_provider("yelp"), YelpProvider)

    def test_unknown_raises(self):
        with self.assertRaises(ValueError):
            get_provider("opentable")


if __name__ == "__main__":
    unittest.main()
