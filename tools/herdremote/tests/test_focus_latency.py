"""Focus replies must not wait for dashboard reads or logging. No real focus."""
from concurrent.futures import Future
import os
import tempfile
import threading
import types
import unittest
from unittest import mock

import bench_focus_latency as bench
from bench_focus_latency import herdremote, hrproto, podlink


class ManualReader:
    def __init__(self):
        self.jobs = []

    def submit(self, *args):
        future = Future()
        self.jobs.append((args, future))
        return future


class Scheduling(unittest.TestCase):
    def setUp(self):
        self.daemon = herdremote.Daemon(types.SimpleNamespace(socket='/nonexistent/fake.sock', state=None))
        self.daemon.command(b'hr\t1')
        self.messages = []
        self.daemon.send = self.messages.append
        self.reader = ManualReader()
        self.daemon.reader = self.reader
        self.clock = mock.patch.object(herdremote.time, 'monotonic', return_value=100)
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)

    def ready(self):
        self.daemon.model_time = 99
        self.daemon.model_key = (self.daemon.session_generation, None, None)
        self.daemon.next_refresh = float('inf')

    def test_one_model_job_and_one_second_refresh_cap(self):
        self.daemon.pump_reads()
        for _ in range(30):
            self.daemon.changed.set()
            self.daemon.pump_reads()
        self.assertEqual(len(self.reader.jobs), 1)
        self.reader.jobs[0][1].set_result((self.daemon.model, 100, None))
        self.now.return_value = 100.9
        self.daemon.pump_reads()
        self.assertEqual(len(self.reader.jobs), 1)
        self.now.return_value = 101
        self.daemon.pump_reads()
        self.assertEqual(len(self.reader.jobs), 2)

    def test_old_subscription_model_is_dropped_and_latest_is_requested(self):
        original = self.daemon.model
        self.daemon.command(b'sub\tw1\tw1:t1')
        self.daemon.pump_reads()
        for i in range(20):
            self.daemon.command(('sub\tw1\tt%d' % i).encode())
            self.daemon.pump_reads()
        self.assertEqual(len(self.reader.jobs), 1)
        self.reader.jobs[0][1].set_result((dict(stale=True), 100, None))
        self.daemon.pump_reads()
        self.assertIs(self.daemon.model, original)
        self.assertEqual(len(self.reader.jobs), 2)
        self.assertEqual(self.reader.jobs[1][0][2:4], ('w1', 't19'))

    def test_stale_detail_is_dropped_and_switches_keep_send_limit(self):
        self.ready()
        self.daemon.command(b'det\tp1')
        self.daemon.pump_reads()
        self.daemon.command(b'det\tp2')
        for _ in range(20):
            self.daemon.pump_reads()
        self.assertEqual(len(self.reader.jobs), 1)
        self.reader.jobs[0][1].set_result(b'D\tp1\told')
        self.daemon.pump_reads()
        self.assertEqual(self.messages, [])
        self.now.return_value = 101
        self.daemon.pump_reads()
        self.assertEqual(len(self.reader.jobs), 2)
        self.reader.jobs[1][1].set_result(b'D\tp2\tnew')
        self.daemon.pump_reads()
        self.assertEqual(self.messages, [b'D\tp2\tnew'])
        self.daemon.command(b'det\tp3')
        self.now.return_value = 101.9
        self.daemon.pump_reads()
        self.assertEqual(len(self.reader.jobs), 2)

    def test_reset_drops_inflight_model_and_same_pane_detail(self):
        self.daemon.command(b'det\tp1')
        self.daemon.pump_reads()
        self.assertEqual(len(self.reader.jobs), 2)
        self.daemon.command(b'hr\t1')
        self.daemon.command(b'det\tp1')
        self.reader.jobs[0][1].set_result((dict(stale=True), 100, None))
        self.reader.jobs[1][1].set_result(b'D\tp1\told session')
        self.daemon.pump_reads()
        self.assertNotIn('stale', self.daemon.model)
        self.assertEqual(self.messages, [])

    def test_replies_precede_dashboard_and_startup_waits_for_model(self):
        self.daemon.results.put(('7', True, 'Answered'))
        self.daemon.push()
        self.assertEqual(self.messages, [b'R\t7\tok\tAnswered'])
        self.ready()
        self.daemon.results.put(('8', True, 'Answered'))
        self.daemon.push()
        self.assertEqual(self.messages[1], b'R\t8\tok\tAnswered')
        self.assertTrue(self.messages[2].startswith(b'W\t'))


