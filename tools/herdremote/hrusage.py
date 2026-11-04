"""Read-only subscription usage. No model calls, API keys, or credential logging.

The link thread reads snapshots only. A supervisor thread owns four persistent
processes: each has a <=5s hard deadline and keeps its incremental log index.
"""
import copy
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import json
import multiprocessing as mp
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request

SERVICES = ('codex', 'claude', 'grok', 'gemini')
INTERVAL = 20.0
TIMEOUT = 5.0
WEEK = 7 * 86400


def timestamp(value):
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, AttributeError):
        return 0.0


def number(value):
    try:
        return max(0, int(value or 0))
    except (ValueError, TypeError, OverflowError):
        return 0


def counts(usage, style):
    """Disjoint token classes: cached input/reasoning are often subsets."""
    if style == 'claude':
        writes = number(usage.get('cache_creation_input_tokens'))
        long = min(writes, number((usage.get('cache_creation') or {}).get(
            'ephemeral_1h_input_tokens')))
        return (number(usage.get('input_tokens')),
                number(usage.get('cache_read_input_tokens')), writes - long,
                number(usage.get('output_tokens')), long)
    if style == 'gemini':
        inp, cached = number(usage.get('input')), number(usage.get('cached'))
        return (max(0, inp - cached), cached, 0,
                number(usage.get('output')) + number(usage.get('thoughts')), 0)
    if style == 'grok':
        inp, cached = number(usage.get('inputTokens')), number(usage.get('cachedReadTokens'))
        # Native totalTokens == inputTokens + outputTokens; reasoning is a subset.
        return (max(0, inp - cached), cached,
                number(usage.get('cacheCreationTokens')), number(usage.get('outputTokens')), 0)
    inp, cached = number(usage.get('input_tokens')), number(usage.get('cached_input_tokens'))
    writes = number(usage.get('cache_write_input_tokens'))
    # Codex input includes cache reads/writes; output includes reasoning.
    return (max(0, inp - cached - writes), cached, writes,
            number(usage.get('output_tokens')), 0)


class Prices:
    def __init__(self, path=None):
        self.models = json.loads(Path(path or Path(__file__).with_name(
            'usage_prices.json')).read_text())['models']

    def cost(self, model, tokens):
        entry = self.models.get(model)
        # Only strip a dated snapshot suffix; never guess a model family/alias.
        if entry is None and len(model) > 9 and model[-8:].isdigit():
            entry = self.models.get(model[:-9])
        if not entry or not entry.get('confirmed'):
            return Decimal(0), False
        if entry.get('long_context') and sum(tokens[:3]) + tokens[4] > entry['long_context']['above']:
            entry = dict(entry, **entry['long_context'])
        keys = ('input', 'cached_input', 'cache_write', 'output', 'cache_write_1h')
        return sum((Decimal(str(entry.get(k, 0))) * n / 1000000
                    for k, n in zip(keys, tokens)), Decimal(0)), True


def row(service, now, state='none', note=''):
    return dict(svc=service, state=state, sampled_at=now, h5_pct=None,
                h5_reset=None, wk_pct=None, wk_reset=None, tokens=0,
                usd_milli=0, note=note)


def limits_codex(data, at):
    result = {}
    for slot in ('primary', 'secondary'):
        value = data.get(slot) or {}
        minutes = value.get('window_minutes')
        prefix = 'h5' if minutes == 300 else 'wk' if minutes == 10080 else None
        if prefix:
            pct = value.get('used_percent')
            result[prefix + '_pct'] = max(0, min(100, round(pct))) if isinstance(pct, (int, float)) else None
            reset = timestamp(value.get('resets_at'))
            if not reset and value.get('resets_in_seconds') is not None:
                reset = at + number(value['resets_in_seconds'])
            result[prefix + '_reset'] = reset or None
    return result


def limits_claude(data):
    result = {}
    for key, prefix in [('five_hour', 'h5'), ('seven_day', 'wk')]:
        value = data.get(key) or {}
        pct = value.get('utilization')
        result[prefix + '_pct'] = max(0, min(100, round(pct))) if isinstance(pct, (int, float)) else None
        result[prefix + '_reset'] = timestamp(value.get('resets_at')) or None
    return result


