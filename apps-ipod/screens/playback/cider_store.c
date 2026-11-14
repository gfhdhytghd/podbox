/* Cider iPod-local art store. See cider_store.h and cider-remote-protocol.md.
 *
 * Layout: <dir>/index.cst = header sector + CS_BUCKETS bucket sectors;
 * <dir>/pNN.pak = append-only packs of sector-aligned records. A record is a
 * 64-byte self-describing head (key, tier, crc, session/seq) plus the
 * validated .aat payload, so a lost or torn index rebuilds from the packs.
 * Every read verifies head CRC, key and payload CRC; the index is a cache of
 * the packs and a torn sector can never return wrong art.
 * All store calls run on the single Cider disk worker. No big locals. */
#include "screens/playback/cider_store.h"
#include "podlink.h"
#include <string.h>

#define ENTRY 20u
#define BHEAD 8u
#define HEADER_MAGIC 0x31545343u   /* "CST1" */
#define REC_MAGIC 0x32545343u      /* "CST2" */
#define F_OVERFLOW 1u

static uint32_t le32(const uint8_t *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 |
           (uint32_t)p[3] << 24;
}
static unsigned int le16(const uint8_t *p) { return p[0] | p[1] << 8; }
static void st32(uint8_t *p, uint32_t v)
{ p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }
static void st16(uint8_t *p, unsigned int v) { p[0] = v; p[1] = v >> 8; }
static uint32_t crc(const void *p, unsigned int n)
{ return podlink_crc32(p, n); }

unsigned int cs_payload_size(unsigned int tier)
{
    static const unsigned int dim[CS_TIERS] = {240, 128, 56, 32};
    return tier < CS_TIERS ? 12 + dim[tier] * dim[tier] * 2 : 0;
}
unsigned int cs_record_size(unsigned int tier)
{
    unsigned int n = cs_payload_size(tier);
    return n ? (CS_REC_HEAD + n + CS_SECTOR - 1) / CS_SECTOR * CS_SECTOR : 0;
}
int cs_tier(const char *name)
{
    if (!strcmp(name, "L")) return CS_L;
    if (!strcmp(name, "M")) return CS_M;
    if (!strcmp(name, "S")) return CS_S;
    if (!strcmp(name, "XS")) return CS_XS;
    return -1;
}
bool cs_key_from_hex(const char *hex, uint8_t key[32])
{
    for (int i = 0; i < 64; i++)
    {
        char c = hex[i];
        int v = c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ?
                c - 'a' + 10 : -1;
        if (v < 0) return false;
        if (i & 1) key[i / 2] |= v; else key[i / 2] = v << 4;
    }
    return hex[64] == 0;
}
void cs_key_to_hex(const uint8_t key[32], char hex[65])
{
    for (int i = 0; i < 32; i++)
    {
        hex[i * 2] = "0123456789abcdef"[key[i] >> 4];
        hex[i * 2 + 1] = "0123456789abcdef"[key[i] & 15];
    }
    hex[64] = 0;
}

static uint32_t mix(uint32_t a, uint32_t b, unsigned int t)
{
    uint32_t h = a ^ (b * 0x9E3779B1u) ^ (t * 0x85EBCA6Bu);
    h ^= h >> 16; h *= 0x7FEB352Du; h ^= h >> 15; h *= 0x846CA68Bu;
    return h ^ (h >> 16);
}
static void bloom_add(struct cider_store *cs, uint32_t k0, uint32_t k1,
                      unsigned int t)
{
    uint32_t h1 = mix(k0, k1, t), h2 = mix(k1, k0, t + 7) | 1;
    for (unsigned int i = 0; i < 3; i++)
    {
        uint32_t bit = (h1 + i * h2) & (CS_BLOOM_BITS - 1);
        cs->bloom[bit >> 3] |= 1u << (bit & 7);
    }
}
bool cs_maybe(const struct cider_store *cs, const uint8_t key[32],
              unsigned int tier)
{
    if (!cs->loaded || tier >= CS_TIERS) return false;
    uint32_t k0 = le32(key), k1 = le32(key + 4);
    uint32_t h1 = mix(k0, k1, tier), h2 = mix(k1, k0, tier + 7) | 1;
    for (unsigned int i = 0; i < 3; i++)
    {
        uint32_t bit = (h1 + i * h2) & (CS_BLOOM_BITS - 1);
        if (!(cs->bloom[bit >> 3] & (1u << (bit & 7)))) return false;
    }
    return true;
}

