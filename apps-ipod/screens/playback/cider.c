/* Resident Cider receiver and bounded cache. Disk I/O stays on its worker. */
#include "config.h"
#include "screens/playback/cider.h"
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

#define DISK_ROOT ROCKBOX_DIR "/cider-cache"
#define M_ROOT ROCKBOX_DIR "/library/cache/art/cider-v1"
#define DISK_LIMIT (32u * 1024u * 1024u)
static struct mutex cider_mutex;
static struct cider_state state;
static struct cider_cache cache;
static struct cider_transfer transfer;
static bool initialized, active, disk_lookup, request_ready, disk_running;
static uint32_t next_request, grant, granted, cancel_request;
static bool cancel_all;
static long hello_at, request_at;
static unsigned int request_tries;
static char commands[12][384];
static char xs_keys[5][65];
static unsigned int xs_count;
static unsigned int ncommands;
static unsigned char disk_data[CIDER_ART_MAX] CACHEALIGN_ATTR;
static long disk_stack[DEFAULT_STACK_SIZE * 3 / sizeof(long)];
static struct event_queue disk_queue;
_Static_assert(sizeof cache + sizeof transfer + sizeof disk_data +
               sizeof disk_stack + 240 * 240 * 2 + sizeof state +
               sizeof commands < 1024 * 1024, "Cider RAM below 1 MiB");

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
    state.available = state.have = false;
    state.generation = 0;
    transfer.active = false;
    disk_lookup = request_ready = false;
    ncommands = grant = granted = cancel_request = 0;
    xs_count = 0; cancel_all = false;
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
         !memcmp(m->data, "CG\t", 3));
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
            if (!memcmp(m->data, "CAR1", 4))
            {
                int result = cider_chunk(&transfer, m->data, m->length);
                if (m->length >= 28)
                    cider_log("chunk req=%lu off=%lu bytes=%lu result=%d",
                              (unsigned long)u32(m->data + 8),
                              (unsigned long)u32(m->data + 12),
                              (unsigned long)m->length - 28, result);
                if (result > 0)
                {
                    cider_cache_put(&cache, transfer.key, transfer.tier,
                                    transfer.data, transfer.total, true);
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
                    cider_cache_put(&cache, key, "XS", m->data + pos,
                                    size, false);
                    pos += size;
                }
                if (pos == m->length) transfer.active = false;
            }
        }
    }
    else
    {
        uint32_t previous = state.generation;
        bool available = state.available;
        cider_state_apply(&state, m->data, m->length, current_tick);
        if (state.generation != previous || state.available != available)
            cider_log("availability gen=%lu available=%d reason=%s",
                      (unsigned long)state.generation, state.available,
                      state.reason);
        if (state.generation != previous)
        {
            transfer.active = false; ncommands = 0;
            disk_lookup = request_ready = false;
            grant = granted = cancel_request = 0;
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
            n = snprintf(record, size, "ci\t1\t%d", CIDER_CREDIT);
            hello_at = current_tick;
        }
    }
    else if (ncommands)
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
    else if (grant)
    {
        n = snprintf(record, size, "cg\t%lu\t%lu",
                     (unsigned long)state.generation, (unsigned long)grant);
        cider_log("CIDER credit tick=%ld grant=%lu outstanding=%lu",
                   current_tick, (unsigned long)grant,
                   (unsigned long)granted);
        granted += grant; grant = 0;
    }
    else if (transfer.active && !disk_lookup &&
             (request_ready ||
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
                             "cr\t%lu\t%lu\t%s\t%s\t%lu\t3\t0",
                             (unsigned long)state.generation,
                             (unsigned long)transfer.request, transfer.key,
                             transfer.tier, (unsigned long)transfer.received);
            cider_log("CIDER request tick=%ld req=%lu off=%lu",
                       current_tick, (unsigned long)transfer.request,
                       (unsigned long)transfer.received);
            request_ready = false; request_at = current_tick;
        }
        else
        {
            cancel_request = transfer.request;
            transfer.active = false;
        }
    }
    mutex_unlock(&cider_mutex);
    return n < size ? n : 0;
}

static void path_for(char *path, unsigned int size, const char *key,
                     const char *tier, bool temporary)
{
    snprintf(path, size, "%s/%s-%s.aat%s",
             !strcmp(tier, "M") ? M_ROOT : DISK_ROOT, key, tier,
             temporary ? ".tmp" : "");
}

/* Evict oldest on-disk entries in closed-directory batches. Cider's
 * namespace is outside the local-library pass's three size folders. */
static void disk_evict(void)
{
    for (;;)
    {
        if (tagcache_is_busy() || !queue_empty(&disk_queue)) break;
        uint64_t bytes = 0;
        time_t oldest = 0;
        char victim[MAX_PATH] = "";
        const char *roots[] = {DISK_ROOT, M_ROOT};
        for (int i = 0; i < 2; i++)
        {
            DIR *dir = opendir(roots[i]);
            if (!dir) continue;
            struct dirent *entry;
            while ((entry = readdir(dir)))
            {
                if (entry->d_name[0] == '.') continue;
                struct dirinfo info = dir_get_info(dir, entry);
                if (info.attribute & ATTR_DIRECTORY) continue;
                bytes += info.size;
                if (!victim[0] || info.mtime < oldest)
                {
                    oldest = info.mtime;
                    snprintf(victim, sizeof victim, "%s/%s",
                             roots[i], entry->d_name);
                }
            }
            closedir(dir);
        }
        if (bytes <= DISK_LIMIT || !victim[0]) break;
        if (remove(victim) < 0) break;
        yield();
    }
}

