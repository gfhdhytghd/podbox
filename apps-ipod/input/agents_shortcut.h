/* Native SELECT+PLAY chord. Copyright (C) 2026. GPL version 2+. */
#ifndef AGENTS_SHORTCUT_H
#define AGENTS_SHORTCUT_H
#include <stdbool.h>
#include "config.h"
#include "input/agents_remote.h"
#define AGENTS_SHORTCUT_BUTTON (BUTTON_REDRAW | BUTTON_SELECT | BUTTON_PLAY)
#ifdef HAVE_USB_PODLINK
int agents_shortcut_get_button(int timeout);
void root_menu_agents_shortcut(void);
void root_menu_wps_shortcut(void);
void root_menu_cover_shortcut(void);
#else
#define agents_shortcut_get_button button_get_w_tmo
#endif
struct agents_shortcut_state {
    bool latched, ignored, select_held;
};
struct agents_cover_chord_state {
    bool latched;
    unsigned int remaining;
};
/* SELECT+MENU owns its down/repeat/releases until both keys are released.
 * This does not and cannot inhibit the hardware reset/DFU chord. */
int agents_cover_chord_button(struct agents_cover_chord_state *s, int button,
                              int held, bool enabled, bool *fire);
/* A fire result consumes the entire chord until both keys are released. */
int agents_shortcut_button(struct agents_shortcut_state *s, int button,
                           int held, bool talking, bool *fire);
void agents_shortcut_talking(bool talking);
#endif
