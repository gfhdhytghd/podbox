/* Cider album provider for the native carousel. GPL version 2 or later. */
#include "config.h"
#include "carousel.h"
#include "screens/playback/cider.h"
#include "screens/playback/cider_library.h"
#include "settings/settings.h"
#include "draw/screen_access.h"
#include "root_menu.h"
#include "input/action.h"
#include "input/agents_shortcut.h"
#include "font.h"
#include "widgets/splash.h"
#include "lang.h"
#include "kernel.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static int resume_index, previous_center;
static int travel = 1;
static bool flicking;
static long moved_at;
static unsigned int revision;
static fb_data surface[128 * 128], tiny[32 * 32];
_Static_assert(sizeof surface + sizeof tiny + 64 < 36 * 1024,
               "Cider module RAM budget");

static int build(void)
{
    return cider_page_total() > 0 ? SUCCESS : ERROR_NO_ALBUMS;
}
static int count(void)
{
    return cider_page_total();
}
static unsigned int key(int index)
{
    (void)index;
    return 0;
}
static void noop(void)
{
}
static int jump(void)
{
    return center_index;
}
static void initial(const char *file)
{
    (void)file;
    set_current_slide(MAX(0, MIN(count() - 1, resume_index)));
    previous_center = center_index;
    moved_at = current_tick - HZ;
    flicking = false;
}
static int enter(int index)
{
    struct cider_row row;
    if (!cider_page_row(index, &row))
        return GO_TO_PREVIOUS;
    resume_index = index;
    cider_browse_album(&row);
    return GO_TO_PREVIOUS;
}
static bool art(int index, struct bitmap *bitmap)
{
    struct cider_row row;
    if (!cider_page_row(index, &row))
        return false;
    int dim = 128;
    if (!cider_copy_art(row.art, "M", surface, sizeof surface))
    {
        if (cider_copy_art(row.art, "XS", tiny, sizeof tiny))
            for (int x = 0; x < dim; x++)
                for (int y = 0; y < dim; y++)
                    surface[x * dim + y] = tiny[(y / 4) * 32 + x / 4];
        else
        {
            fb_data color =
                LCD_RGBPACK(row.bg >> 16, (row.bg >> 8) & 255, row.bg & 255);
            for (int i = 0; i < dim * dim; i++)
                surface[i] = color;
        }
    }
    bitmap->width = bitmap->height = dim;
    bitmap->format = FORMAT_NATIVE;
    bitmap->data = (unsigned char *)surface;
    return true;
}
static void tick(void)
{
    if (center_index != previous_center)
    {
        long elapsed = current_tick - moved_at;
        int distance = center_index - previous_center;
        travel = distance > 0 ? 1 : -1;
        flicking = elapsed > 0 && abs(distance) * HZ >= 3 * elapsed;
        previous_center = center_index;
        moved_at = current_tick;
    }
    else if (!TIME_BEFORE(current_tick, moved_at + HZ / 3))
        flicking = false;
    bool fast = flicking;
    int window[9];
    int n = cider_window(center_index, travel, window);
    const char *visible[9];
    char visible_keys[9][65];
    unsigned int visible_count = 0;
    for (int i = 0; i < n; i++)
    {
        struct cider_row row;
        if (window[i] >= count() || !cider_page_row(window[i], &row))
            continue;
        snprintf(visible_keys[visible_count], sizeof visible_keys[0], "%s",
                 row.art);
        visible[visible_count] = visible_keys[visible_count];
        visible_count++;
    }
    if (visible_count)
        cider_warm_art(visible, MIN(visible_count, 7), fast ? "XS" : "M");
    if (visible_count)
        cider_retain_art(visible, visible_count, fast ? "XS" : "M");
    const char *xs[5];
    char keys[5][65];
    unsigned int missing = 0;
    for (int i = 0; i < n; i++)
    {
        struct cider_row row;
        if (window[i] >= count() || !cider_page_row(window[i], &row))
            continue;
        if (fast)
        {
            if (missing < 5 && !cider_has_art(row.art, "XS"))
            {
                snprintf(keys[missing], sizeof keys[0], "%s", row.art);
                xs[missing] = keys[missing];
                missing++;
            }
        }
        else if (!cider_has_art(row.art, "M"))
        {
            cider_prefetch_art(row.art, "M");
            break;
        }
    }
    if (missing)
        cider_request_xs(xs, missing);
    unsigned int next = cider_art_revision();
    if (revision != next)
    {
        revision = next;
        carousel_provider_changed();
    }
}
static void text(void)
{
    struct cider_row row;
    if (!cider_page_row(center_index, &row))
    {
        snprintf(row.title, sizeof row.title, "...");
        row.subtitle[0] = 0;
        row.year = 0;
    }
    carousel_album_caption(row.title, row.subtitle, row.year, false, revision);
}
static unsigned int version(int index)
{
    struct cider_row row;
    if (!cider_page_row(index, &row))
        return 0;
    uint32_t hash = 2166136261u;
    for (const char *p = row.id; *p; p++)
        hash = (hash ^ (unsigned char)*p) * 16777619u;
    for (const char *p = row.art; *p; p++)
        hash = (hash ^ (unsigned char)*p) * 16777619u;
    return (hash << 2) | (cider_has_art(row.art, "M")    ? 3
                          : cider_has_art(row.art, "XS") ? 2
                                                         : 1);
}
static const struct carousel_model model = {
    .build_index = build,
    .count = count,
    .art_key = key,
    .enter = enter,
    .jump_prev = jump,
    .jump_next = jump,
    .draw_text = text,
    .sort_next = noop,
    .sort_prev = noop,
    .set_initial = initial,
    .title = "Album Covers",
    .external = true,
    .read_art = art,
    .art_version = version,
    .tick = tick,
};
int cider_covers(void)
{
    cider_page_open("albums");
    long deadline = current_tick + 5 * HZ;
    while (cider_page_total() < 0 && TIME_BEFORE(current_tick, deadline))
    {
        int action = get_action(CONTEXT_STD, HZ / 10);
        if (action == ACTION_STD_CANCEL ||
            agents_nav_pending() != AGENTS_NAV_NONE)
            return GO_TO_PREVIOUS;
    }
    if (cider_page_total() <= 0)
    {
        splash(HZ, ID2P(LANG_CAROUSEL_NO_ALBUMS));
        return GO_TO_PREVIOUS;
    }
    revision = cider_art_revision();
    int ret = carousel_run(&model, NULL);
    cider_cancel_art();
    return ret == CAROUSEL_REMOTE_RETURN ? CIDER_COVERS_REMOTE_RETURN : ret;
}
