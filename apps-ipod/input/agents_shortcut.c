/* Native SELECT+PLAY chord. Copyright (C) 2026. GPL version 2+. */
#include "config.h"
#include "button.h"
#include "kernel.h"
#include "button_chord.h"
#include "input/agents_shortcut.h"

volatile bool button_agents_chord_latched;

static unsigned int nav_live;
static enum agents_nav_screen nav_pending;
static bool nav_remote;
void agents_nav_request(enum agents_nav_screen screen, bool remote)
{ nav_pending = screen; nav_remote = remote; }
enum agents_nav_screen agents_nav_pending(void) { return nav_pending; }
bool agents_nav_remote(void) { return nav_remote; }
bool agents_nav_active(void) { return nav_live != 0; }
bool agents_nav_has(enum agents_nav_screen screen)
{ return (nav_live & (1u << screen)) != 0; }
bool agents_nav_take(enum agents_nav_screen screen)
{
    if (nav_pending != screen) return false;
    nav_pending = AGENTS_NAV_NONE; nav_remote = false;
    return true;
}
bool agents_nav_enter(enum agents_nav_screen screen)
{
    if (agents_nav_has(screen))
    { agents_nav_request(screen, false); return false; }
    nav_live |= 1u << screen;
    agents_nav_take(screen);
    return true;
}
void agents_nav_leave(enum agents_nav_screen screen)
{ nav_live &= ~(1u << screen); }

int agents_shortcut_button(struct agents_shortcut_state *s, int button,
                           int held, bool talking, bool *fire)
{
    const int chord = BUTTON_SELECT | BUTTON_PLAY;
    *fire = false;
    /* System events keep their data and are never mistaken for buttons. */
    if (button & (SYS_EVENT | BUTTON_MULTIMEDIA)) return button;
    if (button == BUTTON_REDRAW) return button;
    if (button & BUTTON_REMOTE) return button;
    if (s->latched || s->ignored)
    {
        bool blocked = (button & chord) != 0;
        bool ignored = s->ignored;
        if (!(held & chord)) s->latched = s->ignored = false;
        if (ignored && (button & BUTTON_PLAY))
            return button & ~BUTTON_SELECT;
        return blocked ? BUTTON_NONE : button;
    }
    int pressed = (button & BUTTON_REL) ? 0 : button & BUTTON_MAIN;
    if (!(held & BUTTON_SELECT) && !(pressed & BUTTON_SELECT))
        s->select_held = false;
    if (!s->select_held &&
        ((held & chord) == chord || (pressed & chord) == chord))
    {
        if (talking)
        {
            s->ignored = true;
            /* PLAY release must still end a talk that already started. */
            return BUTTON_NONE;
        }
        s->latched = true;
        if (held & chord) button_chord_begin();
        *fire = true;
        return BUTTON_NONE;
    }
    if ((button & BUTTON_MAIN) == BUTTON_SELECT)
    {
        if (talking) return BUTTON_NONE;
        /* The native held event commits this gesture before PLAY can join. */
        if (button & BUTTON_REPEAT)
            s->select_held = true;
    }
    return button;
}

int agents_cover_chord_button(struct agents_cover_chord_state *s, int button,
                              int held, bool enabled, bool *fire)
{
    const unsigned int chord = BUTTON_SELECT | BUTTON_MENU;
    *fire = false;
    if (button & (SYS_EVENT | BUTTON_MULTIMEDIA | BUTTON_REDRAW | BUTTON_REMOTE))
        return button;
    if (s->latched)
    {
        bool blocked = (button & chord) != 0;
        if (button & BUTTON_REL) s->remaining &= ~(button & chord);
        else s->remaining |= button & chord;
        /* Latest sampled held may already be zero while releases remain
         * queued. Consume both releases before un-latching. */
        if (!s->remaining && !(held & chord)) s->latched = false;
        return blocked ? BUTTON_NONE : button;
    }
    unsigned int pressed = button & BUTTON_REL ? 0 : button & BUTTON_MAIN;
    /* Preserve SELECT+PLAY precedence for a three-key combination. */
    if (enabled && !((held | pressed) & BUTTON_PLAY) &&
        (((unsigned int)held & chord) == chord || (pressed & chord) == chord))
    {
        s->latched = true;
        s->remaining = chord;
        *fire = true;
        return BUTTON_NONE;
    }
    return button;
}
