"""Clock wire/scheduler tests; no serial port or live clock changes."""
import datetime
import unittest
from unittest import mock
from test_herdremote import herdremote, hrproto


class ClockTests(unittest.TestCase):
    def test_local_wire(self):
        for offset in (-18000, -14400, 19800, 50400, -50400):
            dt = datetime.datetime(2026, 11, 13, 19, 2, 3,
                                   tzinfo=datetime.timezone(datetime.timedelta(seconds=offset)))
            self.assertEqual(hrproto.clock(dt),
                             ("TS\t1\t2026-11-13T19:02:03\t%+d" % offset).encode())
            self.assertEqual(hrproto.decode(hrproto.clock(dt))[0][0:2], ["TS", "1"])

    def test_invalid(self):
        for dt in (datetime.datetime(2026, 1, 1),
                   datetime.datetime(2038, 1, 1, tzinfo=datetime.timezone.utc),
                   datetime.datetime(1999, 1, 1, tzinfo=datetime.timezone.utc)):
            with self.assertRaises(ValueError):
                hrproto.clock(dt)

    def test_schedule(self):
        clock = herdremote.ClockSync()
        sent = []
        with mock.patch.object(herdremote.hrproto, 'clock', return_value=b'TS'):
            self.assertTrue(clock.poll(sent.append, 100, 10000, 0))
            self.assertFalse(clock.poll(sent.append, 101, 10001, 0))
            self.assertFalse(clock.poll(sent.append, 699, 10599, 0))
            self.assertTrue(clock.poll(sent.append, 700, 10600, 0))
            self.assertTrue(clock.poll(sent.append, 701, 14201, 0))  # forward NTP step
            self.assertTrue(clock.poll(sent.append, 702, 10002, 0))  # backward step
            self.assertTrue(clock.poll(sent.append, 703, 10003, 3600))  # DST/TZ
            self.assertTrue(clock.poll(sent.append, 704, 13604, 3600))  # suspend
        self.assertEqual(len(sent), 6)

    def test_exact_jump_threshold_and_gradual_slew(self):
        clock = herdremote.ClockSync()
        with mock.patch.object(herdremote.hrproto, 'clock', return_value=b'TS'):
            self.assertTrue(clock.poll(lambda _: None, 0, 10000, 0))
            self.assertFalse(clock.poll(lambda _: None, 1, 10003, 0))
            self.assertTrue(clock.poll(lambda _: None, 2, 10005, 0))

    def test_failed_send_retries(self):
        clock = herdremote.ClockSync()
        with mock.patch.object(herdremote.hrproto, 'clock', return_value=b'TS'):
            with self.assertRaises(OSError):
                clock.poll(mock.Mock(side_effect=OSError), 0, 10000, 0)
            self.assertTrue(clock.poll(lambda _: None, 1, 10001, 0))

    def test_session_reset_and_loop_wiring(self):
        import inspect
        self.assertIn('self.clock_sync = ClockSync()', inspect.getsource(herdremote.Daemon.reset_session))
        self.assertIn('self.clock_sync.poll(self.send)', inspect.getsource(herdremote.Daemon.open_link))
        self.assertIn('self.clock_sync.poll(self.send)', inspect.getsource(herdremote.Daemon.run_loop))
