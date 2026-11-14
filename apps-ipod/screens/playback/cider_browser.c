/* Cider Remote through the native themed database list and search dialog.
 * Copyright (C) 2026. GNU General Public License version 2 or later. */
#include "config.h"
#include "audio.h"
#include "cider.h"
#include "cider_library.h"
#include "input/action.h"
#include "input/agents_shortcut.h"
#include "widgets/list.h"
#include "widgets/search_dialog.h"
#include "widgets/keyboard.h"
#include "widgets/splash.h"
#include "rbunicode.h"
#include "screens/browse/browser.h"
#include "screens/browse/browser_db.h"
#include "system/activity.h"
#include "settings/settings.h"
#include "draw/img_filter.h"
#include "speech/talk.h"
#include "root_menu.h"
#include "lang.h"
#include "kernel.h"
#include "misc.h"
#include "usb.h"
#include <stdio.h>
#include <string.h>
static struct
{
    char route[380], title[124];
    int selected;
    struct browser_db_header header;
    char art[65];
} history[12];
static int depth;
static bool album_pending, show_covers, covers_home, covers_agents, remote_return;
static int cover_album_depth = -1;
static struct cider_row selected_row;
static struct cider_row root_rows[35];
static int root_count;
static bool empty_list;
static fb_data art_source[56 * 56], art_pixels[56 * 56];
static struct bitmap row_bitmap;
static struct cider_row callback_row;
static char query[SEARCH_MAX_QUERY + 1];
static int search_count = -1;

_Static_assert(sizeof history + sizeof root_rows + sizeof art_source +
                       sizeof art_pixels + sizeof selected_row +
                       sizeof callback_row + sizeof row_bitmap + sizeof query +
                       128 <
                   56 * 1024,
               "Cider module RAM budget");

