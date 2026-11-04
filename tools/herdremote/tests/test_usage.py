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
import threading
from email.utils import formatdate
from unittest.mock import Mock, patch
import urllib.error

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import hrusage
import hrproto
import herdremote

# Any unmocked HTTP attempt is a test failure, including spawned workers.
NETWORK_ATTEMPTS = []
def forbidden_network(*args, **kwargs):
    NETWORK_ATTEMPTS.append(True)
    raise AssertionError('unmocked usage network request')
hrusage.urllib.request.OpenerDirector.open = forbidden_network
_ORIGINAL_POPEN = hrusage.subprocess.Popen
def guarded_popen(args, *rest, **kwargs):
    if isinstance(args,(list,tuple)) and Path(args[0]).name in ('codex','secret-tool','grok','agy'):
        return forbidden_network()
    return _ORIGINAL_POPEN(args,*rest,**kwargs)
hrusage.subprocess.Popen = guarded_popen

def tearDownModule():
    assert not NETWORK_ATTEMPTS, 'usage tests attempted real HTTP'

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
            self.assertEqual(c.collect(NOW,budget=1)['state'],'tok')
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
        self.assertEqual(c.collect(NOW, budget=0)['state'], 'tok')
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
        self.assertEqual(result['state'], 'err')
        self.assertEqual(result['note'], 'limits sample expired')
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
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        cache = hrusage.UsageCache(temporary.name, timeout=.4, network=False, worker=fake_worker)
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
        while time.monotonic()<deadline and any(r['note']=='indexing 0%' for r in cache.snapshot()):
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
            while time.monotonic()<deadline and any(r['note'].startswith('indexing ') for r in cache.snapshot()):
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
            case.ipod.pump(.3)
            case.daemon.usage.close()
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




