"""Read-only subscription usage. No model calls, API keys, or credential logging.

The link thread reads snapshots only. A supervisor thread owns four persistent
processes: each has a <=5s hard deadline and keeps its incremental log index.
"""
import copy
import base64
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import json
import multiprocessing as mp
import os
from pathlib import Path
import hashlib
import math
import platform
import re
import select
import socket
import subprocess
import tempfile
import zlib
import resource
import signal
import contextlib
import fcntl
from email.utils import parsedate_to_datetime
try:
    from compression import zstd
except ImportError:
    try:
        import zstandard as zstd
    except ImportError:
        zstd = None
import threading
import time
import urllib.error
import urllib.request

SERVICES = ('codex', 'claude', 'grok', 'gemini')
INTERVAL = 20.0
TIMEOUT = 5.0
WEEK = 7 * 86400
AGY_DEFAULT_MODEL = 'gemini-4-argon'
INDEX_VERSION = 3
SLICE = 0.35
SCAN_CPU_BUDGET = 0.15
CLAUDE_INTERVAL = 900.0  # Omarchy Main.qml default; near-limit minimum is 180s.
PROBE_BUDGET_VERSION = 1


def atomic_json(path, data, compressed=False):
    """Only our cache is writable. Replace complete files, never credentials."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = json.dumps(data, separators=(',', ':')).encode()
    if compressed:
        raw = zlib.compress(raw, 1)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.usage-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def cache_read(path, compressed=False):
    try:
        raw = path.read_bytes()
        data = json.loads(zlib.decompress(raw) if compressed else raw)
        return data if data.get('version') == INDEX_VERSION else {}
    except (OSError, ValueError, TypeError, AttributeError, zlib.error):
        return {}


def cache_root(home):
    return Path(os.environ.get('HERDREMOTE_USAGE_CACHE_DIR') or
                Path(home) / '.cache/podbox-herdremote/usage')


def cached_row(home, service):
    saved = cache_read(cache_root(home)/(service+'-row.json')).get('row')
    if not isinstance(saved,dict) or saved.get('svc') != service:
        return {}
    if saved.get('state') not in ('ok','tok','none','err') or not isinstance(saved.get('note'),str):
        return {}
    if any(isinstance(saved.get(k),bool) or not isinstance(saved.get(k),(int,float)) or
           not math.isfinite(saved[k]) or saved[k] < 0 for k in ('tokens','usd_milli','sampled_at')):
        return {}
    for key in ('h5_pct','wk_pct','h5_reset','wk_reset','probe_at'):
        value = saved.get(key)
        if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or
                                  not math.isfinite(value) or value < 0):
            return {}
    if any(key in saved and not isinstance(saved[key],str) for key in ('quota_note','probe_error')):
        return {}
    if saved.get('last_good') is not None and not valid_good(saved['last_good']):
        return {}
    return saved


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
    def __init__(self, message, status=None, retry_after=0):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def retry_after_seconds(value, now):
    try:
        seconds = float(value)
        return max(0, seconds) if math.isfinite(seconds) else 0
    except (ValueError, TypeError):
        try:
            return max(0,parsedate_to_datetime(value).timestamp()-now)
        except (ValueError, TypeError, OverflowError):
            return 0


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
        delay = retry_after_seconds((error.headers or {}).get('Retry-After'), now)
        error.close()
        raise UsageError('limits HTTP %d' % error.code, error.code, delay) from None
    except UsageError:
        raise
    except Exception:
        raise UsageError('limits unavailable') from None


def _json_request(request, timeout=1.0, opener=None):
    try:
        with (opener or urllib.request.build_opener(NoRedirect()).open)(request, timeout=timeout) as response:
            data = json.loads(response.read(1048576))
        if not isinstance(data, dict):
            raise UsageError('limits response schema')
        return data
    except urllib.error.HTTPError as exc:
        delay = retry_after_seconds((exc.headers or {}).get('Retry-After'), time.time())
        exc.close()
        raise UsageError('limits HTTP %d' % exc.code, exc.code, delay) from None
    except UsageError:
        raise
    except Exception:
        raise UsageError('limits unavailable') from None


def codex_limits(home, now):
    """Omarchy RPC source, reusing the daemon proxy before private stdio."""
    auth_path = home/'.codex/auth.json'
    if auth_path.exists():
        try:
            auth = json.loads(auth_path.read_text())
            if auth.get('auth_mode') == 'apikey':
                return {}, 'API key login; no plan limits'
            access = (auth.get('tokens') or {}).get('access_token') or ''
            if access.count('.') == 2:
                encoded = access.split('.')[1]
                expiry = json.loads(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4))).get('exp')
                if expiry and expiry <= now:
                    raise UsageError('Codex token expired')
        except UsageError:
            raise
        except Exception:
            raise UsageError('Codex login unreadable') from None
    commands = []
    sock = home / '.codex/app-server-control/app-server-control.sock'
    if sock.exists():
        commands.append(['codex', 'app-server', 'proxy', '--sock', str(sock)])
    commands.append(['codex', '-s', 'read-only', '-a', 'on-request', 'app-server'])
    deadline = time.monotonic() + 4.1
    for command in commands:
        proc = None
        try:
            proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL)
            # Pipelining preserves the handshake order and keeps stdin open.
            for message in [
                {'id':1,'method':'initialize','params':{'clientInfo':{'name':'herdremote-usage','version':'1'}}},
                {'method':'initialized','params':{}},
                {'id':2,'method':'account/rateLimits/read','params':{}},
            ]:
                proc.stdin.write((json.dumps(message)+'\n').encode())
            proc.stdin.flush()
            end = min(deadline, time.monotonic() + (.2 if 'proxy' in command else 4.1))
            pending = b''
            while time.monotonic() < end:
                if not select.select([proc.stdout], [], [], max(0,end-time.monotonic()))[0]:
                    break
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    break
                pending += chunk
                while b'\n' in pending:
                    line, pending = pending.split(b'\n',1)
                    answer = json.loads(line)
                    if answer.get('id') != 2:
                        continue
                    if 'error' in answer:
                        raise UsageError('Codex limits RPC refused')
                    data = answer.get('result') or {}
                    rate = (data.get('rateLimitsByLimitId') or {}).get('codex') or data.get('rateLimits')
                    if not isinstance(rate, dict):
                        raise UsageError('Codex limits RPC schema')
                    shaped = {}
                    for key in ('primary','secondary'):
                        window = rate.get(key) or {}
                        shaped[key] = dict(used_percent=window.get('usedPercent'),
                            window_minutes=window.get('windowDurationMins'), resets_at=window.get('resetsAt'))
                    return limits_codex(shaped, now), rate.get('planType') or 'app-server'
        except UsageError:
            raise
        except Exception:
            pass
        finally:
            if proc is not None:
                proc.kill() if proc.poll() is None else None
                proc.communicate(timeout=.2)
    raise UsageError('Codex limits RPC unavailable')


def limits_grok(data, now):
    """Match the CLI weekly view: protobuf JSON omits a zero percentage."""
    config = data.get('config') or {}
    period = config.get('currentPeriod') or {}
    reset = timestamp(period.get('end'))
    if not reset or 'WEEK' not in str(period.get('type')):
        return {}
    value = config.get('creditUsagePercent')
    if value is None:
        if reset <= now:
            raise UsageError('Grok weekly window expired')
        value = 0
    if isinstance(value, dict):
        value = value.get('val',0)
    if isinstance(value, bool) or not math.isfinite(float(value)):
        raise UsageError('Grok limits response schema')
    return dict(wk_pct=max(0,min(100,round(float(value)))), wk_reset=reset)


def grok_limits(home, now, opener=None):
    try:
        path = home / '.grok/auth.json'
        if not path.exists():
            return {}, 'no Grok login'
        auth = json.loads(path.read_text())
        entry = next(v for v in auth.values() if isinstance(v,dict))
        if entry.get('auth_mode') not in ('oidc','oauth'):
            raise UsageError('Grok subscription login required')
        if timestamp(entry.get('expires_at')) <= now:
            raise UsageError('Grok token expired')
        headers = {'Authorization':'Bearer '+entry['key'], 'Accept':'application/json'}
        data = _json_request(urllib.request.Request(
            'https://cli-chat-proxy.grok.com/v1/billing?format=credits', headers=headers), opener=opener)
        # The CLI's user route supplies the actual plan, no guessed label.
        user = _json_request(urllib.request.Request(
            'https://cli-chat-proxy.grok.com/v1/user?include=subscription', headers=headers), opener=opener)
        plan=user.get('subscriptionTier') or 'subscription'
        plan={'XPremiumPlus':'x_premium_plus','XPremium':'x_premium','XBasic':'x_basic',
              'SuperGrok':'supergrok','SuperGrokPlus':'supergrok_plus',
              'SuperGrokHeavy':'supergrok_heavy'}.get(plan,plan)
        return limits_grok(data, now), plan
    except UsageError:
        raise
    except Exception:
        raise UsageError('Grok limits unavailable') from None


def quota_groups(data):
    if not isinstance(data, dict) or 'error' in data or not isinstance(data.get('groups',[]),list):
        raise UsageError('Gemini limits response schema')
    groups = data.get('groups') or ([{'displayName':'Gemini models','buckets':data['buckets']}]
                                   if isinstance(data.get('buckets'),list) else [])
    result = {}
    for group in groups:
        if not isinstance(group,dict):
            continue
        name = str(group.get('displayName') or '').lower()
        key = 'argon' if 'argon' in name else 'gemini' if 'gemini' in name else 'other' if 'claude' in name or 'gpt' in name else None
        if key is None:
            continue
        buckets = group.get('buckets',[])
        if not isinstance(buckets,list):
            raise UsageError('Gemini limits response schema')
        values = {}
        for bucket in buckets:
            if not isinstance(bucket,dict) or bucket.get('disabled') is True:
                continue
            window = str(bucket.get('window') or bucket.get('bucketId') or '').lower()
            prefix = 'wk' if 'week' in window else 'h5' if '5h' in window else None
            remaining = bucket.get('remainingFraction',bucket.get('remaining_fraction'))
            reset = timestamp(bucket.get('resetTime',bucket.get('reset_time'))) or None
            if remaining is None:
                amount = bucket.get('remainingAmount',bucket.get('remaining_amount'))
                if amount is not None:
                    if isinstance(amount,bool) or amount not in (0,'0'):
                        continue
                    remaining = 0
                elif reset:
                    remaining = 0
            if prefix and remaining is not None:
                if isinstance(remaining,bool) or not math.isfinite(float(remaining)):
                    raise UsageError('Gemini limits response schema')
                values[prefix+'_pct'] = max(0,min(100,round((1-float(remaining))*100)))
                values[prefix+'_reset'] = reset
        result[key] = values
    note = []
    for key, label in [('other','C/G'),('argon','A')]:
        if key in result:
            note.append('%s %s/%s' % (label,result[key].get('wk_pct','-'),result[key].get('h5_pct','-')))
    # Do not substitute the first group's Claude allowance for Gemini.
    if 'gemini' not in result:
        note.append('Gemini unlisted')
    return result.get('gemini',{}), ' '.join(note)


def gemini_user_agent(home):
    # agy http_helpers.go:261 constructs name/kind/version plus client attributes.
    # Read only the startup header, never launch the CLI (which could refresh auth).
    try:
        with (home/'.gemini/antigravity-cli/cli.log').open('rb') as stream:
            header=stream.read(16384).decode('utf-8',errors='replace')
        version=re.search(r'Language server version: ([0-9]+\.[0-9]+\.[0-9]+(?:[-+][\w.-]+)?)',header)[1]
    except (OSError,TypeError):
        raise UsageError('agy client version unavailable') from None
    arch={'x86_64':'amd64','aarch64':'arm64'}.get(platform.machine(),platform.machine())
    return 'antigravity/cli/%s (aidev_client; os_type=%s; arch=%s)' % (
        version,platform.system().lower(),arch)


def gemini_limits(home, now, opener=None):
    """Omarchy source: agy's Secret Service login, then the file fallback."""
    try:
        try:
            raw = subprocess.check_output(['secret-tool','lookup','service','gemini','username','antigravity'],
                        text=True,stderr=subprocess.DEVNULL,timeout=.8)
            auth = json.loads(raw)
        except Exception:
            path = home / '.gemini/antigravity-cli/antigravity-oauth-token'
            if not path.exists():
                return {}, 'no agy login'
            auth = json.loads(path.read_text())
        token = auth.get('token') or auth
        if timestamp(token.get('expiry')) <= now:
            raise UsageError('Gemini token expired')
        headers = {'Authorization':'Bearer '+token['access_token'],
                   'Content-Type':'application/json','User-Agent':gemini_user_agent(home)}
        def post(method, body):
            return _json_request(urllib.request.Request(
                'https://daily-cloudcode-pa.googleapis.com/v1internal:'+method,
                data=json.dumps(body).encode(),headers=headers), opener=opener)
        # agy's consumer view: user_management.go:150, ClientMetadata.IdeType=9.
        assist = post('loadCodeAssist',{'metadata':{'ideType':'ANTIGRAVITY'}})
        project = assist.get('cloudaicompanionProject')
        if isinstance(project,dict):
            project = project.get('id')
        return quota_groups(post('retrieveUserQuotaSummary',{'project':project} if project else {}))
    except UsageError:
        raise
    except Exception:
        raise UsageError('Gemini limits unavailable') from None


