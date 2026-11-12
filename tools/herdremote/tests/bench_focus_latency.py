"""Offline focus timing; optional real herdr measurements are strictly read-only.

Run from the repository root. --revision loads the pinned daemon in memory.
No device discovery, recording, real focus or key injection is performed.
"""
import argparse
import collections
import json
import logging
import os
from pathlib import Path
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import types
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'tools/herdremote'))
import herdremote
import hrherdr
import hrproto
import podlink


class FakeHerdr:
    """Independent connections; delays simulate expensive dashboard reads."""
    def __init__(self, path, slow=False):
        self.path, self.slow = path, slow
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(path)
        self.server.listen(32)
        self.stopping = threading.Event()
        self.read_started = threading.Event()
        self.calls = []
        self.moved = [1]
        self.cursors = {}
        self.texts = {}
        self.read_gates = {}
        self.read_gate = None
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.handlers = []
        self.thread.start()

    def run(self):
        while not self.stopping.is_set():
            try:
                conn, _ = self.server.accept()
            except OSError:
                break
            worker = threading.Thread(target=self.reply, args=(conn,), daemon=True)
            self.handlers.append(worker)
            worker.start()

    def reply(self, conn):
        with conn:
            data = b''
            while not data.endswith(b'\n'):
                chunk = conn.recv(65536)
                if not chunk:
                    return
                data += chunk
            request = json.loads(data)
            method = request['method']
            self.calls.append((method, time.perf_counter()))
            if method == 'ping':
                time.sleep(.002)
                result = dict(capabilities=dict(client_targeted_focus=True))
            elif method == 'client.focus':
                time.sleep(.004)
                result = dict(moved=self.moved)
            elif method == 'client.list':
                time.sleep(.002)
                result = dict(clients=[dict(origin='local')])
            elif method == 'session.snapshot':
                time.sleep(.02 if self.slow else 0)
                result = dict(snapshot=dict(focused_workspace_id='w1', agents=[],
                    workspaces=[dict(workspace_id='w1', label='fake')],
                    tabs=[dict(workspace_id='w1', tab_id='w1:t1', label='build')],
                    panes=[dict(pane_id='w1:p%d' % i, tab_id='w1:t1',
                                agent='codex', agent_status='idle') for i in range(8)]))
            elif method == 'pane.read':
                self.read_started.set()
                gate = self.read_gates.get(request['params'].get('source'), self.read_gate)
                if gate is not None:
                    gate.wait(3)
                time.sleep(.02 if self.slow else 0)
                result = dict(read=dict(text=self.texts.get(request['params']['pane_id'], 'fake activity')))
                if request['params']['pane_id'] in self.cursors:
                    result['read']['cursor'] = self.cursors[request['params']['pane_id']]
            else:
                raise AssertionError('unexpected fake method: ' + method)
            try:
                conn.sendall(json.dumps(dict(id=request['id'], result=result)).encode() + b'\n')
            except (BrokenPipeError, ConnectionResetError):
                pass

    def close(self):
        self.stopping.set()
        self.server.shutdown(socket.SHUT_RDWR)
        self.server.close()
        self.thread.join(2)
        for worker in self.handlers:
            worker.join(2)


class MemoryLink:
    def __init__(self):
        self.opened = True
        self.max_message = 16384
        self.frames = collections.deque()
        self.messages = []
        self.results = collections.deque()
        self.condition = threading.Condition()

    def poll(self):
        pass

    def send(self, kind, payload):
        now = time.perf_counter()
        with self.condition:
            self.messages.append((payload, now))
            if payload.startswith(b'R\t'):
                self.results.append((payload, now))
            self.condition.notify_all()

    def close(self, *args):
        self.opened = False

    def wait_result(self, timeout=3):
        with self.condition:
            if not self.condition.wait_for(lambda: bool(self.results), timeout):
                raise AssertionError('no focus result')
            return self.results.popleft()


def stats(values):
    ordered = sorted(values)
    return dict(n=len(values), median_ms=round(statistics.median(values), 3),
                p95_ms=round(ordered[max(0, int(len(values) * .95 + .999) - 1)], 3),
                max_ms=round(max(values), 3))


def load_daemon(revision):
    if not revision:
        return herdremote
    source = subprocess.check_output(['git', '-C', str(ROOT), 'show',
                                     revision + ':tools/herdremote/herdremote.py'])
    module = types.ModuleType('pinned_daemon')
    module.__file__ = str(ROOT / 'tools/herdremote/herdremote.py')
    exec(compile(source, module.__file__, 'exec'), module.__dict__)
    return module