class UsageError(Exception):
    """Only fixed, credential-free strings may leave a collector."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise UsageError('usage redirect refused')


def claude_limits(home, now, opener=None):
    """The existing Claude Code OAuth login; GET only, no refresh/login."""
    path = home / '.claude/.credentials.json'
    if not path.exists():
        return {}, 'no OAuth login'
    try:
        auth = json.loads(path.read_text()).get('claudeAiOauth') or {}
        token = auth.get('accessToken')
        if not token:
            return {}, 'no OAuth login'
        if auth.get('expiresAt', 0) / 1000 <= now:
            raise UsageError('OAuth expired; use Claude login')
        request = urllib.request.Request('https://api.anthropic.com/api/oauth/usage',
            headers={'Authorization': 'Bearer ' + token,
                     'anthropic-beta': 'oauth-2025-04-20',
                     'User-Agent': 'herdremote-usage/1'})
        open_url = opener or urllib.request.build_opener(NoRedirect()).open
        with open_url(request, timeout=1.5) as response:
            data = json.loads(response.read(65536))
        if not isinstance(data, dict) or not any(k in data for k in ('five_hour', 'seven_day')):
            raise UsageError('usage response schema')
        return limits_claude(data), 'subscription'
    except urllib.error.HTTPError as error:
        raise UsageError('limits HTTP %d' % error.code) from None
    except UsageError:
        raise
    except Exception:
        raise UsageError('limits unavailable') from None


class LogFile:
    def __init__(self, path):
        self.path = path
        self.offset = 0
        self.signature = None
        self.complete = False
        self.model = 'unknown'
        self.session = str(path)
        self.previous = (0, 0, 0, 0, 0)
        self.events = {}
        self.records = {}
        self.limit = ({}, 0, '')

    def reset(self):
        self.__init__(self.path)


class Collector:
    """Indices contain counters and timestamps only, never prompts or credentials."""
    def __init__(self, service, home=None, prices=None, network=True):
        self.service = service
        self.home = Path(home or Path.home())
        self.prices = prices or Prices()
        self.network = network
        self.files = {}
        self.bytes_read = 0

    def paths(self, now):
        roots = {
            'codex': [('.codex/sessions', '**/*.jsonl')],
            'claude': [('.claude/projects', '**/*.jsonl')],
            'grok': [('.grok/sessions', '*/*/usage.json')],
            'gemini': [('.gemini/antigravity-cli/brain', '*/.system_generated/logs/transcript.jsonl'),
                       ('.gemini/tmp', '**/session-*.json')],
        }
        for root, pattern in roots[self.service]:
            base = self.home / root
            for path in base.glob(pattern):
                # Future weekly reset can select up to seven days back. Skip
                # old immutable logs, but keep already indexed files for expiry.
                if path.stat().st_mtime >= now - WEEK:
                    yield path

    def consume(self, file, data, line_number):
        at = timestamp(data.get('timestamp', data.get('created_at')))
        if self.service == 'codex':
            kind, payload = data.get('type'), data.get('payload') or {}
            if kind == 'session_meta':
                file.session = payload.get('id', file.session)
            elif kind == 'turn_context':
                file.model = payload.get('model') or 'unknown'
            elif kind == 'token_usage_record':
                key = payload.get('response_id') or (file.session, line_number)
                file.records[key] = (at, file.model, counts(payload.get('usage') or {}, 'codex'))
            elif kind == 'event_msg' and payload.get('type') == 'token_count':
                limit = payload.get('rate_limits') or {}
                if limit and at >= file.limit[1] and limit.get('limit_id', 'codex') == 'codex':
                    file.limit = (limits_codex(limit, at), at, limit.get('plan_type') or '')
                total = (payload.get('info') or {}).get('total_token_usage')
                if total:
                    current = counts(total, 'codex')
                    delta = tuple(max(0, a - b) for a, b in zip(current, file.previous))
                    file.previous = current
                    if any(delta):
                        file.events[(at, file.model, tuple(total.get(k, 0) for k in (
                            'input_tokens', 'cached_input_tokens', 'output_tokens')))] = (at, file.model, delta)
        elif self.service == 'claude':
            msg = data.get('message') or {}
            if data.get('type') == 'assistant' and msg.get('usage'):
                key = (msg.get('id') or data.get('uuid') or (file.session, line_number), data.get('requestId'))
                value = counts(msg['usage'], 'claude')
                if key in file.events:
                    value = tuple(max(a, b) for a, b in zip(value, file.events[key][2]))
                file.events[key] = (at, msg.get('model') or 'unknown', value)
        elif self.service == 'gemini' and data.get('type') == 'PLANNER_RESPONSE' and 'input_tokens' in data:
            # Only canonical transcript.jsonl, never transcript_full or chunks.
            key = (file.session, data.get('step_index', line_number))
            usage = {'input': data.get('input_tokens'), 'cached': data.get('cache_read_tokens'),
                     'output': data.get('output_tokens'), 'thoughts': data.get('thinking_tokens')}
            file.events[key] = (at, data.get('model') or data.get('model_id') or 'unknown', counts(usage, 'gemini'))

    def read_json(self, file, stat):
        sig = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        if file.signature == sig:
            return
        data = json.loads(file.path.read_text())
        file.events.clear()
        if self.service == 'grok':
            for i, turn in enumerate(data.get('turns', [])):
                at = timestamp(turn.get('endedAt'))
                models = turn.get('modelUsage') or {turn.get('primaryModelId') or 'unknown': turn}
                for model, usage in models.items():
                    key = (data.get('sessionId', file.session), turn.get('turnNumber', i), model)
                    file.events[key] = (at, model, counts(usage, 'grok'))
        else:
            for i, msg in enumerate(data.get('messages', [])):
                if msg.get('tokens'):
                    key = (data.get('sessionId', file.session), msg.get('id', i))
                    file.events[key] = (timestamp(msg.get('timestamp')), msg.get('model') or 'unknown', counts(msg['tokens'], 'gemini'))
        self.bytes_read += stat.st_size
        file.signature = sig

    def read_log(self, file, stat, deadline):
        sig = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        if file.signature == sig and file.complete:
            return True
        if file.signature and (file.signature[0] != stat.st_ino or stat.st_size < file.offset
                               or (stat.st_size == file.offset and file.signature != sig)):
            file.reset()
        file.signature = sig
        file.complete = False
        with file.path.open('rb') as stream:
            stream.seek(file.offset)
            while time.monotonic() < deadline:
                start = stream.tell()
                line = stream.readline()
                if not line:
                    file.complete = True
                    return True
                if not line.endswith(b'\n'):
                    # Retry a partially appended record on the next refresh.
                    return True
                file.offset = stream.tell()
                self.bytes_read += len(line)
                if self.service == 'codex' and not any(tag in line for tag in (
                        b'"token_count"', b'"turn_context"', b'"session_meta"', b'"token_usage_record"')):
                    continue
                try:
                    data = json.loads(line)
                    if isinstance(data, dict):
                        self.consume(file, data, start)
                except (ValueError, TypeError, AttributeError):
                    # Completed malformed lines do not prevent later records.
                    continue
        return False

    def collect(self, now=None, budget=3.0):
        now = time.time() if now is None else now
        deadline = time.monotonic() + budget
        complete = True
        paths = list(self.paths(now))
        # Newest first makes limits available during the first indexing pass.
        paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        active = set(paths)
        for path in list(self.files):
            if path not in active:
                del self.files[path]
        for path in paths:
            if time.monotonic() >= deadline:
                complete = False
                break
            file = self.files.setdefault(path, LogFile(path))
            stat = path.stat()
            if path.suffix == '.json':
                self.read_json(file, stat)
            elif not self.read_log(file, stat, deadline):
                complete = False
                break
        limits, sampled, note, error = {}, now, '', ''
        if self.service == 'codex':
            limits, at, note = max((f.limit for f in self.files.values()), key=lambda x: x[1], default=({}, 0, ''))
            sampled = at or now
        if self.service == 'claude' and complete and self.network:
            try:
                limits, note = claude_limits(self.home, now)
            except UsageError as exc:
                error = str(exc)
        # Never roll a known expired window forward without a fresh limit sample.
        end = limits.get('wk_reset')
        expired = end is not None and end <= now
        if expired:
            end = None
        start = end - WEEK if end else now - WEEK
        out = row(self.service, sampled)
        out.update(limits)
        entries = {}
        record_sessions = {f.session for f in self.files.values() if f.records}
        for file in self.files.values():
            events = file.records if file.session in record_sessions else file.events
            for key, event in events.items():
                if start <= event[0] <= now and (end is None or event[0] < end):
                    if self.service == 'claude' and key in entries:
                        old = entries[key]
                        event = (max(old[0], event[0]), event[1],
                                 tuple(max(a, b) for a, b in zip(old[2], event[2])))
                    entries[key] = event
        cost = Decimal(0)
        unknown = False
        for _, model, tokens in entries.values():
            out['tokens'] += sum(tokens)
            dollars, known = self.prices.cost(model, tokens)
            cost += dollars
            unknown |= not known and any(tokens)
        out['usd_milli'] = int((cost * 1000).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        has_limits = any(limits.get(k) is not None for k in ('h5_pct', 'wk_pct'))
        out['state'] = 'ok' if has_limits else 'tok' if entries else 'none'
        if unknown:
            note = 'unpriced model; cost partial'
        elif self.service in ('grok', 'gemini'):
            note = 'local logs; limits unavailable'
        if expired:
            note = 'weekly expired; unpriced model' if unknown else 'weekly sample expired'
        if not complete:
            error = 'indexing local logs; partial totals'
        if error:
            out['state'], note = 'err', error
            if unknown:
                note = error.split(';')[0] + '; unpriced model'
        out['note'] = note
        return out


def _worker(conn, service, home, network):
    collector = Collector(service, home, network=network)
    try:
        while True:
            now = conn.recv()
            try:
                result = collector.collect(now)
            except Exception:
                result = row(service, now, 'err', 'local collector failed')
            conn.send(result)
    except (EOFError, BrokenPipeError):
        pass
    finally:
        conn.close()


class UsageCache:
    def __init__(self, home=None, interval=INTERVAL, timeout=TIMEOUT, network=True,
                 worker=_worker):
        self.home = str(home or Path.home())
        self.interval, self.timeout = max(INTERVAL, interval), min(TIMEOUT, timeout)
        self.network, self.worker = network, worker
        self.lock = threading.Lock()
        self.rows = {s: row(s, time.time(), 'err', 'collecting usage') for s in SERVICES}
        self.next_refresh = 0.0
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.thread = None
        self.processes = {}

    def request(self):
        """Called after sending cached U. Never waits for I/O or a collector."""
        with self.lock:
            now = time.monotonic()
            if now < self.next_refresh or self.stopping.is_set():
                return
            self.next_refresh = now + self.interval
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, daemon=True, name='usage-supervisor')
                self.thread.start()
            self.wake.set()

    def snapshot(self):
        with self.lock:
            return copy.deepcopy([self.rows[s] for s in SERVICES])

    def _drop(self, service):
        proc, conn = self.processes.pop(service)
        conn.close()
        if proc.is_alive():
            proc.kill()
        proc.join(timeout=0.2)

    def _run(self):
        # spawn avoids inheriting the daemon's serial/herdr descriptors/locks.
        context = mp.get_context('spawn')
        try:
            while not self.stopping.is_set():
                self.wake.wait(0.1)
                if not self.wake.is_set():
                    continue
                self.wake.clear()
                pending = {}
                for service in SERVICES:
                    if service not in self.processes:
                        conn, child = context.Pipe()
                        proc = context.Process(target=self.worker,
                            args=(child, service, self.home, self.network), daemon=True)
                        proc.start()
                        child.close()
                        self.processes[service] = proc, conn
                    proc, conn = self.processes[service]
                    try:
                        conn.send(time.time())
                        pending[service] = time.monotonic() + self.timeout
                    except (OSError, EOFError):
                        self._drop(service)
                        with self.lock:
                            self.rows[service].update(state='err', note='collector exited')
                while pending and not self.stopping.is_set():
                    for service, deadline in list(pending.items()):
                        proc, conn = self.processes[service]
                        try:
                            if conn.poll():
                                result = conn.recv()
                                with self.lock:
                                    self.rows[service] = result
                                del pending[service]
                                continue
                        except (OSError, EOFError):
                            deadline = 0
                        if time.monotonic() >= deadline:
                            self._drop(service)
                            with self.lock:
                                self.rows[service].update(state='err', note='collector timeout')
                            del pending[service]
                    self.stopping.wait(0.01)
        finally:
            for service in list(self.processes):
                self._drop(service)

    def close(self):
        self.stopping.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=1.5)