uint32_t cs_item_hash(const struct cs_item *item)
{
    uint8_t b[13];
    memcpy(b, item->key, 8); b[8] = item->tier; st32(b + 9, item->crc);
    return crc(b, sizeof b);
}

/* ---- paths and descriptors ---- */

static const char *pack_path(struct cider_store *cs, unsigned int i)
{
    unsigned int n = strlen(cs->dir);
    memcpy(cs->path, cs->dir, n);
    cs->path[n++] = '/'; cs->path[n++] = 'p';
    cs->path[n++] = '0' + i / 10; cs->path[n++] = '0' + i % 10;
    memcpy(cs->path + n, ".pak", 5);
    return cs->path;
}
static const char *index_path(struct cider_store *cs)
{
    unsigned int n = strlen(cs->dir);
    memcpy(cs->path, cs->dir, n);
    memcpy(cs->path + n, "/index.cst", 11);
    return cs->path;
}
static void close_fd(struct cider_store *cs, int *fd)
{
    if (*fd >= 0)
    {
        cs->io->sync(*fd);
        cs->io->close(*fd);
        *fd = -1;
    }
}
void cs_release(struct cider_store *cs)
{
    close_fd(cs, &cs->ifd);
    close_fd(cs, &cs->pfd);
    cs->pfd_index = -1;
}
void cs_unload(struct cider_store *cs)
{
    cs_release(cs);
    cs->loaded = false;
}
static int index_fd(struct cider_store *cs)
{
    if (cs->ifd < 0) cs->ifd = cs->io->open(index_path(cs), CS_O_WRITE);
    return cs->ifd;
}
static int pack_fd(struct cider_store *cs, unsigned int i)
{
    if (cs->pfd >= 0 && cs->pfd_index != (int)i) close_fd(cs, &cs->pfd);
    if (cs->pfd < 0)
    {
        cs->pfd = cs->io->open(pack_path(cs, i), CS_O_WRITE);
        cs->pfd_index = i;
    }
    return cs->pfd;
}
static bool read_at(struct cider_store *cs, int fd, uint32_t off, void *buf,
                    unsigned int n)
{
    return fd >= 0 && cs->io->seek(fd, off) >= 0 &&
           cs->io->read(fd, buf, n) == (int)n;
}
static bool write_at(struct cider_store *cs, int fd, uint32_t off,
                     const void *buf, unsigned int n)
{
    return fd >= 0 && cs->io->seek(fd, off) >= 0 &&
           cs->io->write(fd, buf, n) == (int)n;
}

/* ---- bucket helpers ---- */

static uint8_t *slot(uint8_t *b, unsigned int i) { return b + BHEAD + i * ENTRY; }
static bool slot_valid(const uint8_t *e) { return e[15] & 0x80; }
static void seal(uint8_t *b) { st32(b, crc(b + 4, CS_SECTOR - 4)); }
static bool sealed(const uint8_t *b) { return le32(b) == crc(b + 4, CS_SECTOR - 4); }
static void empty_bucket(uint8_t *b) { memset(b, 0, CS_SECTOR); seal(b); }
static unsigned int bucket_of(uint32_t k0, uint32_t k1, unsigned int t)
{ return mix(k0, k1, t) & (CS_BUCKETS - 1); }
static unsigned int entry_tier(const uint8_t *e) { return e[15] & 3; }

static void item_of(const uint8_t *e, struct cs_item *it)
{
    st32(it->key, le32(e)); st32(it->key + 4, le32(e + 4));
    it->tier = entry_tier(e); it->crc = le32(e + 8);
}
static void account(struct cider_store *cs, const uint8_t *e, int sign)
{
    struct cs_item it; item_of(e, &it);
    uint32_t h = cs_item_hash(&it), bytes = cs_record_size(it.tier);
    unsigned int p = e[14];
    if (sign > 0)
    {
        cs->count++; cs->digest += h;
        if (p < CS_PACKS)
        {
            cs->pack[p].live += bytes;
            unsigned int st = le16(e + 16);
            if ((int16_t)(st - cs->pack[p].last) > 0) cs->pack[p].last = st;
        }
        bloom_add(cs, le32(e), le32(e + 4), it.tier);
    }
    else
    {
        cs->count--; cs->digest -= h;
        if (p < CS_PACKS && cs->pack[p].live >= bytes) cs->pack[p].live -= bytes;
    }
    cs->changed = true;
}

