"""Tests for notifications — composition, console/null channels, SMTP wiring.

The SMTP test swaps in a fake `smtplib.SMTP`, so no network or real mail server
is touched.
"""

import io
import unittest
from unittest import mock

from reservations import (
    Booker, MockProvider, ReservationRequest, DTF_SCOTTSDALE,
)
from reservations.notify import (
    ConsoleNotifier, EmailNotifier, NotifyError, NullNotifier,
    compose_booking, compose_openings, get_notifier,
)
from reservations.model import Offer
from datetime import datetime


def req(party=2, day="2026-07-04", earliest="18:00", latest="20:00"):
    return ReservationRequest.of(
        party_size=party, day=day, earliest=earliest, latest=latest,
        name="Ada Lovelace", phone="480-555-0100")


class _FakeSMTP:
    """Records what would have been sent. Works as a context manager."""

    last: "_FakeSMTP | None" = None

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port
        self.started_tls = False
        self.login_args = None
        self.sent = []
        _FakeSMTP.last = self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.started_tls = True

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, msg):
        self.sent.append(msg)


class TestCompose(unittest.TestCase):
    def test_openings_subject_and_times(self):
        offers = [Offer(when=datetime(2026, 7, 4, 18, 30), party_size=2, provider="mock"),
                  Offer(when=datetime(2026, 7, 4, 19, 0), party_size=2, provider="mock")]
        subject, body = compose_openings(offers, req(), DTF_SCOTTSDALE)
        self.assertIn("2 table(s) open", subject)
        self.assertIn("18:30", body)
        self.assertIn("19:00", body)
        self.assertIn(DTF_SCOTTSDALE["yelp_reservations"], body)

    def test_booking_confirmed_message(self):
        booker = Booker(MockProvider(fill_pressure=0.0))
        booking = booker.book(req())
        subject, body = compose_booking(booking, req(), DTF_SCOTTSDALE)
        self.assertIn("Reserved", subject)
        self.assertIn(booking.confirmation_id, body)

    def test_booking_waitlist_message(self):
        booker = Booker(MockProvider(fill_pressure=1.0))
        booking = booker.book(req())
        subject, body = compose_booking(booking, req(), DTF_SCOTTSDALE)
        self.assertIn("Waitlisted", subject)
        self.assertIn("waitlist", body.lower())


class TestChannels(unittest.TestCase):
    def test_null_is_noop(self):
        self.assertFalse(NullNotifier().send("s", "b"))

    def test_console_writes_and_returns_true(self):
        buf = io.StringIO()
        ok = ConsoleNotifier(stream=buf).send("subj", "body")
        self.assertTrue(ok)
        self.assertIn("subj", buf.getvalue())
        self.assertIn("body", buf.getvalue())


class TestEmail(unittest.TestCase):
    def test_missing_config_raises(self):
        with self.assertRaises(NotifyError):
            EmailNotifier(to="", host="").send("s", "b")
        with self.assertRaises(NotifyError):
            EmailNotifier(to="a@b.com", host="").send("s", "b")

    def test_sends_over_smtp_with_starttls(self):
        n = EmailNotifier(to="me@example.com", host="smtp.example.com", port=587,
                          user="u@example.com", password="pw", sender="u@example.com")
        with mock.patch("reservations.notify.smtplib.SMTP", _FakeSMTP):
            self.assertTrue(n.send("Hi", "There"))
        sent = _FakeSMTP.last
        self.assertTrue(sent.started_tls)
        self.assertEqual(sent.login_args, ("u@example.com", "pw"))
        self.assertEqual(len(sent.sent), 1)
        msg = sent.sent[0]
        self.assertEqual(msg["To"], "me@example.com")
        self.assertEqual(msg["Subject"], "Hi")
        self.assertIn("There", msg.get_content())

    def test_from_env_reads_recipient(self):
        env = {"SMTP_HOST": "h", "RESERVATIONS_EMAIL_TO": "x@y.com"}
        with mock.patch.dict("os.environ", env, clear=False):
            n = EmailNotifier.from_env()
            self.assertEqual(n.to, "x@y.com")
            self.assertEqual(n.host, "h")


class TestFactory(unittest.TestCase):
    def test_none(self):
        self.assertIsInstance(get_notifier("none"), NullNotifier)

    def test_console(self):
        self.assertIsInstance(get_notifier("console"), ConsoleNotifier)

    def test_email(self):
        self.assertIsInstance(get_notifier("email", to="x@y.com"), EmailNotifier)

    def test_unknown(self):
        with self.assertRaises(ValueError):
            get_notifier("sms")


class TestAlertWhenOpen(unittest.TestCase):
    def test_returns_offers_when_open(self):
        offers = Booker(MockProvider(fill_pressure=0.0)).alert_when_open(
            req(), attempts=1, interval_s=0)
        self.assertTrue(offers)

    def test_empty_when_full(self):
        offers = Booker(MockProvider(fill_pressure=1.0)).alert_when_open(
            req(), attempts=2, interval_s=0)
        self.assertEqual(offers, [])


if __name__ == "__main__":
    unittest.main()
