"""Tests for the derived intelligence — the part that earns 'mission control'."""

import os
import tempfile
import unittest

from mission_control.collector import (
    EventLog, derive, STALE_AFTER_S, HIGH_BURN_USD_PER_MIN)
from mission_control.schema import Heartbeat, Status


def hb(aid, status=Status.RUNNING, ts=0.0, **kw):
    return Heartbeat(agent_id=aid, status=status, ts=ts, **kw)


class TestRollups(unittest.TestCase):
    def test_latest_heartbeat_wins(self):
        history = [
            hb("a", Status.RUNNING, ts=1, progress=0.2),
            hb("a", Status.DONE, ts=5, progress=1.0),
        ]
        state = derive(history, now=5)
        self.assertEqual(len(state.agents), 1)
        self.assertEqual(state.agents[0].hb.status, Status.DONE)

    def test_status_counts_and_spend(self):
        history = [
            hb("a", Status.RUNNING, ts=1, spend_usd=1.0),
            hb("b", Status.DONE, ts=1, spend_usd=2.5),
            hb("c", Status.FAILED, ts=1, spend_usd=0.5),
        ]
        state = derive(history, now=2)
        self.assertEqual(state.counts[Status.RUNNING], 1)
        self.assertEqual(state.counts[Status.DONE], 1)
        self.assertEqual(state.counts[Status.FAILED], 1)
        self.assertAlmostEqual(state.total_spend, 4.0)


class TestStaleness(unittest.TestCase):
    def test_running_but_silent_is_stale(self):
        state = derive([hb("hung", Status.RUNNING, ts=0)],
                       now=STALE_AFTER_S + 5)
        self.assertTrue(state.agents[0].stale)
        self.assertTrue(any("stale" in a for a in state.alerts))

    def test_fresh_running_is_not_stale(self):
        state = derive([hb("ok", Status.RUNNING, ts=0)], now=2)
        self.assertFalse(state.agents[0].stale)

    def test_done_agent_never_stale(self):
        # a finished agent legitimately stops emitting — not an alert
        state = derive([hb("d", Status.DONE, ts=0)], now=999)
        self.assertFalse(state.agents[0].stale)


class TestBurn(unittest.TestCase):
    def test_burn_rate_from_spend_delta(self):
        # +$2 over 60s == $2/min
        history = [
            hb("a", Status.RUNNING, ts=0, spend_usd=1.0),
            hb("a", Status.RUNNING, ts=60, spend_usd=3.0),
        ]
        state = derive(history, now=60)
        self.assertAlmostEqual(state.agents[0].burn_usd_per_min, 2.0, places=2)

    def test_high_burn_alert(self):
        history = [
            hb("a", Status.RUNNING, ts=0, spend_usd=0.0),
            hb("a", Status.RUNNING, ts=60, spend_usd=HIGH_BURN_USD_PER_MIN + 2),
        ]
        state = derive(history, now=60)
        self.assertTrue(any("burn" in a for a in state.alerts))

    def test_no_burn_without_time_spread(self):
        history = [hb("a", ts=0.0, spend_usd=1.0), hb("a", ts=0.1, spend_usd=9.0)]
        state = derive(history, now=0.1)
        self.assertEqual(state.agents[0].burn_usd_per_min, 0.0)


class TestAlerts(unittest.TestCase):
    def test_failed_agent_alerts_with_error(self):
        state = derive([hb("dep", Status.FAILED, ts=0, error="429")], now=1)
        self.assertTrue(any("dep failed (429)" in a for a in state.alerts))

    def test_long_blocked_waiter_alerts(self):
        state = derive([hb("t", Status.WAITING, ts=0, blocked_on=["x"])],
                       now=STALE_AFTER_S + 5)
        self.assertTrue(any("blocked" in a and "x" in a for a in state.alerts))

    def test_over_budget_alert(self):
        state = derive([hb("a", ts=0, spend_usd=25.0)], now=1, budget_usd=20.0)
        self.assertTrue(any("budget" in a for a in state.alerts))

    def test_nominal_fleet_has_no_alerts(self):
        state = derive([hb("a", Status.RUNNING, ts=0, spend_usd=0.1)], now=1)
        self.assertEqual(state.alerts, [])


class TestEventLog(unittest.TestCase):
    def test_roundtrip(self):
        path = os.path.join(tempfile.mkdtemp(), "log.jsonl")
        log = EventLog(path)
        log.emit(hb("a", Status.RUNNING, ts=1, blocked_on=["b"]))
        log.emit(hb("a", Status.DONE, ts=2))
        back = log.read_all()
        self.assertEqual(len(back), 2)
        self.assertEqual(back[0].blocked_on, ["b"])
        self.assertEqual(back[1].status, Status.DONE)

    def test_tolerates_partial_tail_line(self):
        path = os.path.join(tempfile.mkdtemp(), "log.jsonl")
        log = EventLog(path)
        log.emit(hb("a", ts=1))
        with open(path, "a") as f:
            f.write('{"agent_id": "b", "stat')  # truncated write
        self.assertEqual(len(log.read_all()), 1)


if __name__ == "__main__":
    unittest.main()
