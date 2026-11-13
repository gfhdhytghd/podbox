/* Dock tap/hold input. Copyright (C) 2026. GPL version 2+. */
#include "config.h"
#include "button.h"
#include "kernel.h"
#include "input/agents_remote.h"

bool agents_remote_pending(const struct agents_remote_state *s)
{
    return s->replay || s->hold[0].down || s->hold[1].down ||
           s->hold[2].down || s->hold[3].down;
}

int agents_remote_button(struct agents_remote_state *s, int context,
                         int button, int held, unsigned long now)
{
    static const int keys[] = {
        BUTTON_RC_MENU, BUTTON_RC_RIGHT, BUTTON_RC_LEFT, BUTTON_RC_PLAY
    };
    if (s->replay)
    {
        int replay = s->replay;
        s->replay = 0;
        return replay;
    }
    for (unsigned int i = 0; i < sizeof(keys) / sizeof(keys[0]); i++)
    {
        struct agents_remote_hold *h = &s->hold[i];
        int key = keys[i];
        bool event = (button & BUTTON_REMOTE) == key;
        bool release = event && (button & BUTTON_REL);
        bool allowed = key == BUTTON_RC_MENU ? context == REMOTE_AGENTS :
                       key == BUTTON_RC_PLAY ? context == REMOTE_OTHER : true;
        if (!h->down && event && !release && allowed)
        {
            h->down = true;
            h->fired = false;
            h->at = now;
        }
        /* A held Play used to open WPS must not become WPS's held Stop. */
        if (h->down && key == BUTTON_RC_PLAY && context != REMOTE_OTHER)
            h->fired = true;
        if (h->down && (event || button == BUTTON_NONE))
        {
            bool ended = release || !(held & key);
            bool fire = !h->fired && now - h->at >= (3 * HZ + 4) / 5;
            bool tap = ended && !h->fired && !fire;
            if (ended) h->down = false;
            if (fire || tap)
            {
                h->fired = true;
                if (fire && key == BUTTON_RC_PLAY)
                    return REMOTE_WPS_BUTTON;
                int result = fire ? (key == BUTTON_RC_LEFT ? BUTTON_MENU :
                                                            BUTTON_SELECT) :
                             context == REMOTE_AGENTS ?
                             (key == BUTTON_RC_MENU ? BUTTON_MENU :
                              key == BUTTON_RC_LEFT ? BUTTON_LEFT : BUTTON_RIGHT) :
                             key;
                s->replay = result | BUTTON_REL;
                return result;
            }
            return BUTTON_NONE;
        }
        if (event && allowed) return BUTTON_NONE;
    }
    if (context == REMOTE_AGENTS && (button & BUTTON_RC_PLAY))
        return (button & ~BUTTON_RC_PLAY) | BUTTON_PLAY;
    return button;
}
