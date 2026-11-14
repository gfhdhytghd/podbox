/* Native WPS source data, no layout or input loop. GPL version 2+. */
#include "config.h"
#include "cider.h"
#include "cider_wps.h"
#include "wps.h"
#include "input/action.h"
#include "metadata.h"
#include "audio/playback.h"
#include "audio/buffering.h"
#include "skin/skin_albumart_color.h"
#include "skin/skin_engine.h"
#include "kernel.h"
#include "lcd.h"
#include "settings/settings.h"
#include "system/activity.h"
#include <string.h>
#include <stdio.h>
static struct mp3entry id3;
static struct cider_state source;
static fb_data pixels[240 * 240];
static struct bitmap bitmap;
static char key[65];
static bool have_art;

_Static_assert(sizeof id3 + sizeof source + sizeof pixels + sizeof bitmap + sizeof key + 64 < 120 * 1024, "Cider module RAM budget");

void cider_wps_update(struct wps_state *state)
{
    if (!cider_active())
        return;
    int old_volume = source.volume;
    bool old_mute = source.muted;
    cider_snapshot(&source);
    if (source.volume != old_volume || source.muted != old_mute)
        global_status.last_volume_change = current_tick;
    if (strcmp(key, source.art))
    {
        snprintf(key, sizeof key, "%s", source.art);
        have_art = false;

        skin_request_full_update(WPS);
    }
    if (!have_art && get_current_activity() == ACTIVITY_WPS)
        cider_request_art(key, "L");
    if (!have_art && cider_copy_art(key, "L", pixels, sizeof pixels))
    {
        have_art = true;
        bitmap.width = bitmap.height = 240;
        bitmap.format = FORMAT_NATIVE;
        bitmap.data = (unsigned char *)pixels;
        skin_albumart_art_opened(CIDER_ART_HANDLE);
        skin_request_full_update(WPS);
    }
    if (have_art && get_current_activity() == ACTIVITY_WPS)
    {
        const char *neighbors[] = {source.art, source.previous_art, source.next_art};
        cider_warm_art(neighbors, ARRAYLEN(neighbors), "L");
        cider_retain_art(neighbors, ARRAYLEN(neighbors), "L");
        if (!cider_has_art(source.previous_art, "L"))
            cider_prefetch_art(source.previous_art, "L");
        if (!cider_has_art(source.next_art, "L"))
            cider_prefetch_art(source.next_art, "L");
    }
    id3.title = source.title;
    id3.artist = source.artist;
    id3.albumartist = source.artist;
    id3.album = source.album;
    id3.composer = source.composer;
    id3.length = source.duration;
    id3.elapsed = cider_position(&source, current_tick, HZ);
    snprintf(id3.path, sizeof id3.path, "cider/%s", source.id);
    state->id3 = &id3;
    state->nid3 = NULL;
    state->paused = !source.available || !source.rate;
}
bool cider_wps_action(int action)
{
    if (!cider_active())
        return false;
    if (cider_available())
        switch (action)
        {
            case ACTION_WPS_PLAY:
                cider_control("toggle", 0);
                break;
            case ACTION_WPS_SKIPNEXT:
                cider_control("next", 0);
                break;
            case ACTION_WPS_SKIPPREV:
                cider_control("previous", 0);
                break;
            case ACTION_WPS_BROWSE:
                /* A-B repeat: like Rockbox's single marker key, SELECT sets
                 * A, then B, then clears; the host holds the markers. */
                if (cider_mode_get(CIDER_MODE_REPEAT) == CIDER_REPEAT_AB)
                    cider_control("ab-mark",
                                  cider_position(&source, current_tick, HZ));
                break;
            case ACTION_WPS_VOLUP:
                cider_control("volume-step", 1);
                break;
            case ACTION_WPS_VOLDOWN:
                cider_control("volume-step", -1);
                break;
        }
    switch (action)
    {
        case ACTION_WPS_PLAY:
        case ACTION_WPS_SKIPNEXT:
        case ACTION_WPS_SKIPPREV:
        case ACTION_WPS_VOLUP:
        case ACTION_WPS_VOLDOWN:
            return true;
        case ACTION_WPS_SEEKFWD:
        case ACTION_WPS_SEEKBACK:
            return !cider_available();
        case ACTION_WPS_MENU:
        case ACTION_WPS_CONTEXT:     /* Cider shuffle/repeat page, wps.c */
        case ACTION_WPS_QUICKSCREEN: /* Cider quick screen, wps.c */
        case ACTION_WPS_STOP:
        case ACTION_AGENTS_COVERS:
        case ACTION_AGENTS_REMOTE_COVERS:
        case ACTION_AGENTS_SWITCH:
        case ACTION_AGENTS_REMOTE_SWITCH:
        case ACTION_REDRAW:
        case ACTION_WPS_ID3SCREEN:
        case ACTION_NONE:
            return false;
        default:
            return true; /* local file/queue actions have no remote meaning */
    }
}
int wps_art_handle(int slot)
{
    if (cider_active())
        return have_art ? CIDER_ART_HANDLE : -1;
    return playback_current_aa_hid(slot);
}
bool wps_art_bitmap(int handle, struct bitmap **out)
{
    if (handle == CIDER_ART_HANDLE)
    {
        if (!have_art)
            return false;
        *out = &bitmap;
        return true;
    }
    return bufgetdata(handle, 0, (void *)out) > 0;
}
bool wps_art_pin(int handle, bool pin)
{
    return handle == CIDER_ART_HANDLE ? have_art : buf_pin_handle(handle, pin);
}
int cider_wps_volume(bool *muted)
{
    struct cider_status s;
    cider_status(&s);
    if (muted)
        *muted = s.muted;
    return s.volume;
}