static int bucket_read(struct cider_store *cs, unsigned int b)
{
    int fd = index_fd(cs);
    if (!read_at(cs, fd, (1 + b) * CS_SECTOR, cs->bucket, CS_SECTOR))
        return CS_ERR;
    return sealed(cs->bucket) ? CS_OK : CS_ERR;
}
static int bucket_write(struct cider_store *cs, unsigned int b)
{
    seal(cs->bucket);
    return write_at(cs, index_fd(cs), (1 + b) * CS_SECTOR, cs->bucket,
                    CS_SECTOR) ? CS_OK : CS_ERR;
}

/* Locates key/tier. On CS_OK cs->bucket holds the bucket, *at the slot. */
static int find(struct cider_store *cs, uint32_t k0, uint32_t k1,
                unsigned int t, unsigned int *bucket, unsigned int *at)
{
    unsigned int home = bucket_of(k0, k1, t);
    for (unsigned int p = 0; p < CS_PROBE; p++)
    {
        unsigned int b = (home + p) & (CS_BUCKETS - 1);
        if (bucket_read(cs, b) != CS_OK) return CS_ERR;
        for (unsigned int i = 0; i < CS_SLOTS; i++)
        {
            const uint8_t *e = slot(cs->bucket, i);
            if (slot_valid(e) && le32(e) == k0 && le32(e + 4) == k1 &&
                entry_tier(e) == t)
            { *bucket = b; *at = i; return CS_OK; }
        }
        if (!(le16(cs->bucket + 4) & F_OVERFLOW)) break;
    }
    return CS_MISS;
}

static void fill_entry(uint8_t *e, uint32_t k0, uint32_t k1, unsigned int t,
                       uint32_t c, unsigned int pack, uint32_t sector,
                       unsigned int stamp)
{
    st32(e, k0); st32(e + 4, k1); st32(e + 8, c);
    st16(e + 12, sector); e[14] = pack; e[15] = 0x80 | t;
    st16(e + 16, stamp); st16(e + 18, 0);
}

/* Inserts or replaces. The caller already appended the record. */
static int index_put(struct cider_store *cs, uint32_t k0, uint32_t k1,
                     unsigned int t, uint32_t c, unsigned int pack,
                     uint32_t sector)
{
    unsigned int b, i, home = bucket_of(k0, k1, t);
    unsigned int stamp = cs->session & 0xffff;
    int r = find(cs, k0, k1, t, &b, &i);
    if (r == CS_ERR) return CS_ERR;
    if (r == CS_OK)
    {
        uint8_t *e = slot(cs->bucket, i);
        account(cs, e, -1);
        fill_entry(e, k0, k1, t, c, pack, sector, stamp);
        account(cs, e, 1);
        return bucket_write(cs, b);
    }
    for (unsigned int p = 0; p < CS_PROBE; p++)
    {
        b = (home + p) & (CS_BUCKETS - 1);
        if (bucket_read(cs, b) != CS_OK) return CS_ERR;
        for (i = 0; i < CS_SLOTS; i++)
            if (!slot_valid(slot(cs->bucket, i)))
            {
                uint8_t *e = slot(cs->bucket, i);
                fill_entry(e, k0, k1, t, c, pack, sector, stamp);
                account(cs, e, 1);
                if (bucket_write(cs, b) != CS_OK) return CS_ERR;
                /* Earlier buckets of the chain are full: mark overflow. */
                for (unsigned int q = 0; q < p; q++)
                {
                    unsigned int pb = (home + q) & (CS_BUCKETS - 1);
                    if (bucket_read(cs, pb) != CS_OK) return CS_ERR;
                    st16(cs->bucket + 4, le16(cs->bucket + 4) | F_OVERFLOW);
                    if (bucket_write(cs, pb) != CS_OK) return CS_ERR;
                }
                return CS_OK;
            }
    }
    /* Chain full: replace the least recently used entry of the home bucket. */
    if (bucket_read(cs, home) != CS_OK) return CS_ERR;
    unsigned int victim = 0;
    for (i = 1; i < CS_SLOTS; i++)
        if ((int16_t)(le16(slot(cs->bucket, i) + 16) -
                      le16(slot(cs->bucket, victim) + 16)) < 0) victim = i;
    uint8_t *e = slot(cs->bucket, victim);
    account(cs, e, -1);
    fill_entry(e, k0, k1, t, c, pack, sector, stamp);
    account(cs, e, 1);
    st16(cs->bucket + 4, le16(cs->bucket + 4) | F_OVERFLOW);
    return bucket_write(cs, home);
}

