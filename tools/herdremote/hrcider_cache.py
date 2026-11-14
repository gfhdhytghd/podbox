"""Local Cider metadata and capped native art cache; no credentials or playback state."""
from pathlib import Path
import hashlib
import io
import struct
import json
import os
import sqlite3
import threading
import time

SCHEMA = 2
ART_LIMIT = 500 * 1024 * 1024
KINDS = ('albums', 'songs', 'playlists', 'artists')


def art_id(template):
    return hashlib.sha256(template.encode()).hexdigest() if template else '-'


class Cache:
    def __init__(self, root=None, art_limit=ART_LIMIT):
        self.root = Path(root or Path(os.environ.get('XDG_CACHE_HOME', Path.home()/'.cache'))/'podbox-cider')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.lock = threading.RLock()
        self.art_limit = art_limit
        self.touches = {}
        self.closed = False
        self.db = sqlite3.connect(self.root/'library.sqlite3', check_same_thread=False, timeout=1)
        with self.lock, self.db:
            self.db.execute('PRAGMA journal_mode=WAL')
            version = self.db.execute('PRAGMA user_version').fetchone()[0]
            if version != SCHEMA:
                self.db.executescript('DROP TABLE IF EXISTS items; DROP TABLE IF EXISTS art; DROP TABLE IF EXISTS settings;')
            self.db.executescript('''CREATE TABLE IF NOT EXISTS items (
                kind TEXT, id TEXT, digest TEXT, value TEXT, scan INTEGER, art TEXT,
                PRIMARY KEY(kind,id));
                CREATE INDEX IF NOT EXISTS items_art ON items(art);
                CREATE TABLE IF NOT EXISTS art (key TEXT, tier TEXT, path TEXT,
                bytes INTEGER, touched REAL, PRIMARY KEY(key,tier));
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value INTEGER);
                INSERT OR IGNORE INTO settings VALUES ('generation',1);''')
            self.db.execute('PRAGMA user_version='+str(SCHEMA))
        # Interrupted temp writes are not cache entries. Never retain a large original.
        for p in self.root.glob('*.tmp'):
            p.unlink(missing_ok=True)
        self.evict()

    @property
    def generation(self):
        with self.lock:
            return self.db.execute("SELECT value FROM settings WHERE key='generation'").fetchone()[0]

    def _touches(self):
        for (key,tier),stamp in self.touches.items():
            self.db.execute('UPDATE art SET touched=? WHERE key=? AND tier=?', (stamp,key,tier))
        self.touches.clear()

    def close(self):
        with self.lock:
            if self.closed:return
            with self.db:self._touches()
            self.db.close();self.closed=True

    def ingest(self, kind, items, scan):
        changed = False
        with self.lock, self.db:
            for item in items:
                attrs = item.get('attributes', {})
                template = attrs.get('artwork', {}).get('url', '')
                value = dict(id=item['id'], kind=kind, title=attrs.get('name', ''),
                             subtitle=attrs.get('artistName', ''), count=attrs.get('trackCount', 0),
                             duration=attrs.get('durationInMillis', 0), art=art_id(template),
                             artwork=template, dateAdded=attrs.get('dateAdded', ''))
                raw = json.dumps(value, sort_keys=True, ensure_ascii=False)
                digest = hashlib.sha256(raw.encode()).hexdigest()
                old = self.db.execute('SELECT digest FROM items WHERE kind=? AND id=?', (kind,item['id'])).fetchone()
                changed |= old is None or old[0] != digest
                self.db.execute('INSERT OR REPLACE INTO items VALUES (?,?,?,?,?,?)', (kind,item['id'],digest,raw,scan,value['art']))
            if changed:
                self.db.execute("UPDATE settings SET value=value+1 WHERE key='generation'")
        return changed

    def finish_scan(self, kind, scan):
        with self.lock, self.db:
            count = self.db.execute('DELETE FROM items WHERE kind=? AND scan<>?', (kind,scan)).rowcount
            if count:
                self.db.execute("UPDATE settings SET value=value+1 WHERE key='generation'")

    def page(self, kind, offset=0, limit=50, stale=False):
        if kind not in KINDS or offset < 0 or not 1 <= limit <= 50:
            raise ValueError('Invalid library page')
        with self.lock:
            total = self.db.execute('SELECT count(*) FROM items WHERE kind=?', (kind,)).fetchone()[0]
            rows = self.db.execute('SELECT value FROM items WHERE kind=? ORDER BY id LIMIT ? OFFSET ?', (kind,limit,offset)).fetchall()
            return dict(generation=self.generation, offset=offset, total=total, stale=bool(stale),
                        items=[{k:v for k,v in json.loads(r[0]).items() if k != 'artwork'} for r in rows])

    def template(self, key):
        with self.lock:
            row = self.db.execute('SELECT value FROM items WHERE art=? LIMIT 1',(key,)).fetchone()
            return json.loads(row[0])['artwork'] if row else None

    def get_art(self, key, tier):
        with self.lock:
            row = self.db.execute('SELECT path FROM art WHERE key=? AND tier=?', (key,tier)).fetchone()
            if not row:
                return None
            try:
                raw = (self.root/row[0]).read_bytes()
            except OSError:
                with self.db:self.db.execute('DELETE FROM art WHERE key=? AND tier=?', (key,tier))
                return None
            self.touches[(key,tier)] = time.time()
            return raw

    def put_art(self, key, tier, raw):
        if len(key) != 64 or any(c not in '0123456789abcdef' for c in key) or tier not in ('original','L','M','S','XS'):
            raise ValueError('Invalid art key')
        if tier=='original':
            from PIL import Image
            if len(raw)>2*1024*1024:raise ValueError('Capped original exceeds byte limit')
            with Image.open(io.BytesIO(raw)) as image:
                if image.width>240 or image.height>240:raise ValueError('Capped original exceeds 240 pixels')
        else:
            dim={'L':240,'M':128,'S':56,'XS':32}[tier]
            header=struct.pack('<6H',0x5441,8,dim,dim,int(tier=='M'),0)
            if len(raw)!=12+2*dim*dim or raw[:12]!=header:raise ValueError('Invalid native artwork')
        name = key+'-'+tier+('.img' if tier=='original' else '.aat')
        with self.lock:
            tmp = self.root/(name+'.tmp')
            with tmp.open('wb') as out:
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp,self.root/name)
            self.touches.pop((key,tier),None)
            with self.db:self.db.execute('INSERT OR REPLACE INTO art VALUES (?,?,?,?,?)', (key,tier,name,len(raw),time.time()))
            self.evict()

    def evict(self):
        with self.lock, self.db:
            self._touches()
            rows = self.db.execute('SELECT key,tier,path,bytes FROM art ORDER BY touched DESC').fetchall()
            used = 0
            for key,tier,path,size in rows:
                used += size
                if used > self.art_limit:
                    (self.root/path).unlink(missing_ok=True)
                    self.db.execute('DELETE FROM art WHERE key=? AND tier=?', (key,tier))
            # A rename before its SQLite transaction can leave an orphan after a crash.
            known = {r[0] for r in self.db.execute('SELECT path FROM art')}
            for p in self.root.iterdir():
                if p.suffix in ('.img','.aat') and p.name not in known:
                    p.unlink(missing_ok=True)