def request_root(home):
    # Deliberately ignore the scan-cache override: diagnostics share quotas.
    return Path(home)/'.cache/podbox-herdremote/usage/requests'


def process_start(pid):
    try:
        return Path('/proc/%s/stat'%pid).read_text().rsplit(')',1)[1].split()[19]
    except (OSError,IndexError):
        return ''


def register_probe_client(home):
    atomic_json(request_root(home)/('client-%d.json'%os.getpid()),
                dict(version=PROBE_BUDGET_VERSION,pid=os.getpid(),start=process_start(os.getpid())))


def legacy_claude_poller(home):
    """A running old daemon cannot share reservations. Do not compete with it."""
    if Path(home) != Path.home():
        return False
    for path in Path('/proc').iterdir():
        if not path.name.isdigit() or int(path.name)==os.getpid():
            continue
        try:
            if path.stat().st_uid != os.getuid():
                continue
            args=(path/'cmdline').read_bytes().split(b'\0')
            if not any(a.endswith(b'/herdremote.py') or a.endswith(b'/herdremote') or
                       a==b'tools.herdremote.herdremote' for a in args):
                continue
            marker=json.loads((request_root(home)/('client-'+path.name+'.json')).read_text())
            if marker.get('version')==PROBE_BUDGET_VERSION and marker.get('start')==process_start(path.name):
                continue
            return True
        except (ValueError,OSError,AttributeError):
            # A vanished process is harmless; an active unregistered daemon isn't.
            if path.exists():
                return True
    return False


