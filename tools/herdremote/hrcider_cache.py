"""Local Cider metadata and capped native art cache; no credentials or playback state."""
from pathlib import Path
import hashlib
import re
import io
import struct
import json
import os
import sqlite3
import threading
import time
import zlib
from hrcider_featured import featured_guests

SCHEMA = 4
ART_LIMIT = 500 * 1024 * 1024
KINDS = ('albums', 'songs', 'playlists', 'artists')


def canonical_art_url(template):
    url=re.sub(r'%7B([whfc])%7D',lambda m:'{'+m[1].lower()+'}',template,flags=re.I)
    for key,value in (('w','240'),('h','240'),('f','png'),('c','bb')):
        url=url.replace('{'+key+'}',value)
    return re.sub(r'\d+x\d+(?=(?:bb|cc|sr)?[.])','240x240',url)


def art_id(template):
    return hashlib.sha256(canonical_art_url(template).encode()).hexdigest() if template else '-'


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
            self.db.execute('PRAGMA synchronous=NORMAL')
            version = self.db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (3, SCHEMA):
                self.db.executescript('DROP TABLE IF EXISTS items; DROP TABLE IF EXISTS art; DROP TABLE IF EXISTS settings; DROP TABLE IF EXISTS catalog; DROP TABLE IF EXISTS templates;')
            self.db.executescript('''CREATE TABLE IF NOT EXISTS items (
                kind TEXT, id TEXT, digest TEXT, value TEXT, scan INTEGER, art TEXT,
                PRIMARY KEY(kind,id));
                CREATE INDEX IF NOT EXISTS items_art ON items(art);
                CREATE INDEX IF NOT EXISTS items_name ON items(kind,json_extract(value,'$.title') COLLATE NOCASE,id);
                CREATE TABLE IF NOT EXISTS templates (art TEXT PRIMARY KEY,url TEXT);
                CREATE TABLE IF NOT EXISTS catalog (song TEXT, id TEXT, PRIMARY KEY(song,id));
                CREATE INDEX IF NOT EXISTS catalog_id ON catalog(id);
                CREATE TABLE IF NOT EXISTS art (key TEXT, tier TEXT, path TEXT,
                bytes INTEGER, touched REAL, PRIMARY KEY(key,tier));
                CREATE TABLE IF NOT EXISTS art_crc (key TEXT, tier TEXT, crc INTEGER, PRIMARY KEY(key,tier));
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value INTEGER);
                INSERT OR IGNORE INTO settings VALUES ('generation',1);''')
            if version == 3:self._canonicalize_art_keys()
            self.db.execute('PRAGMA user_version='+str(SCHEMA))
        # Interrupted temp writes are not cache entries. Never retain a large original.
        for p in self.root.glob('*.tmp'):
            p.unlink(missing_ok=True)
        self.evict()

    def _canonicalize_art_keys(self):
        # Preserve the FU23 index and native files; only their lookup keys change.
        for old,url in self.db.execute('SELECT art,url FROM templates').fetchall():
            key=art_id(url)
            if old==key:continue
            self.db.execute('INSERT OR REPLACE INTO templates VALUES (?,?)',(key,url))
            self.db.execute('INSERT OR IGNORE INTO art SELECT ?,tier,path,bytes,touched FROM art WHERE key=?',(key,old))
            self.db.execute('DELETE FROM art WHERE key=?',(old,))
            self.db.execute('DELETE FROM templates WHERE art=?',(old,))
        for kind,key,raw in self.db.execute('SELECT kind,id,value FROM items').fetchall():
            value=json.loads(raw);value['art']=art_id(value.get('artwork',''))
            raw=json.dumps(value,sort_keys=True,ensure_ascii=False)
            digest=hashlib.sha256(raw.encode()).hexdigest()
            self.db.execute('UPDATE items SET digest=?,value=?,art=? WHERE kind=? AND id=?',
                            (digest,raw,value['art'],kind,key))
        self.db.execute("UPDATE settings SET value=value+1 WHERE key='generation'")

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
                             artwork=template, dateAdded=attrs.get('dateAdded', ''),
                             type=item.get('type','library-'+kind),
                             albums=[a['id'] for a in item.get('relationships',{}).get('albums',{}).get('data',[])],
                             catalog=[a['id'] for a in item.get('relationships',{}).get('catalog',{}).get('data',[])],
                             artist=attrs.get('artistName',''),
                             album=attrs.get('albumName',''),
                             albumartist=attrs.get('albumArtistName',attrs.get('artistName','')),
                             composer=attrs.get('composerName',''),
                             genres=attrs.get('genreNames',[]),
                             releaseDate=attrs.get('releaseDate',''),
                             track=attrs.get('trackNumber',0), disc=attrs.get('discNumber',0),
                             rating=attrs.get('rating',0),
                             bg=attrs.get('artwork',{}).get('bgColor','181818'),
                             guests=featured_guests(attrs.get('name','')) +
                                    featured_guests(attrs.get('artistName','')))
                if kind=='songs' and 'rating' not in attrs:
                    previous=self.db.execute('SELECT value FROM items WHERE kind=? AND id=?',(kind,item['id'])).fetchone()
                    if previous:value['rating']=json.loads(previous[0]).get('rating',0)
                raw = json.dumps(value, sort_keys=True, ensure_ascii=False)
                digest = hashlib.sha256(raw.encode()).hexdigest()
                old = self.db.execute('SELECT digest FROM items WHERE kind=? AND id=?', (kind,item['id'])).fetchone()
                changed |= old is None or old[0] != digest
                self.db.execute('INSERT OR REPLACE INTO items VALUES (?,?,?,?,?,?)', (kind,item['id'],digest,raw,scan,value['art']))
                if kind=='songs':
                    self.db.execute('DELETE FROM catalog WHERE song=?',(item['id'],))
                    self.db.executemany('INSERT OR IGNORE INTO catalog VALUES (?,?)',[(item['id'],key) for key in value['catalog']])
            if changed:
                self.db.execute("UPDATE settings SET value=value+1 WHERE key='generation'")
        return changed

    def apply_ratings(self, ratings):
        changed=False
        with self.lock,self.db:
            for item in ratings:
                value=int(item.get('attributes',{}).get('value',0))
                for key,raw in self.db.execute("SELECT items.id,items.value FROM items JOIN catalog ON items.id=catalog.song WHERE items.kind='songs' AND catalog.id=?",(item['id'],)).fetchall():
                    data=json.loads(raw)
                    if data.get('rating',0)==value:continue
                    data['rating']=value
                    encoded=json.dumps(data,sort_keys=True,ensure_ascii=False)
                    self.db.execute("UPDATE items SET value=?,digest=? WHERE kind='songs' AND id=?",(encoded,hashlib.sha256(encoded.encode()).hexdigest(),key))
                    changed=True
            if changed:self.db.execute("UPDATE settings SET value=value+1 WHERE key='generation'")
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
            if row:return json.loads(row[0])['artwork']
            row=self.db.execute('SELECT url FROM templates WHERE art=?',(key,)).fetchone()
            return row[0] if row else None

    def register_templates(self, items):
        with self.lock,self.db:
            for item in items:
                template=item.get('attributes',{}).get('artwork',{}).get('url','')
                if template:self.db.execute('INSERT OR REPLACE INTO templates VALUES (?,?)',(art_id(template),template))

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

    def art_crc(self, key, tier):
        """CRC-32 of the rendered native payload the iPod store keeps, or None if not rendered."""
        with self.lock:
            row=self.db.execute('SELECT crc FROM art_crc WHERE key=? AND tier=?',(key,tier)).fetchone()
            if row:return row[0]
            raw=self.get_art(key,tier)
            if raw is None:return None
            crc=zlib.crc32(raw)
            with self.db:self.db.execute('INSERT OR REPLACE INTO art_crc VALUES (?,?,?)',(key,tier,crc))
            return crc

    def library_arts(self, after='', limit=64):
        """Distinct artwork ids of the indexed library, in id order, after `after`."""
        with self.lock:
            return [r[0] for r in self.db.execute("SELECT DISTINCT art FROM items WHERE art<>'-' AND art>? ORDER BY art LIMIT ?",(after,limit))]

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
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO art VALUES (?,?,?,?,?)', (key,tier,name,len(raw),time.time()))
                if tier!='original':self.db.execute('INSERT OR REPLACE INTO art_crc VALUES (?,?,?)',(key,tier,zlib.crc32(raw)))
            if self.db.execute('SELECT COALESCE(SUM(bytes),0) FROM art').fetchone()[0] > self.art_limit:
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
            self.db.execute('DELETE FROM art_crc WHERE NOT EXISTS (SELECT 1 FROM art WHERE art.key=art_crc.key AND art.tier=art_crc.tier)')
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
        self.rating_jobs=[];self.rating_seen={}
        self.rating_retry=0

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
        if self.rating_jobs and hasattr(self.client,'library_path'):
            ids=self.rating_jobs.pop(0)
            try:
                reply=self.client.library_path('/v1/me/ratings/songs?ids='+','.join(ids))
                if isinstance(reply.get('data'),dict):reply=reply['data']
                if isinstance(reply.get('data'),list):
                    ratings={r['id']:r.get('attributes',{}).get('value',0) for r in reply['data']}
                    self.cache.apply_ratings([{'id':key,'attributes':{'value':ratings.get(key,0)}} for key in ids])
            except (OSError,ValueError,TimeoutError):
                self.rating_jobs.clear();self.rating_retry=now+300
            self.next=now+self.interval
            return
        kind = KINDS[self.kind]
        try:
            response = self.client.library(kind,self.offset,50)
            # Apple responses may be wrapped by Cider's API result.
            if isinstance(response.get('data'),dict):response=response['data']
            items = response.get('data')
            if not isinstance(items,list):raise ValueError('Invalid library response')
            if kind=='songs' and now>=self.rating_retry:
                ids=[r['id'] for item in items for r in item.get('relationships',{}).get('catalog',{}).get('data',[]) if now-self.rating_seen.get(r['id'],-1000)>=180]
                for key in ids:self.rating_seen[key]=now
                if ids:self.rating_jobs.append(ids[:50])
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