static int index_drop(struct cider_store *cs, uint32_t k0, uint32_t k1,
                      unsigned int t)
{
    unsigned int b, i;
    int r = find(cs, k0, k1, t, &b, &i);
    if (r != CS_OK) return r;
    uint8_t *e = slot(cs->bucket, i);
    account(cs, e, -1);
    memset(e, 0, ENTRY);
    return bucket_write(cs, b);
}

/* ---- init / load ---- */

void cs_init(struct cider_store *cs, const struct cs_io *io, const char *dir,
             uint32_t cap)
{
    memset(cs, 0, sizeof *cs);
    cs->io = io;
    strncpy(cs->dir, dir, sizeof cs->dir - 1);
    cs->cap = cap;
    cs->ifd = cs->pfd = cs->pfd_index = -1;
    cs->cur = cs->evict = cs->compact = -1;
}

static bool table_create(struct cider_store *cs, uint8_t *buf,
                         unsigned int size, uint32_t seed)
{
    unsigned int per = CS_SCAN_BUCKETS * CS_SECTOR;
    if (size < per) return false;
    close_fd(cs, &cs->ifd);
    cs->io->remove(index_path(cs));
    int fd = index_fd(cs);
    if (fd < 0) return false;
    memset(buf, 0, CS_SECTOR);
    st32(buf, HEADER_MAGIC); st32(buf + 4, 1); st32(buf + 8, CS_BUCKETS);
    st32(buf + 12, seed ? seed : 1); st32(buf + 16, 0);
    st32(buf + 508, crc(buf, 508));
    if (!write_at(cs, fd, 0, buf, CS_SECTOR)) return false;
    empty_bucket(buf);
    for (unsigned int i = 1; i < CS_SCAN_BUCKETS; i++)
        memcpy(buf + i * CS_SECTOR, buf, CS_SECTOR);
    for (unsigned int b = 0; b < CS_BUCKETS; b += CS_SCAN_BUCKETS)
        if (!write_at(cs, fd, (1 + b) * CS_SECTOR, buf, per)) return false;
    cs->epoch = seed ? seed : 1;
    return cs->io->sync(fd) >= 0;
}

static bool header_write(struct cider_store *cs)
{
    uint8_t *h = cs->bucket;     /* scratch: keeps the worker stack small */
    memset(h, 0, CS_SECTOR);
    st32(h, HEADER_MAGIC); st32(h + 4, 1); st32(h + 8, CS_BUCKETS);
    st32(h + 12, cs->epoch); st32(h + 16, cs->session);
    st32(h + 508, crc(h, 508));
    return write_at(cs, index_fd(cs), 0, h, CS_SECTOR);
}

static void pack_head(struct cider_store *cs, unsigned int i)
{
    uint8_t *h = cs->bucket;
    struct cs_pack *p = &cs->pack[i];
    int fd = pack_fd(cs, i);
    p->session0 = p->seq0 = 0;
    if (read_at(cs, fd, 0, h, CS_SECTOR) && le32(h) == REC_MAGIC &&
        le32(h + 60) == crc(h, 60))
    { p->seq0 = le32(h + 4); p->session0 = le32(h + 52); }
}
static bool older(const struct cs_pack *a, const struct cs_pack *b)
{
    return a->session0 != b->session0 ? a->session0 < b->session0 :
                                        a->seq0 < b->seq0;
}

static void rebuild_begin(struct cider_store *cs)
{
    cs->order_n = 0;
    for (unsigned int i = 0; i < CS_PACKS; i++)
        if (cs->pack[i].exists)
        {
            unsigned int j = cs->order_n++;
            while (j && older(&cs->pack[i], &cs->pack[cs->order[j - 1]]))
            { cs->order[j] = cs->order[j - 1]; j--; }
            cs->order[j] = i;
        }
    cs->order_at = 0; cs->rebuild_off = 0;
    cs->rebuild = cs->order_n > 0;
}

