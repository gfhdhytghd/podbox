/* Resident Cider receiver and bounded cache. Disk I/O stays on its worker. */
#include "config.h"
#include "screens/playback/cider.h"
#include "screens/playback/cider_library.h"
#include "screens/playback/cider_store.h"
#include "audio.h"
#include "podlink.h"
#include "kernel.h"
#include "thread.h"
#include "system.h"
#include "storage.h"
#include "usb.h"
#include "file.h"
#include "dir.h"
#include "rbpaths.h"
#include "remote_log.h"
#include "database/tagcache.h"
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>

/* iPod-local art store: pack files + on-disk hash table (cider_store.c). The
 * old per-cover files under cider-cache/ and cider-v1/ are ignored. */
#define STORE_ROOT ROCKBOX_DIR "/cider-art"
#ifndef CIDER_STORE_CAP
#define CIDER_STORE_CAP CS_DEFAULT_CAP
#endif
/* How long a store hit holds back the USB request before racing it. */
#define HOLD_ACTIVE (HZ)
#define HOLD_ASLEEP (5 * HZ / 2)
static struct mutex cider_mutex;
static struct cider_state state;
static struct cider_cache cache;
static struct cider_transfer transfer;
static bool initialized, active, disk_lookup, request_ready, disk_running;
static uint32_t next_request, grant, granted, cancel_request;
static bool cancel_all;
static long hello_at, request_at;
static unsigned int request_tries;
static uint32_t play_request;
static struct cider_modes modes;
static int play_reply;
static long play_at, art_used_at;
static char warm_keys[7 * 65], warm_tier[3];
static bool warm_ready;
static char commands[12][384];
static struct cider_store store;
static bool host_store, ls_due, want_ready, list_req, list_ready, pin_on;
static long hold_until, want_at, pin_at;
static uint32_t list_cursor;
static unsigned int list_len, nfills, noffers, nacks;
static char list_page[480], pin_key[65];
static struct { char key[65]; unsigned char tier; } fills[8], offers[8];
static struct { char key[65]; unsigned char tier; uint32_t crc; } acks[8];
static struct { uint8_t key[32]; unsigned char tier; long until; } missed[8];
static unsigned int miss_next;
static struct { uint32_t epoch, count, digest, total; } snap;
static struct cs_item list_items[16];
static char xs_keys[5][65];
static unsigned int xs_count;
static unsigned int ncommands, art_revision;
static unsigned char disk_data[CS_REC_MAX] CACHEALIGN_ATTR;
static long disk_stack[DEFAULT_STACK_SIZE * 6 / sizeof(long)];
static struct event_queue disk_queue;
_Static_assert(sizeof cache + sizeof transfer + sizeof disk_data +
               sizeof disk_stack + sizeof state + sizeof commands +
               sizeof store + sizeof list_page + sizeof fills +
               sizeof offers + sizeof acks + sizeof missed +
               sizeof list_items + sizeof modes +
               (120 + 112 + 56 + 36) * 1024 < 1152 * 1024,
               "Cider resident, WPS, pages, browser and carousel below 1152 KiB");

static void pin_apply(void);
static void store_text(const unsigned char *d, unsigned int n);
static void background_start(void);
static unsigned int store_record(char *record, unsigned int size);

static void cider_log(const char *format, ...)
{
    char text[REMOTE_LOG_DATA];
    va_list ap;
    va_start(ap, format);
    vsnprintf(text, sizeof text, format, ap);
    va_end(ap);
    remote_log_event(REMOTE_LOG_CIDER, current_tick, 0, 0,
                     text, strlen(text) + 1);
}