static void escape(char *out, unsigned int size, const char *s)
{
    unsigned int n = 0;
    for (; *s && n + 4 < size; s++)
    {
        unsigned char c = *s;
        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
            (c >= '0' && c <= '9') || c == '-' || c == '_')
            out[n++] = c;
        else
        {
            snprintf(out + n, size - n, "%%%02X", c);
            n += 3;
        }
    }
    out[n] = 0;
}
static void root(void)
{
    root_count = 0;
    struct cider_row *r = &root_rows[root_count++];
    memset(r, 0, sizeof *r);
    snprintf(r->title, sizeof r->title, "%s", str(LANG_ALBUM_COVERS));
    snprintf(r->route, sizeof r->route, "covers");
    r = &root_rows[root_count++];
    memset(r, 0, sizeof *r);
    snprintf(r->title, sizeof r->title, "%s", str(LANG_PLAYLISTS));
    snprintf(r->route, sizeof r->route, "playlists");
    r = &root_rows[root_count++];
    memset(r, 0, sizeof *r);
    snprintf(r->title, sizeof r->title, "%s",
             str(cider_active() || audio_status() ? LANG_NOW_PLAYING
                                                  : LANG_RESUME_PLAYBACK));
    snprintf(r->route, sizeof r->route, "wps");
    for (int n = 0; n < browser_db_root_row_count() && root_count < 35; n++)
    {
        int index;
        const unsigned char *name;
        if (!browser_db_get_root_row(n, &index, &name))
            break;
        if (browser_db_root_row_is_hidden(index))
            continue;
        r = &root_rows[root_count++];
        memset(r, 0, sizeof *r);
        snprintf(r->title, sizeof r->title, "%s", P2STR(name));
        char escaped[270];
        escape(escaped, sizeof escaped, browser_db_root_row_key(index));
        snprintf(r->route, sizeof r->route, "root/%s", escaped);
    }
}
static bool get_row(int i, struct cider_row *out)
{
    if (!depth)
    {
        if (!cider_available() || i < 0 || i >= root_count)
            return false;
        *out = root_rows[i];
        return true;
    }
    return cider_page_row(i, out);
}
static const char *name(int i, void *data, char *buf, size_t size)
{
    (void)data;
    if (empty_list)
        return str(LANG_NO_FILES);
    if (get_row(i, &callback_row))
        snprintf(buf, size, "%s", callback_row.title);
    else
        snprintf(buf, size, "...");
    return buf;
}
static enum themable_icons icon(int i, void *data)
{
    (void)data;
    if (!get_row(i, &callback_row))
        return Icon_NOICON;
    if (!strcmp(callback_row.route, "wps"))
        return Icon_Playback_menu;
    return callback_row.kind == CIDER_ROW_SONG       ? Icon_Audio
           : callback_row.kind == CIDER_ROW_PLAYLIST ? Icon_Playlist
                                                     : Icon_Folder;
}
static enum list_row_kind kind(int i, void *data)
{
    (void)data;
    if (!get_row(i, &callback_row))
        return LIST_ROW_PLAIN;
    return callback_row.kind == CIDER_ROW_SONG ? LIST_ROW_TRACK
                                               : LIST_ROW_CONTAINER;
}
static const struct bitmap *bitmap_for(const char *key, struct dim *size,
                                       const struct img_filter *filter)
{
    if (!cider_copy_art(key, "S", art_source, sizeof art_source))
    {
        cider_prefetch_art(key, "S");
        return NULL;
    }
    int dim = MIN(56, MIN(size->width, size->height));
    if (dim <= 0)
        return NULL;
    for (int y = 0; y < dim; y++)
        for (int x = 0; x < dim; x++)
            art_pixels[y * dim + x] =
                art_source[y * 56 / dim * 56 + x * 56 / dim];
    if (filter)
        img_filter_apply(art_pixels, dim, dim, filter);
    row_bitmap.width = row_bitmap.height = dim;
    row_bitmap.format = FORMAT_NATIVE;
    row_bitmap.data = (unsigned char *)art_pixels;
    return &row_bitmap;
}
static const struct bitmap *art(int i, void *data, struct dim *size,
                                const struct img_filter *filter, uint32_t hash)
{
    (void)data;
    (void)hash;
    if (!get_row(i, &callback_row) || callback_row.kind != CIDER_ROW_ALBUM)
        return NULL;
    return bitmap_for(callback_row.art, size, filter);
}
static const struct bitmap *header_art(struct dim *size)
{
    return bitmap_for(history[depth].art, size, NULL);
}
static int search_scan(const char *text, void *ctx)
{
    (void)ctx;
    char value[260], route[380];
    escape(value, sizeof value, text);
    snprintf(route, sizeof route, "search?q=%s", value);
    cider_page_open(route);
    int n = cider_page_total();
    return n < 0 ? 0 : MIN(n, 50);
}
static bool search_ready(void *ctx)
{
    (void)ctx;
    int count = cider_page_total();
    if (count == search_count)
        return false;
    search_count = count;
    return count >= 0;
}
static const char *search_text(int i, void *ctx)
{
    (void)ctx;
    if (cider_page_row(i, &callback_row))
        return callback_row.title;
    return "...";
}
static bool search(struct cider_row *out)
{
    const struct search_provider provider = {
        .title = "Search",
        .activity = ACTIVITY_DB_SEARCH,
        .scan = search_scan,
        .row_text = search_text,
        .refresh = search_ready,
    };
    int result = search_dialog_run(&provider, NULL, query, sizeof query);
    return result >= 0 && cider_page_row(result, out);
}
void cider_browse_album(const struct cider_row *row)
{
    selected_row = *row;
    album_pending = true;
}
static void enter(const struct cider_row *r)
{
    if (depth + 1 >= (int)ARRAYLEN(history))
        return;
    depth++;
    snprintf(history[depth].route, sizeof history[depth].route, "%s", r->route);
    snprintf(history[depth].title, sizeof history[depth].title, "%s", r->title);
    history[depth].selected = 0;
    memset(&history[depth].header, 0, sizeof history[depth].header);
    if (r->kind == CIDER_ROW_ALBUM)
    {
        snprintf(history[depth].header.name, sizeof history[depth].header.name,
                 "%s", r->title);
        snprintf(history[depth].header.artist,
                 sizeof history[depth].header.artist, "%s", r->subtitle);
        history[depth].header.tracks = r->count;
        history[depth].header.year = r->year;
        history[depth].header.length_ms = r->duration;
        snprintf(history[depth].art, sizeof history[depth].art, "%s", r->art);
    }
    cider_cancel_art();
    cider_page_open(history[depth].route);
}
int cider_browse_covers(bool home)
{
    show_covers = true;
    covers_home = home;
    covers_agents = cover_nav_origin_is(GO_TO_AGENTS);
    return GO_TO_CIDER;
}
bool cider_browse_remote_return(void)
{
    bool remote = remote_return;
    remote_return = false;
    return remote;
}