class Index:
    """One 50-item request per step; complete scans alone remove missing ids."""
    def __init__(self, cache, client, interval=1):
        self.cache,self.client,self.interval = cache,client,interval
        self.kind = self.offset = 0
        self.scan = time.time_ns()
        self.next = 0
        self.backoff = 1
        self.fingerprints = {}
        self.initial = True
        self.force = False
        self.last_full = 0
        self.requested = threading.Event()
        self.errors = {}

    def request(self):
        self.requested.set()

    def step(self, now=None):
        now = time.monotonic() if now is None else now
        if self.requested.is_set():
            self.requested.clear();self.next=0;self.force=True
            self.kind=self.offset=0;self.scan=time.time_ns()
        if now < self.next:
            return
        if self.kind==0 and self.offset==0 and now-self.last_full>=900:
            self.force=True
        kind = KINDS[self.kind]
        try:
            response = self.client.library(kind,self.offset,50)
            # Apple responses may be wrapped by Cider's API result.
            if isinstance(response.get('data'),dict):response=response['data']
            items = response.get('data')
            if not isinstance(items,list):raise ValueError('Invalid library response')
            total = response.get('meta',{}).get('total')
            fingerprint = (total,tuple((i.get('id'),hashlib.sha256(json.dumps(i.get('attributes',{}),sort_keys=True).encode()).hexdigest()) for i in items))
            unchanged = not self.initial and not self.force and self.offset == 0 and self.fingerprints.get(kind) == fingerprint
            if not unchanged:
                self.cache.ingest(kind,items,self.scan)
            done = unchanged or not response.get('next') and len(items) < 50 or type(total) is int and self.offset+len(items)>=total
            if done:
                if not unchanged:self.cache.finish_scan(kind,self.scan)
                if self.offset == 0:self.fingerprints[kind] = fingerprint
                self.kind += 1;self.offset=0;self.scan=time.time_ns()
                if self.kind == len(KINDS):
                    self.kind=0;self.initial=False;self.next=now+180
                    if self.force:self.last_full=now
                    self.force=False
                else:self.next=now+self.interval
            else:
                if self.offset == 0:self.fingerprints[kind]=fingerprint
                self.offset += len(items);self.next=now+self.interval
            self.backoff=1;self.errors.pop(kind,None)
        except (OSError,ValueError,TimeoutError):
            self.errors[kind]=self.errors.get(kind,0)+1
            self.next=now+min(300,self.backoff);self.backoff=min(300,self.backoff*2)
            if self.errors[kind]>=3:
                # One unsupported endpoint must not starve the other kinds.
                self.kind=(self.kind+1)%len(KINDS);self.offset=0
                self.scan=time.time_ns();self.next=now+180
                self.errors[kind]=0
