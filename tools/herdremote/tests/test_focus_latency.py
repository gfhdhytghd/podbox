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
        self.reader.jobs[0][1].set_result(hrproto.detail('p1', dict(say='old')))
        self.daemon.pump_reads()
        self.assertEqual(self.messages, [])
        self.now.return_value = 101
        self.daemon.pump_reads()
        self.assertEqual(len(self.reader.jobs), 2)
        self.reader.jobs[1][1].set_result(hrproto.detail('p2', dict(say='new')))
        self.daemon.pump_reads()
        self.assertEqual(self.messages, [hrproto.detail('p2', dict(say='new'))])
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
        self.reader.jobs[1][1].set_result(hrproto.detail('p1', dict(say='old session')))
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

    def test_elapsed_age_alone_does_not_resend_but_mode_changes_do(self):
        self.daemon.detail_pane = 'p1'
        for age in (1, 2, 3):
            self.daemon.send_detail(hrproto.detail('p1', dict(now='done', say='Summary', state_age_s=age)))
        self.assertEqual(len(self.messages), 1)
        self.daemon.send_detail(hrproto.detail('p1', dict(now='done', say='Summary', state_age_s=-1)))
        self.daemon.send_detail(hrproto.detail('p1', dict(now='done', say='Summary', state_age_s=-2)))
        self.assertEqual(len(self.messages), 3)

    def test_working_timer_alone_does_not_resend_until_it_is_stale(self):
        self.daemon.detail_pane = 'p1'
        def send(now, say='Out'):
            self.daemon.send_detail(hrproto.detail('p1', dict(now=now, say=say, state_age_s=1)))
        for sec in range(1, 6):
            send('Working 5m %02ds' % sec)
        self.assertEqual(len(self.messages), 1)
        send('Working 5m 06s', say='New output')
        self.assertEqual(len(self.messages), 2)
        self.daemon.sent_detail_at -= 20
        send('Working 5m 30s', say='New output')
        self.assertEqual(len(self.messages), 3)

    def test_changed_agent_metadata_discards_an_old_detail_result(self):
        self.ready()
        meta = dict(kind='claude', status='working', title='Task')
        self.daemon.model['pane_meta'] = {'p1': meta}
        self.daemon.command(b'det\tp1')
        self.daemon.pump_reads()
        meta['status'] = 'done'
        self.reader.jobs[0][1].set_result(hrproto.detail('p1', dict(now='Working')))
        self.daemon.pump_reads()
        self.assertEqual(self.messages, [])
        self.now.return_value = 101
        self.daemon.pump_reads()
        self.assertEqual(self.reader.jobs[1][0][3]['status'], 'done')
        self.reader.jobs[1][1].set_result(hrproto.detail('p1', dict(now='done')))
        self.daemon.pump_reads()
        self.assertEqual(self.messages, [hrproto.detail('p1', dict(now='done'))])

    def test_state_age_starts_at_an_observed_transition_not_first_sight(self):
        model = dict(self.daemon.model)
        model['pane_meta'] = {'p1': dict(kind='codex', title='Task', status='done')}
        self.daemon.apply_model((model, 100, None))
        self.assertEqual(self.daemon.detail_context('p1')['state_age_s'], -1)
        self.now.return_value = 101
        self.daemon.apply_model((model, 101, None))
        self.assertEqual(self.daemon.detail_context('p1')['state_age_s'], -1)
        model['pane_meta']['p1']['status'] = 'working'
        self.daemon.apply_model((model, 102, None))
        self.now.return_value = 105
        self.assertEqual(self.daemon.detail_context('p1')['state_age_s'], 3)
        self.daemon.apply_model((None, 106, 'fake outage'))
        self.daemon.apply_model((model, 107, None))
        self.assertEqual(self.daemon.detail_context('p1')['state_age_s'], -1)

    def key_reply(self, req='k1'):
        self.daemon.key_details[req] = (self.daemon.session_generation, self.daemon.detail_pane)
        with mock.patch.object(self.daemon.keys, 'replies', return_value=[(req, True, None)]):
            self.daemon.flush_replies()

    def test_key_reply_schedules_fresh_read_after_settle_without_waiting_a_second(self):
        self.ready()
        self.daemon.command(b'det\tp1')
        self.daemon.next_detail = 101
        self.key_reply()
        self.assertEqual(self.messages, [hrproto.result('k1', True)])
        self.daemon.pump_reads()
        self.assertEqual(len(self.reader.jobs), 0)
        self.now.return_value = 100.075
        self.daemon.pump_reads()
        self.assertEqual(len(self.reader.jobs), 1)

    def test_accepted_detail_key_is_associated_with_its_completion(self):
        self.ready()
        self.daemon.command(b'det\tp1')
        with mock.patch.object(self.daemon.keys, 'request', return_value=None):
            self.daemon.key_command('k1', 'left')
        self.assertEqual(self.daemon.key_details['k1'], (self.daemon.session_generation, 'p1'))
        with mock.patch.object(self.daemon.keys, 'replies', return_value=[('k1', True, None)]):
            self.daemon.flush_replies()
        self.assertNotIn('k1', self.daemon.key_details)
        self.assertAlmostEqual(self.daemon.detail_fast_due, 100.075)

    def test_pre_key_read_result_is_dropped_and_does_not_delay_fresh_read(self):
        self.ready()
        self.daemon.command(b'det\tp1')
        self.daemon.pump_reads()
        self.key_reply()
        self.reader.jobs[0][1].set_result(hrproto.detail('p1', dict(say='pre-key')))
        self.daemon.pump_reads()
        self.assertEqual(self.messages, [hrproto.result('k1', True)])
        self.now.return_value = 100.075
        self.daemon.pump_reads()
        self.assertEqual(len(self.reader.jobs), 2)
        self.reader.jobs[1][1].set_result(hrproto.detail('p1', dict(say='post-key')))
        self.daemon.pump_reads()
        self.assertEqual(self.messages[-1], hrproto.detail('p1', dict(say='post-key')))

    def test_key_burst_keeps_one_pending_read_and_does_not_postpone_it_forever(self):
        self.ready()
        self.daemon.command(b'det\tp1')
        self.daemon.next_detail = 101
        for i in range(5):
            self.now.return_value = 100 + i * .01
            self.key_reply('k%d' % i)
            self.daemon.pump_reads()
        self.assertEqual(len(self.reader.jobs), 0)
        self.now.return_value = 100.075
        self.daemon.pump_reads()
        self.assertEqual(len(self.reader.jobs), 1)
        self.now.return_value = 100.08
        self.key_reply('k6')
        self.reader.jobs[0][1].set_result(hrproto.detail('p1', dict(say='older-key')))
        self.daemon.pump_reads()
        self.now.return_value = 100.15
        self.daemon.pump_reads()
        self.assertEqual(len(self.reader.jobs), 1)
        self.now.return_value = 100.155
        self.daemon.pump_reads()
        self.assertEqual(len(self.reader.jobs), 2)

    def test_key_reply_after_pane_switch_or_reset_does_not_refresh_another_input(self):
        self.ready()
        self.daemon.command(b'det\tp1')
        self.daemon.key_details['k1'] = (self.daemon.session_generation, 'p1')
        self.daemon.command(b'det\tp2')
        with mock.patch.object(self.daemon.keys, 'replies', return_value=[('k1', True, None)]):
            self.daemon.flush_replies()
        self.assertIsNone(self.daemon.detail_fast_due)
        self.daemon.reset_session()
        self.assertEqual(self.daemon.key_details, {})
        self.assertIsNone(self.daemon.detail_fast_due)


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

    def test_worker_sends_post_key_detail_while_model_read_is_blocked(self):
        self.daemon.command(b'det\tw1:p0')
        self.server.texts['w1:p0'] = '• Summary\n› a'
        self.server.cursors['w1:p0'] = dict(row=1, col=3, visible=True)
        gate = threading.Event()
        original_model_read = self.daemon.read_model
        def blocked_model(*args):
            self.server.read_started.set()
            gate.wait(5)
            return original_model_read(*args)
        self.daemon.read_model = blocked_model
        self.server.read_started.clear()
        samples = []
        def tap(key, count):
            current = self.server.texts['w1:p0'] + 'b'
            self.server.texts['w1:p0'] = current
            self.server.cursors['w1:p0']['col'] += 1
        def latest_input(expected):
            for payload, stamp in reversed(self.link.messages):
                if payload.startswith(b'D\t') and hrproto.decode(payload)[0][9] == expected:
                    return stamp
            return None
        with mock.patch.object(herdremote.hrherdr, 'Watcher'), \
             mock.patch.object(self.daemon.answerer, 'start'), \
             mock.patch.object(self.daemon.usage, 'start'), \
             mock.patch.object(self.daemon.usage, 'close'), \
             mock.patch.object(self.daemon.keys.tool, 'tap', side_effect=tap):
            runner = threading.Thread(target=self.daemon.run, daemon=True)
            runner.start()
            try:
                self.assertTrue(self.server.read_started.wait(2))
                with self.link.condition:
                    self.assertTrue(self.link.condition.wait_for(lambda: latest_input('a') is not None, 1))
                for i in range(3):
                    req = 'k%d' % i
                    self.link.frames.append(types.SimpleNamespace(type=podlink.TEXT,
                        payload=('k\t%s\tright' % req).encode()))
                    reply, reply_at = self.link.wait_result(1)
                    self.assertEqual(hrproto.decode(reply)[0][1:3], [req, 'ok'])
                    expected = 'a' + 'b' * (i + 1)
                    with self.link.condition:
                        self.assertTrue(self.link.condition.wait_for(lambda: latest_input(expected) is not None, 1))
                        updated_at = latest_input(expected)
                    samples.append((updated_at - reply_at) * 1000)
                    self.assertLess(updated_at - reply_at, .3)
                    self.assertFalse(gate.is_set())
                self.assertFalse(any(m == 'client.focus' for m, _ in self.server.calls))
            finally:
                self.daemon.stopping.set()
                gate.set()
                runner.join(5)
            self.assertFalse(runner.is_alive())
        print('detail_key_R_to_D_ms=' + ','.join('%.3f' % s for s in samples))

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
