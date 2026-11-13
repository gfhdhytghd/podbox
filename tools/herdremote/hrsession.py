"""Bounded, read-only Claude transcript tailing by session UUID."""
from collections import OrderedDict, deque
import hashlib
import json
from pathlib import Path
import re
import threading

UUID = re.compile(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z')
MAX_SCAN = 8 * 1024 * 1024
MAX_LINE = 256 * 1024


def text_content(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    return '\n'.join(str(part.get('text', '')) for part in content
                     if isinstance(part, dict) and part.get('type') in
                     ('text', 'input_text', 'output_text'))


class Tail:
    def __init__(self, path, kind):
        self.path, self.kind = path, kind
        self.identity = None
        self.offset = 0
        self.pending = b''
        self.skip_prefix = False
        self.messages = deque(maxlen=8)
        self.tools = OrderedDict()
        self.ids = deque(maxlen=256)
        self.revision = hashlib.sha256()
        self.records = 0

    def item(self, row):
        if row.get('isSidechain') or row.get('type') not in ('user', 'assistant'):
            return
        item = row.get('message') or {}
        tag = item.get('type')
        role = item.get('role')
        content = item.get('content') or []
        identity = row.get('uuid')
        if identity and identity in self.ids:
            return
        changed = []
        if role in ('user', 'assistant'):
            text = text_content(content).strip()
            if text:
                changed.append((role, text))
                self.messages.append((role, text[-1600:]))
        blocks = content if isinstance(content, list) else []
        if tag in ('function_call', 'custom_tool_call'):
            blocks = [dict(type='tool_use', id=item.get('call_id'), name=item.get('name'))]
        elif tag in ('function_call_output', 'custom_tool_call_output'):
            blocks = [dict(type='tool_result', tool_use_id=item.get('call_id'))]
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use':
                call = str(block.get('id') or '')[:128]
                caption = str(block.get('name') or 'tool')[:60] + ' …'
            elif block.get('type') == 'tool_result':
                call = str(block.get('tool_use_id') or '')[:128]
                caption = self.tools.get(call, 'tool …').removesuffix(' …') + ' done'
            else:
                continue
            if self.tools.get(call) != caption:
                self.tools[call] = caption
                self.tools.move_to_end(call)
                while len(self.tools) > 8:
                    self.tools.popitem(last=False)
                changed.append((call, caption))
        if changed:
            if identity:
                self.ids.append(identity)
            self.revision.update(json.dumps(changed, ensure_ascii=False).encode())
            self.records += 1

    def read(self):
        stat = self.path.stat()
        identity = (stat.st_dev, stat.st_ino)
        if identity != self.identity or stat.st_size < self.offset:
            self.__init__(self.path, self.kind)
            self.identity = identity
            self.offset = max(0, stat.st_size - MAX_SCAN)
            self.skip_prefix = self.offset != 0
        with self.path.open('rb') as source:
            source.seek(self.offset)
            if self.skip_prefix:
                # A bounded initial suffix starts at a complete JSONL record.
                source.readline(MAX_LINE)
                self.offset = source.tell()
                self.skip_prefix = False
            budget = MAX_SCAN
            while budget > 0:
                line = source.readline(min(MAX_LINE + 1, budget))
                if not line:
                    break
                budget -= len(line)
                self.offset = source.tell()
                if len(line) > MAX_LINE or (len(self.pending) + len(line) > MAX_LINE):
                    self.pending = b''
                    continue
                line = self.pending + line
                if not line.endswith(b'\n'):
                    self.pending = line
                    break
                self.pending = b''
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(row, dict):
                    self.item(row)
        recent = list(self.messages)
        say = ''
        if recent:
            role, text = recent[-1]
            say = role.capitalize() + ': ' + '\n'.join(text.splitlines()[-5:])[-280:]
        return dict(task='', say=say, steps=list(self.tools.values())[-3:],
                    input='', cursor=0, revision=self.revision.hexdigest()[:16])


class Sessions:
    def __init__(self, home=None):
        self.home = Path.home() if home is None else Path(home)
        self.cache = OrderedDict()
        self.lock = threading.Lock()

    def read(self, meta):
        session = meta.get('agent_session') or {}
        sid = session.get('value') if session.get('kind') == 'id' else None
        kind = meta.get('kind')
        if kind != 'claude' or not isinstance(sid, str) or not UUID.fullmatch(sid):
            return None
        key = (kind, sid)
        with self.lock:
            tail = self.cache.get(key)
            if tail is None:
                root = self.home / '.claude/projects'
                pattern = '*/' + sid + '.jsonl'
                paths = list(root.glob(pattern))
                if len(paths) != 1:
                    return None
                tail = Tail(paths[0], kind)
                self.cache[key] = tail
                while len(self.cache) > 16:
                    self.cache.popitem(last=False)
            self.cache.move_to_end(key)
            try:
                result = tail.read()
            except OSError:
                self.cache.pop(key, None)
                return None
            result['now'] = meta.get('status', 'unknown')
            result['state_age_s'] = meta.get('state_age_s', -1)
            return result
