"""Read-only recent Codex items over the existing daemon's Unix WebSocket."""
from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
import re
import threading

UUID = re.compile(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z')


class Reader:
    def __init__(self, socket_path=None, connect=None):
        self.socket_path = socket_path or os.environ.get('PODBOX_CODEX_SOCKET') or str(
            Path.home()/'.codex/app-server-control/app-server-control.sock')
        self.connect = connect
        self.ws = None
        self.sequence = 0
        self.lock = threading.Lock()
        self.latest = OrderedDict()

    def request(self, method, params):
        # This is a read-only allowlist, never a generic app-server client.
        if method not in ('initialize', 'thread/items/list'):
            raise ValueError('read-only app-server method')
        self.sequence += 1
        self.ws.send(json.dumps(dict(id=self.sequence, method=method, params=params)))
        for _ in range(64):
            message = json.loads(self.ws.recv(timeout=2))
            if message.get('id') == self.sequence:
                if 'error' in message:
                    raise ValueError('app-server read unavailable')
                return message['result']
        raise ValueError('notification bound')

    def read(self, meta):
        session = meta.get('agent_session') or {}
        sid = session.get('value') if session.get('kind') == 'id' else None
        if meta.get('kind') != 'codex' or not isinstance(sid, str) or not UUID.fullmatch(sid):
            return None
        with self.lock:
            try:
                if self.ws is None:
                    if self.connect is None:
                        from websockets.sync.client import unix_connect
                        connect = unix_connect
                    else:
                        connect = self.connect
                    self.ws = connect(self.socket_path, uri='ws://localhost/',
                                      open_timeout=2, max_size=2*1024*1024)
                    self.request('initialize', dict(clientInfo=dict(name='podbox-herdremote-reader', version='1'),
                                                   capabilities=dict(experimentalApi=True)))
                    self.ws.send(json.dumps(dict(method='initialized', params={})))
                page = self.request('thread/items/list', dict(threadId=sid, limit=5, sortDirection='desc'))
                rows = page.get('data', [])
                if not isinstance(rows, list) or len(rows) > 5:
                    raise ValueError('item page bound')
                cached = self.latest.get(sid, {})
                latest = cached.get('message', '')
                message_id = cached.get('message_id')
                completed = cached.get('completed')
                steps = []
                found = False
                for row in rows:
                    item = row.get('item', row)
                    kind = item.get('type')
                    if kind == 'agentMessage' and not found:
                        latest = '\n'.join(str(item.get('text', '')).splitlines()[-5:])[-300:]
                        message_id = item.get('id')
                        completed = item.get('completedAtMs', row.get('completedAtMs'))
                        found = True
                    if kind in ('commandExecution', 'fileChange', 'mcpToolCall', 'dynamicToolCall'):
                        label = item.get('command') or item.get('tool') or kind
                        steps.append(str(label)[:56] + ' ' + str(item.get('status', ''))[:16])
                if not steps:
                    steps = cached.get('steps', [])
                self.latest[sid] = dict(message=latest, message_id=message_id,
                                        completed=completed, steps=steps[:3])
                self.latest.move_to_end(sid)
                while len(self.latest) > 16:
                    self.latest.popitem(last=False)
                revision = hashlib.sha256(json.dumps((message_id, completed, latest, steps[:3]), sort_keys=True).encode()).hexdigest()[:16]
                return dict(task='', now=meta.get('status', 'unknown'), say=latest,
                            steps=steps[:3], input='', cursor=0, revision=revision,
                            state_age_s=meta.get('state_age_s', -1))
            except Exception:  # Optional, read-only boundary; fall back to visible data.
                if self.ws is not None:
                    try:
                        self.ws.close()
                    except Exception:
                        pass
                self.ws = None
                return None
