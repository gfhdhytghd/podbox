/* Stable, bounded message layout. GPL version 2 or later. */
#include <string.h>
#include "detail_layout.h"

static void drop_line(struct detail_layout *c)
{
    int bytes = c->start[1];
    memmove(c->text, c->text + bytes, c->used - bytes + 1);
    c->used -= bytes;
    for (int i = 1; i < c->count; i++)
    {
        c->start[i - 1] = c->start[i] - bytes;
        c->end[i - 1] = c->end[i] - bytes;
    }
    c->count--;
}

static void next_line(struct detail_layout *c)
{
    if (c->count == DETAIL_LAYOUT_LINES) drop_line(c);
    c->start[c->count] = c->end[c->count] = c->used;
    c->count++;
    c->pixels = 0;
}

void detail_layout_update(struct detail_layout *c, const char *pane,
    const char *identity, const char *text, const struct hr_detail *detail,
    int width, bool freeze, int (*glyph)(const char *, int *, void *), void *ctx)
{
    if (freeze && c->count) return;
    bool same = !strcmp(c->pane, pane) && c->width == width &&
                !strcmp(c->identity, identity);
    size_t old = strlen(c->wire), length = strlen(text), overlap = 0;
    if (same)
    {
        if (length >= old && !strncmp(c->wire, text, old)) overlap = old;
        else
            for (size_t n = old < length ? old : length; n >= 8; n--)
                if (!memcmp(c->wire + old - n, text, n))
                { overlap = n; break; }
    }
    if (!same || (!overlap && strcmp(c->wire, text)))
    {
        memset(c, 0, sizeof *c);
        hr_copy(c->pane, pane, sizeof c->pane);
        hr_copy(c->identity, identity, sizeof c->identity);
        c->width = width;
    }
    const char *p = text + overlap;
    while (*p)
    {
        int bytes = 1, pixels = glyph(p, &bytes, ctx);
        if (!c->count) next_line(c);
        while (c->used + bytes >= DETAIL_LAYOUT_BYTES && c->count > 1)
            drop_line(c);
        /* A pathological single line still has a strict RAM bound. */
        if (c->used + bytes >= DETAIL_LAYOUT_BYTES) break;
        if (*p != '\n' && c->pixels && c->pixels + pixels > width)
            next_line(c);
        memcpy(c->text + c->used, p, bytes);
        c->used += bytes;
        c->text[c->used] = '\0';
        if (*p == '\n') next_line(c);
        else
        {
            c->end[c->count - 1] = c->used;
            c->pixels += pixels;
        }
        p += bytes;
    }
    hr_copy(c->wire, text, sizeof c->wire);
    memcpy(c->steps, detail->step, sizeof c->steps);
}