def fake_benchmark(module, samples):
    output = {}
    for slow in (False, True):
        with tempfile.TemporaryDirectory() as tmp:
            server = FakeHerdr(os.path.join(tmp, 'fake.sock'), slow)
            daemon = module.Daemon(types.SimpleNamespace(socket=server.path, state=None))
            link = MemoryLink()
            daemon.link = link
            daemon.send = lambda payload: link.send(podlink.TEXT, payload)
            daemon.command(b'hr\t1')
            daemon.refresh()
            received = {}
            command = daemon.command
            def observe(payload):
                fields = hrproto.decode(payload)[0]
                if fields[0] in ('fw', 'fp', 'ft'):
                    received[fields[1]] = time.perf_counter()
                return command(payload)
            daemon.command = observe
            try:
                with mock.patch.object(module.hrherdr, 'Watcher'), \
                     mock.patch.object(daemon.answerer, 'start'), \
                     mock.patch.object(daemon.usage, 'start'), \
                     mock.patch.object(daemon.usage, 'close'):
                    runner = threading.Thread(target=daemon.run, daemon=True)
                    server.read_started.clear()
                    runner.start()
                    for kind, args in (('fw', 'w1'), ('fp', 'w1:p0'), ('ft', 'w1\tw1:t1')):
                        elapsed, handler = [], []
                        for i in range(samples):
                            if slow:
                                # Each request arrives while a dashboard read is
                                # outstanding, not between two lucky refreshes.
                                server.read_started.clear()
                                daemon.changed.set()
                                if not server.read_started.wait(2):
                                    raise AssertionError('no background refresh')
                            req = '%s%d' % (kind, i)
                            payload = ('%s\t%s\t%s' % (kind, req, args)).encode()
                            start = time.perf_counter()
                            link.frames.append(types.SimpleNamespace(type=podlink.TEXT, payload=payload))
                            reply, end = link.wait_result()
                            assert hrproto.decode(reply)[0][1:3] == [req, 'ok'], reply
                            elapsed.append((end - start) * 1000)
                            handler.append((end - received[req]) * 1000)
                        output[('loaded' if slow else 'idle') + '_' + kind] = dict(
                            arrival_to_R=stats(elapsed), receipt_to_R=stats(handler))
                    output['loaded' if slow else 'idle'] = dict(
                        dashboard_frames=sum(p.startswith((b'W\t', b'A\t', b'S\t')) or
                            (p.startswith(b'T\t') and len(hrproto.decode(p)[0]) == 7)
                            for p, _ in link.messages),
                        focus_calls=sum(m == 'client.focus' for m, _ in server.calls),
                        snapshot_calls=sum(m == 'session.snapshot' for m, _ in server.calls),
                        pane_read_calls=sum(m == 'pane.read' for m, _ in server.calls),
                        observation_ms=round((server.calls[-1][1] - server.calls[0][1]) * 1000, 3))
                    daemon.stopping.set()
                    runner.join(5)
                    assert not runner.is_alive()
            finally:
                daemon.stopping.set()
                daemon.close_link()
                server.close()
    return output


def real_read_only(samples):
    client = hrherdr.Herdr(timeout=3)
    allowed = {'ping', 'client.list', 'session.snapshot', 'pane.read'}
    original = client.call
    def call(method, **params):
        assert method in allowed, 'read-only allowlist violation'
        return original(method, **params)
    client.call = call
    output = dict(socket=client.path, methods={})
    snapshot = None
    for method, n in (('ping', samples), ('client.list', samples),
                      ('session.snapshot', min(samples, 10))):
        elapsed = []
        for _ in range(n):
            start = time.perf_counter()
            result = client.call(method)
            elapsed.append((time.perf_counter() - start) * 1000)
            if method == 'session.snapshot':
                snapshot = result['snapshot']
        output['methods'][method] = stats(elapsed)
    panes = snapshot.get('panes', [])
    output['pane_count'] = len(panes)
    # Do not write terminal text, labels, ids or snapshots to the evidence file.
    if panes:
        elapsed = []
        for _ in range(min(samples, 10)):
            start = time.perf_counter()
            client.read(panes[0]['pane_id'])
            elapsed.append((time.perf_counter() - start) * 1000)
        output['methods']['pane.read'] = stats(elapsed)
    elapsed = []
    for _ in range(min(samples, 10)):
        start = time.perf_counter()
        current = client.snapshot()
        hrherdr.build_model(current, client.read)
        elapsed.append((time.perf_counter() - start) * 1000)
    output['methods']['snapshot_and_model_reads'] = stats(elapsed)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision')
    parser.add_argument('--samples', type=int, default=10)
    parser.add_argument('--real-read-only', action='store_true')
    args = parser.parse_args()
    logging.getLogger('herdremote').setLevel(logging.ERROR)
    data = (real_read_only(args.samples) if args.real_read_only else
            fake_benchmark(load_daemon(args.revision), args.samples))
    print(json.dumps(data, indent=2))


if __name__ == '__main__':
    main()