class Amendments(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def put(self, path, data):
        path = self.home/path
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(data) if not isinstance(data,str) else data)
        os.utime(path,(NOW,NOW))
        return path

    def test_grok_weekly_and_omitted_zero_including_unified(self):
        data = json.loads(sample('grok-limits.json'))
        limits = hrusage.limits_grok(data,NOW)
        self.assertEqual(limits['wk_pct'],12)
        self.assertNotIn('h5_pct',limits)
        del data['config']['creditUsagePercent']
        self.assertEqual(hrusage.limits_grok(data,NOW)['wk_pct'],0)
        data['config']['isUnifiedBillingUser'] = True
        self.assertEqual(hrusage.limits_grok(data,NOW),{'wk_pct':0,'wk_reset':hrusage.timestamp('2026-11-08T00:00:00Z')})
        data['config']['creditUsagePercent'] = float('nan')
        with self.assertRaises(hrusage.UsageError):hrusage.limits_grok(data,NOW)

    def test_grok_read_only_requests_plan_and_expiry(self):
        self.put('.grok/auth.json',{'fixture':{'auth_mode':'oidc','key':'fixture-secret',
            'expires_at':'2026-11-05T12:00:00Z'}})
        opener = Mock(side_effect=[io.BytesIO(sample('grok-limits.json').encode()),
                                  io.BytesIO(b'{"subscriptionTier":"XPremiumPlus"}')])
        limits,plan = hrusage.grok_limits(self.home,NOW,opener)
        self.assertEqual((limits['wk_pct'],plan),(12,'x_premium_plus'))
        self.assertTrue(all(c.args[0].get_method()=='GET' for c in opener.call_args_list))
        self.assertTrue(all('billing' in c.args[0].full_url or '/user?' in c.args[0].full_url
                            for c in opener.call_args_list))
        with self.assertRaisesRegex(hrusage.UsageError,'token expired'):
            hrusage.grok_limits(self.home,NOW+86400*2,opener)
        self.assertEqual(opener.call_count,2)

    def test_google_groups_main_and_note_include_argon_at_zero(self):
        data = json.loads(sample('agy-limits.json'))
        limits,note = hrusage.quota_groups(data)
        self.assertEqual((limits['wk_pct'],limits['h5_pct']),(25,40))
        self.assertEqual(note,'C/G 10/0 A 20/30')
        self.assertLessEqual(len(note.encode()),40)
        data['groups'][2]['buckets'][0]['remainingFraction'] = 1
        self.assertIn('A 0/30',hrusage.quota_groups(data)[1])

    def test_google_does_not_substitute_claude_group_for_missing_gemini(self):
        data = json.loads(sample('agy-limits.json'))
        data['groups'] = data['groups'][1:2]
        limits,note = hrusage.quota_groups(data)
        self.assertEqual(limits,{})
        self.assertEqual(note,'C/G 10/0 Gemini unlisted')
        data['groups'][0]['buckets'] = 'bad'
        with self.assertRaises(hrusage.UsageError):hrusage.quota_groups(data)

    def test_keyring_preferred_post_project_and_no_refresh(self):
        self.put('.gemini/antigravity-cli/cli.log','Language server version: 1.5.0\n')
        auth = json.dumps({'token':{'access_token':'fixture-secret','expiry':'2026-11-05T00:00:00Z'}})
        opener = Mock(side_effect=[io.BytesIO(b'{"cloudaicompanionProject":{"id":"fixture"}}'),
                                   io.BytesIO(sample('agy-limits.json').encode())])
        with patch.object(hrusage.subprocess,'check_output',return_value=auth) as keyring:
            limits,note = hrusage.gemini_limits(self.home,NOW,opener)
        self.assertEqual(limits['h5_pct'],40)
        self.assertEqual(keyring.call_args.args[0],['secret-tool','lookup','service','gemini','username','antigravity'])
        requests = [c.args[0] for c in opener.call_args_list]
        self.assertTrue(all(r.get_method()=='POST' for r in requests))
        self.assertTrue(requests[0].full_url.endswith(':loadCodeAssist'))
        self.assertTrue(requests[1].full_url.endswith(':retrieveUserQuotaSummary'))
        self.assertEqual(json.loads(requests[0].data),{'metadata':{'ideType':'ANTIGRAVITY'}})
        self.assertTrue(all(r.full_url.startswith('https://daily-cloudcode-pa.googleapis.com/') for r in requests))
        self.assertTrue(all(r.get_header('User-agent').startswith('antigravity/cli/1.5.0 (aidev_client;') for r in requests))
        self.assertEqual(json.loads(requests[1].data),{'project':'fixture'})
        with patch.object(hrusage.subprocess,'check_output',return_value=auth):
            with self.assertRaisesRegex(hrusage.UsageError,'token expired'):
                hrusage.gemini_limits(self.home,NOW+86400*2,opener)
        self.assertEqual(opener.call_count,2)

    def test_codex_rpc_only_allowed_messages_and_no_model_call(self):
        proc = Mock()
        proc.stdin = io.BytesIO()
        proc.stdout = Mock()
        proc.stdout.fileno.return_value = 123
        proc.poll.return_value = None
        answer = {'id':2,'result':{'rateLimits':{'planType':'fixture','primary':
            {'usedPercent':30,'windowDurationMins':10080,'resetsAt':NOW+1000}}}}
        with patch.object(hrusage.subprocess,'Popen',return_value=proc), \
             patch.object(hrusage.select,'select',return_value=([proc.stdout],[],[])), \
             patch.object(hrusage.os,'read',return_value=(json.dumps(answer)+'\n').encode()):
            limits,plan = hrusage.codex_limits(self.home,NOW)
        self.assertEqual((limits['wk_pct'],plan),(30,'fixture'))
        messages = [json.loads(line) for line in proc.stdin.getvalue().splitlines()]
        self.assertEqual([m['method'] for m in messages],['initialize','initialized','account/rateLimits/read'])
        proc.kill.assert_called_once()

    def test_index_survives_restart_without_reread_and_corrupt_rebuild(self):
        path = self.put('.codex/sessions/log.jsonl',sample('codex.jsonl'))
        c = hrusage.Collector('codex',self.home,network=False)
        before = c.collect(NOW)
        self.assertEqual(before['tokens'],1200)
        restarted = hrusage.Collector('codex',self.home,network=False)
        self.assertEqual(restarted.collect(NOW)['tokens'],1200)
        self.assertEqual(restarted.bytes_read,0)
        c.index_path(path).write_bytes(b'corrupt index')
        rebuilt = hrusage.Collector('codex',self.home,network=False)
        self.assertEqual(rebuilt.collect(NOW)['tokens'],1200)
        self.assertGreater(rebuilt.bytes_read,0)

    def test_partial_status_and_cached_row_are_available_at_restart(self):
        self.put('.codex/sessions/log.jsonl',sample('codex.jsonl'))
        c = hrusage.Collector('codex',self.home,network=False)
        partial = c.collect(NOW,budget=0)
        self.assertEqual(partial['state'],'tok')
        self.assertEqual(partial['note'],'indexing 0%')
        complete = c.collect(NOW)
        hrusage.atomic_json(hrusage.cache_root(self.home)/'codex-row.json',
                            {'version':hrusage.INDEX_VERSION,'row':complete})
        cache = hrusage.UsageCache(self.home,network=False)
        self.addCleanup(cache.close)
        self.assertEqual(cache.snapshot()[0]['tokens'],1200)
        self.assertIsNone(cache.thread)

    def test_mtime_window_filter_uses_authoritative_week_start(self):
        old = self.put('.codex/sessions/old.jsonl',sample('codex.jsonl'))
        os.utime(old,(NOW-6*86400,NOW-6*86400))
        c = hrusage.Collector('codex',self.home,network=True)
        with patch.object(hrusage,'codex_limits',return_value=({'wk_pct':1,'wk_reset':NOW+2*86400},'fixture')):
            result = c.collect(NOW)
        self.assertEqual(result['source_bytes'],0)
        self.assertEqual(c.bytes_read,0)

    def test_probe_is_throttled_during_index_slices(self):
        c = hrusage.Collector('codex',self.home,network=True)
        with patch.object(hrusage,'codex_limits',return_value=({'wk_pct':1},'fixture')) as probe:
            c.collect(NOW)
            c.collect(NOW+1)
            c.collect(NOW+19)
            self.assertEqual(probe.call_count,1)
            c.collect(NOW+20)
            self.assertEqual(probe.call_count,2)

    def test_atomic_write_failure_keeps_previous_cache(self):
        path = self.home/'cached.json'
        hrusage.atomic_json(path,{'version':hrusage.INDEX_VERSION,'value':1})
        with patch.object(hrusage.os,'replace',side_effect=OSError('fixture')):
            with self.assertRaises(OSError):hrusage.atomic_json(path,{'version':hrusage.INDEX_VERSION,'value':2})
        self.assertEqual(hrusage.cache_read(path)['value'],1)
        self.assertEqual(len(list(self.home.glob('.usage-*'))),0)

    def test_corrupt_row_shape_rebuilds_and_background_start_needs_no_us(self):
        root = hrusage.cache_root(self.home)
        hrusage.atomic_json(root/'codex-row.json',{'version':hrusage.INDEX_VERSION,'row':7})
        cache = hrusage.UsageCache(self.home,network=False)
        self.addCleanup(cache.close)
        cache.start()
        end = time.monotonic()+3
        while time.monotonic()<end and any(r['note'].startswith('indexing ') for r in cache.snapshot()):
            time.sleep(.02)
        self.assertTrue(all(r['state']=='none' for r in cache.snapshot()))
        self.assertTrue((root/'codex-row.json').exists())

    def test_round_robin_progress_does_not_starve_older_files(self):
        for name in ('a','b'):
            self.put('.claude/projects/p/'+name+'.jsonl',sample('claude.jsonl'))
        c = hrusage.Collector('claude',self.home,network=False)
        read = []
        def slice_one(file,stat,deadline):
            read.append(file.path)
            return False
        with patch.object(c,'read_log',side_effect=slice_one):
            c.collect(NOW)
            c.collect(NOW+1)
        self.assertEqual(len(read),2)
        self.assertNotEqual(read[0],read[1])

    def test_codex_expired_login_never_launches_refresh(self):
        payload = hrusage.base64.urlsafe_b64encode(json.dumps({'exp':NOW-1}).encode()).decode().rstrip('=')
        self.put('.codex/auth.json',{'auth_mode':'chatgpt','tokens':{'access_token':'header.'+payload+'.signature'}})
        with patch.object(hrusage.subprocess,'Popen') as process:
            with self.assertRaisesRegex(hrusage.UsageError,'token expired'):
                hrusage.codex_limits(self.home,NOW)
        process.assert_not_called()

    def test_partial_index_keeps_authoritative_limits_ok(self):
        self.put('.codex/sessions/log.jsonl',sample('codex.jsonl'))
        c = hrusage.Collector('codex',self.home,network=True)
        with patch.object(hrusage,'codex_limits',return_value=({'wk_pct':30,'wk_reset':NOW+1000},'fixture')):
            result = c.collect(NOW,budget=0)
        self.assertEqual(result['state'],'ok')
        self.assertEqual(result['wk_pct'],30)
        self.assertEqual(result['note'],'indexing 0%')

    @unittest.skipIf(hrusage.zstd is None,'zstd reader not installed')
    def test_compressed_codex_archive_counts_and_warm_restart_skips(self):
        path = self.home/'.codex/sessions/archive.jsonl.zst'
        path.parent.mkdir(parents=True)
        path.write_bytes(hrusage.zstd.compress(sample('codex.jsonl').encode()))
        os.utime(path,(NOW,NOW))
        c = hrusage.Collector('codex',self.home,network=False)
        self.assertEqual(c.collect(NOW)['tokens'],1200)
        warm = hrusage.Collector('codex',self.home,network=False)
        self.assertEqual(warm.collect(NOW)['tokens'],1200)
        self.assertEqual(warm.bytes_read,0)

    def test_restart_reuses_recent_probe_for_twenty_second_guard(self):
        c = hrusage.Collector('codex',self.home,network=True)
        with patch.object(hrusage,'codex_limits',return_value=({'wk_pct':30,'wk_reset':NOW+1000},'fixture')) as probe:
            result = c.collect(NOW)
            hrusage.atomic_json(hrusage.cache_root(self.home)/'codex-row.json',
                {'version':hrusage.INDEX_VERSION,'row':result})
            warm = hrusage.Collector('codex',self.home,network=True)
            self.assertEqual(warm.collect(NOW+1)['wk_pct'],30)
            self.assertEqual(probe.call_count,1)
            warm.collect(NOW+20)
            self.assertEqual(probe.call_count,2)

    def test_cached_probe_failure_stays_error_and_group_note_is_not_duplicated(self):
        c = hrusage.Collector('claude',self.home,network=True)
        with patch.object(hrusage,'claude_limits',side_effect=hrusage.UsageError('limits HTTP 429')) as probe:
            result = c.collect(NOW)
            hrusage.atomic_json(hrusage.cache_root(self.home)/'claude-row.json',
                {'version':hrusage.INDEX_VERSION,'row':result})
            warm = hrusage.Collector('claude',self.home,network=True)
            self.assertEqual(warm.collect(NOW+1)['state'],'err')
            self.assertEqual(probe.call_count,1)
        self.put('.gemini/antigravity-cli/brain/s/.system_generated/logs/transcript.jsonl',sample('agy.jsonl'))
        c = hrusage.Collector('gemini',self.home,network=True)
        with patch.object(hrusage,'gemini_limits',return_value=({},'C/G 0/0')):
            result = c.collect(NOW)
            hrusage.atomic_json(hrusage.cache_root(self.home)/'gemini-row.json',
                {'version':hrusage.INDEX_VERSION,'row':result})
            warm = hrusage.Collector('gemini',self.home,network=True)
            self.assertEqual(warm.collect(NOW+1)['note'],result['note'])

    def test_reset_only_grok_limit_is_data_not_unused_service(self):
        c = hrusage.Collector('grok',self.home,network=True)
        with patch.object(hrusage,'grok_limits',return_value=({'wk_pct':None,'wk_reset':NOW+1000},'fixture')):
            result = c.collect(NOW)
        self.assertEqual(result['state'],'ok')
        self.assertIsNone(result['wk_pct'])
        self.assertEqual(result['wk_reset'],NOW+1000)