int cs_load(struct cider_store *cs, uint8_t *buf, unsigned int size,
            uint32_t seed)
{
    cs_release(cs);
    cs->loaded = false; cs->count = cs->digest = cs->total = 0;
    cs->rebuild = false; cs->evict = cs->compact = cs->cur = -1;
    memset(cs->bloom, 0, sizeof cs->bloom);
    memset(cs->pack, 0, sizeof cs->pack);
    if (size < CS_SCAN_BUCKETS * CS_SECTOR) return CS_ERR;
    cs->io->mkdir(cs->dir);
    unsigned int packs = 0;
    uint32_t maxs = 0;
    for (unsigned int i = 0; i < CS_PACKS; i++)
    {
        int n = cs->io->fsize(pack_path(cs, i));
        if (n < 0) continue;
        cs->pack[i].exists = true; cs->pack[i].size = n;
        cs->total += n; packs++;
        pack_head(cs, i);
        if (cs->pack[i].session0 > maxs) maxs = cs->pack[i].session0;
    }
    bool fresh = false, ok = false;
    uint8_t *h = cs->bucket;
    int isz = cs->io->fsize(index_path(cs));
    if (isz == (int)((1 + CS_BUCKETS) * CS_SECTOR) &&
        read_at(cs, index_fd(cs), 0, h, CS_SECTOR) &&
        le32(h) == HEADER_MAGIC && le32(h + 8) == CS_BUCKETS &&
        le32(h + 508) == crc(h, 508))
    {
        cs->epoch = le32(h + 12); cs->session = le32(h + 16);
        ok = true;
    }
    if (!ok)
    {
        if (!table_create(cs, buf, size, seed)) return CS_ERR;
        cs->session = maxs;
        fresh = true;
    }
    cs->session++;
    if (!header_write(cs)) return CS_ERR;
    unsigned int bad = 0;
    if (!fresh)
        for (unsigned int b = 0; b < CS_BUCKETS && !bad; b += CS_SCAN_BUCKETS)
        {
            if (!read_at(cs, cs->ifd, (1 + b) * CS_SECTOR, buf,
                         CS_SCAN_BUCKETS * CS_SECTOR)) { bad++; break; }
            for (unsigned int j = 0; j < CS_SCAN_BUCKETS; j++)
            {
                uint8_t *bk = buf + j * CS_SECTOR;
                if (!sealed(bk)) { bad++; break; }
                for (unsigned int i = 0; i < CS_SLOTS; i++)
                    if (slot_valid(slot(bk, i)))
                        account(cs, slot(bk, i), 1);
            }
            if (cs->io->yield) cs->io->yield();
        }
    if (bad)
    {   /* Torn or unreadable table: the packs are the journal. */
        uint32_t keep = cs->session;
        if (!table_create(cs, buf, size, cs->epoch ? cs->epoch : seed))
            return CS_ERR;
        cs->session = keep + 1;
        header_write(cs);
        cs->count = cs->digest = 0;
        memset(cs->bloom, 0, sizeof cs->bloom);
        for (unsigned int i = 0; i < CS_PACKS; i++) cs->pack[i].live = 0;
        rebuild_begin(cs);
    }
    else if (fresh && packs) rebuild_begin(cs);
    /* Append target: the newest pack that still has room. */
    for (unsigned int i = 0; i < CS_PACKS; i++)
        if (cs->pack[i].exists && cs->pack[i].size % CS_SECTOR == 0 &&
            cs->pack[i].size + CS_REC_MAX <= CS_PACK_BYTES &&
            (cs->cur < 0 || older(&cs->pack[cs->cur], &cs->pack[i])))
            cs->cur = i;
    cs->changed = true;
    cs->loaded = true;
    return CS_OK;
}

bool cs_busy(const struct cider_store *cs)
{
    return cs->rebuild || cs->evict >= 0;
}

/* ---- append ---- */

static int choose_pack(struct cider_store *cs, unsigned int need)
{
    if (cs->cur >= 0 && cs->pack[cs->cur].size % CS_SECTOR)
        cs->cur = -1;          /* torn tail: never append behind it */
    if (cs->cur >= 0 && cs->pack[cs->cur].size + need <= CS_PACK_BYTES)
        return cs->cur;
    for (unsigned int i = 0; i < CS_PACKS; i++)
        if (!cs->pack[i].exists) { cs->cur = i; return i; }
    return -1;
}

static int pick_victim(struct cider_store *cs)
{
    int v = -1;
    for (unsigned int i = 0; i < CS_PACKS; i++)
    {
        if (!cs->pack[i].exists || (int)i == cs->cur ||
            (int)i == cs->compact) continue;
        if (v < 0 ||
            (int16_t)(cs->pack[i].last - cs->pack[v].last) < 0 ||
            (cs->pack[i].last == cs->pack[v].last &&
             older(&cs->pack[i], &cs->pack[v]))) v = i;
    }
    if (v < 0 && cs->cur >= 0) v = cs->cur;
    return v;
}

