"""Scrubbed local-shaped usage fixtures; all usage network calls are mocked."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch
import urllib.error

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import hrusage
import hrproto
import herdremote

NOW = hrusage.timestamp('2026-11-04T12:05:00Z')


def sample(name):
    return (HERE / 'samples/usage' / name).read_text()


def fake_worker(conn, service, home, network):
    while True:
        try:
            now = conn.recv()
        except EOFError:
            return
        if service == 'codex':
            time.sleep(10)
        elif service == 'grok':
            os._exit(1)  # a crashed worker, with no traceback noise
        else:
            conn.send(hrusage.row(service, now, 'tok', 'fixture'))


class Parsers(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def put(self, path, content):
        file = self.home / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content)
        os.utime(file, (NOW, NOW))
        return file

    def collector(self, service):
        return hrusage.Collector(service, self.home, network=False)

    def test_codex_records_preferred_not_double_counted(self):
        self.put('.codex/sessions/day/log.jsonl', sample('codex.jsonl'))
        out = self.collector('codex').collect(NOW)
        self.assertEqual(out['tokens'], 1200)
        self.assertEqual(out['usd_milli'], 3)  # .6 + .06 + .25 + 2 = 2.91 milli
        self.assertEqual((out['h5_pct'], out['wk_pct']), (13, 41))
        self.assertEqual(out['h5_reset'], hrusage.timestamp('2026-11-04T13:01:00Z'))
        self.assertEqual(out['state'], 'ok')

    def test_legacy_cumulative_duplicates_and_window(self):
        records = [json.loads(l) for l in sample('codex.jsonl').splitlines()]
        records = [d for d in records if d['type'] != 'token_usage_record']
        self.put('.codex/sessions/log.jsonl', ''.join(json.dumps(d)+'\n' for d in records))
        c = self.collector('codex')
        self.assertEqual(c.collect(NOW)['tokens'], 1200)
        self.assertEqual(c.collect(NOW+hrusage.WEEK+1)['tokens'], 0)

    def test_copied_codex_legacy_log_does_not_duplicate_new_records(self):
        self.put('.codex/sessions/new.jsonl', sample('codex.jsonl'))
        old = [l for l in sample('codex.jsonl').splitlines() if 'token_usage_record' not in l]
        self.put('.codex/sessions/copy.jsonl', '\n'.join(old)+'\n')
        self.assertEqual(self.collector('codex').collect(NOW)['tokens'],1200)

    def test_mid_file_budget_retains_offset_and_resumes(self):
        self.put('.claude/projects/p/log.jsonl', sample('claude.jsonl'))
        c = self.collector('claude')
        # paths, first record, then deadline. No re-read of that first record.
        with patch.object(hrusage.time, 'monotonic', side_effect=[0,0,0,2]):
            self.assertEqual(c.collect(NOW,budget=1)['state'],'err')
        first_bytes = c.bytes_read
        self.assertGreater(first_bytes,0)
        self.assertLess(first_bytes,len(sample('claude.jsonl')))
        self.assertEqual(c.collect(NOW)['tokens'],1000)
        self.assertEqual(c.bytes_read,len(sample('claude.jsonl')))

    def test_claude_streaming_chunks_and_copied_logs(self):
        self.put('.claude/projects/a/log.jsonl', sample('claude.jsonl'))
        # An older partial copy must not overwrite the final streamed usage.
        self.put('.claude/projects/b/subagents/copy.jsonl', sample('claude.jsonl').splitlines()[0]+'\n')
        out = self.collector('claude').collect(NOW)
        self.assertEqual(out['tokens'], 1000)
        self.assertEqual(out['usd_milli'], 4)  # .4 + .12 + .75 + .4 + 2 = 3.67
        self.assertEqual(out['state'], 'tok')

    def test_grok_turns_not_session_totals(self):
        self.put('.grok/sessions/project/session/usage.json', sample('grok.json'))
        out = self.collector('grok').collect(NOW)
        self.assertEqual(out['tokens'], 1200)
        self.assertEqual(out['usd_milli'], 2)
        self.assertEqual(out['state'], 'tok')

    def test_agy_canonical_no_full_or_chunk_duplicates(self):
        base = '.gemini/antigravity-cli/brain/session/.system_generated/logs/'
        for name in ('transcript.jsonl', 'transcript_full.jsonl', 'chunks/transcript/0.jsonl'):
            self.put(base+name, sample('agy.jsonl'))
        out = self.collector('gemini').collect(NOW)
        self.assertEqual(out['tokens'], 1200)
        self.assertEqual(out['usd_milli'], 0)
        self.assertIn('unpriced', out['note'])

    def test_gemini_cli_thinking_and_cached_subsets(self):
        self.put('.gemini/tmp/project/chats/session-1.json', sample('gemini.json'))
        out = self.collector('gemini').collect(NOW)
        self.assertEqual(out['tokens'], 1200)
        self.assertEqual(out['usd_milli'], 3)

    def test_incremental_append_partial_line_truncate_rotate(self):
        path = self.put('.claude/projects/p/log.jsonl', sample('claude.jsonl'))
        c = self.collector('claude')
        self.assertEqual(c.collect(NOW)['tokens'], 1000)
        size = c.bytes_read
        c.collect(NOW)
        self.assertEqual(c.bytes_read, size)
        line = json.loads(sample('claude.jsonl').splitlines()[-1])
        line['message']['id'] = 'msg-2'
        raw = json.dumps(line)
        with path.open('a') as f:
            f.write(raw[:30])
        self.assertEqual(c.collect(NOW)['tokens'], 1000)
        with path.open('a') as f:
            f.write(raw[30:]+'\n')
        self.assertEqual(c.collect(NOW)['tokens'], 2000)
        path.write_text(sample('claude.jsonl'))
        self.assertEqual(c.collect(NOW)['tokens'], 1000)
        path.unlink()
        self.put('.claude/projects/p/log.jsonl', sample('claude.jsonl'))
        self.assertEqual(c.collect(NOW)['tokens'], 1000)

    def test_index_budget_returns_error_and_resumes(self):
        self.put('.codex/sessions/log.jsonl', sample('codex.jsonl'))
        c = self.collector('codex')
        self.assertEqual(c.collect(NOW, budget=0)['state'], 'err')
        self.assertEqual(c.collect(NOW)['tokens'], 1200)

    def test_json_mtime_unchanged_does_not_reread(self):
        self.put('.grok/sessions/p/s/usage.json', sample('grok.json'))
        c = self.collector('grok')
        c.collect(NOW)
        size = c.bytes_read
        c.collect(NOW)
        self.assertEqual(c.bytes_read, size)

    def test_rolling_and_known_weekly_windows(self):
        record = json.loads(sample('grok.json'))
        record['turns'][0]['endedAt'] = '2026-10-27T12:01:00Z'
        self.put('.grok/sessions/p/s/usage.json', json.dumps(record))
        self.assertEqual(self.collector('grok').collect(NOW)['tokens'], 0)
        self.put('.claude/projects/p/log.jsonl', sample('claude.jsonl'))
        c = self.collector('claude')
        c.network = True
        limits = hrusage.limits_claude(json.loads(sample('claude-limits.json')))
        with patch.object(hrusage, 'claude_limits', return_value=(limits, 'fixture')):
            self.assertEqual(c.collect(NOW)['tokens'], 1000)
            result = c.collect(hrusage.timestamp('2026-11-08T12:00:01Z'))
        self.assertEqual(result['note'], 'weekly sample expired')
        self.assertEqual(result['tokens'], 1000)

    def test_network_failure_preserves_local_tokens(self):
        self.put('.claude/projects/p/log.jsonl', sample('claude.jsonl'))
        c = self.collector('claude')
        c.network = True
        with patch.object(hrusage, 'claude_limits', side_effect=hrusage.UsageError('limits HTTP 429')):
            result = c.collect(NOW)
        self.assertEqual(result['state'], 'err')
        self.assertEqual(result['tokens'], 1000)
        self.assertEqual(result['note'], 'limits HTTP 429')


class Network(unittest.TestCase):
    def test_oauth_get_only_no_key_or_refresh_and_fixed_error(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / '.claude/.credentials.json'
            path.parent.mkdir()
            path.write_text(json.dumps({'claudeAiOauth': {'accessToken': 'fixture-secret', 'expiresAt': (NOW+60)*1000}}))
            response = io.BytesIO(sample('claude-limits.json').encode())
            opener = Mock(return_value=response)
            limits, _ = hrusage.claude_limits(home, NOW, opener)
            request = opener.call_args.args[0]
            self.assertEqual(request.get_method(), 'GET')
            self.assertEqual(request.full_url, 'https://api.anthropic.com/api/oauth/usage')
            self.assertEqual(limits['wk_pct'], 62)
            opener.side_effect = urllib.error.HTTPError(request.full_url, 401, 'fixture-secret', None, None)
            with self.assertRaisesRegex(hrusage.UsageError, '^limits HTTP 401$'):
                hrusage.claude_limits(home, NOW, opener)
            with self.assertRaisesRegex(hrusage.UsageError, 'OAuth expired'):
                hrusage.claude_limits(home, NOW+61, opener)
            self.assertEqual(opener.call_count, 2)

    def test_redirect_refused(self):
        with self.assertRaises(hrusage.UsageError):
            hrusage.NoRedirect().redirect_request(None, None, None, None, None, None)


class MathProtocol(unittest.TestCase):
    def test_classes_no_reasoning_or_cached_double_count(self):
        self.assertEqual(hrusage.counts({'input_tokens':1000,'cached_input_tokens':600,'output_tokens':200,'reasoning_output_tokens':70}, 'codex'), (400,600,0,200,0))
        self.assertEqual(hrusage.counts({'input':1000,'cached':600,'output':100,'thoughts':100}, 'gemini'), (400,600,0,200,0))

    def test_decimal_cost_unknown_and_long_context(self):
        p = hrusage.Prices()
        self.assertEqual(p.cost('claude-opus-5-5', (1000000,1000000,1000000,1000000,1000000))[0], hrusage.Decimal('37.2'))
        self.assertEqual(p.cost('unknown-model', (1000000,0,0,0,0)), (0,False))
        self.assertEqual(p.cost('grok-4.6-build', (1000000,0,0,0,0)), (0,False))
        self.assertEqual(p.cost('gemini-3.1-pro-preview', (300000,0,0,1000000,0))[0], hrusage.Decimal('19.2'))
        self.assertEqual(p.cost('gpt-6.1-sol', (300000,0,0,1000000,0))[0], hrusage.Decimal('16.2'))
        self.assertEqual(p.cost('claude-haiku-5-5', (200000,0,0,1000000,0))[0], hrusage.Decimal('2.6'))

    def test_protocol_order_totals_age_countdown_and_sanitization(self):
        rows = [hrusage.row(s, NOW-10, 'tok', '汉字\n\t'*30) for s in hrusage.SERVICES]
        rows[0].update(tokens=123,usd_milli=456,h5_pct=24,wk_pct=55,h5_reset=NOW+61,wk_reset=NOW-1)
        payload = hrproto.usage(list(reversed(rows)), NOW)
        records = hrproto.decode(payload)
        self.assertEqual(records[0], ['U','10','1'])
        self.assertEqual([r[1] for r in records[1:5]], list(hrusage.SERVICES))
        self.assertEqual(records[1][3:9], ['24','61','55','0','123','456'])
        self.assertEqual(records[-1], ['N','123','456'])
        self.assertTrue(all(len(r[-1].encode())<=40 for r in records[1:5]))
        self.assertEqual(len(records[1]),10)
        self.assertLessEqual(len(payload), hrproto.MAX_MESSAGE)
        self.assertEqual(hrproto.parse_command(b'us'), ('us',[]))
        for invalid in (b'us\t1',b'us\nfw\t1\tw1'):
            self.assertIsNone(hrproto.parse_command(invalid))

    def test_all_errors_and_extreme_values_still_fit(self):
        rows = [dict(hrusage.row(s,NOW,'err','x'*4000),tokens=10**400,usd_milli=10**400) for s in hrusage.SERVICES]
        payload = hrproto.usage(rows,NOW)
        self.assertEqual(hrproto.decode(payload)[0][-1], '0')
        self.assertLessEqual(len(payload),4096)
        with self.assertRaises(ValueError):
            hrproto.encode([['x'*4097]])


class Cache(unittest.TestCase):
    def test_hard_timeout_failure_isolation_throttle_and_shutdown(self):
        cache = hrusage.UsageCache(timeout=.4, network=False, worker=fake_worker)
        self.addCleanup(cache.close)
        cache.rows['codex'].update(tokens=123,usd_milli=4,sampled_at=NOW)
        before = time.monotonic()
        cache.request()
        self.assertLess(time.monotonic()-before,.1)
        next_refresh = cache.next_refresh
        for _ in range(5):
            cache.request()
            cache.snapshot()
        self.assertEqual(cache.next_refresh,next_refresh)
        deadline = time.monotonic()+3
        while time.monotonic()<deadline and any(r['note']=='collecting usage' for r in cache.snapshot()):
            time.sleep(.02)
        rows = {r['svc']:r for r in cache.snapshot()}
        self.assertEqual(rows['codex']['note'],'collector timeout')
        self.assertEqual((rows['codex']['tokens'],rows['codex']['usd_milli'],rows['codex']['sampled_at']),
                         (123,4,NOW))
        self.assertEqual(rows['grok']['state'],'err')
        self.assertEqual(rows['claude']['state'],'tok')
        self.assertEqual(rows['gemini']['state'],'tok')
        cache.close()
        self.assertFalse(cache.thread.is_alive())
        self.assertFalse(cache.processes)

    def test_actual_workers_empty_home_no_network(self):
        with tempfile.TemporaryDirectory() as home:
            cache = hrusage.UsageCache(home, network=False)
            self.addCleanup(cache.close)
            cache.request()
            deadline = time.monotonic()+3
            while time.monotonic()<deadline and any(r['state']=='err' for r in cache.snapshot()):
                time.sleep(.02)
            self.assertTrue(all(r['state']=='none' for r in cache.snapshot()))

    def test_daemon_us_serves_before_refresh_without_herdr(self):
        daemon = herdremote.Daemon(types.SimpleNamespace(socket='/nonexistent',state='/nonexistent'))
        self.addCleanup(daemon.usage.close)
        events = []
        rows = [hrusage.row(s,NOW) for s in hrusage.SERVICES]
        daemon.usage = types.SimpleNamespace(snapshot=lambda:rows, request=lambda:events.append('request'), close=lambda:None)
        daemon.send = lambda payload:events.append(payload)
        daemon.command(b'us')
        self.assertTrue(events[0].startswith(b'U\t'))
        self.assertEqual(events[1],'request')
        self.assertFalse(daemon.herdr_ok)

    def test_dump_bypasses_daemon_herdr_and_serial(self):
        rows = [hrusage.row(s,NOW) for s in hrusage.SERVICES]
        cache = Mock()
        cache.snapshot.return_value = rows
        with patch.object(sys,'argv',['herdremote','--usage-dump']), \
                patch.object(hrusage,'UsageCache',return_value=cache), \
                patch.object(herdremote,'Daemon',side_effect=AssertionError('must not construct daemon')), \
                patch('sys.stdout',new_callable=io.StringIO) as stdout:
            herdremote.main()
        self.assertTrue(stdout.getvalue().startswith('U\t'))
        cache.close.assert_called_once()


class UsageLink(unittest.TestCase):
    def test_fake_pty_us_immediate_cached_reply(self):
        # Reuse the existing private fake herdr/pty harness, never the real UI.
        import test_herdremote as existing
        case = existing.EndToEnd('test_session')
        case.setUp()
        try:
            calls = []
            rows = [hrusage.row(s,NOW,'tok','fixture') for s in hrusage.SERVICES]
            rows[0].update(tokens=1200,usd_milli=3)
            case.daemon.usage = types.SimpleNamespace(snapshot=lambda:rows,
                request=lambda:calls.append('request'), close=lambda:None)
            case.ipod.pump(.3)
            case.ipod.send('hr\t1')
            case.ipod.wait_for('W')
            case.ipod.send('us')
            frame = case.ipod.wait_for('U')
            self.assertEqual(frame[-1],['N','1200','3'])
            self.assertEqual([r[1] for r in frame[1:5]],list(hrusage.SERVICES))
            self.assertEqual(calls,['request'])
            case.ipod.wait_for('T')  # polling/heartbeat still works
            self.assertFalse(any(method in ('pane.send_keys','client.focus','workspace.focus','pane.focus')
                                 for method,_ in case.herdr.calls))
        finally:
            case.tearDown()
            case.ipod.close()


if __name__ == '__main__':
    unittest.main()