def usable_limits(limits, sampled_at, now):
    """Retain only windows whose stated reset has not elapsed."""
    result={}
    if not sampled_at or sampled_at > now:
        return result
    for prefix,duration in [('h5',18000),('wk',WEEK)]:
        pct,reset=limits.get(prefix+'_pct'),limits.get(prefix+'_reset')
        if pct is None and reset is None:
            continue
        if (reset or sampled_at+duration) <= now:
            continue
        result[prefix+'_pct']=pct
        result[prefix+'_reset']=reset
    return result


def valid_good(good):
    if not isinstance(good,dict) or not isinstance(good.get('limits'),dict) or not isinstance(good.get('note'),str):
        return False
    for key,value in dict(good['limits'],at=good.get('at')).items():
        if key not in ('h5_pct','wk_pct','h5_reset','wk_reset','at'):
            return False
        if value is None and key!='at':
            continue
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
            return False
        if key.endswith('_pct') and value>100:
            return False
    return True


def budget_read(path):
    try:
        data=json.loads(path.read_text())
        if data.get('version') != PROBE_BUDGET_VERSION:
            return {}
        for key in ('request_at','next_at','failures'):
            value=data.get(key,0)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
                return {}
        good=data.get('good')
        if good is not None and not valid_good(good):
            return {}
        if not isinstance(data.get('error',''),str):
            return {}
        return data
    except (OSError,ValueError,TypeError,AttributeError):
        return {}