static void record_head(struct cider_store *cs, uint8_t *rec,
                        const uint8_t key[32], unsigned int tier,
                        unsigned int length, uint32_t c)
{
    memset(rec, 0, CS_REC_HEAD);
    st32(rec, REC_MAGIC); st32(rec + 4, ++cs->seq);
    memcpy(rec + 8, key, 32); rec[40] = tier;
    st32(rec + 44, length); st32(rec + 48, c); st32(rec + 52, cs->session);
    st32(rec + 60, crc(rec, 60));
}

/* Appends a prepared record (head + payload already in buf). */
static int append(struct cider_store *cs, const uint8_t key[32],
                  unsigned int tier, uint8_t *buf, uint32_t c)
{
    unsigned int need = cs_record_size(tier);
    int p = choose_pack(cs, need);
    if (p < 0) return CS_BUSY;
    int fd = pack_fd(cs, p);
    uint32_t off = cs->pack[p].size;
    if (!write_at(cs, fd, off, buf, need)) { close_fd(cs, &cs->pfd); return CS_ERR; }
    if (cs->io->sync(fd) < 0) return CS_ERR;
    cs->pack[p].exists = true;
    cs->pack[p].size += need; cs->total += need;
    if (!off) { cs->pack[p].session0 = cs->session; cs->pack[p].seq0 = cs->seq; }
    int r = index_put(cs, le32(key), le32(key + 4), tier, c, p, off / CS_SECTOR);
    return r == CS_OK ? CS_OK : CS_ERR;
}

int cs_put(struct cider_store *cs, const uint8_t key[32], unsigned int tier,
           const void *payload, unsigned int length, uint8_t *buf,
           unsigned int size, uint32_t *crc_out)
{
    if (!cs->loaded) return CS_NOTREADY;
    unsigned int need = cs_record_size(tier);
    if (!need || length != cs_payload_size(tier) || size < need) return CS_BAD;
    if (cs->rebuild || cs->evict >= 0) return CS_BUSY;
    uint32_t c = crc(payload, length);
    if (crc_out) *crc_out = c;
    unsigned int b, i;
    int r = find(cs, le32(key), le32(key + 4), tier, &b, &i);
    if (r == CS_ERR) return CS_ERR;
    if (r == CS_OK && le32(slot(cs->bucket, i) + 8) == c) return CS_DUP;
    if (cs->total + need > cs->cap ||
        (r == CS_MISS && cs->count >= CS_MAX_ENTRIES) ||
        choose_pack(cs, need) < 0)
    {
        int v = pick_victim(cs);
        if (v < 0) return CS_BUSY;
        cs->evict = v; cs->evict_at = 0;
        return CS_BUSY;
    }
    memmove(buf + CS_REC_HEAD, payload, length);
    memset(buf + CS_REC_HEAD + length, 0, need - CS_REC_HEAD - length);
    record_head(cs, buf, key, tier, length, c);
    return append(cs, key, tier, buf, c);
}

/* ---- read ---- */

int cs_get(struct cider_store *cs, const uint8_t key[32], unsigned int tier,
           uint8_t *buf, unsigned int size, const uint8_t **payload,
           unsigned int *length)
{
    if (!cs->loaded) return CS_NOTREADY;
    unsigned int need = cs_record_size(tier), b, i;
    if (!need || size < need) return CS_BAD;
    uint32_t k0 = le32(key), k1 = le32(key + 4);
    int r = find(cs, k0, k1, tier, &b, &i);
    if (r != CS_OK) return r;
    uint8_t e[ENTRY];
    memcpy(e, slot(cs->bucket, i), ENTRY);
    unsigned int p = e[14], len = cs_payload_size(tier);
    uint32_t off = le16(e + 12) * CS_SECTOR;
    bool good = p < CS_PACKS && cs->pack[p].exists &&
                off + need <= cs->pack[p].size &&
                read_at(cs, pack_fd(cs, p), off, buf, need) &&
                le32(buf) == REC_MAGIC && le32(buf + 60) == crc(buf, 60) &&
                !memcmp(buf + 8, key, 32) && buf[40] == tier &&
                le32(buf + 44) == len && le32(buf + 48) == le32(e + 8) &&
                crc(buf + CS_REC_HEAD, len) == le32(e + 8);
    if (!good)
    {
        index_drop(cs, k0, k1, tier);
        return CS_ERR;
    }
    unsigned int stamp = cs->session & 0xffff;
    if (le16(e + 16) != stamp && find(cs, k0, k1, tier, &b, &i) == CS_OK)
    {
        st16(slot(cs->bucket, i) + 16, stamp);
        if (bucket_write(cs, b) == CS_OK &&
            (int16_t)(stamp - cs->pack[p].last) > 0) cs->pack[p].last = stamp;
    }
    *payload = buf + CS_REC_HEAD; *length = len;
    return CS_OK;
}

