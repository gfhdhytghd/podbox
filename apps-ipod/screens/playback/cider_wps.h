/* Remote source adapter for the system WPS. GPL version 2+. */
#ifndef CIDER_WPS_H
#define CIDER_WPS_H
#include <stdbool.h>
#include "config.h"
struct bitmap;
struct mp3entry;
struct wps_state;
#define CIDER_ART_HANDLE 0x7ffffffe
#ifdef HAVE_USB_PODLINK
void cider_wps_update(struct wps_state *state);
bool cider_wps_action(int action);
int wps_art_handle(int slot);
bool wps_art_bitmap(int handle, struct bitmap **bitmap);
bool wps_art_pin(int handle, bool pin);
int cider_wps_volume(bool *muted);
#else
#include "audio/playback.h"
#include "audio/buffering.h"
static inline void cider_wps_update(struct wps_state *s)
{
    (void)s;
}
static inline bool cider_wps_action(int action)
{
    (void)action;
    return false;
}
static inline int wps_art_handle(int slot)
{
    return playback_current_aa_hid(slot);
}
static inline bool wps_art_bitmap(int h, struct bitmap **b)
{
    return bufgetdata(h, 0, (void *)b) > 0;
}
static inline bool wps_art_pin(int h, bool pin)
{
    return buf_pin_handle(h, pin);
}
static inline int cider_wps_volume(bool *muted)
{
    if (muted)
        *muted = false;
    return 0;
}
#endif
#endif
