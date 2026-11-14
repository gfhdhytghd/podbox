/* Dock tap/hold input. Copyright (C) 2026. GPL version 2+. */
#include "config.h"
#include "button.h"
#include "kernel.h"
#include "input/agents_remote.h"
#include "remote_log.h"

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

static void play_tap(struct agents_remote_state *s, unsigned long now)
{
    remote_log_event(REMOTE_LOG_TAP, now, s->play_context, 0, NULL, 0);
    /* Agents already discarded the short recording on physical release. */
    if (s->play_context == REMOTE_AGENTS || s->play_context == REMOTE_COVERS ||
        s->play_context == REMOTE_ALBUM_LIST)
    {
        emit(s, BUTTON_SELECT);
        emit(s, BUTTON_SELECT | BUTTON_REL);
    }
    else
    {
        emit(s, BUTTON_RC_PLAY);
        emit(s, BUTTON_RC_PLAY | BUTTON_REL);
    }
    s->tap_pending = false;
    s->taps = 0;
}

static void play_taps(struct agents_remote_state *s, unsigned long now)
{
    if (s->taps == 2)
    {
        remote_log_event(REMOTE_LOG_DOUBLE, now, s->play_context, 0, NULL, 0);
        emit(s, REMOTE_SWITCH_BUTTON);
        s->tap_pending = false;
        s->taps = 0;
    }
    else play_tap(s, now);
}

static bool play_button(struct agents_remote_state *s, int context, int button,
                        int held, unsigned long now)
{
    struct agents_remote_hold *h = &s->hold[3];
    bool event = (button & BUTTON_REMOTE) == BUTTON_RC_PLAY;
    bool release = event && (button & BUTTON_REL);
    unsigned long window = REMOTE_TAP_WINDOW_TICKS;
    unsigned long threshold = context == REMOTE_AGENTS ? (3 * HZ + 9) / 10 :
                                                       (3 * HZ + 4) / 5;
    if (context != s->play_context)
    {
        s->tap_pending = s->second = false;
        s->taps = 0;
        if (h->down) h->fired = true;
    }
    if (s->tap_pending && !s->second && now - s->released > window)
        play_taps(s, now);
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
            if (s->tap_pending) play_taps(s, now);
            remote_log_event(REMOTE_LOG_HOLD, now, context, 0, NULL, 0);
            s->second = false;
            h->fired = true;
            if (context == REMOTE_OTHER || context == REMOTE_COVERS ||
                context == REMOTE_ALBUM_LIST)
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
                if (s->second && s->taps == 2)
                {
                    remote_log_event(REMOTE_LOG_TRIPLE, now, context, 0,
                                     NULL, 0);
                    s->tap_pending = s->second = false;
                    s->taps = 0;
                    emit(s, REMOTE_COVERS_BUTTON);
                }
                else
                {
                    s->taps = s->second ? 2 : 1;
                    s->second = false;
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
    if (s->queued) return take(s);
    if (play_button(s, context, button, held, now)) return take(s);
    /* Menu/Select retain their native press/repeat/release meanings. Direction
     * keys retain their normal press/repeat/release meanings everywhere. */
    if (context == REMOTE_AGENTS &&
        ((button & BUTTON_REMOTE) == BUTTON_RC_LEFT ||
         (button & BUTTON_REMOTE) == BUTTON_RC_RIGHT))
        button = (button & (BUTTON_REL | BUTTON_REPEAT)) |
                 ((button & BUTTON_REMOTE) == BUTTON_RC_LEFT ?
                  BUTTON_LEFT : BUTTON_RIGHT);
    if (button != BUTTON_NONE) emit(s, button);
    return take(s);
}