def shared_probe(service, home, now, fetch, seed=None):
    """Reserve BEFORE network I/O under flock; publish only credential-free data."""
    root=request_root(home)
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=root/(service+'.json')
    interval=CLAUDE_INTERVAL if service=='claude' else INTERVAL
    with (root/(service+'.lock')).open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            state=budget_read(path)
            return dict(state,error='limits probe busy',next_at=now+INTERVAL,good=state.get('good') or seed)
        state=budget_read(path)
        if not state.get('good') and seed:
            state['good']=seed
        if service=='claude' and legacy_claude_poller(home):
            state.update(version=PROBE_BUDGET_VERSION,error='legacy Claude poller active',
                         next_at=max(state.get('next_at',0),now+INTERVAL))
            atomic_json(path,state)
            return state
        if now < state.get('next_at',0):
            return state
        request_at=now
        state.update(version=PROBE_BUDGET_VERSION,request_at=request_at,
                     next_at=now+interval,error='limits probe pending')
        atomic_json(path,state)
    # The stable lock file stays distinct from atomically replaced JSON.
    try:
        limits,note=fetch(home,now)
        good=dict(limits=limits,at=now,note=note)
        error=''
        failures=0
        next_at=now+interval
    except UsageError as exc:
        good=state.get('good')
        error=str(exc)
        failures=min(16,int(state.get('failures',0))+1) if exc.status==429 else 0
        delay=max(interval,min(7200,interval*2**max(0,failures-1)),exc.retry_after)
        next_at=now+delay
    except Exception:
        good=state.get('good')
        error='limits probe failed'
        failures=0
        next_at=now+interval
    with (root/(service+'.lock')).open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        latest=budget_read(path)
        if latest.get('request_at') != request_at:
            return latest
        state.update(good=good,error=error,failures=failures,next_at=next_at)
        atomic_json(path,state)
    return state


