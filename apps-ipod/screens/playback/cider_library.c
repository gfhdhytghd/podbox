/* Paged Cider rows; called under the resident client mutex. GPL v2+. */
#include "config.h"
#include "cider_library.h"
#include "kernel.h"
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#define PAGE_SLOTS 3
static struct
{
    struct cider_row rows[CIDER_PAGE_ROWS];
    int offset, count;
    unsigned int request, used;
    bool pending;
    long requested;
} pages[PAGE_SLOTS];
static char query[380];
static uint32_t library_generation;
static unsigned int clock, request_id = 0x10000000;
static int total = -1, wanted;
static bool stale;

_Static_assert(sizeof pages + sizeof query + 64 < 112 * 1024, "Cider module RAM budget");

void cider_library_reset(void)
{
    memset(pages, 0, sizeof pages);
    total = -1;
    wanted = 0;
}
void cider_library_invalidate(uint32_t generation)
{
    if (!generation || generation == library_generation)
        return;
    cider_library_reset();
    library_generation = generation;
}
void cider_library_open(const char *route)
{
    if (!strcmp(query, route))
        return;
    snprintf(query, sizeof query, "%s", route);
    cider_library_reset();
}
int cider_library_total(void)
{
    return total;
}
bool cider_library_stale(void)
{
    return stale;
}
bool cider_library_row(int index, struct cider_row *row)
{
    if (index < 0 || (total >= 0 && index >= total))
        return false;
    for (int i = 0; i < PAGE_SLOTS; i++)
        if (index >= pages[i].offset &&
            index < pages[i].offset + pages[i].count)
        {
            *row = pages[i].rows[index - pages[i].offset];
            pages[i].used = ++clock;
            /* Prefetch before reaching the end of a page. */
            if (index >= pages[i].offset + pages[i].count - 8)
                wanted = pages[i].offset + pages[i].count;
            return true;
        }
    wanted = index;
    return false;
}
unsigned int cider_library_next(char *out, unsigned int size, uint32_t gen)
{
    if (!*query || !gen || (total >= 0 && wanted >= total))
        return 0;
    for (int i = 0; i < PAGE_SLOTS; i++)
    {
        if (wanted >= pages[i].offset &&
            wanted < pages[i].offset + pages[i].count)
            return 0;
        if (pages[i].pending)
        {
            if (TIME_BEFORE(current_tick, pages[i].requested + 5 * HZ))
                return 0;
            pages[i].pending = false;
        }
    }
    int oldest = 0;
    for (int i = 1; i < PAGE_SLOTS; i++)
        if (pages[i].used < pages[oldest].used)
            oldest = i;
    pages[oldest].offset = wanted;
    pages[oldest].count = 0;
    pages[oldest].pending = true;
    pages[oldest].request = ++request_id;
    pages[oldest].requested = current_tick;
    pages[oldest].used = ++clock;
    return snprintf(out, size, "cq\t%lu\t%u\t%d\t50\t%s", (unsigned long)gen,
                    pages[oldest].request, wanted, query);
}
static bool field(const char **p, const char *end, char *out, unsigned int size)
{
    unsigned int n = 0;
    while (*p < end && **p != '\t' && **p != '\n')
    {
        if (n + 1 < size)
            out[n++] = **p;
        (*p)++;
    }
    out[n] = 0;
    if (*p == end)
        return false;
    (*p)++;
    return true;
}
bool cider_library_receive(const void *data, unsigned int size,
                           uint32_t generation, uint32_t library)
{
    const char *p = data, *end = p + size;
    char number[32];
    unsigned long v[7];
    if (!field(&p, end, number, sizeof number) || strcmp(number, "CL"))
        return false;
    for (int i = 0; i < 7; i++)
    {
        if (!field(&p, end, number, sizeof number))
            return false;
        char *tail;
        v[i] = strtoul(number, &tail, 10);
        if (!*number || *tail)
            return false;
    }
    if (v[0] != generation || v[2] < library || v[6] > CIDER_PAGE_ROWS ||
        v[3] > 1000000 || v[4] > 1000000)
        return false;
    int slot = -1;
    for (int i = 0; i < PAGE_SLOTS; i++)
        if (pages[i].pending && pages[i].request == v[1] &&
            (unsigned long)pages[i].offset == v[3])
            slot = i;
    if (slot < 0)
        return false;
    if (library_generation && v[2] != library_generation)
        for (int i = 0; i < PAGE_SLOTS; i++)
            if (i != slot)
                memset(&pages[i], 0, sizeof pages[i]);
    pages[slot].count = 0;
    for (unsigned int i = 0; i < v[6]; i++)
    {
        struct cider_row *r = &pages[slot].rows[i];
        memset(r, 0, sizeof *r);
        if (!field(&p, end, r->id, sizeof r->id) ||
            !field(&p, end, r->title, sizeof r->title) ||
            !field(&p, end, r->subtitle, sizeof r->subtitle) ||
            !field(&p, end, r->art, sizeof r->art))
            return false;
        if (!field(&p, end, number, sizeof number))
            return false;
        r->duration = strtoul(number, NULL, 10);
        if (!field(&p, end, number, sizeof number))
            return false;
        r->count = atoi(number);
        if (!field(&p, end, number, sizeof number))
            return false;
        r->kind = !strcmp(number, "album")      ? CIDER_ROW_ALBUM
                  : !strcmp(number, "artist")   ? CIDER_ROW_ARTIST
                  : !strcmp(number, "playlist") ? CIDER_ROW_PLAYLIST
                  : !strcmp(number, "song")     ? CIDER_ROW_SONG
                                                : CIDER_ROW_MENU;
        if (!field(&p, end, r->route, sizeof r->route) ||
            !field(&p, end, number, sizeof number))
            return false;
        r->bg = strtoul(number, NULL, 16);
        if (!field(&p, end, number, sizeof number))
            return false;
        r->year = atoi(number);
    }
    if (p != end)
        return false;
    library_generation = v[2];
    total = v[4];
    stale = v[5] != 0;
    pages[slot].count = v[6];
    pages[slot].pending = false;
    return true;
}
