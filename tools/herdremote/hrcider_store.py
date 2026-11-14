"""Host side of the iPod-local cover store: inventory ledger, diff and offers.

The player keeps covers in its own packs behind a hash table. The host never
trusts memory of what it sent: it mirrors the player's (art-id prefix, tier,
CRC) inventory in SQLite, checks it against the player's count/digest on every
connect, and re-lists only on a mismatch. Offers are cheap text; the player
pulls the bytes itself, only while its disk already spins."""
import struct
import threading
import time
import zlib

TIERS = {'L': 0, 'M': 1, 'S': 2, 'XS': 3}
NAMES = {v: k for k, v in TIERS.items()}
LIST_END = 4096 << 5          # player cursor value that ends a listing
FILL_ORDER = ('XS', 'M', 'S')  # whole library, cheapest browse tier first


def item_hash(k8, tier, crc):
    return zlib.crc32(k8 + bytes([tier]) + struct.pack('<I', crc))


def digest_of(items):
    """items: iterable of (k8, tier, crc). Same sum the player keeps."""
    return sum(item_hash(*i) for i in items) & 0xffffffff


class Ledger:
    """What the player holds. Rows are only ever replaced by player reports."""
    def __init__(self, cache):
        self.cache = cache
        with cache.lock, cache.db:
            cache.db.executescript('''CREATE TABLE IF NOT EXISTS ipod_art (
                k8 BLOB, tier INTEGER, crc INTEGER, PRIMARY KEY(k8,tier));
                CREATE TABLE IF NOT EXISTS ipod_meta (id INTEGER PRIMARY KEY, epoch INTEGER);
                INSERT OR IGNORE INTO ipod_meta VALUES (0,0);''')
        self.pending = None          # listing in progress: dict, epoch, expected
        self.synced = False

    def _rows(self):
        return [(bytes(k), t, c) for k, t, c in self.cache.db.execute('SELECT k8,tier,crc FROM ipod_art')]

    def summary(self):
        with self.cache.lock:
            rows = self._rows()
            epoch = self.cache.db.execute('SELECT epoch FROM ipod_meta').fetchone()[0]
        return epoch, len(rows), digest_of(rows)

    def status(self, epoch, count, digest):
        """The player's `ls`. True when the ledger already matches it."""
        self.synced = self.summary() == (epoch, count, digest)
        if not self.synced:
            self.pending = dict(rows={}, epoch=epoch, count=count, digest=digest, cursor=0)
        return self.synced

    def listing_start(self):
        return 0 if self.pending is not None else None

    def page(self, cursor, nxt, rows):
        """One `lv` page. Returns the next cursor to request, or None when the
        listing ended (ledger replaced on a verified match) or failed."""
        p = self.pending
        if p is None or cursor != p['cursor'] or nxt <= cursor:
            self.pending = None
            return None
        for k8, tier, crc in rows:
            p['rows'][(k8, tier)] = crc
        p['cursor'] = nxt
        if nxt < LIST_END:
            return nxt
        self.pending = None
        items = [(k, t, c) for (k, t), c in p['rows'].items()]
        if len(items) != p['count'] or digest_of(items) != p['digest']:
            return None              # inconsistent while the player changed; retry later
        with self.cache.lock, self.cache.db:
            self.cache.db.execute('DELETE FROM ipod_art')
            self.cache.db.executemany('INSERT INTO ipod_art VALUES (?,?,?)', items)
            self.cache.db.execute('UPDATE ipod_meta SET epoch=?', (p['epoch'],))
        self.synced = True
        return None

    def ack(self, key, tier, crc):
        k8 = bytes.fromhex(key[:16])
        with self.cache.lock, self.cache.db:
            self.cache.db.execute('INSERT OR REPLACE INTO ipod_art VALUES (?,?,?)', (k8, TIERS[tier], crc))

    def get(self, key, tier):
        with self.cache.lock:
            row = self.cache.db.execute('SELECT crc FROM ipod_art WHERE k8=? AND tier=?',
                                        (bytes.fromhex(key[:16]), TIERS[tier])).fetchone()
        return row[0] if row else None


def parse_rows(text):
    """`lv` body: 16-hex key prefix, tier digit, 8-hex CRC per line."""
    rows = []
    for line in text.split('\n'):
        if not line:
            continue
        if len(line) != 25 or not line[16].isdigit() or int(line[16]) > 3:
            raise ValueError('Invalid inventory row')
        rows.append((bytes.fromhex(line[:16]), int(line[16]), int(line[17:], 16)))
    return rows


class Planner:
    """Chooses what to offer next: now playing L first, then the library by tier."""
    def __init__(self, cache, ledger, recent=60):
        self.cache, self.ledger, self.recent = cache, ledger, recent
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.tier = 0
        self.after = ''
        self.offered = {}
        self.done_at = None

    def wanted(self, key, tier):
        if not key or key == '-':
            return False
        have = self.ledger.get(key, tier)
        if have is None:
            return True
        mine = self.cache.art_crc(key, tier)   # changed render -> push the new one
        return mine is not None and mine != have

    def next(self, n, priority=(), now=None):
        """Up to n (key, tier) pairs the player lacks or holds stale."""
        now = time.monotonic() if now is None else now
        out = []
        with self.lock:
            self.offered = {k: t for k, t in self.offered.items() if now - t < self.recent}
            if self.tier >= len(FILL_ORDER):
                # A finished pass rescans after a pause: offers the player skipped come back.
                if self.done_at is None:self.done_at = now
                if now - self.done_at >= self.recent:self.tier, self.after, self.done_at = 0, '', None
            for key in priority:
                if len(out) < n and (key, 'L') not in self.offered and self.wanted(key, 'L'):
                    out.append((key, 'L'))
            while len(out) < n and self.tier < len(FILL_ORDER):
                tier = FILL_ORDER[self.tier]
                keys = self.cache.library_arts(self.after, 64)
                if not keys:
                    self.tier += 1
                    self.after = ''
                    continue
                for i, key in enumerate(keys):
                    self.after = key
                    if (key, tier) not in self.offered and self.wanted(key, tier):
                        out.append((key, tier))
                        if len(out) == n:
                            break
            for pair in out:
                self.offered[pair] = now
        return out


def offer_records(generation, pairs):
    """CO records, one tier each, at most five keys."""
    by_tier = {}
    for key, tier in pairs:
        by_tier.setdefault(tier, []).append(key)
    return [f'CO\t{generation}\t{tier}\t{",".join(keys[i:i + 5])}'.encode()
            for tier, keys in by_tier.items() for i in range(0, len(keys), 5)]
