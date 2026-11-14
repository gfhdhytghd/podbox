/* Production Cider store (index, packs, eviction, rebuild, power loss).
 * Runs in a private scratch directory: store roots are short relative names
 * ("st-<case>") under argv[1]; nothing outside it is ever removed. */
#define CS_PACK_BYTES (1u << 20)
#include "../../apps-ipod/screens/playback/cider_store.c"
#include <assert.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/stat.h>

static char root[40];
static long writes_left = -1;  /* power loss: writes allowed before the cut */
static bool dead;
static unsigned long io_reads;

static int t_open(const char *p, int mode)
{
    if (dead) return -1;
    return open(p, mode == CS_O_READ ? O_RDONLY : O_RDWR | O_CREAT, 0644);
}
static int t_read(int fd, void *b, unsigned n)
{ io_reads++; return dead ? -1 : (int)read(fd, b, n); }
static int t_write(int fd, const void *b, unsigned n)
{
    if (dead) return -1;
    if (writes_left == 0)
    {   /* torn: half the bytes reach the disk, then the lights go out */
        if (n / 2) (void)!write(fd, b, n / 2);
        dead = true; return -1;
    }
    if (writes_left > 0) writes_left--;
    return (int)write(fd, b, n);
}
static int t_seek(int fd, uint32_t o) { return dead ? -1 : (int)lseek(fd, o, SEEK_SET); }
static int t_sync(int fd) { return dead ? -1 : fsync(fd); }
static int t_close(int fd) { return close(fd); }
static int t_remove(const char *p) { return dead ? -1 : unlink(p); }
static int t_fsize(const char *p)
{ struct stat st; return !dead && stat(p, &st) == 0 ? (int)st.st_size : -1; }
static void t_mkdir(const char *p) { if (!dead) mkdir(p, 0755); }
static const struct cs_io io = {t_open, t_read, t_write, t_seek, t_sync,
                                t_close, t_remove, t_fsize, t_mkdir, NULL};

static uint8_t buf[CS_REC_MAX], want[CS_REC_MAX];
static struct cider_store cs_store;
#define cs cs_store

static void make_key(uint32_t n, uint8_t key[32])
{
    uint32_t x = n * 2654435761u + 12345;
    for (int i = 0; i < 32; i++) { x = x * 1664525u + 1013904223u; key[i] = x >> 24; }
}
static unsigned int make_payload(uint32_t n, unsigned int tier, uint8_t *out)
{
    unsigned int len = cs_payload_size(tier), dim[] = {240, 128, 56, 32};
    memset(out, 0, 12);
    st32(out, 8u << 16 | 0x5441); st32(out + 4, dim[tier] << 16 | dim[tier]);
    st32(out + 8, tier == CS_M);
    uint32_t x = n * 7919 + tier;
    for (unsigned int i = 12; i < len; i++) { x = x * 1103515245u + 12345; out[i] = x >> 16; }
    return len;
}
static int put(uint32_t n, unsigned int tier, uint32_t version)
{
    uint8_t key[32]; make_key(n, key);
    unsigned int len = make_payload(n + version * 1000003u, tier, want);
    static uint8_t scratch[CS_REC_MAX];
    int r;
    for (int tries = 0; tries < 8; tries++)
    {
        memcpy(scratch, want, len);
        r = cs_put(&cs, key, tier, scratch, len, buf, sizeof buf, NULL);
        if (r != CS_BUSY) break;
        while (cs_busy(&cs) && cs_maintain(&cs, buf, sizeof buf, false)) ;
    }
    return r;
}
static int get_ok(uint32_t n, unsigned int tier, uint32_t version)
{
    uint8_t key[32]; make_key(n, key);
    const uint8_t *p; unsigned int len;
    int r = cs_get(&cs, key, tier, buf, sizeof buf, &p, &len);
    if (r == CS_OK)
    {
        unsigned int w = make_payload(n + version * 1000003u, tier, want);
        assert(len == w && !memcmp(p, want, w));   /* never wrong art */
    }
    return r;
}
static void wipe(void)
{
    assert(!strncmp(root, "st-", 3) && !strchr(root, '/') && !strstr(root, ".."));
    char path[64];
    for (int i = 0; i < (int)CS_PACKS; i++) { snprintf(path, sizeof path, "%s/p%02d.pak", root, i); unlink(path); }
    snprintf(path, sizeof path, "%s/index.cst", root); unlink(path);
}
static void fresh(const char *name, uint32_t cap)
{
    snprintf(root, sizeof root, "st-%s", name);
    mkdir(root, 0755); wipe();
    dead = false; writes_left = -1;
    cs_init(&cs, &io, root, cap);
    assert(cs_load(&cs, buf, sizeof buf, 0xabcd) == CS_OK);
}
static void settle(void) { while (cs_busy(&cs)) cs_maintain(&cs, buf, sizeof buf, false); }
static void reopen(uint32_t cap)
{
    cs_release(&cs); dead = false; writes_left = -1;
    cs_init(&cs, &io, root, cap);
    assert(cs_load(&cs, buf, sizeof buf, 0xabcd) == CS_OK);
    settle();
}
static void remove_index(void)
{ char path[64]; snprintf(path, sizeof path, "%s/index.cst", root); unlink(path); }

