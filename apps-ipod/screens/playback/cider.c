/* Resident Cider receiver and bounded cache. Disk I/O stays on its worker. */
#include "config.h"
#include "screens/playback/cider.h"
#include "screens/playback/cider_library.h"
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
static uint32_t play_request;
static struct cider_modes modes;
static int play_reply;
static long play_at, art_used_at;
static char warm_keys[7 * 65], warm_tier[3];
static bool warm_ready;
static char commands[12][384];
static char xs_keys[5][65];
static unsigned int xs_count;
static unsigned int ncommands, art_revision;
static unsigned char disk_data[CIDER_ART_MAX] CACHEALIGN_ATTR;
static long disk_stack[DEFAULT_STACK_SIZE * 3 / sizeof(long)];
static struct event_queue disk_queue;
_Static_assert(sizeof cache + sizeof transfer + sizeof disk_data +
               sizeof disk_stack + sizeof state + sizeof commands +
               (120 + 112 + 56 + 36) * 1024 < 1152 * 1024,
               "Cider resident, WPS, pages, browser and carousel below 1152 KiB");

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
         !memcmp(m->data, "CG\t", 3) || !memcmp(m->data, "CP\t", 3));
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
            n = snprintf(record, size, "ci\t1\t%d\tcn18,neighbors,warm,play-ack,modes5", CIDER_CREDIT);
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
    if (!n && warm_ready && state.generation)
    {
        n = snprintf(record, size, "cw\t%lu\t%s\t%s",
                     (unsigned long)state.generation, warm_tier, warm_keys);
        warm_ready = false;
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
    long evict_at = current_tick;
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
                            disk_data, length, false))
                {
                    cancel_request = transfer.request;
                    transfer.active = false; art_revision++;
                }
                else if (!request_tries) request_ready = true;
                disk_lookup = false;
            }
            mutex_unlock(&cider_mutex);
        }
        mutex_lock(&cider_mutex);
        bool busy = transfer.active || TIME_BEFORE(current_tick, art_used_at + HZ);
        mutex_unlock(&cider_mutex);
        if (!storage_disk_is_active() || busy) continue;
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
        if (wrote && TIME_AFTER(current_tick, evict_at + 60 * HZ))
        { disk_evict(); evict_at = current_tick; }
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
         strcmp(transfer.tier, tier)))
    {
        if (transfer.active)
    {
        cancel_all |= cancel_request != 0;
        cancel_request = transfer.request;
    }
        xs_count = 0;
        art_used_at = current_tick;
        memset(&transfer, 0, sizeof transfer);
        transfer.active = true; transfer.generation = state.generation;
        transfer.request = ++next_request;
        snprintf(transfer.key, sizeof transfer.key, "%s", key);
        snprintf(transfer.tier, sizeof transfer.tier, "%s", tier);
        disk_lookup = disk_running && storage_disk_is_active();
        request_ready = true; request_tries = 0;
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
    if (transfer.active && !strcmp(transfer.tier,"XS"))
    { mutex_unlock(&cider_mutex); return; }
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