/* ---- inventory ---- */

int cs_list(struct cider_store *cs, unsigned int cursor, struct cs_item *out,
            unsigned int max, unsigned int *count, uint8_t *buf,
            unsigned int size)
{
    unsigned int b = cursor >> 5, i = cursor & 31;
    *count = 0;
    if (!cs->loaded) return CS_NOTREADY;
    if (size < CS_SECTOR || !max) return CS_BAD;
    for (; b < CS_BUCKETS; b++, i = 0)
    {
        if (!read_at(cs, index_fd(cs), (1 + b) * CS_SECTOR, buf, CS_SECTOR) ||
            !sealed(buf)) return CS_ERR;
        for (; i < CS_SLOTS; i++)
            if (slot_valid(slot(buf, i)))
            {
                if (*count == max) return b << 5 | i;
                item_of(slot(buf, i), &out[(*count)++]);
            }
    }
    return CS_BUCKETS << 5;
}

/* ---- maintenance: rebuild, evict, compact ---- */

static void drop_pack(struct cider_store *cs, int p)
{
    close_fd(cs, &cs->pfd);
    cs->io->remove(pack_path(cs, p));
    cs->total -= cs->pack[p].size;
    memset(&cs->pack[p], 0, sizeof cs->pack[p]);
    if (cs->cur == p) cs->cur = -1;
    cs->changed = true;
}

static bool step_evict(struct cider_store *cs, uint8_t *buf)
{
    unsigned int b = cs->evict_at, n = CS_BUCKETS - b;
    if (n > CS_SCAN_BUCKETS) n = CS_SCAN_BUCKETS;
    int fd = index_fd(cs);
    if (!read_at(cs, fd, (1 + b) * CS_SECTOR, buf, n * CS_SECTOR))
    { cs->evict = -1; return false; }
    bool dirty = false;
    for (unsigned int j = 0; j < n; j++)
    {
        uint8_t *bk = buf + j * CS_SECTOR;
        if (!sealed(bk)) continue;
        bool hit = false;
        for (unsigned int i = 0; i < CS_SLOTS; i++)
        {
            uint8_t *e = slot(bk, i);
            if (slot_valid(e) && e[14] == cs->evict)
            { account(cs, e, -1); memset(e, 0, ENTRY); hit = true; }
        }
        if (hit) { seal(bk); dirty = true; }
    }
    if (dirty && !write_at(cs, fd, (1 + b) * CS_SECTOR, buf, n * CS_SECTOR))
    { cs->evict = -1; return false; }
    cs->evict_at += n;
    if (cs->evict_at >= CS_BUCKETS)
    {
        cs->io->sync(fd);
        drop_pack(cs, cs->evict);
        cs->evict = -1;
        return false;
    }
    return true;
}

static bool step_rebuild(struct cider_store *cs, uint8_t *buf)
{
    for (unsigned int n = 0; n < 8; n++)
    {
        if (cs->order_at >= cs->order_n) { cs->rebuild = false; return false; }
        unsigned int p = cs->order[cs->order_at];
        uint32_t off = cs->rebuild_off;
        int fd = pack_fd(cs, p);
        if (!read_at(cs, fd, off, buf, CS_SECTOR) || le32(buf) != REC_MAGIC ||
            le32(buf + 60) != crc(buf, 60) || buf[40] >= CS_TIERS ||
            le32(buf + 44) != cs_payload_size(buf[40]) ||
            off + cs_record_size(buf[40]) > cs->pack[p].size)
        { cs->order_at++; cs->rebuild_off = 0; continue; }
        unsigned int t = buf[40];
        uint32_t c = le32(buf + 48), seq = le32(buf + 4);
        if (seq > cs->seq && le32(buf + 52) == cs->session) cs->seq = seq;
        index_put(cs, le32(buf + 8), le32(buf + 12), t, c, p, off / CS_SECTOR);
        cs->rebuild_off += cs_record_size(t);
    }
    return true;
}