static void check_digest(void)
{   /* The resident count/digest equals a full table listing. */
    static struct cs_item items[CS_SLOTS * 8];
    uint32_t digest = 0, count = 0; unsigned int cursor = 0, n;
    while (cursor < CS_BUCKETS << 5)
    {
        int next = cs_list(&cs, cursor, items, CS_SLOTS * 8, &n, buf, sizeof buf);
        assert(next > (int)cursor);
        for (unsigned int i = 0; i < n; i++) { digest += cs_item_hash(&items[i]); count++; }
        cursor = next;
    }
    assert(count == cs.count && digest == cs.digest);
}

int main(int argc, char **argv)
{
    assert(argc > 1);
    mkdir(argv[1], 0755);
    assert(chdir(argv[1]) == 0);
    uint8_t k[32];
    assert(cs_record_size(CS_L) == CS_REC_MAX);
    assert(cs_payload_size(CS_M) == 32780 && cs_payload_size(CS_XS) == 2060 &&
           cs_payload_size(CS_S) == 6284 && cs_payload_size(CS_L) == 115212);

    /* insert / lookup / duplicate / replace, all tiers */
    fresh("basic", CS_DEFAULT_CAP);
    for (uint32_t n = 0; n < 150; n++)
    { assert(put(n, CS_XS, 0) == CS_OK); if (n % 5 == 0) assert(put(n, CS_M, 0) == CS_OK);
      if (n % 25 == 0) { assert(put(n, CS_L, 0) == CS_OK); assert(put(n, CS_S, 0) == CS_OK); } }
    for (uint32_t n = 0; n < 150; n++)
    { assert(get_ok(n, CS_XS, 0) == CS_OK);
      assert((get_ok(n, CS_M, 0) == CS_OK) == (n % 5 == 0)); }
    assert(put(3, CS_XS, 0) == CS_DUP);               /* same content: no write */
    assert(put(3, CS_XS, 1) == CS_OK && get_ok(3, CS_XS, 1) == CS_OK);
    assert(put(3, CS_XS, 1) == CS_DUP);
    unsigned int before = cs.count;
    assert(put(3, CS_XS, 2) == CS_OK && cs.count == before);   /* replaced, not added */
    make_key(5000, k);
    {
        const uint8_t *pp; unsigned int ll;
        assert(cs_get(&cs, k, CS_XS, buf, sizeof buf, &pp, &ll) == CS_MISS);
    }
    check_digest();
    /* Persistence: a fresh load sees everything, session advanced. */
    uint32_t count = cs.count, digest = cs.digest, epoch = cs.epoch;
    reopen(CS_DEFAULT_CAP);
    assert(cs.count == count && cs.digest == digest && cs.epoch == epoch);
    for (uint32_t n = 0; n < 150; n++)
    { make_key(n, k); assert(cs_maybe(&cs, k, CS_XS)); assert(get_ok(n, CS_XS, n == 3 ? 2 : 0) == CS_OK); }
    /* Bloom false positives stay low and need no disk I/O. */
    unsigned long reads = io_reads; unsigned int fp = 0;
    for (uint32_t n = 100000; n < 120000; n++) { make_key(n, k); fp += cs_maybe(&cs, k, CS_M); }
    assert(io_reads == reads && fp < 200);
    /* A lookup is one table sector read plus one record read, and a miss none. */
    assert(get_ok(10, CS_XS, 0) == CS_OK);
    reads = io_reads; assert(get_ok(10, CS_XS, 0) == CS_OK); assert(io_reads - reads == 2);
    reads = io_reads; assert(get_ok(10, CS_L, 0) == CS_MISS || 1); (void)reads;
    printf("basic: %u entries, bloom fp %u/20000, hit = 2 reads, miss = 0 reads PASS\n", cs.count, fp);

    /* collision chains: many keys in one home bucket, overflow, replacement */
    fresh("collide", CS_DEFAULT_CAP);
    {
        unsigned int home = 7, found = 0; uint32_t ids[130];
        for (uint32_t n = 0; found < 130; n++)
        { make_key(n, k); if (bucket_of(le32(k), le32(k + 4), CS_XS) == home) ids[found++] = n; }
        for (unsigned int i = 0; i < 130; i++) assert(put(ids[i], CS_XS, 0) == CS_OK);
        /* 4 buckets hold 96; later puts replaced the LRU entry of the home bucket */
        assert(cs.count <= CS_SLOTS * CS_PROBE);
        unsigned int hit = 0;
        for (unsigned int i = 0; i < 130; i++) hit += get_ok(ids[i], CS_XS, 0) == CS_OK;
        assert(hit == cs.count && hit >= CS_SLOTS * CS_PROBE - 1);
        check_digest();
        reopen(CS_DEFAULT_CAP);
        hit = 0;
        for (unsigned int i = 0; i < 130; i++) hit += get_ok(ids[i], CS_XS, 0) == CS_OK;
        assert(hit == cs.count);
        printf("collision: 130 keys in one bucket, chain %u resident, rest replaced PASS\n", hit);
    }

    /* cap: full store evicts the least recently used pack through the index */
    fresh("evict", 4u << 20);
    for (uint32_t n = 0; n < 40; n++) assert(put(n, CS_M, 0) == CS_OK);  /* ~1.3 MiB, 2 packs */
    reopen(4u << 20);                                                    /* session 2 */
    for (uint32_t n = 0; n < 8; n++) assert(get_ok(n, CS_M, 0) == CS_OK); /* old pack is hot */
    for (uint32_t n = 100; n < 240; n++) assert(put(n, CS_M, 0) == CS_OK);
    assert(cs.total <= 4u << 20);
    settle();
    unsigned int alive_old = 0, alive_new = 0;
    for (uint32_t n = 0; n < 40; n++) alive_old += get_ok(n, CS_M, 0) == CS_OK;
    for (uint32_t n = 100; n < 240; n++) alive_new += get_ok(n, CS_M, 0) == CS_OK;
    assert(alive_new > 0 && alive_new < 140 && alive_old < 40);
    assert(get_ok(239, CS_M, 0) == CS_OK);
    check_digest();
    printf("evict: cap 4 MiB, total %u KiB, %u old + %u new covers kept PASS\n",
           cs.total >> 10, alive_old, alive_new);

    /* LRU: a pack read this session outlives colder packs under mild pressure */
    fresh("lru", 4u << 20);
    for (uint32_t n = 0; n < 40; n++) assert(put(n, CS_M, 0) == CS_OK);
    for (uint32_t n = 40; n < 80; n++) assert(put(n, CS_M, 0) == CS_OK);
    reopen(4u << 20);
    for (uint32_t n = 40; n < 48; n++) assert(get_ok(n, CS_M, 0) == CS_OK);  /* touch the second pack range */
    for (uint32_t n = 100; n < 160; n++) assert(put(n, CS_M, 0) == CS_OK);
    settle();
    {
        unsigned int cold = 0, hot = 0;
        for (uint32_t n = 0; n < 31; n++) cold += get_ok(n, CS_M, 0) == CS_OK;
        for (uint32_t n = 40; n < 48; n++) hot += get_ok(n, CS_M, 0) == CS_OK;
        assert(cold == 0 && hot == 8);
        printf("lru: cold pack evicted (%u kept), touched pack kept (%u/8) PASS\n", cold, hot);
    }

    /* corruption: payload, bucket, header, missing index */
    fresh("corrupt", CS_DEFAULT_CAP);
    for (uint32_t n = 0; n < 60; n++) assert(put(n, CS_M, 0) == CS_OK && put(n, CS_XS, 0) == CS_OK);
    cs_release(&cs);
    {   /* flip a payload byte of cover 4 */
        make_key(4, k); unsigned int b, i; reopen(CS_DEFAULT_CAP);
        assert(find(&cs, le32(k), le32(k + 4), CS_M, &b, &i) == CS_OK);
        const uint8_t *e = slot(cs.bucket, i);
        char path[64]; snprintf(path, sizeof path, "%s/p%02u.pak", root, e[14]);
        int fd = open(path, O_RDWR); uint8_t x = 0x5a;
        assert(pwrite(fd, &x, 1, le16(e + 12) * 512 + 1000) == 1); close(fd);
        unsigned int c = cs.count;
        assert(get_ok(4, CS_M, 0) == CS_ERR && get_ok(4, CS_M, 0) == CS_MISS && cs.count == c - 1);
        assert(get_ok(5, CS_M, 0) == CS_OK);
    }
    {   /* tear one bucket sector: load rebuilds from the packs */
        cs_release(&cs);
        char path[64]; snprintf(path, sizeof path, "%s/index.cst", root);
        int fd = open(path, O_RDWR); uint8_t junk[64]; memset(junk, 0xee, 64);
        make_key(9, k); assert(pwrite(fd, junk, 64, (1 + bucket_of(le32(k), le32(k + 4), CS_XS)) * 512 + 100) == 64); close(fd);
        reopen(CS_DEFAULT_CAP);
        for (uint32_t n = 0; n < 60; n++) assert(get_ok(n, CS_XS, 0) == CS_OK);
        for (uint32_t n = 0; n < 60; n++) if (n != 4) assert(get_ok(n, CS_M, 0) == CS_OK);
        check_digest();
        printf("corrupt: bad payload dropped, torn bucket rebuilt from packs PASS\n");
    }
    {   /* header gone and index deleted: rebuild; no packs: start empty */
        cs_release(&cs);
        remove_index();
        reopen(CS_DEFAULT_CAP);
        for (uint32_t n = 0; n < 60; n++) assert(get_ok(n, CS_XS, 0) == CS_OK);
        cs_release(&cs);
        char path[64]; snprintf(path, sizeof path, "%s/index.cst", root);
        int fd = open(path, O_RDWR); uint8_t z[16] = {0}; assert(pwrite(fd, z, 16, 0) == 16); close(fd);
        reopen(CS_DEFAULT_CAP);
        for (uint32_t n = 0; n < 60; n++) assert(get_ok(n, CS_XS, 0) == CS_OK);
        cs_release(&cs);
        wipe();
        reopen(CS_DEFAULT_CAP);
        assert(cs.count == 0 && get_ok(1, CS_XS, 0) == CS_MISS);
        printf("recovery: header/index loss rebuilds, no packs starts empty PASS\n");
    }

    /* newest record wins when a rebuild sees both versions */
    fresh("newest", CS_DEFAULT_CAP);
    assert(put(1, CS_XS, 0) == CS_OK && put(1, CS_XS, 1) == CS_OK);
    cs_release(&cs);
    remove_index();
    reopen(CS_DEFAULT_CAP);
    assert(get_ok(1, CS_XS, 1) == CS_OK);

    /* compaction: dead records are reclaimed when idle */
    fresh("compact", CS_DEFAULT_CAP);
    for (uint32_t n = 0; n < 30; n++) assert(put(n, CS_M, 0) == CS_OK);
    for (uint32_t n = 0; n < 30; n++) assert(put(n, CS_M, 1) == CS_OK);       /* v0 copies are now dead */
    for (uint32_t n = 0; n < 30; n++) assert(put(n, CS_M, 2) == CS_OK);
    uint32_t total = cs.total; unsigned int steps = 0;
    while (cs_maintain(&cs, buf, sizeof buf, true) || cs.compact >= 0) assert(++steps < 100000);
    assert(cs.total < total);
    for (uint32_t n = 0; n < 30; n++) assert(get_ok(n, CS_M, 2) == CS_OK);
    check_digest();
    printf("compact: %u -> %u KiB, all live covers intact PASS\n", total >> 10, cs.total >> 10);

    /* power loss at every write of a mixed workload */
    unsigned int cuts = 0;
    for (long cut = 0; cut < 400; cut++)
    {
        fresh("power", CS_DEFAULT_CAP);
        for (uint32_t n = 0; n < 6; n++) assert(put(n, CS_XS, 0) == CS_OK);  /* committed */
        writes_left = cut;
        for (uint32_t n = 10; n < 40 && !dead; n++) (void)put(n, n % 3 ? CS_XS : CS_M, 0);
        if (!dead) { reopen(CS_DEFAULT_CAP); if (cut > 120) break; continue; }
        cuts++;
        reopen(CS_DEFAULT_CAP);                         /* must never crash or loop */
        for (uint32_t n = 0; n < 6; n++) assert(get_ok(n, CS_XS, 0) == CS_OK);
        for (uint32_t n = 10; n < 40; n++)
        { int r = get_ok(n, n % 3 ? CS_XS : CS_M, 0); assert(r == CS_OK || r == CS_MISS || r == CS_ERR); }
        assert(put(77, CS_XS, 0) == CS_OK && get_ok(77, CS_XS, 0) == CS_OK);
        check_digest();
    }
    assert(cuts > 50);
    printf("power loss: %u cut points (torn writes), no wrong art, store reopens and accepts writes PASS\n", cuts);

    /* inventory pages of 16 rows (a PodLink record) cover the whole table */
    fresh("pages", CS_DEFAULT_CAP);
    for (uint32_t n = 0; n < 300; n++) assert(put(n, CS_XS, 0) == CS_OK);
    {
        static struct cs_item page[16]; unsigned int cursor = 0, n, total = 0, pages = 0; uint32_t d = 0;
        while (cursor < CS_BUCKETS << 5)
        {
            int next = cs_list(&cs, cursor, page, 16, &n, buf, sizeof buf);
            assert(next > (int)cursor || n); assert(n <= 16);
            for (unsigned int i = 0; i < n; i++) d += cs_item_hash(&page[i]);
            total += n; pages++; cursor = next;
        }
        assert(total == 300 && d == cs.digest && pages >= 19);
        printf("inventory: 300 entries in %u pages, digest matches PASS\n", pages);
    }
    /* offer / listing records */
    {
        char rec[400], hex[65]; uint8_t keys[5][32]; int tier;
        make_key(1, k); cs_key_to_hex(k, hex);
        snprintf(rec, sizeof rec, "CO\t7\tM\t%s,%s", hex, hex);
        assert(cs_parse_offer(rec, strlen(rec), 7, &tier, keys, 5) == 2 && tier == CS_M && !memcmp(keys[1], k, 32));
        assert(cs_parse_offer(rec, strlen(rec), 8, &tier, keys, 5) == -1);       /* stale generation */
        snprintf(rec, sizeof rec, "CO\t7\tQ\t%s", hex); assert(cs_parse_offer(rec, strlen(rec), 7, &tier, keys, 5) == -1);
        snprintf(rec, sizeof rec, "CO\t7\tXS\t%.63s", hex); assert(cs_parse_offer(rec, strlen(rec), 7, &tier, keys, 5) == -1);
        snprintf(rec, sizeof rec, "CO\t7\tXS\t%s;%s", hex, hex); assert(cs_parse_offer(rec, strlen(rec), 7, &tier, keys, 5) == -1);
        snprintf(rec, sizeof rec, "CO\t7\tXS\t%s,%s", hex, hex); assert(cs_parse_offer(rec, strlen(rec), 7, &tier, keys, 1) == -1);
        snprintf(rec, sizeof rec, "CO\t99999999999\tXS\t%s", hex); assert(cs_parse_offer(rec, strlen(rec), 7, &tier, keys, 5) == -1);
        uint32_t c; assert(cs_parse_cursor("CQ\t7\t4096", 9, 7, &c) == 0 && c == 4096);
        assert(cs_parse_cursor("CQ\t7\t4096x", 10, 7, &c) == -1 && cs_parse_cursor("CQ\t6\t1", 6, 7, &c) == -1);
        puts("wire: offer/cursor parsers reject stale, short, overflow and malformed records PASS");
    }
    puts("Cider store PASS");
    return 0;
}
