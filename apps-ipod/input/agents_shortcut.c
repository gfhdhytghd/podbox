/* Native SELECT+PLAY chord. Copyright (C) 2026. GPL version 2+. */
#include "config.h"
#include "button.h"
#include "kernel.h"
#include "button_chord.h"
#include "input/agents_shortcut.h"

volatile bool button_agents_chord_latched;

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