int cider_browse(void)
{
    if (!agents_nav_enter(AGENTS_NAV_TRACKS))
        return GO_TO_PREVIOUS;
    push_current_activity(ACTIVITY_DATABASEBROWSER);
    struct gui_synclist list;
    gui_synclist_init(&list, name, NULL, false, 1, NULL);
    gui_synclist_set_icon_callback(&list, icon);
    gui_synclist_set_row_callbacks(&list, kind, NULL, NULL);
    root();
    cider_warm_art(NULL, 0, "S");
    agents_nav_take(AGENTS_NAV_TRACKS);
    if (depth)
        cider_page_open(history[depth].route);
    int result = GO_TO_PREVIOUS;
    remote_return = false;
    bool play_pending = false;
    for (;;)
    {
        if (play_pending)
        {
            int status = cider_play_status();
            if (status > 0) { result = GO_TO_WPS; break; }
            if (status < 0)
            {
                play_pending = false;
                splash(HZ, "Cider could not play the selected item");
            }
        }
        if (show_covers)
        {
            show_covers = false;
            gui_synclist_scroll_stop(&list);
            int ret = cider_covers();
            if (ret == CIDER_COVERS_REMOTE_RETURN)
            {
                if (covers_home || covers_agents)
                {
                    remote_return = covers_home;
                    result = covers_home ? GO_TO_WPS : GO_TO_AGENTS;
                    break;
                }
                ret = GO_TO_PREVIOUS;
            }
            if (album_pending)
            {
                album_pending = false;
                enter(&selected_row);
                cover_album_depth = depth;
            }
            else if (ret != GO_TO_PREVIOUS || covers_home || covers_agents)
            {
                result = ret != GO_TO_PREVIOUS ? ret :
                         covers_home ? GO_TO_ROOT : GO_TO_AGENTS;
                break;
            }
            else if (depth) cider_page_open(history[depth].route);
        }
        int count = !cider_available() ? 0
                    : depth            ? cider_page_total()
                                       : root_count;
        if (!play_pending && depth && count > 0 &&
            (!strcmp(history[depth].route, "random") ||
             !strcmp(history[depth].route, "shuffle")) &&
            cider_page_row(0, &selected_row) && cider_play(selected_row.route))
        {
            depth--;
            cider_page_open(history[depth].route);
            play_pending = true;
            continue;
        }
        bool albums = depth && (!strncmp(history[depth].route, "albums", 6) ||
                                !strcmp(history[depth].route, "root/Album"));
        bool header = depth && !strncmp(history[depth].route, "album/", 6);
        if (header && count >= 0)
            history[depth].header.tracks = count;
        browser_set_external_header(header ? &history[depth].header : NULL,
                                    header ? header_art : NULL);
        browser_set_showing_art(albums && global_settings.db_albumart);
        gui_synclist_set_albumart_callback(
            &list, albums && global_settings.db_albumart ? art : NULL);
        gui_synclist_set_row_height(&list,
                                    albums && global_settings.db_albumart
                                        ? global_settings.db_art_row_height
                                        : 0);
        char title[160];
        snprintf(title, sizeof title, "%s%s",
                 depth ? history[depth].title : "Cider Remote",
                 !cider_available()            ? " (unavailable)"
                 : depth && cider_page_stale() ? " *"
                                               : "");
        gui_synclist_set_title(&list, title, Icon_Audio);
        empty_list = count == 0;
        gui_synclist_set_nb_items(&list, empty_list ? 1 : MAX(0, count));
        gui_synclist_select_item(&list, history[depth].selected);
        if (albums && count > 0)
        {
            static char keys[7][65]; /* UI stack is tight */
            const char *window[7]; unsigned int n = 0;
            int first = MAX(0, history[depth].selected - 2);
            for (int i = first; i < MIN(count, first + 7); i++)
                if (cider_page_row(i, &callback_row))
                {
                    snprintf(keys[n], sizeof keys[n], "%s", callback_row.art);
                    window[n] = keys[n]; n++;
                }
            cider_warm_art(window, n, "S");
        }
        gui_synclist_draw(&list);
        int action;
        list_do_action(CONTEXT_ID3DB, HZ / 10, &list, &action);
        history[depth].selected = gui_synclist_get_sel_pos(&list);
        enum agents_nav_screen nav = agents_nav_pending();
        if (nav == AGENTS_NAV_AGENTS)
        {
            result = GO_TO_AGENTS;
            break;
        }
        if (nav == AGENTS_NAV_WPS)
        {
            result = GO_TO_WPS;
            break;
        }
        if (nav == AGENTS_NAV_COVERS)
        {
            result = GO_TO_ALBUM_COVERS;
            break;
        }
        if (nav == AGENTS_NAV_BACK)
        {
            agents_nav_take(nav);
            action = ACTION_STD_CANCEL;
        }
        if (action == ACTION_STD_CANCEL || action == ACTION_STD_MENU)
        {
            play_pending = false;
            if (!depth)
                break;
            if (depth == cover_album_depth)
            {
                cover_album_depth = -1;
                show_covers = true;
            }
            depth--;
            cider_page_open(history[depth].route);
        }
        else if (action == ACTION_TREE_WPS || action == ACTION_REMOTE_WPS)
        {
            result = GO_TO_WPS;
            break;
        }
        else if (action == ACTION_STD_OK && !play_pending && cider_available() && !empty_list &&
                 get_row(history[depth].selected, &selected_row))
        {
            if (!strcmp(selected_row.route, "wps"))
            {
                result = GO_TO_WPS;
                break;
            }
            else if (!strcmp(selected_row.route, "covers"))
            {
                show_covers = true;
                covers_home = covers_agents = false;
            }
            else if (!strcmp(selected_row.route, "root/Search"))
            {
                if (search(&selected_row))
                {
                    if (selected_row.kind == CIDER_ROW_SONG)
                    {
                        if (cider_play(selected_row.route))
                        {
                            play_pending = true;
                        }
                    }
                    else enter(&selected_row);
                }
                else if (depth)
                    cider_page_open(history[depth].route);
            }
            else if (!strncmp(selected_row.route, "play/", 5) ||
                     !strcmp(selected_row.route, "root/Random%20Album") ||
                     !strcmp(selected_row.route, "root/Shuffle%20Songs"))
            {
                if (!strncmp(selected_row.route, "play/", 5))
                {
                    if (cider_play(selected_row.route))
                    {
                        play_pending = true;
                    }
                }
                else
                {
                    snprintf(selected_row.route, sizeof selected_row.route,
                             "%s",
                             !strcmp(selected_row.route, "root/Random%20Album")
                                 ? "random"
                                 : "shuffle");
                    enter(&selected_row);
                }
            }
            else if (!strncmp(selected_row.route, "input?field=", 12))
            {
                char value[64] = "", encoded[270];
                char field[32];
                snprintf(field, sizeof field, "%s", selected_row.route + 12);
                if (dialog_input(value, sizeof value) == 0)
                {
                    if (!strcmp(field, "range") ||
                        !strcmp(field, "artistrange"))
                    {
                        char end[16] = "";
                        if (dialog_input(end, sizeof end) != 0)
                            continue;
                        char start[16];
                        snprintf(start, sizeof start, "%.15s", value);
                        snprintf(value, sizeof value, "%s-%s", start, end);
                    }
                    escape(encoded, sizeof encoded, value);
                    const char *target =
                        !strcmp(field, "title") || !strcmp(field, "rating")
                            ? "songs"
                        : !strcmp(field, "artist")      ? "artists"
                        : !strcmp(field, "albumartist") ? "albumartists"
                                                        : "albums";
                    if (!strcmp(field, "artistrange"))
                    {
                        snprintf(field, sizeof field, "range");
                        target = "albumartists";
                    }
                    bool contains = !strcmp(field,"album") ||
                                    !strcmp(field,"artist") ||
                                    !strcmp(field,"albumartist") ||
                                    !strcmp(field,"title");
                    snprintf(selected_row.route, sizeof selected_row.route,
                             "%s?%s%s=%s", target, field,
                             contains ? "~" : "", encoded);
                    enter(&selected_row);
                }
            }
            else
                enter(&selected_row);
        }
        else if (default_event_handler(action) == SYS_USB_CONNECTED)
        {
            result = GO_TO_ROOT;
            break;
        }
    }
    gui_synclist_scroll_stop(&list);
    browser_set_showing_art(false);
    browser_set_external_header(NULL, NULL);
    pop_current_activity();
    agents_nav_leave(AGENTS_NAV_TRACKS);
    return result;
}