class ReviewFixes(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name)
        self.limits={'h5_pct':17,'h5_reset':NOW+5000,'wk_pct':38,'wk_reset':NOW+20000}

    def probe(self,service,at,fetch):
        return hrusage.shared_probe(service,self.home,at,fetch)

    def test_claude_900s_interval_shared_across_instances_and_scan_override(self):
        fetch=Mock(return_value=(self.limits,'fixture'))
        self.probe('claude',NOW,fetch)
        for delta in (20,120,899):
            with patch.dict(os.environ,{'HERDREMOTE_USAGE_CACHE_DIR':str(self.home/'different-scan-cache')}):
                self.probe('claude',NOW+delta,fetch)
        self.assertEqual(fetch.call_count,1)
        self.probe('claude',NOW+900,fetch)
        self.assertEqual(fetch.call_count,2)

    def test_429_backoff_retry_after_numeric_date_and_restart(self):
        fetch=Mock(side_effect=hrusage.UsageError('limits HTTP 429',429,1000))
        first=self.probe('claude',NOW,fetch)
        self.assertEqual(first['next_at'],NOW+1000)
        self.probe('claude',NOW+999,fetch)
        self.assertEqual(fetch.call_count,1)
        second=self.probe('claude',NOW+1000,fetch)
        self.assertEqual(second['next_at'],NOW+2800)
        third=self.probe('claude',NOW+2800,fetch)
        self.assertEqual(third['next_at'],NOW+6400)
        self.assertEqual(hrusage.retry_after_seconds('123',NOW),123)
        self.assertEqual(hrusage.retry_after_seconds(formatdate(NOW+3000,usegmt=True),NOW),3000)
        self.assertEqual(hrusage.retry_after_seconds('invalid',NOW),0)
        self.assertEqual(hrusage.retry_after_seconds('nan',NOW),0)

    def test_http_429_header_reaches_shared_backoff(self):
        path=self.home/'.claude/.credentials.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'claudeAiOauth':{'accessToken':'fixture-secret','expiresAt':(NOW+10000)*1000}}))
        for value,expected in [('2400',2400),(formatdate(NOW+4000,usegmt=True),4000)]:
            opener=Mock(side_effect=urllib.error.HTTPError('https://fixture',429,'private',{'Retry-After':value},None))
            with self.assertRaises(hrusage.UsageError) as error:
                hrusage.claude_limits(self.home,NOW,opener)
            self.assertEqual((error.exception.status,error.exception.retry_after),(429,expected))
            self.assertNotIn('private',str(error.exception))

    def test_keep_last_good_each_provider_across_restart_and_resets(self):
        for service in hrusage.SERVICES:
            with self.subTest(service=service):
                fetch=Mock(return_value=(self.limits,'fixture'))
                self.probe(service,NOW,fetch)
                failed=Mock(side_effect=hrusage.UsageError('limits HTTP 429',429))
                state=self.probe(service,NOW+901,failed)
                self.assertEqual(state['good']['at'],NOW)
                old=dict(hrusage.row(service,NOW),last_good=state['good'])
                hrusage.atomic_json(hrusage.cache_root(self.home)/(service+'-row.json'),
                                    dict(version=hrusage.INDEX_VERSION,row=old))
                collector=hrusage.Collector(service,self.home,network=True)
                with patch.object(hrusage,service+'_limits',failed):
                    result=collector.collect(NOW+902)
                self.assertEqual((result['state'],result['h5_pct'],result['wk_pct']),('ok',17,38))
                self.assertEqual(result['sampled_at'],NOW)
                self.assertEqual(result['note'],'limits 15m old')
                result=hrusage.keep_limits_on_failure(result,'offline',NOW+5000)
                self.assertIsNone(result['h5_pct'])
                self.assertEqual((result['state'],result['wk_pct']),('ok',38))
                self.assertEqual(hrusage.keep_limits_on_failure(result,'offline',NOW+20000)['state'],'err')

    def test_reservation_precedes_network_and_serializes_overlapping_probes(self):
        started=threading.Event();release=threading.Event();answers=[]
        def fetch(home,now):
            started.set()
            self.assertTrue(release.wait(2))
            return self.limits,'fixture'
        thread=threading.Thread(target=lambda:answers.append(self.probe('claude',NOW,fetch)))
        thread.start()
        try:
            self.assertTrue(started.wait(2))
            other=Mock(return_value=(self.limits,'other'))
            pending=self.probe('claude',NOW+1,other)
            self.assertEqual(pending['request_at'],NOW)
            self.assertEqual(other.call_count,0)
        finally:
            release.set();thread.join(3)
        self.assertEqual(answers[0]['good']['at'],NOW)

    def test_legacy_daemon_blocks_claude_only_without_shortening_backoff(self):
        fetch=Mock(return_value=(self.limits,'fixture'))
        first=self.probe('claude',NOW,fetch)
        with patch.object(hrusage,'legacy_claude_poller',return_value=True):
            blocked=self.probe('claude',NOW+20,fetch)
            self.probe('grok',NOW+20,fetch)
        self.assertEqual(fetch.call_count,2)
        self.assertEqual(blocked['error'],'legacy Claude poller active')
        self.assertEqual(blocked['next_at'],first['next_at'])
        self.assertEqual(blocked['good']['at'],NOW)

    def test_unknown_failure_sanitized_and_corrupt_cache_rebuilt(self):
        fetch=Mock(side_effect=RuntimeError('fixture-secret'))
        self.assertEqual(self.probe('grok',NOW,fetch)['error'],'limits probe failed')
        self.probe('grok',NOW+1,fetch)
        self.assertEqual(fetch.call_count,1)
        path=hrusage.request_root(self.home)/'grok.json'
        data=json.loads(path.read_text())
        data['good']={'limits':{'wk_pct':'bad'},'at':NOW,'note':'fixture'}
        path.write_text(json.dumps(data))
        self.assertEqual(hrusage.budget_read(path),{})
        self.assertFalse(hrusage.valid_good({'limits':{},'at':float('nan'),'note':''}))

    def test_lock_busy_uses_shared_good_without_calling_provider(self):
        import fcntl
        fetch=Mock(return_value=(self.limits,'fixture'))
        self.probe('grok',NOW,fetch)
        with (hrusage.request_root(self.home)/'grok.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result=self.probe('grok',NOW+21,fetch)
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(result['good']['at'],NOW)

    def test_old_process_detection_requires_matching_start_time_marker(self):
        proc=self.home/'987654'
        proc.mkdir()
        (proc/'cmdline').write_bytes(b'python3\0/fixture/herdremote.py\0')
        with patch.object(hrusage.Path,'home',return_value=self.home), \
             patch.object(hrusage.Path,'iterdir',return_value=iter([proc])):
            self.assertTrue(hrusage.legacy_claude_poller(self.home))
        marker=hrusage.request_root(self.home)/'client-987654.json'
        hrusage.atomic_json(marker,dict(version=1,pid=987654,start='fixture-start'))
        with patch.object(hrusage.Path,'home',return_value=self.home), \
             patch.object(hrusage.Path,'iterdir',side_effect=lambda:iter([proc])), \
             patch.object(hrusage,'process_start',return_value='fixture-start'):
            self.assertFalse(hrusage.legacy_claude_poller(self.home))
        with patch.object(hrusage.Path,'home',return_value=self.home), \
             patch.object(hrusage.Path,'iterdir',side_effect=lambda:iter([proc])), \
             patch.object(hrusage,'process_start',return_value='reused-pid'):
            self.assertTrue(hrusage.legacy_claude_poller(self.home))

    def test_watchdog_recovers_good_published_before_worker_reply(self):
        self.probe('codex',NOW,Mock(return_value=(self.limits,'fixture')))
        cache=hrusage.UsageCache(home=self.home,network=False)
        with patch.object(hrusage.time,'time',return_value=NOW+240):
            cache._failed('codex','collector timeout')
        result=cache.snapshot()[0]
        self.assertEqual((result['state'],result['note'],result['sampled_at']),('ok','limits 4m old',NOW))
        warm=hrusage.cached_row(self.home,'codex')
        self.assertEqual(warm['last_good']['at'],NOW)

    def test_grok_expired_week_is_not_omitted_zero(self):
        data=json.loads(sample('grok-limits.json'))
        del data['config']['creditUsagePercent']
        with self.assertRaises(hrusage.UsageError):
            hrusage.limits_grok(data,NOW+hrusage.WEEK)

    def test_agy_version_requires_startup_header_and_live_group_fixture(self):
        with self.assertRaisesRegex(hrusage.UsageError,'version unavailable'):
            hrusage.gemini_user_agent(self.home)
        data=json.loads(sample('agy-view-limits.json'))
        limits,note=hrusage.quota_groups(data)
        self.assertEqual((limits['wk_pct'],limits['h5_pct']),(0,0))
        self.assertEqual(note,'C/G 0/0 A 100/-')


if __name__ == "__main__":
    unittest.main()
