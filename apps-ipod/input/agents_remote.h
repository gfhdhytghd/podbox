/* Dock remote input. Copyright (C) 2026. GPL version 2+. */
#ifndef AGENTS_REMOTE_H
#define AGENTS_REMOTE_H
#include <stdbool.h>
#define REMOTE_WPS_BUTTON (BUTTON_REDRAW | 0x100)
#define REMOTE_SWITCH_BUTTON (BUTTON_REDRAW | BUTTON_SELECT | BUTTON_PLAY)
#define REMOTE_COVERS_BUTTON (BUTTON_REDRAW | 0x200)
#define REMOTE_TAP_WINDOW_TICKS ((HZ + 4) / 5)
enum agents_remote_context {
    REMOTE_OTHER, REMOTE_AGENTS, REMOTE_WPS, REMOTE_COVERS, REMOTE_ALBUM_LIST
};
struct agents_remote_hold {
    bool down, fired;
    unsigned long at;
};
struct agents_remote_state {
    struct agents_remote_hold hold[4];
    /* A pending tap, a newly classified hold, release and native key. */
    int replay[6];
    unsigned int queued;
    unsigned int taps;
    bool tap_pending, second;
    unsigned long released;
    int play_context;
};
bool agents_remote_pending(const struct agents_remote_state *s);
int agents_remote_button(struct agents_remote_state *s, int context,
                         int button, int held, unsigned long now);
#endif