static bool step_compact(struct cider_store *cs, uint8_t *buf, unsigned int size)
{
    int p = cs->compact;
    for (unsigned int n = 0; n < 4; n++)
    {
        uint32_t off = cs->compact_off;
        if (off >= cs->pack[p].size) goto done;
        if (!read_at(cs, pack_fd(cs, p), off, buf, CS_SECTOR) ||
            le32(buf) != REC_MAGIC || le32(buf + 60) != crc(buf, 60) ||
            buf[40] >= CS_TIERS) { cs->compact = -1; return false; }
        unsigned int t = buf[40], need = cs_record_size(t), b, i;
        uint8_t key[32];
        memcpy(key, buf + 8, 32);
        if (need > size) { cs->compact = -1; return false; }
        int r = find(cs, le32(key), le32(key + 4), t, &b, &i);
        if (r == CS_ERR) { cs->compact = -1; return false; }
        const uint8_t *e = slot(cs->bucket, i);
        if (r == CS_OK && e[14] == p && le16(e + 12) == off / CS_SECTOR)
        {
            if (!read_at(cs, pack_fd(cs, p), off, buf, need) ||
                cs->total + need > cs->cap + CS_PACK_BYTES)
            { cs->compact = -1; return false; }
            uint32_t c = le32(buf + 48);
            if (crc(buf + CS_REC_HEAD, le32(buf + 44)) != c)
                index_drop(cs, le32(key), le32(key + 4), t);
            else
            {
                /* Re-stamp the head: the copy sorts after its source. */
                record_head(cs, buf, key, t, le32(buf + 44), c);
                if (append(cs, key, t, buf, c) != CS_OK)
                { cs->compact = -1; return false; }
            }
        }
        cs->compact_off += need;
    }
    return true;
done:
    drop_pack(cs, p);
    cs->compact = -1;
    return false;
}

bool cs_maintain(struct cider_store *cs, uint8_t *buf, unsigned int size,
                 bool allow_compact)
{
    if (!cs->loaded) return false;
    if (cs->rebuild) return step_rebuild(cs, buf);
    if (cs->evict >= 0) return step_evict(cs, buf);
    if (cs->compact >= 0) return allow_compact && step_compact(cs, buf, size);
    if (!allow_compact) return false;
    for (unsigned int i = 0; i < CS_PACKS; i++)
        if (cs->pack[i].exists && (int)i != cs->cur &&
            cs->pack[i].size >= CS_PACK_BYTES / 4 &&
            (uint64_t)cs->pack[i].live * 100 < (uint64_t)cs->pack[i].size * 30)
        {
            cs->compact = i; cs->compact_off = 0;
            return true;
        }
    return false;
}

/* ---- wire parsing (no sscanf in the firmware libc) ---- */

static bool dec(const char **p, const char *end, uint32_t *v)
{
    uint64_t n = 0; unsigned int digits = 0;
    while (*p < end && **p >= '0' && **p <= '9')
    { n = n * 10 + (**p - '0'); if (n > 0xffffffffu) return false; (*p)++; digits++; }
    *v = n;
    return digits > 0 && digits <= 10;
}

/* "CO<TAB>gen<TAB>tier<TAB>key,key,..." -> tier, keys; returns key count. */
int cs_parse_offer(const char *s, unsigned int n, uint32_t gen, int *tier,
                   uint8_t keys[][32], unsigned int max)
{
    const char *p = s, *end = s + n;
    uint32_t g;
    if (n < 8 || memcmp(p, "CO\t", 3)) return -1;
    p += 3;
    if (!dec(&p, end, &g) || g != gen || p >= end || *p++ != '\t') return -1;
    const char *t = p;
    while (p < end && *p != '\t') p++;
    char name[3];
    if (p >= end || p - t < 1 || p - t > 2) return -1;
    memcpy(name, t, p - t); name[p - t] = 0; p++;
    *tier = cs_tier(name);
    if (*tier < 0) return -1;
    unsigned int count = 0;
    while (p < end)
    {
        char hex[65];
        if (end - p < 64 || count >= max) return -1;
        memcpy(hex, p, 64); hex[64] = 0;
        if (!cs_key_from_hex(hex, keys[count])) return -1;
        count++; p += 64;
        if (p < end && *p++ != ',') return -1;
    }
    return count;
}

/* "CQ<TAB>gen<TAB>cursor" -> cursor, or -1. */
int cs_parse_cursor(const char *s, unsigned int n, uint32_t gen, uint32_t *cursor)
{
    const char *p = s, *end = s + n;
    uint32_t g;
    if (n < 6 || memcmp(p, "CQ\t", 3)) return -1;
    p += 3;
    if (!dec(&p, end, &g) || g != gen || p >= end || *p++ != '\t') return -1;
    if (!dec(&p, end, cursor) || p != end) return -1;
    return 0;
}
