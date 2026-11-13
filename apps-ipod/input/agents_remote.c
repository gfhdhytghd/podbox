/* Dock remote input for Agents. Copyright (C) 2026. GPL version 2+. */
#include "config.h"
#include "button.h"
#include "kernel.h"
#include "input/agents_remote.h"

int agents_remote_button(struct agents_remote_state *s, bool active,
                         int button, int held, unsigned long now)
{
    if (!active)
    {
        s->menu_down = s->menu_long = false;
        return button;
    }

    bool menu = (button & BUTTON_REMOTE) == BUTTON_RC_MENU;
    bool release = menu && (button & BUTTON_REL);
    if (menu && !release && !s->menu_down)
    {
        s->menu_down = true;
        s->menu_long = false;
        s->menu_at = now;
    }
    if (s->menu_down && (menu || button == BUTTON_NONE))
    {
        bool ended = release || !(held & BUTTON_RC_MENU);
        bool fire = !s->menu_long && now - s->menu_at >= (3 * HZ + 4) / 5;
        if (fire)
            s->menu_long = true;
        bool tap = ended && !s->menu_long;
        if (ended)
            s->menu_down = false;
        if (fire)
            return BUTTON_RC_SELECT;
        if (tap)
            return BUTTON_RC_MENU | BUTTON_REL;
    }
    if (menu)
        return BUTTON_NONE;

    int rc = button & BUTTON_REMOTE;
    int local = 0;
    if (rc & BUTTON_RC_LEFT) local |= BUTTON_LEFT;
    if (rc & BUTTON_RC_RIGHT) local |= BUTTON_RIGHT;
    if (rc & BUTTON_RC_PLAY) local |= BUTTON_PLAY;
    if (local)
        return (button & ~(BUTTON_RC_LEFT | BUTTON_RC_RIGHT | BUTTON_RC_PLAY))
               | local;
    return button;
}
