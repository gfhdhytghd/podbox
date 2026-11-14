/* Bounded Cider state, LRU and chunk assembly; no UI, disk or allocation. */
#include "cider_model.h"
#include "podlink.h"
#include <string.h>
#include <stdlib.h>
#include <stdio.h>

static uint32_t u32(const unsigned char *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 |
           (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static void copy(char *to, const char *from, unsigned int size)
{
    unsigned int n = strlen(from);
    if (n >= size)
    {
        n = size - 1;
        while (n && ((unsigned char)from[n] & 0xc0) == 0x80) n--;
    }
    memcpy(to, from, n);
    to[n] = 0;
}

static bool number(const char *text, uint32_t *out)
{
    uint64_t v = 0;
    if (!*text) return false;
    for (; *text; text++)
    {
        if (*text < '0' || *text > '9') return false;
        v = v * 10 + *text - '0';
        if (v > UINT32_MAX) return false;
    }
    *out = v;
    return true;
}

int cider_state_apply(struct cider_state *s, const void *data,
                      unsigned int length, uint32_t tick)
{
    char buf[1024], *f[18];
    if (length >= sizeof buf || memchr(data, 0, length)) return 0;
    memcpy(buf, data, length); buf[length] = 0;
    int n = 1; f[0] = buf;
    for (unsigned int i = 0; i < length; i++)
        if (buf[i] == '\t')
        {
            if (n == 18) return 0;
            buf[i] = 0; f[n++] = &buf[i + 1];
        }
    uint32_t gen;
    if (n < 2 || !number(f[1], &gen) || !gen) return 0;
    if (n == 4 && !strcmp(f[0], "CA"))
    {
        if (strcmp(f[2], "0") && strcmp(f[2], "1")) return 0;
        if (gen != s->generation)
        {
            s->seq = 0; s->have = false;
            s->generation = gen;
        }
        s->available = f[2][0] == '1';
        copy(s->reason, f[3], sizeof s->reason);
        s->received = tick;
        return 1;
    }
    if (n == 3 && !strcmp(f[0], "CG") && gen == s->generation)
    {
        uint32_t library;
        if (!number(f[2], &library)) return 0;
        s->library_generation = library;
        return 1;
    }
    if (n == 5 && !strcmp(f[0], "CP") && gen == s->generation &&
        s->have && !strcmp(f[2], s->id))
    {
        for (int i = 3; i < 5; i++)
        {
            if (!strcmp(f[i], "-")) continue;
            if (strlen(f[i]) != 64) return 0;
            for (int j = 0; j < 64; j++)
                if (!((f[i][j] >= '0' && f[i][j] <= '9') ||
                      (f[i][j] >= 'a' && f[i][j] <= 'f'))) return 0;
        }
        /* "-" means no neighbour: keep the slot empty, never the dash. */
        copy(s->previous_art, strcmp(f[3], "-") ? f[3] : "", sizeof s->previous_art);
        copy(s->next_art, strcmp(f[4], "-") ? f[4] : "", sizeof s->next_art);
        return 1;
    }
    if ((n != 16 && n != 18) || strcmp(f[0], "CN") || gen != s->generation)
        return 0;
    uint32_t values[8];
    for (int i = 0; i < 8; i++)
        if (!number(f[i + 2], &values[i])) return 0;
    if ((s->have && (int32_t)(values[0] - s->seq) <= 0) ||
        values[2] > 7 * 24 * 3600 * 1000u || values[3] > values[2] ||
        (values[4] != 0 && values[4] != 1000) || values[5] > 100 ||
        values[6] > 1 || values[7] > 2)
        return 0;
    s->seq = values[0]; s->host_ms = values[1];
    s->duration = values[2]; s->position = values[3];
    s->rate = values[4]; s->volume = values[5];
    s->shuffle = values[6]; s->repeat = values[7];
    if (strcmp(s->id, f[10]))
        s->previous_art[0] = s->next_art[0] = 0;
    copy(s->id, f[10], sizeof s->id);
    copy(s->art, f[11], sizeof s->art);
    s->bg = strtoul(f[12], NULL, 16) & 0xffffff;
    copy(s->title, f[13], sizeof s->title);
    copy(s->artist, f[14], sizeof s->artist);
    copy(s->album, f[15], sizeof s->album);
    s->muted = n == 18 && !strcmp(f[16], "1");
    if (n == 18) copy(s->composer, f[17], sizeof s->composer);
    s->received = tick; s->have = true;
    return 1;
}

/* Firmware scanf has no %u/%lu conversion. Parse unsigned wire fields directly. */
int cider_reply_status(const void *data, unsigned int length,
                       uint32_t generation, uint32_t request)
{
    const char *p = data, *end = p + length;
    if (!request || length < 10 || memchr(data, 0, length) ||
        memcmp(p, "CK\t", 3)) return 0;
    p += 3;
    uint32_t fields[3];
    for (int i = 0; i < 3; i++)
    {
        char text[11]; unsigned int n = 0;
        while (p < end && *p != '\t')
        {
            if (n + 1 >= sizeof text) return 0;
            text[n++] = *p++;
        }
        text[n] = 0;
        if (p == end || !number(text, &fields[i])) return 0;
        p++;
    }
    if (fields[0] != generation || fields[1] != request || fields[2] > 1)
        return 0;
    return fields[2] ? 1 : -1;
}

uint32_t cider_position(const struct cider_state *s, uint32_t tick, int hz)
{
    uint64_t p = s->position;
    if (s->available && hz > 0)
        p += (uint64_t)(uint32_t)(tick - s->received) * s->rate / hz;
    return p > s->duration ? s->duration : p;
}

int cider_cache_find(struct cider_cache *c, const char *key, const char *tier)
{
    for (int i = 0; i < CIDER_SLOTS; i++)
        if (c->slot[i].length && !strcmp(c->slot[i].key, key) &&
            !strcmp(c->slot[i].tier, tier))
        {
            c->slot[i].used = ++c->clock;
            return i;
        }
    return -1;
}

static void remove_slot(struct cider_cache *c, int index)
{
    unsigned int offset = c->slot[index].offset;
    unsigned int length = c->slot[index].length;
    memmove(c->data + offset, c->data + offset + length,
            c->bytes - offset - length);
    for (int i = 0; i < CIDER_SLOTS; i++)
        if (c->slot[i].length && c->slot[i].offset > offset)
            c->slot[i].offset -= length;
    c->bytes -= length;
    memset(&c->slot[index], 0, sizeof c->slot[index]);
}

bool cider_art_valid(const void *data, unsigned int length, const char *tier)
{
    const unsigned char *p = data;
    unsigned int dim = !strcmp(tier, "L") ? 240 : !strcmp(tier, "M") ?
                       128 : !strcmp(tier, "S") ? 56 :
                       !strcmp(tier, "XS") ? 32 : 0;
    return dim && length == 12 + dim * dim * 2 &&
           u32(p) == (8u << 16 | 0x5441) &&
           u32(p + 4) == (dim << 16 | dim) &&
           u32(p + 8) == (!strcmp(tier, "M") ? 1u : 0u);
}

bool cider_cache_put(struct cider_cache *c, const char *key, const char *tier,
                     const void *data, unsigned int length, bool dirty)
{
    if (!cider_art_valid(data, length, tier) || strlen(key) != 64)
        return false;
    int slot = cider_cache_find(c, key, tier);
    if (slot >= 0) remove_slot(c, slot);
    for (;;)
    {
        slot = -1;
        for (int i = 0; i < CIDER_SLOTS; i++)
            if (!c->slot[i].length) { slot = i; break; }
        if (slot >= 0 && c->bytes + length <= sizeof c->data) break;
        int oldest = -1;
        for (int i = 0; i < CIDER_SLOTS; i++)
            if (c->slot[i].length && (oldest < 0 ||
                c->slot[i].used < c->slot[oldest].used)) oldest = i;
        if (oldest < 0) return false;
        remove_slot(c, oldest);
    }
    struct cider_slot *s = &c->slot[slot];
    copy(s->key, key, sizeof s->key); copy(s->tier, tier, sizeof s->tier);
    s->offset = c->bytes; s->length = length;
    s->used = ++c->clock; s->dirty = dirty;
    memcpy(c->data + c->bytes, data, length); c->bytes += length;
    return true;
}

int cider_chunk(struct cider_transfer *t, const void *data, unsigned int length)
{
    const unsigned char *p = data;
    if (length < 29 || length > 12 * 1024 || memcmp(p, "CAR1", 4))
        return -1;
    unsigned int gen = u32(p + 4), req = u32(p + 8);
    unsigned int offset = u32(p + 12), total = u32(p + 16);
    unsigned int crc = u32(p + 20), part_crc = u32(p + 24);
    unsigned int bytes = length - 28;
    if (!t->active || gen != t->generation || req != t->request)
        return 0;
    if (total > sizeof t->data || !total || offset > total ||
        bytes > total - offset || offset != t->received ||
        podlink_crc32(p + 28, bytes) != part_crc ||
        (t->received && (t->total != total || t->crc != crc)))
        return -1;
    t->total = total; t->crc = crc;
    memcpy(t->data + offset, p + 28, bytes); t->received += bytes;
    if (t->received < total) return 0;
    if (podlink_crc32(t->data, total) != crc ||
        !cider_art_valid(t->data, total, t->tier)) return -1;
    t->active = false;
    return 1;
}

int cider_window(int center, int velocity, int out[9])
{
    int n = 0;
    if (center < 0) return 0;
    out[n++] = center;
    for (int d = 1; d <= 4; d++)
    {
        int first = velocity > 0 ? center + d : center - d;
        int second = velocity > 0 ? center - d : center + d;
        if (first >= 0) out[n++] = first;
        if (second >= 0) out[n++] = second;
    }
    return n;
}

int cider_slide_slot(const int loaded[9], int center, int wanted)
{
    int distance = abs(wanted - center), farthest = -1;
    if (wanted < 0 || distance > 4) return -1;
    for (int i = 0; i < 9; i++)
        if (loaded[i] == wanted) return i;
    for (int i = 0; i < 9; i++)
        if (loaded[i] < 0) return i;
    for (int i = 0; i < 9; i++)
        if (abs(loaded[i] - center) > distance &&
            (farthest < 0 || abs(loaded[i] - center) >
                            abs(loaded[farthest] - center))) farthest = i;
    return farthest;
}