def keep_limits_on_failure(item, error, now):
    """Use on watchdog failures as well as HTTP failures, with original age."""
    old=item.get('last_good') or dict(limits={k:item.get(k) for k in
        ('h5_pct','wk_pct','h5_reset','wk_reset')},at=item.get('sampled_at',0),note='')
    limits=usable_limits(old['limits'],old['at'],now)
    result=dict(item)
    for key in ('h5_pct','wk_pct','h5_reset','wk_reset'):
        result[key]=limits.get(key)
    result.update(state='ok' if limits else 'err',
                  note=('limits %dm old'%max(0,int((now-old['at'])/60))) if limits else error)
    if limits:
        result['sampled_at']=old['at']
    return result


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
        self.reader = None

    def reset(self):
        if self.reader is not None:
            self.reader.close()
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
        self.cache = cache_root(self.home) / service
        self.window_start = 0
        self.probed_at = 0
        self.live_limits = {}
        self.limit_note = self.limit_error = ''
        self.limit_at = 0
        saved = cached_row(self.home,service)
        if saved:
            self.live_limits = {k:saved.get(k) for k in ('h5_pct','h5_reset','wk_pct','wk_reset')}
            self.limit_at = saved.get('sampled_at',0)
            self.limit_note = saved.get('quota_note',saved.get('note',''))
            self.limit_error = saved.get('probe_error','')
            self.probed_at = saved.get('probe_at') or 0
        self.last_row = saved
        self.scan_cursor = None
        self.next_probe_at = 0
        self.last_good = saved.get('last_good') if saved else None
        if not self.last_good and self.live_limits:
            self.last_good = dict(limits=self.live_limits,at=self.limit_at,note=self.limit_note)

    def paths(self, now):
        roots = {
            'codex': [('.codex/sessions', '**/*.jsonl'), ('.codex/sessions', '**/*.jsonl.zst')],
            'claude': [('.claude/projects', '**/*.jsonl')],
            'grok': [('.grok/sessions', '*/*/usage.json')],
            'gemini': [('.gemini/antigravity-cli/brain', '*/.system_generated/logs/transcript.jsonl')],
        }
        for root, pattern in roots[self.service]:
            base = self.home / root
            for path in base.glob(pattern):
                # Future weekly reset can select up to seven days back. Skip
                # old immutable logs, but keep already indexed files for expiry.
                if path.stat().st_mtime >= max(now - WEEK, self.window_start):
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
            file.events[key] = (at, data.get('model') or data.get('model_id') or AGY_DEFAULT_MODEL,
                                counts(usage, 'gemini'))

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
        self.bytes_read += stat.st_size
        file.signature = sig

    def read_log(self, file, stat, deadline):
        compressed = file.path.name.endswith('.jsonl.zst')
        sig = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        if file.signature == sig and file.complete:
            return True
        if file.signature and (file.signature[0] != stat.st_ino or
                               (compressed and file.signature != sig) or
                               (not compressed and (stat.st_size < file.offset or
                                (stat.st_size == file.offset and file.signature != sig)))):
            file.reset()
        file.signature = sig
        file.complete = False
        if compressed:
            if zstd is None:
                raise UsageError('zstd reader unavailable')
            if file.reader is None:
                file.reader = zstd.open(file.path,'rb')
                file.reader.seek(file.offset)
            opened = contextlib.nullcontext(file.reader)
        else:
            opened = file.path.open('rb')
        with opened as stream:
            if not compressed:
                stream.seek(file.offset)
            while time.monotonic() < deadline and time.process_time() < self.cpu_deadline:
                start = stream.tell()
                line = stream.readline()
                if not line:
                    file.complete = True
                    if compressed:
                        file.reader.close()
                        file.reader = None
                    return True
                if not line.endswith(b'\n'):
                    # Retry a partially appended record on the next refresh.
                    if compressed:
                        file.reader.seek(start)
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

    def index_path(self, path):
        return self.cache / (hashlib.sha256(str(path).encode()).hexdigest() + '.json.z')

    def load_file(self, path):
        file = LogFile(path)
        data = cache_read(self.index_path(path), True)
        try:
            if data.get('path') != str(path):
                return file
            for field in ('offset', 'model', 'session', 'complete'):
                setattr(file, field, data[field])
            file.signature = tuple(data['signature']) if data['signature'] else None
            file.previous = tuple(data['previous'])
            file.limit = tuple(data['limit'])
            if len(file.previous)!=5 or not all(isinstance(n,int) and n>=0 for n in file.previous):
                raise ValueError()
            if len(file.limit)!=3 or not isinstance(file.limit[0],dict) or not isinstance(file.limit[1],(int,float)):
                raise ValueError()
            def freeze(value):
                return tuple(freeze(v) for v in value) if isinstance(value, list) else value
            for name in ('events', 'records'):
                setattr(file, name, {freeze(k): (v[0], v[1], tuple(v[2])) for k, v in data[name]})
            if file.offset < 0 or not isinstance(file.model, str):
                raise ValueError()
            if file.signature is not None and (len(file.signature) != 3 or
                    not all(isinstance(v,int) and v >= 0 for v in file.signature)):
                raise ValueError()
            for events in (file.events,file.records):
                for at,model,tokens in events.values():
                    if not isinstance(at,(int,float)) or not math.isfinite(at) or not isinstance(model,str) or len(tokens)!=5 or not all(isinstance(n,int) and n>=0 for n in tokens):
                        raise ValueError()
            if self.service=='gemini' and path.name=='transcript.jsonl':
                # v3 canonical agy indices used 'unknown' for missing model ids.
                # Reprice those counters without invalidating multi-GB indices.
                file.events={k:(at,AGY_DEFAULT_MODEL if model=='unknown' else model,tokens)
                             for k,(at,model,tokens) in file.events.items()}
            return file
        except (KeyError, TypeError, ValueError):
            return LogFile(path)

    def save_file(self, file):
        data = {k: getattr(file, k) for k in ('offset', 'model', 'session', 'complete',
                'signature', 'previous', 'limit')}
        for key in ('events', 'records'):
            events = getattr(file, key)
            # Retain seven days, so a quota window change can reuse the index.
            events = {k:v for k,v in events.items() if v[0] >= self.now - WEEK}
            setattr(file, key, events)
            data[key] = list(events.items())
        aggregate = {}
        for at,model,tokens in (file.records or file.events).values():
            if self.window_start <= at <= self.now:
                aggregate[model] = [a+b for a,b in zip(aggregate.get(model,[0]*5),tokens)]
        data.update(window_start=self.window_start, window_aggregates=aggregate)
        atomic_json(self.index_path(file.path), dict(data, version=INDEX_VERSION,
                    path=str(file.path)), True)

    def collect(self, now=None, budget=SLICE):
        now = time.time() if now is None else now
        self.now = now
        error = ''
        # Every process uses the same reservation ledger, even cold diagnostics.
        if self.network and now >= self.next_probe_at:
            fetch = {'codex':codex_limits,'claude':claude_limits,
                     'grok':grok_limits,'gemini':gemini_limits}[self.service]
            state=shared_probe(self.service,self.home,now,fetch,self.last_good)
            self.next_probe_at=state.get('next_at',now+INTERVAL)
            self.probed_at=state.get('request_at',self.probed_at)
            self.last_good=state.get('good') or self.last_good
            self.limit_error=state.get('error','')
        good=self.last_good or dict(limits=self.live_limits,at=self.limit_at,note=self.limit_note)
        self.live_limits=dict(good['limits'])
        self.limit_at=good['at']
        self.limit_note=good['note']
        limits=usable_limits(self.live_limits,self.limit_at,now) if self.network else dict(self.live_limits)
        sampled,note,error=self.limit_at or now,self.limit_note,self.limit_error
        if self.network and self.live_limits and not limits and not error:
            error='limits sample expired'
        end = limits.get('wk_reset')
        expired = end is not None and end <= now
        if expired:
            end = None
        start = end - WEEK if end else now - WEEK
        self.window_start = start
        deadline = time.monotonic() + budget
        self.cpu_deadline = time.process_time() + SCAN_CPU_BUDGET
        paths = list(self.paths(now))
        paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        if self.scan_cursor in paths:
            cursor = paths.index(self.scan_cursor)
            paths = paths[cursor:] + paths[:cursor]
        active = set(paths)
        for path in list(self.files):
            if path not in active:
                if self.files[path].reader is not None:
                    self.files[path].reader.close()
                del self.files[path]
        complete = True
        for index, path in enumerate(paths):
            if time.monotonic() >= deadline:
                complete = False
                break
            if path not in self.files:
                self.files[path] = self.load_file(path)
            file = self.files[path]
            self.scan_cursor = paths[(index+1) % len(paths)]
            stat = path.stat()
            before = (file.signature, file.offset, file.complete)
            if path.suffix == '.json':
                self.read_json(file, stat)
                file.complete = True
            elif not self.read_log(file, stat, deadline):
                complete = False
            if before != (file.signature, file.offset, file.complete):
                self.save_file(file)
            if not complete:
                break
        # Offline fixtures and older clients can still use recorded Codex limits.
        if self.service == 'codex' and not self.network:
            limits, at, note = max((f.limit for f in self.files.values()), key=lambda x:x[1], default=({},0,''))
            sampled = at or now
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
                                 tuple(max(a,b) for a,b in zip(old[2],event[2])))
                    entries[key] = event
        cost, unknown = Decimal(0), False
        for _, model, tokens in entries.values():
            out['tokens'] += sum(tokens)
            dollars, known = self.prices.cost(model, tokens)
            cost += dollars
            unknown |= not known and any(tokens)
        out['usd_milli'] = int((cost*1000).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        has_limits = any(limits.get(k) is not None for k in ('h5_pct','wk_pct','h5_reset','wk_reset'))
        out['state'] = 'ok' if has_limits else 'tok' if entries or not complete else 'none'
        if unknown:
            note = (note + '; unpriced') if note.startswith('C/G') else 'unpriced model; cost partial'
        if expired:
            note = 'weekly expired; unpriced model' if unknown else 'weekly sample expired'
        total = sum(p.stat().st_size for p in paths)
        read = sum(min(f.offset if f.path.suffix != '.json' else (f.signature or (0,0))[1],
                       f.path.stat().st_size) for f in self.files.values())
        if not complete:
            note = 'indexing %d%%' % min(99, int(100*read/total) if total else 0)
            if unknown:
                note += '; unpriced'
        out.update(note=note,indexing=not complete,indexed_bytes=read,source_bytes=total,
                   probe_at=self.probed_at,probe_error=self.limit_error,quota_note=self.limit_note,
                   last_good=self.last_good,
                   next_probe_at=self.next_probe_at if self.network else now+INTERVAL)
        if error:
            out=keep_limits_on_failure(out,error,now)
            if out['state']=='ok':
                if not complete:
                    out['note']+='; index %d%%'%min(99,int(100*read/total) if total else 0)
                if unknown:
                    out['note']+='; unpriced'
        self.last_row = out
        return out


def _worker(conn, service, home, network):
    # A hard timeout/close also terminates any read-only RPC subprocess.
    try:
        os.setsid()
    except OSError:
        pass
    try:
        os.nice(10)
    except OSError:
        pass
    collector = Collector(service, home, network=network)
    try:
        while True:
            now = conn.recv()
            cpu = time.process_time()
            wall = time.monotonic()
            child_cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
            before = collector.bytes_read
            try:
                result = collector.collect(now)
                result.update(cpu_seconds=time.process_time()-cpu,
                              child_cpu_seconds=(lambda usage: usage.ru_utime+usage.ru_stime-
                                  child_cpu.ru_utime-child_cpu.ru_stime)(resource.getrusage(resource.RUSAGE_CHILDREN)),
                              wall_seconds=time.monotonic()-wall, updated_at=now,
                              bytes_read=collector.bytes_read-before)
                atomic_json(cache_root(home)/(service+'-row.json'),
                            dict(version=INDEX_VERSION,row=result))
            except Exception:
                previous=dict(collector.last_row or row(service,now))
                if collector.last_good:
                    previous['last_good']=collector.last_good
                result = keep_limits_on_failure(previous,
                                                'local collector failed',now)
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
        self.rows = {}
        for service in SERVICES:
            saved = cached_row(self.home,service)
            self.rows[service] = saved or row(service,time.time(),'tok','indexing 0%')
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
        try:
            os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError:
            if proc.is_alive():
                proc.kill()
        proc.join(timeout=0.2)

    def start(self):
        self.request()

    def _failed(self, service, error):
        now=time.time()
        previous=dict(self.rows[service])
        good=budget_read(request_root(self.home)/(service+'.json')).get('good')
        if good and good['at'] >= (previous.get('last_good') or {}).get('at',0):
            previous['last_good']=good
        result=dict(keep_limits_on_failure(previous,error,now),updated_at=now)
        self.rows[service]=result
        try:
            atomic_json(cache_root(self.home)/(service+'-row.json'),dict(version=INDEX_VERSION,row=result))
        except OSError:
            pass

    def _run(self):
        try:
            register_probe_client(self.home)
        except OSError:
            pass  # Other instances conservatively classify us as uncoordinated.
        context = mp.get_context('spawn')
        scheduled = {s:0 for s in SERVICES}
        pending = {}
        try:
            while not self.stopping.is_set():
                for service in SERVICES:
                    now = time.monotonic()
                    if service not in pending and now >= scheduled[service]:
                        if service not in self.processes:
                            conn, child = context.Pipe()
                            proc = context.Process(target=self.worker,
                                args=(child,service,self.home,self.network),daemon=True)
                            proc.start()
                            child.close()
                            self.processes[service] = proc,conn
                        proc,conn = self.processes[service]
                        try:
                            conn.send(time.time())
                            pending[service] = now+self.timeout
                        except (OSError,EOFError):
                            self._drop(service)
                            scheduled[service] = now+INTERVAL
                            with self.lock:
                                self._failed(service,'collector exited')
                    if service not in pending:
                        continue
                    proc,conn = self.processes[service]
                    try:
                        if conn.poll():
                            result = conn.recv()
                            with self.lock:
                                self.rows[service] = result
                            # Small CPU slices keep indexing alive with Usage closed.
                            scheduled[service] = now+(0.75 if result.get('indexing') else
                                max(.75,min(INTERVAL,result.get('next_probe_at',time.time()+INTERVAL)-time.time())))
                            del pending[service]
                            continue
                    except (OSError,EOFError):
                        pending[service] = 0
                    if now >= pending[service]:
                        self._drop(service)
                        with self.lock:
                            self._failed(service,'collector timeout')
                        del pending[service]
                        scheduled[service] = now+INTERVAL
                self.stopping.wait(.01)
        finally:
            for service in list(self.processes):
                self._drop(service)

    def close(self):
        with self.lock:
            self.stopping.set()
            self.wake.set()
            thread = self.thread
        if thread:
            thread.join(timeout=1.5)