static void disk_worker(void)
{
    for (;;)
    {
        struct queue_event ev;
        queue_wait_w_tmo(&disk_queue, &ev, HZ);
        if (ev.id == SYS_USB_CONNECTED)
        {
            usb_acknowledge(SYS_USB_CONNECTED_ACK, ev.data);
            do { queue_wait(&disk_queue, &ev); }
            while (ev.id != SYS_USB_DISCONNECTED);
            continue;
        }
        if (tagcache_is_busy()) continue;
        char key[65], tier[3], path[MAX_PATH], temp[MAX_PATH];
        bool lookup;
        uint32_t req;
        mutex_lock(&cider_mutex);
        lookup = disk_lookup; req = transfer.request;
        snprintf(key, sizeof key, "%s", transfer.key);
        snprintf(tier, sizeof tier, "%s", transfer.tier);
        mutex_unlock(&cider_mutex);
        if (lookup)
        {
            path_for(path, sizeof path, key, tier, false);
            int fd = open(path, O_RDONLY);
            int length = -1;
            if (fd >= 0)
            {
                int size = ffilesize(fd);
                if (size > 0 && (unsigned int)size <= sizeof disk_data)
                    length = read(fd, disk_data, size);
                close(fd);
            }
            mutex_lock(&cider_mutex);
            if (transfer.active && transfer.request == req && disk_lookup)
            {
                if (length > 0 && cider_cache_put(&cache, key, tier,
                            disk_data, length, false)) transfer.active = false;
                else request_ready = true;
                disk_lookup = false;
            }
            mutex_unlock(&cider_mutex);
        }
        if (!storage_disk_is_active()) continue;
        /* Four writes amortize a wake; none wakes a sleeping disk. */
        bool wrote = false;
        for (int batch = 0; batch < 4; batch++)
        {
            unsigned int length = 0;
            mutex_lock(&cider_mutex);
            for (int i = 0; i < CIDER_SLOTS; i++)
                if (cache.slot[i].length && cache.slot[i].dirty)
                {
                    struct cider_slot *s = &cache.slot[i];
                    length = s->length;
                    memcpy(disk_data, cache.data + s->offset, length);
                    snprintf(key, sizeof key, "%s", s->key);
                    snprintf(tier, sizeof tier, "%s", s->tier);
                    s->dirty = false;
                    break;
                }
            mutex_unlock(&cider_mutex);
            if (!length || tagcache_is_busy() ||
                !queue_empty(&disk_queue)) break;
            mkdir(DISK_ROOT); mkdir(M_ROOT);
            path_for(path, sizeof path, key, tier, false);
            path_for(temp, sizeof temp, key, tier, true);
            int fd = open(temp, O_WRONLY | O_CREAT | O_TRUNC, 0666);
            bool ok = fd >= 0 && write(fd, disk_data, length) == (int)length;
            if (fd >= 0) { fsync(fd); close(fd); }
            if (ok) wrote |= rename(temp, path) == 0;
            else remove(temp);
            yield();
        }
        if (wrote) disk_evict();
    }
}

void cider_init(void)
{
    static const struct podlink_resident consumer = {receive, next, reset};
    if (initialized) return;
    mutex_init(&cider_mutex);
    queue_init(&disk_queue, true);
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
void cider_set_active(bool value) { active = value; }
void cider_snapshot(struct cider_state *out)
{
    mutex_lock(&cider_mutex); *out = state; mutex_unlock(&cider_mutex);
}
void cider_control(const char *command, int value)
{
    mutex_lock(&cider_mutex);
    if (!strcmp(command, "volume")) state.volume = value;
    if (state.available && ncommands < ARRAYLEN(commands))
        snprintf(commands[ncommands++], sizeof commands[0],
                 "cc\t%lu\t%lu\t%s\t%d", (unsigned long)state.generation,
                 (unsigned long)++next_request, command, value);
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
         strcmp(transfer.tier, tier)))
    {
        if (transfer.active)
    {
        cancel_all |= cancel_request != 0;
        cancel_request = transfer.request;
    }
        xs_count = 0;
        memset(&transfer, 0, sizeof transfer);
        transfer.active = true; transfer.generation = state.generation;
        transfer.request = ++next_request;
        snprintf(transfer.key, sizeof transfer.key, "%s", key);
        snprintf(transfer.tier, sizeof transfer.tier, "%s", tier);
        disk_lookup = disk_running;
        request_ready = !disk_running; request_tries = 0;
        if (!granted) grant = CIDER_CREDIT;
        if (disk_running) queue_post(&disk_queue, 1, 0);
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
    xs_count = 0;
    for (unsigned int i = 0; i < count; i++)
        if (cider_cache_find(&cache, keys[i], "XS") < 0)
            snprintf(xs_keys[xs_count++], sizeof xs_keys[0], "%s", keys[i]);
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
        if (!granted) grant = CIDER_CREDIT;
    }
    mutex_unlock(&cider_mutex);
}