class Replies(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = bench.FakeHerdr(os.path.join(self.tmp.name, 'fake.sock'))
        self.daemon = herdremote.Daemon(types.SimpleNamespace(socket=self.server.path, state=None))
        self.daemon.command(b'hr\t1')
        self.daemon.refresh()
        self.link = bench.MemoryLink()
        self.daemon.link = self.link
        self.daemon.send = lambda payload: self.link.send(podlink.TEXT, payload)

    def tearDown(self):
        self.daemon.stopping.set()
        if self.server.read_gate:
            self.server.read_gate.set()
        self.daemon.close_link()
        self.server.close()
        self.tmp.cleanup()

    def test_success_and_error_reply_are_sent_before_logging(self):
        def check(*args, **kwargs):
            self.assertTrue(self.link.messages[-1][0].startswith(b'R\t'))
        with mock.patch.object(herdremote.log, 'debug', side_effect=check), \
             mock.patch.object(herdremote.log, 'warning', side_effect=check):
            self.daemon.command(b'fw\t1\tw1')
            self.daemon.command(b'fp\t2\tw1:p0')
            self.daemon.command(b'ft\t3\tw1\tw1:t1')
            self.daemon.command(b'ft\t4\tw1\tmissing')
        self.assertEqual(len(self.link.messages), 4)
        self.assertFalse(self.daemon.changed.is_set())

    def test_all_focus_commands_reply_while_model_read_is_blocked(self):
        self.server.read_gate = threading.Event()
        self.server.read_started.clear()
        with mock.patch.object(herdremote.hrherdr, 'Watcher'), \
             mock.patch.object(self.daemon.answerer, 'start'), \
             mock.patch.object(self.daemon.usage, 'start'), \
             mock.patch.object(self.daemon.usage, 'close'):
            runner = threading.Thread(target=self.daemon.run, daemon=True)
            runner.start()
            try:
                self.assertTrue(self.server.read_started.wait(2))
                for req, payload in (('1', b'fw\t1\tw1'), ('2', b'fp\t2\tw1:p0'),
                                     ('3', b'ft\t3\tw1\tw1:t1')):
                    self.link.frames.append(types.SimpleNamespace(type=podlink.TEXT, payload=payload))
                    reply, _ = self.link.wait_result(1)
                    self.assertEqual(hrproto.decode(reply)[0][1:3], [req, 'ok'])
                    self.assertFalse(self.server.read_gate.is_set())
                calls = [m for m, _ in self.server.calls]
                self.assertEqual(calls.count('client.focus'), 3)
                self.assertEqual(calls.count('session.snapshot'), 2)  # setup + one worker
                self.assertNotIn('client.list', calls)
            finally:
                self.daemon.stopping.set()
                self.server.read_gate.set()
                runner.join(5)
            self.assertFalse(runner.is_alive())

    def test_unexpected_loop_error_stops_and_closes_the_reader(self):
        with mock.patch.object(herdremote.hrherdr, 'Watcher'), \
             mock.patch.object(self.daemon.answerer, 'start'), \
             mock.patch.object(self.daemon.usage, 'start'), \
             mock.patch.object(self.daemon.usage, 'close'), \
             mock.patch.object(self.daemon, 'run_loop', side_effect=RuntimeError('fake loop failure')):
            with self.assertRaisesRegex(RuntimeError, 'fake loop failure'):
                self.daemon.run()
        self.assertTrue(self.daemon.stopping.is_set())
        self.assertIsNone(self.daemon.reader)
        self.assertIsNone(self.daemon.link)


if __name__ == '__main__':
    unittest.main()