static uint32_t u32(const unsigned char *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 |
           (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static bool valid_key(const char *key)
{
    if (strlen(key) != 64) return false;
    for (int i = 0; i < 64; i++)
        if (!((key[i] >= '0' && key[i] <= '9') ||
              (key[i] >= 'a' && key[i] <= 'f'))) return false;
    return true;
}

static void reset(void)
{
    mutex_lock(&cider_mutex);
    state.position = cider_position(&state,current_tick,HZ);
    state.available = false;
    if (play_request) play_reply = -1;
    cider_library_reset();
    state.generation = 0;
    transfer.active = false;
    disk_lookup = request_ready = false;
    ncommands = grant = granted = cancel_request = 0;
    memset(&modes, 0, sizeof modes);
    xs_count = 0; cancel_all = false;
    warm_keys[0] = 0; warm_ready = false;
    host_store = want_ready = list_req = list_ready = false;
    nfills = noffers = nacks = 0; hold_until = 0;
    hello_at = current_tick - 2 * HZ;
    mutex_unlock(&cider_mutex);
}

static bool receive(const struct podlink_message *m)
{
    bool binary = m->type == PODLINK_BINARY && m->length >= 4 &&
        (!memcmp(m->data, "CAR1", 4) || !memcmp(m->data, "CXS1", 4));
    bool text = m->type == PODLINK_TEXT && m->length >= 3 &&
        (!memcmp(m->data, "CA\t", 3) || !memcmp(m->data, "CN\t", 3) ||
         !memcmp(m->data, "CK\t", 3) || !memcmp(m->data, "CL\t", 3) ||
         !memcmp(m->data, "CG\t", 3) || !memcmp(m->data, "CP\t", 3) ||
         !memcmp(m->data, "CF\t", 3) || !memcmp(m->data, "CO\t", 3) ||
         !memcmp(m->data, "CQ\t", 3));
    if (!binary && !text) return false;
    mutex_lock(&cider_mutex);
    if (binary && !state.generation &&
        (!transfer.generation || m->length < 12 ||
         u32(m->data + 4) != transfer.generation))
    {
        mutex_unlock(&cider_mutex);
        return false;
    }
    if (binary || (text && !memcmp(m->data, "CL\t", 3)))
    {
        if (binary && (m->length < 12 ||
                       u32(m->data + 4) != state.generation))
        {
            mutex_unlock(&cider_mutex);
            return true;
        }
        /* Bytes are returned only after the dedicated sink consumes them. */
        if (m->length > granted)
        {
            transfer.active = false;
            cider_log("CIDER credit violation tick=%ld bytes=%lu credit=%lu",
                       current_tick, (unsigned long)m->length,
                       (unsigned long)granted);
        }
        else
        {
            granted -= m->length; grant += m->length;
            if (text)
            {
                if (cider_library_receive(m->data, m->length,
                                         state.generation, state.library_generation))
                {
                    art_revision++;
                    cider_log("page received tick=%ld bytes=%lu",current_tick,
                              (unsigned long)m->length);
                }
            }
            else if (!memcmp(m->data, "CAR1", 4))
            {
                int result = cider_chunk(&transfer, m->data, m->length);
                if (m->length >= 28)
                    cider_log("chunk req=%lu off=%lu bytes=%lu result=%d",
                              (unsigned long)u32(m->data + 8),
                              (unsigned long)u32(m->data + 12),
                              (unsigned long)m->length - 28, result);
                if (result > 0)
                {
                    art_revision++;
                    disk_lookup = false;
                    cider_cache_put(&cache, transfer.key, transfer.tier,
                                    transfer.data, transfer.total, true);
                    if (transfer.fill)
                        cider_cache_demote(&cache, transfer.key, transfer.tier);
                    pin_apply();
                    cider_log("CIDER art done tick=%ld req=%lu bytes=%lu",
                               current_tick, (unsigned long)transfer.request,
                               (unsigned long)transfer.total);
                }
                else if (result < 0)
                {
                    transfer.received = 0;
                    request_ready = transfer.active;
                }
            }
            else if (binary && m->length >= 16 && transfer.active &&
                     u32(m->data + 8) == transfer.request &&
                     u32(m->data + 4) == state.generation)
            {
                unsigned int count = u32(m->data + 12), pos = 16;
                for (unsigned int i = 0; i < count && count <= 5; i++)
                {
                    char key[65];
                    if (pos + 40 > m->length) break;
                    for (int j = 0; j < 32; j++)
                        snprintf(key + j * 2, 3, "%02x", m->data[pos + j]);
                    unsigned int size = u32(m->data + pos + 32);
                    unsigned int crc = u32(m->data + pos + 36);
                    pos += 40;
                    if (size > m->length - pos || size != 2060 ||
                        podlink_crc32(m->data + pos, size) != crc) break;
                    art_revision++;
                    cider_cache_put(&cache, key, "XS", m->data + pos,
                                    size, false);
                    pos += size;
                }
                if (pos == m->length) transfer.active = false;
            }
        }
    }
    else if (text && (!memcmp(m->data, "CF\t", 3) ||
                      !memcmp(m->data, "CO\t", 3) ||
                      !memcmp(m->data, "CQ\t", 3)))
        store_text(m->data, m->length);
    else
    {
        uint32_t previous = state.generation;
        uint32_t library = state.library_generation;
        bool available = state.available;
        uint32_t position = cider_position(&state,current_tick,HZ);
        cider_state_apply(&state, m->data, m->length, current_tick);
        if (available && !state.available) state.position = position;
        if (library != state.library_generation)
            cider_library_invalidate(state.library_generation);
        if (state.generation != previous || state.available != available)
            cider_log("availability gen=%lu available=%d reason=%s",
                      (unsigned long)state.generation, state.available,
                      state.reason);
        if (state.generation != previous)
        {
            if (play_request) play_reply = -1;
            transfer.active = false; ncommands = 0;
            memset(&modes, 0, sizeof modes);
            disk_lookup = request_ready = false;
            grant = granted = cancel_request = 0;
        }
        if (!memcmp(m->data, "CK\t", 3))
        {
            int reply = cider_reply_status(m->data, m->length,
                                           state.generation, play_request);
            if (reply) play_reply = reply;
            for (int i = 0; i < CIDER_MODE_COUNT; i++)
                if (modes.pending[i] &&
                    cider_reply_status(m->data, m->length, state.generation,
                                       modes.request[i]) < 0)
                    cider_mode_failed(&modes, modes.request[i]);
        }
        if (!memcmp(m->data, "CK\t", 3))
            cider_log("CIDER reply tick=%ld %.*s", current_tick,
                       (int)m->length, (const char *)m->data);
    }
    mutex_unlock(&cider_mutex);
    return true;
}

static unsigned int next(char *record, unsigned int size)
{
    unsigned int n = 0;
    mutex_lock(&cider_mutex);
    if (!state.generation)
    {
        if (TIME_AFTER(current_tick, hello_at + 2 * HZ))
        {
            n = snprintf(record, size, "ci\t1\t%d\tcn18,neighbors,warm,play-ack,localstore,modes5", CIDER_CREDIT);
            hello_at = current_tick;
        }
    }
    else if (background_start(), ncommands)
    {
        n = snprintf(record, size, "%s", commands[0]);
        memmove(commands[0], commands[1], --ncommands * sizeof commands[0]);
    }
    else if (cancel_request || cancel_all)
    {
        n = snprintf(record, size, "cx\t%lu\t%lu",
                     (unsigned long)state.generation,
                     cancel_all ? 0ul : (unsigned long)cancel_request);
        cancel_request = 0; cancel_all = false;
    }
    else if ((n = cider_library_next(record, size, state.generation)))
    {
        if (!granted) grant = CIDER_CREDIT;
        cider_log("page request tick=%ld %s", current_tick, record);
    }
    else if (grant)
    {
        n = snprintf(record, size, "cg\t%lu\t%lu",
                     (unsigned long)state.generation, (unsigned long)grant);
        cider_log("CIDER credit tick=%ld grant=%lu outstanding=%lu",
                   current_tick, (unsigned long)grant,
                   (unsigned long)granted);
        granted += grant; grant = 0;
    }
    else if (transfer.active &&
             !(disk_lookup && TIME_BEFORE(current_tick, hold_until)) &&
             (request_ready || (hold_until && disk_lookup) ||
              TIME_AFTER(current_tick, request_at + 3 * HZ)))
    {
        if (request_tries++ < 3)
        {
            if (xs_count)
            {
                n = snprintf(record, size, "cb\t%lu\t%lu\t",
                             (unsigned long)state.generation,
                             (unsigned long)transfer.request);
                for (unsigned int i = 0; i < xs_count && n < size; i++)
                    n += snprintf(record + n, size - n, "%s%s",
                                  i ? "," : "", xs_keys[i]);
            }
            else
                n = snprintf(record, size,
                             "cr\t%lu\t%lu\t%s\t%s\t%lu\t%d\t0",
                             (unsigned long)state.generation,
                             (unsigned long)transfer.request, transfer.key,
                             transfer.tier, (unsigned long)transfer.received,
                             transfer.fill ? 4 : 3);
            cider_log("CIDER request tick=%ld req=%lu off=%lu",
                       current_tick, (unsigned long)transfer.request,
                       (unsigned long)transfer.received);
            request_ready = false; request_at = current_tick;
            hold_until = 0;
        }
        else
        {
            cancel_request = transfer.request;
            transfer.active = false;
        }
    }
    if (!n && warm_ready && state.generation)
    {
        n = snprintf(record, size, "cw\t%lu\t%s\t%s",
                     (unsigned long)state.generation, warm_tier, warm_keys);
        warm_ready = false;
    }
    if (!n && state.generation) n = store_record(record, size);
    mutex_unlock(&cider_mutex);
    return n < size ? n : 0;
}

/* ---- iPod-local store plumbing. Callers hold cider_mutex unless noted. ---- */

/* Inspect, do not consume: only the worker's outer loop may ACK and park.
 * Search past art wakeups so a queued USB event takes precedence. */
static bool disk_usb_pending(void)
{
    return queue_peek_ex(&disk_queue, NULL, 0,
                         QPEEK_FILTER1(SYS_USB_CONNECTED));
}

static int io_open(const char *path, int mode)
{
    return open(path, mode == CS_O_READ ? O_RDONLY : O_RDWR | O_CREAT, 0666);
}
static int io_read(int fd, void *buf, unsigned int n) { return read(fd, buf, n); }
static int io_write(int fd, const void *buf, unsigned int n) { return write(fd, buf, n); }
static int io_seek(int fd, uint32_t offset) { return lseek(fd, offset, SEEK_SET) < 0 ? -1 : 0; }
static int io_sync(int fd) { return fsync(fd); }
static int io_close(int fd) { return close(fd); }
static int io_remove(const char *path) { return remove(path); }
static int io_fsize(const char *path)
{
    int fd = open(path, O_RDONLY), n = -1;
    if (fd >= 0) { n = ffilesize(fd); close(fd); }
    return n;
}
static void io_mkdir(const char *path) { mkdir(ROCKBOX_DIR); mkdir(path); }
static void io_yield(void) { yield(); }
static const struct cs_io store_io = {io_open, io_read, io_write, io_seek,
    io_sync, io_close, io_remove, io_fsize, io_mkdir, io_yield};

/* RAM-only: could the local store hold it? Recent failed reads are skipped. */
static bool store_local(const char *key, const char *tier)
{
    uint8_t kb[32];
    int t = cs_tier(tier);
    if (!disk_running || t < 0 || !cs_key_from_hex(key, kb) ||
        !cs_maybe(&store, kb, t)) return false;
    for (unsigned int i = 0; i < ARRAYLEN(missed); i++)
        if (missed[i].until && missed[i].tier == t &&
            !memcmp(missed[i].key, kb, 32) &&
            TIME_BEFORE(current_tick, missed[i].until)) return false;
    return true;
}
static void miss_note(const char *key, const char *tier)
{
    uint8_t kb[32];
    int t = cs_tier(tier);
    if (t < 0 || !cs_key_from_hex(key, kb)) return;
    memcpy(missed[miss_next].key, kb, 32);
    missed[miss_next].tier = t;
    missed[miss_next].until = current_tick + 30 * HZ;
    miss_next = (miss_next + 1) % ARRAYLEN(missed);
}
static void fill_add(const char *key, const char *tier)
{
    if (nfills >= ARRAYLEN(fills) || cider_cache_find(&cache, key, tier) >= 0)
        return;
    for (unsigned int i = 0; i < nfills; i++)
        if (!strcmp(fills[i].key, key) && fills[i].tier == cs_tier(tier)) return;
    snprintf(fills[nfills].key, sizeof fills[0].key, "%s", key);
    fills[nfills++].tier = cs_tier(tier);
    queue_post(&disk_queue, 1, 0);
}
static const char *tier_name(unsigned int t)
{
    static const char *const names[] = {"L", "M", "S", "XS"};
    return names[t & 3];
}

/* The album track list pins its L cover: never evicted while pinned. */
static void pin_apply(void)
{
    if (pin_on) cider_cache_pin(&cache, pin_key, "L");
}

static void store_text(const unsigned char *d, unsigned int n)
{
    if (d[1] == 'F')
    {
        char want[48];
        int len = snprintf(want, sizeof want, "CF\t%lu\tlocalstore",
                           (unsigned long)state.generation);
        if (len > 0 && n == (unsigned int)len && !memcmp(d, want, n))
        { host_store = true; ls_due = true; }
    }
    else if (d[1] == 'Q' && host_store)
    {
        uint32_t cursor;
        if (cs_parse_cursor((const char *)d, n, state.generation, &cursor) == 0)
        { list_req = true; list_cursor = cursor; queue_post(&disk_queue, 1, 0); }
    }
    else if (d[1] == 'O' && host_store)
    {
        static uint8_t keys[5][32];
        int tier, count = cs_parse_offer((const char *)d, n, state.generation,
                                         &tier, keys, 5);
        for (int i = 0; i < count && noffers < ARRAYLEN(offers); i++)
        {
            char hex[65];
            cs_key_to_hex(keys[i], hex);
            bool dup = false;
            for (unsigned int j = 0; j < noffers; j++)
                dup |= offers[j].tier == tier && !strcmp(offers[j].key, hex);
            if (dup) continue;
            memcpy(offers[noffers].key, hex, sizeof hex);
            offers[noffers++].tier = tier;
        }
    }
}

static void begin_transfer(const char *key, const char *tier, bool fill)
{
    if (transfer.active)
    {
        cancel_all |= cancel_request != 0;
        cancel_request = transfer.request;
    }
    xs_count = 0;
    if (!fill) art_used_at = current_tick;
    memset(&transfer, 0, sizeof transfer);
    transfer.active = true; transfer.generation = state.generation;
    transfer.request = ++next_request; transfer.fill = fill;
    snprintf(transfer.key, sizeof transfer.key, "%s", key);
    snprintf(transfer.tier, sizeof transfer.tier, "%s", tier);
    /* A local hit holds the USB request back; a miss goes out at once. */
    bool local = !fill && store_local(key, tier);
    disk_lookup = local;
    request_ready = !local; request_tries = 0; request_at = current_tick;
    hold_until = local ? current_tick +
        (storage_disk_is_active() ? HOLD_ACTIVE : HOLD_ASLEEP) : 0;
    if (!granted) grant = CIDER_CREDIT;
    if (disk_running) queue_post(&disk_queue, 1, 0);
}

/* Idle-time work, started from next(): the pinned L cover first (above idle
 * fill, below any visible cover, which replaces the transfer), then fills the
 * host offered. Fills run only while the disk already spins and the UI idles;
 * nothing here wakes a sleeping disk. */
static void background_start(void)
{
    if (!state.generation || transfer.active || ncommands || cancel_request ||
        cancel_all || xs_count) return;
    if (pin_on)
    {
        int i = cider_cache_find(&cache, pin_key, "L");
        if (i >= 0) pin_apply();
        else if (TIME_AFTER(current_tick, pin_at + 5 * HZ))
        {
            pin_at = current_tick;
            begin_transfer(pin_key, "L", false);
            return;
        }
    }
    if (!host_store || !store.loaded || cs_busy(&store) ||
        !storage_disk_is_active() ||
        TIME_BEFORE(current_tick, art_used_at + 3 * HZ) ||
        store.total + CS_REC_MAX > store.cap) return;
    for (int i = 0; i < CIDER_SLOTS; i++)
        if (cache.slot[i].length && cache.slot[i].dirty && cache.slot[i].bg)
            return;                            /* previous fill not written */
    if (noffers)
    {
        char key[65];
        unsigned int tier = offers[0].tier;
        snprintf(key, sizeof key, "%s", offers[0].key);
        memmove(&offers[0], &offers[1], --noffers * sizeof offers[0]);
        if (cider_cache_find(&cache, key, tier_name(tier)) < 0)
            begin_transfer(key, tier_name(tier), true);
    }
    else if (noffers < 2 && TIME_AFTER(current_tick, want_at + 4 * HZ))
        want_ready = true;
}

static unsigned int hex8(char *out, const uint8_t *p, unsigned int n)
{
    for (unsigned int i = 0; i < n; i++)
    {
        out[i * 2] = "0123456789abcdef"[p[i] >> 4];
        out[i * 2 + 1] = "0123456789abcdef"[p[i] & 15];
    }
    return n * 2;
}

/* Store protocol records, lowest priority. See cider-remote-protocol.md. */
static unsigned int store_record(char *record, unsigned int size)
{
    unsigned long gen = state.generation;
    if (!host_store) return 0;
    if (ls_due && store.loaded)
    {
        ls_due = false;
        return snprintf(record, size, "ls\t%lu\t%lu\t%lu\t%lu\t%lu\t%lu", gen,
                        (unsigned long)snap.epoch, (unsigned long)snap.count,
                        (unsigned long)snap.digest,
                        (unsigned long)(snap.total >> 10),
                        (unsigned long)(store.cap >> 10));
    }
    if (list_ready && list_len < size)
    {
        memcpy(record, list_page, list_len);
        list_ready = false;
        return list_len;
    }
    if (nacks)
    {
        unsigned int n = snprintf(record, size, "la\t%lu\t%s\t%s\t%lu", gen,
                                  acks[0].key, tier_name(acks[0].tier),
                                  (unsigned long)acks[0].crc);
        memmove(&acks[0], &acks[1], --nacks * sizeof acks[0]);
        return n;
    }
    if (want_ready)
    {
        want_ready = false; want_at = current_tick;
        return snprintf(record, size, "lw\t%lu\t%u", gen,
                        (unsigned int)(ARRAYLEN(offers) - noffers));
    }
    return 0;
}

/* ---- worker (single owner of the store) ---- */

static void snap_store(void)
{
    mutex_lock(&cider_mutex);
    snap.epoch = store.epoch; snap.count = store.count;
    snap.digest = store.digest; snap.total = store.total;
    ls_due = true;
    mutex_unlock(&cider_mutex);
}

static void build_list(void)
{
    uint32_t cursor;
    unsigned int n = 0, len;
    mutex_lock(&cider_mutex);
    cursor = list_cursor;
    mutex_unlock(&cider_mutex);
    int next = cs_list(&store, cursor, list_items, ARRAYLEN(list_items), &n,
                       disk_data, sizeof disk_data);
    if (disk_usb_pending()) return;
    mutex_lock(&cider_mutex);
    if (next < 0 || (uint32_t)next <= cursor) { next = 0; n = 0; }
    len = snprintf(list_page, sizeof list_page, "lv\t%lu\t%lu\t%lu\t%u\n",
                   (unsigned long)state.generation, (unsigned long)cursor,
                   (unsigned long)next, n);
    for (unsigned int i = 0; i < n && len + 28 < sizeof list_page; i++)
    {
        len += hex8(list_page + len, list_items[i].key, 8);
        list_page[len++] = '0' + list_items[i].tier;
        uint8_t c[4] = {list_items[i].crc >> 24, list_items[i].crc >> 16,
                        list_items[i].crc >> 8, list_items[i].crc};
        len += hex8(list_page + len, c, 4);
        list_page[len++] = '\n';
    }
    list_len = len; list_ready = true; list_req = false;
    mutex_unlock(&cider_mutex);
}

/* Store -> arena: one hit ends the USB wait for the transfer's cover. */
static bool read_store(const char *key, const char *tier, bool transfer_job,
                       uint32_t req)
{
    uint8_t kb[32];
    const uint8_t *payload = NULL;
    unsigned int length = 0;
    int t = cs_tier(tier);
    int r = t < 0 || !cs_key_from_hex(key, kb) ? CS_BAD :
            cs_get(&store, kb, t, disk_data, sizeof disk_data, &payload, &length);
    if (disk_usb_pending()) return false;
    mutex_lock(&cider_mutex);
    bool ok = r == CS_OK && cider_cache_put(&cache, key, tier, payload, length, false);
    if (ok) { art_revision++; pin_apply(); }
    else miss_note(key, tier);
    if (transfer_job && transfer.active && transfer.request == req && disk_lookup)
    {
        if (ok)
        {
            if (request_tries) cancel_request = transfer.request;
            transfer.active = false;
        }
        else if (!request_tries) request_ready = true;
        disk_lookup = false;
    }
    mutex_unlock(&cider_mutex);
    return ok;
}

/* Dirty arena covers -> store, a few per wake. Returns true when a started
 * eviction means the rest must retry. Callers check the disk already spins. */
static bool store_write_batch(void)
{
    char key[65], tier[3];
    bool more = false;
    for (int batch = 0; batch < 6; batch++)
    {
        unsigned int length = 0;
        bool bg = false;
        mutex_lock(&cider_mutex);
        for (int i = 0; i < CIDER_SLOTS; i++)
            if (cache.slot[i].length && cache.slot[i].dirty)
            {
                struct cider_slot *s = &cache.slot[i];
                length = s->length; bg = s->bg;
                memcpy(disk_data + CS_REC_HEAD, cache.data + s->offset, length);
                snprintf(key, sizeof key, "%s", s->key);
                snprintf(tier, sizeof tier, "%s", s->tier);
                s->dirty = false;
                break;
            }
        mutex_unlock(&cider_mutex);
        if (length && (tagcache_is_busy() || !queue_empty(&disk_queue)))
        {   /* the UI or the database needs the disk: write it next time */
            mutex_lock(&cider_mutex);
            int i = cider_cache_find(&cache, key, tier);
            if (i >= 0) { cache.slot[i].dirty = true; cache.slot[i].bg = bg; }
            mutex_unlock(&cider_mutex);
            break;
        }
        if (!length) break;
        uint8_t kb[32];
        uint32_t crc = 0;
        int t = cs_tier(tier);
        int r = t < 0 || !cs_key_from_hex(key, kb) ? CS_BAD :
                cs_put(&store, kb, t, disk_data + CS_REC_HEAD, length,
                       disk_data, sizeof disk_data, &crc);
        if (disk_usb_pending()) return false;
        mutex_lock(&cider_mutex);
        if (r == CS_OK || r == CS_DUP)
        {
            if (host_store && nacks < ARRAYLEN(acks))
            {
                snprintf(acks[nacks].key, sizeof acks[0].key, "%s", key);
                acks[nacks].tier = t; acks[nacks++].crc = crc;
            }
            if (bg) cider_cache_drop_bg(&cache, key, tier);
        }
        else if (r == CS_BUSY && cs_busy(&store))
        {   /* eviction started: keep the cover, retry after it */
            int i = cider_cache_find(&cache, key, tier);
            if (i >= 0) { cache.slot[i].dirty = true; cache.slot[i].bg = bg; }
            more = true;
            mutex_unlock(&cider_mutex);
            break;
        }
        else if (bg) cider_cache_drop_bg(&cache, key, tier);
        mutex_unlock(&cider_mutex);
        if (r == CS_OK) snap_store();
        yield();
    }
    return more;
}

static void disk_worker(void)
{
    bool more = false, was_busy = false;
    long load_at = current_tick - 30 * HZ;
    for (;;)
    {
        struct queue_event ev;
        queue_wait_w_tmo(&disk_queue, &ev, more ? HZ / 20 : HZ);
        if (ev.id == SYS_USB_CONNECTED)
        {
            cs_unload(&store);            /* sync, close, forget the index */
            /* No UI/service mutex on the handover path. The service resets
             * the link on detach; clean up any late list after remount. */
            usb_acknowledge(SYS_USB_CONNECTED_ACK, ev.data);
            do { queue_wait(&disk_queue, &ev); }
            while (ev.id != SYS_USB_DISCONNECTED);
            mutex_lock(&cider_mutex);
            list_req = list_ready = false;
            mutex_unlock(&cider_mutex);
            more = was_busy = false;
            load_at = current_tick - 30 * HZ;
            continue;
        }
        /* Drain older wakeups without work, preserving USB connect/disconnect
         * order. Pulling a later connect out of the queue could mistake an
         * older disconnect for the end of the new storage session. */
        if (disk_usb_pending()) continue;
        if (tagcache_is_busy()) continue;
        bool active = storage_disk_is_active();
        char key[65], tier[3];
        bool lookup;
        uint32_t req;
        mutex_lock(&cider_mutex);
        lookup = transfer.active && disk_lookup; req = transfer.request;
        snprintf(key, sizeof key, "%s", transfer.key);
        snprintf(tier, sizeof tier, "%s", transfer.tier);
        bool wanted = lookup || nfills;
        mutex_unlock(&cider_mutex);
        /* Load when the disk already spins, or when the user's browsing
         * needs it (that spin-up is theirs, not a background wake). */
        if (!store.loaded && (active || wanted) &&
            TIME_AFTER(current_tick, load_at + 30 * HZ))
        {
            load_at = current_tick;
            int result = cs_load(&store, disk_data, sizeof disk_data,
                        (uint32_t)current_tick * 2654435761u + 0x5bd1e995u);
            if (disk_usb_pending()) continue;
            if (result == CS_OK) snap_store();
        }
        if (disk_usb_pending()) continue;
        if (lookup) read_store(key, tier, true, req);
        /* Visible window and neighbours share one spin-up. */
        for (int n = 0; n < 6; n++)
        {
            if (disk_usb_pending()) break;
            mutex_lock(&cider_mutex);
            bool have = nfills > 0;
            if (have)
            {
                snprintf(key, sizeof key, "%s", fills[0].key);
                snprintf(tier, sizeof tier, "%s", tier_name(fills[0].tier));
                memmove(&fills[0], &fills[1], --nfills * sizeof fills[0]);
            }
            mutex_unlock(&cider_mutex);
            if (!have) break;
            read_store(key, tier, false, 0);
        }
        if (disk_usb_pending()) continue;
        mutex_lock(&cider_mutex);
        bool busy = transfer.active || TIME_BEFORE(current_tick, art_used_at + HZ);
        bool answer = list_req && !list_ready;
        mutex_unlock(&cider_mutex);
        active = storage_disk_is_active();
        if (answer && active && store.loaded) build_list();
        if (disk_usb_pending()) continue;
        more = false;
        if (active && !busy && store.loaded)
        {
            more = store_write_batch();
            bool idle = TIME_AFTER(current_tick, art_used_at + 5 * HZ);
            if (!queue_empty(&disk_queue)) continue;
            more |= cs_maintain(&store, disk_data, sizeof disk_data, idle);
            if (disk_usb_pending()) continue;
            if (was_busy && !cs_busy(&store)) snap_store();
            was_busy = cs_busy(&store);
        }
        if (!more && queue_empty(&disk_queue)) cs_release(&store);
    }
}

void cider_init(void)
{
    static const struct podlink_resident consumer = {receive, next, reset};
    if (initialized) return;
    mutex_init(&cider_mutex);
    queue_init(&disk_queue, true);
    cs_init(&store, &store_io, STORE_ROOT, CIDER_STORE_CAP);
    store.cancelled = disk_usb_pending;
    initialized = true;
    reset();
    podlink_set_resident(&consumer);
    disk_running = create_thread(disk_worker, disk_stack,
                    sizeof disk_stack, 0, "cider-cache"
                    IF_PRIO(, PRIORITY_BACKGROUND) IF_COP(, CPU)) != 0;
    if (!disk_running) queue_delete(&disk_queue);
}

bool cider_available(void)
{
    return initialized && state.available && state.have &&
           (unsigned long)current_tick - state.received < 30 * HZ &&
           podlink_host_connected();
}
bool cider_active(void) { return active; }
void cider_snapshot(struct cider_state *out)
{
    mutex_lock(&cider_mutex);
    *out = state;
    if (!podlink_host_connected() ||
        (unsigned long)current_tick - state.received >= 30 * HZ)
        out->available = false;
    mutex_unlock(&cider_mutex);
}
void cider_status(struct cider_status *out)
{
    mutex_lock(&cider_mutex);
    out->available = state.available && podlink_host_connected() &&
        (unsigned long)current_tick - state.received < 30 * HZ;
    out->muted = state.muted;
    out->rate = state.rate;
    out->volume = state.volume;
    out->shuffle = state.shuffle;
    out->repeat = state.repeat;
    mutex_unlock(&cider_mutex);
}
/* Queue one control; the request id, or 0 when nothing was queued. */
static uint32_t queue_control(const char *command, int value)
{
    if (!state.available || ncommands >= ARRAYLEN(commands)) return 0;
    snprintf(commands[ncommands++], sizeof commands[0],
             "cc\t%lu\t%lu\t%s\t%d", (unsigned long)state.generation,
             (unsigned long)++next_request, command, value);
    return next_request;
}
void cider_control(const char *command, int value)
{
    mutex_lock(&cider_mutex);
    if (!strcmp(command, "volume-step"))
        state.volume = MAX(0, MIN(100, state.volume + value));
    queue_control(command, value);
    mutex_unlock(&cider_mutex);
}
/* Cider's own shuffle (0/1) or repeat (0 none, 1 one, 2 all), or the value
 * just asked for while Cider has not yet reported it. */
int cider_mode_get(int mode)
{
    mutex_lock(&cider_mutex);
    int live = mode == CIDER_MODE_REPEAT ? state.repeat : state.shuffle;
    int value = cider_mode_show(&modes, mode, live, current_tick, HZ);
    mutex_unlock(&cider_mutex);
    return value;
}
/* Set an explicit value on Cider, shown at once; retries are harmless. */
void cider_mode_set(int mode, int value)
{
    if (mode != CIDER_MODE_SHUFFLE && mode != CIDER_MODE_REPEAT) return;
    mutex_lock(&cider_mutex);
    uint32_t request = queue_control(mode == CIDER_MODE_REPEAT ? "repeat5"
                                                               : "shuffle", value);
    if (request) cider_mode_ask(&modes, mode, value, current_tick, request);
    mutex_unlock(&cider_mutex);
}
void cider_cancel_art(void)
{
    mutex_lock(&cider_mutex);
    if (transfer.active)
    {
        cancel_all |= cancel_request != 0;
        cancel_request = transfer.request;
    }
    transfer.active = disk_lookup = request_ready = false;
    mutex_unlock(&cider_mutex);
}
void cider_request_art(const char *key, const char *tier)
{
    if (!valid_key(key) || (strcmp(tier, "L") && strcmp(tier, "M") &&
        strcmp(tier, "S") && strcmp(tier, "XS"))) return;
    mutex_lock(&cider_mutex);
    if (cider_cache_find(&cache, key, tier) < 0 &&
        (!transfer.active || strcmp(transfer.key, key) ||
         strcmp(transfer.tier, tier) || transfer.fill))
        begin_transfer(key, tier, false);
    mutex_unlock(&cider_mutex);
}
void cider_unpin_art(void)
{
    mutex_lock(&cider_mutex);
    pin_on = false;
    cider_cache_pin(&cache, NULL, "L");
    mutex_unlock(&cider_mutex);
}
/* Album track list: keep that album's L cover resident (local store hit,
 * else USB + store write). Visible list covers replace the transfer first. */
void cider_pin_art(const char *key)
{
    if (!valid_key(key)) { cider_unpin_art(); return; }
    mutex_lock(&cider_mutex);
    if (!pin_on || strcmp(pin_key, key))
    {
        snprintf(pin_key, sizeof pin_key, "%s", key);
        pin_on = true; pin_at = current_tick - 10 * HZ;
        pin_apply();
    }
    mutex_unlock(&cider_mutex);
}
bool cider_copy_art(const char *key, const char *tier, void *pixels,
                     unsigned int size)
{
    bool found = false;
    mutex_lock(&cider_mutex);
    int i = cider_cache_find(&cache, key, tier);
    if (i >= 0 && cache.slot[i].length >= size + 12)
    {
        memcpy(pixels, cache.data + cache.slot[i].offset + 12, size);
        found = true;
    }
    mutex_unlock(&cider_mutex);
    return found;
}

void cider_request_xs(const char *const *keys, unsigned int count)
{
    if (!count || count > 5) return;
    for (unsigned int i = 0; i < count; i++)
        if (!valid_key(keys[i])) return;
    mutex_lock(&cider_mutex);
    if (transfer.active && !strcmp(transfer.tier,"XS"))
    { mutex_unlock(&cider_mutex); return; }
    xs_count = 0;
    for (unsigned int i = 0; i < count; i++)
        if (cider_cache_find(&cache, keys[i], "XS") < 0)
        {
            if (store_local(keys[i], "XS")) fill_add(keys[i], "XS");
            else snprintf(xs_keys[xs_count++], sizeof xs_keys[0], "%s", keys[i]);
        }
    if (xs_count)
    {
        if (transfer.active)
    {
        cancel_all |= cancel_request != 0;
        cancel_request = transfer.request;
    }
        memset(&transfer, 0, sizeof transfer);
        transfer.active = true; transfer.generation = state.generation;
        transfer.request = ++next_request;
        snprintf(transfer.key, sizeof transfer.key, "%s", xs_keys[0]);
        strcpy(transfer.tier, "XS");
        disk_lookup = false; request_ready = true; request_tries = 0;
        hold_until = 0; request_at = current_tick;
        if (!granted) grant = CIDER_CREDIT;
    }
    mutex_unlock(&cider_mutex);
}

/* context: a list route, or "" for none; the host then queues the list
 * around the song. A host that does not know native-context just refuses it. */
static bool play_route(const char *route, const char *context, int index)
{
    if (!cider_available()) return false;
    bool with = context && *context && index >= 0 &&
                strlen(context) + 64 < sizeof commands[0];
    mutex_lock(&cider_mutex);
    bool queued = !play_request && ncommands + (with ? 1 : 0) < ARRAYLEN(commands) &&
                  strlen(route) + 48 < sizeof commands[0];
    if (queued && with)
    {
        snprintf(commands[ncommands++], sizeof commands[0],
                 "cc\t%lu\t%lu\tnative-context\t%d|%s",
                 (unsigned long)state.generation,
                 (unsigned long)++next_request, index, context);
    }
    if (queued)
    {
        play_request = ++next_request;
        play_at = current_tick; play_reply = 0;
        snprintf(commands[ncommands++], sizeof commands[0],
                 "cc\t%lu\t%lu\tnative-play\t%s",
                 (unsigned long)state.generation,
                 (unsigned long)play_request, route);
        cider_log("play queued req=%lu", (unsigned long)play_request);
    }
    mutex_unlock(&cider_mutex);
    return queued;
}
bool cider_play(const char *route) { return play_route(route, "", -1); }
bool cider_play_in(const char *route, const char *context, int index)
{
    return play_route(route, context, index);
}

/* Only the UI commits the playback source, after the host confirms selection. */
int cider_play_status(void)
{
    if (!initialized) return 0;
    if (!play_request) return play_reply;
    /* Called from button_get() on the UI thread: never block on a PIP mutex
     * there. The fields are words; a short IRQ-off section is atomic on this
     * single core. */
    int oldlevel = disable_irq_save();
    int reply = play_reply;
    if (play_request && !reply &&
        TIME_AFTER(current_tick, play_at + 6 * HZ)) reply = -1;
    bool commit = reply > 0 && play_request != 0;
    if (reply) { play_request = 0; play_reply = reply; }
    restore_irq(oldlevel);
    if (commit)
    {
        if (audio_status() & AUDIO_STATUS_PLAY) audio_pause();
        active = true;
    }
    return reply;
}
void cider_local_play(void)
{
    if (!initialized || (!active && !play_request)) return;
    mutex_lock(&cider_mutex);
    bool pending = play_request != 0;
    if (pending) { play_request = 0; play_reply = -1; }
    mutex_unlock(&cider_mutex);
    if (!active && !pending) return;
    cider_control("source-local", 0);
    active = false;
}
void cider_page_open(const char *route)
{
    mutex_lock(&cider_mutex);
    cider_library_open(route);
    mutex_unlock(&cider_mutex);
}
int cider_page_total(void)
{
    mutex_lock(&cider_mutex);
    int n = cider_library_total();
    mutex_unlock(&cider_mutex);
    return n;
}
bool cider_page_stale(void)
{
    mutex_lock(&cider_mutex);
    bool value = cider_library_stale();
    mutex_unlock(&cider_mutex);
    return value;
}
bool cider_page_row(int index, struct cider_row *row)
{
    mutex_lock(&cider_mutex);
    bool have = cider_library_row(index, row);
    mutex_unlock(&cider_mutex);
    return have;
}

unsigned int cider_art_revision(void)
{
    mutex_lock(&cider_mutex);
    unsigned int value = art_revision;
    mutex_unlock(&cider_mutex);
    return value;
}

bool cider_has_art(const char *key, const char *tier)
{
    mutex_lock(&cider_mutex);
    bool found = cider_cache_find(&cache,key,tier) >= 0;
    mutex_unlock(&cider_mutex);
    return found;
}

void cider_prefetch_art(const char *key, const char *tier)
{
    mutex_lock(&cider_mutex);
    bool busy = transfer.active;
    mutex_unlock(&cider_mutex);
    if (!busy) cider_request_art(key,tier);
}

void cider_warm_art(const char *const *keys, unsigned int count,
                    const char *tier)
{
    if (count > 7 || (strcmp(tier, "L") && strcmp(tier, "M") &&
                     strcmp(tier, "S") && strcmp(tier, "XS"))) return;
    /* Off the caller's stack: the WPS and browser run on the UI stack. */
    static char joined[sizeof warm_keys];
    unsigned int n = 0;
    mutex_lock(&cider_mutex);
    joined[0] = 0;
    for (unsigned int i = 0; i < count; i++)
    {
        if (!valid_key(keys[i])) continue;
        if (store_local(keys[i], tier)) { fill_add(keys[i], tier); continue; }
        n += snprintf(joined + n, sizeof joined - n, "%s%s", n ? "," : "", keys[i]);
    }
    if (strcmp(joined, warm_keys) || strcmp(tier, warm_tier))
    {
        snprintf(warm_keys, sizeof warm_keys, "%s", joined);
        snprintf(warm_tier, sizeof warm_tier, "%s", tier);
        warm_ready = true;
    }
    mutex_unlock(&cider_mutex);
}

void cider_retain_art(const char *const *keys, unsigned int count,
                      const char *tier)
{
    mutex_lock(&cider_mutex);
    bool keep = false;
    for (unsigned int i = 0; i < count; i++)
        if (!strcmp(keys[i],transfer.key)) keep = true;
    if (transfer.active && (!keep || strcmp(tier,transfer.tier)))
    {
        cancel_all |= cancel_request != 0;
        cancel_request = transfer.request;
        transfer.active = disk_lookup = request_ready = false;
    }
    mutex_unlock(&cider_mutex);
}
