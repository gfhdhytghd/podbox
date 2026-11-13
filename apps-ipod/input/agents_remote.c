/* Dock tap/hold input. Copyright (C) 2026. GPL version 2+. */
#include "config.h"
#include "button.h"
#include "kernel.h"
#include "input/agents_remote.h"

bool agents_remote_pending(const struct agents_remote_state *s)
{
    return s->queued || s->tap_pending || s->hold[0].down || s->hold[1].down ||
           s->hold[2].down || s->hold[3].down;
}

static void emit(struct agents_remote_state *s, int button)
{
    s->replay[s->queued++] = button;
}

static int take(struct agents_remote_state *s)
{
    if (!s->queued) return BUTTON_NONE;
    int button = s->replay[0];
    for (unsigned int i = 1; i < s->queued; i++)
        s->replay[i - 1] = s->replay[i];
    s->queued--;
    return button;
}

static void play_tap(struct agents_remote_state *s)
{
    /* Agents already discarded the short recording on physical release. */
    if (s->play_context != REMOTE_AGENTS)
    {
        emit(s, BUTTON_RC_PLAY);
        emit(s, BUTTON_RC_PLAY | BUTTON_REL);
    }
    s->tap_pending = false;
}

static bool play_button(struct agents_remote_state *s, int context, int button,
                        int held, unsigned long now)
{
    struct agents_remote_hold *h = &s->hold[3];
    bool event = (button & BUTTON_REMOTE) == BUTTON_RC_PLAY;
    bool release = event && (button & BUTTON_REL);
    unsigned long window = (HZ + 4) / 5;
    unsigned long threshold = context == REMOTE_AGENTS ? (3 * HZ + 9) / 10 :
                                                       (3 * HZ + 4) / 5;
    if (context != s->play_context)
    {
        s->tap_pending = s->second = false;
        if (h->down) h->fired = true;
    }
    if (s->tap_pending && !s->second && now - s->released > window)
        play_tap(s);
    if (!h->down && event && !release)
    {
        h->down = true;
        h->fired = false;
        h->at = now;
        s->second = s->tap_pending;
        s->play_context = context;
        if (context == REMOTE_AGENTS)
            emit(s, BUTTON_RC_PLAY);
    }
    if (h->down && (event || button == BUTTON_NONE))
    {
        bool ended = release || !(held & BUTTON_RC_PLAY);
        if (!h->fired && now - h->at >= threshold)
        {
            if (s->tap_pending) play_tap(s);
            s->second = false;
            h->fired = true;
            if (context == REMOTE_OTHER)
                emit(s, REMOTE_WPS_BUTTON);
            else if (context == REMOTE_WPS)
            {
                emit(s, BUTTON_RC_PLAY);
                emit(s, BUTTON_RC_PLAY | BUTTON_REPEAT);
            }
        }
        if (ended)
        {
            h->down = false;
            if (!h->fired)
            {
                if (context == REMOTE_AGENTS)
                    emit(s, BUTTON_RC_PLAY | BUTTON_REL | BUTTON_REPEAT);
                if (s->second)
                {
                    s->tap_pending = s->second = false;
                    emit(s, REMOTE_SWITCH_BUTTON);
                }
                else
                {
                    s->tap_pending = true;
                    s->released = now;
                }
            }
            else if (context == s->play_context)
            {
                if (context == REMOTE_AGENTS)
                    emit(s, BUTTON_PLAY | BUTTON_REL);
                else if (context == REMOTE_WPS)
                    emit(s, BUTTON_RC_PLAY | BUTTON_REL);
            }
        }
    }
    if (event && (button & BUTTON_MAIN))
        emit(s, (button & BUTTON_MAIN) |
                (button & (BUTTON_REL | BUTTON_REPEAT)));
    return event;
}

int agents_remote_button(struct agents_remote_state *s, int context,
                         int button, int held, unsigned long now)
{
    static const int keys[] = {
        BUTTON_RC_MENU, BUTTON_RC_RIGHT, BUTTON_RC_LEFT
    };
    if (s->queued) return take(s);
    if (play_button(s, context, button, held, now)) return take(s);
    for (unsigned int i = 0; i < sizeof(keys) / sizeof(keys[0]); i++)
    {
        struct agents_remote_hold *h = &s->hold[i];
        int key = keys[i];
        bool event = (button & BUTTON_REMOTE) == key;
        bool release = event && (button & BUTTON_REL);
        bool allowed = key != BUTTON_RC_MENU || context == REMOTE_AGENTS;
        if (!h->down && event && !release && allowed)
        {
            h->down = true;
            h->fired = false;
            h->at = now;
        }
        if (h->down && (event || button == BUTTON_NONE))
        {
            bool ended = release || !(held & key);
            bool fire = !h->fired && now - h->at >= (3 * HZ + 4) / 5;
            bool tap = ended && !h->fired && !fire;
            if (ended) h->down = false;
            if (fire || tap)
            {
                h->fired = true;
                int result = fire ? (key == BUTTON_RC_LEFT ? BUTTON_MENU :
                                                            BUTTON_SELECT) :
                             context == REMOTE_AGENTS ?
                             (key == BUTTON_RC_MENU ? BUTTON_MENU :
                              key == BUTTON_RC_LEFT ? BUTTON_LEFT : BUTTON_RIGHT) :
                             key;
                emit(s, result);
                emit(s, result | BUTTON_REL);
            }
            return take(s);
        }
        if (event && allowed) return take(s);
    }
    if (button != BUTTON_NONE) emit(s, button);
    return take(s);
}
