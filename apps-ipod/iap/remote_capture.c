/* Serial iAP capture and ContextButtonStatus decode. GPL version 2+. */
#include "config.h"
#include "button.h"
#include <string.h>
#include <stdio.h>
#include "iap/remote_capture.h"

struct remote_capture iap_remote_capture;
static struct remote_volume_trace volume_trace[REMOTE_VOLUME_HISTORY];
static unsigned long volume_sequence;

void remote_volume_log(unsigned long tick, unsigned char echo,
                       unsigned char target, int direction, char decision)
{
    unsigned long seq = ++volume_sequence;
    volume_trace[(seq - 1) % REMOTE_VOLUME_HISTORY] =
        (struct remote_volume_trace){seq, tick, echo, target,
                                     direction, decision};
}

unsigned long remote_volume_count(void)
{
    return volume_sequence;
}

bool remote_volume_get(unsigned long sequence, struct remote_volume_trace *out)
{
    if (!sequence || volume_sequence - sequence >= REMOTE_VOLUME_HISTORY)
        return false;
    *out = volume_trace[(sequence - 1) % REMOTE_VOLUME_HISTORY];
    return out->sequence == sequence;
}

void remote_capture_begin(struct remote_capture *r, unsigned int length)
{
    r->pending.length = length;
    r->pending.received = 0;
}

void remote_capture_byte(struct remote_capture *r, unsigned char byte)
{
    if (r->pending.received < REMOTE_FRAME_MAX)
        r->pending.data[r->pending.received] = byte;
    r->pending.received++;
}

void remote_capture_end(struct remote_capture *r, unsigned int flags,
                        unsigned long tick)
{
    struct remote_frame *p = &r->pending;
    p->sequence = r->frames + 1;
    p->tick = tick;
    p->delta = r->frames ? tick - r->previous_tick : 0;
    p->flags = flags | (p->received > REMOTE_FRAME_MAX ? REMOTE_TRUNC : 0);
    struct remote_frame *f = &r->history[r->frames % REMOTE_HISTORY];
    /* Copy only received bytes; neither ISR nor reader allocates memory. */
    memcpy(f, p, offsetof(struct remote_frame, data) + remote_frame_stored(p));
    r->previous_tick = tick;
    r->bad += (flags & REMOTE_BAD_MASK) != 0;
    r->frames++;
}

bool remote_capture_get(const struct remote_capture *r, unsigned long sequence,
                        struct remote_frame *out)
{
    unsigned long age = r->frames - sequence;
    if (!sequence || age >= REMOTE_HISTORY)
        return false;
    const struct remote_frame *f = &r->history[(sequence - 1) % REMOTE_HISTORY];
    if (f->sequence != sequence)
        return false;
    memcpy(out, f, offsetof(struct remote_frame, data) + remote_frame_stored(f));
    return true;
}

unsigned long remote_context_buttons(unsigned int len, const unsigned char *b)
{
    unsigned long keys = 0;
    if (len < 3 || b[0] != 2 || b[1] != 0)
        return 0;
    if (b[2] & 0x01) keys |= BUTTON_RC_PLAY;
    if (b[2] & 0x02) keys |= BUTTON_RC_VOL_UP;
    if (b[2] & 0x04) keys |= BUTTON_RC_VOL_DOWN;
    if (b[2] & 0x08) keys |= BUTTON_RC_RIGHT;
    if (b[2] & 0x10) keys |= BUTTON_RC_LEFT;
    if (b[2] & 0x20) keys |= BUTTON_RC_NEXT_ALBUM;
    if (b[2] & 0x40) keys |= BUTTON_RC_PREV_ALBUM;
    if (len > 3 && (b[3] & 0x20)) keys |= BUTTON_RC_NEXT_PLAYLIST;
    if (len > 3 && (b[3] & 0x40)) keys |= BUTTON_RC_PREV_PLAYLIST;
    if (len > 4)
    {
        if (b[4] & 0x10) keys |= BUTTON_RC_RIGHT;
        if (b[4] & 0x20) keys |= BUTTON_RC_LEFT;
        if (b[4] & 0x40) keys |= BUTTON_RC_MENU;
        if (b[4] & 0x80) keys |= BUTTON_RC_SELECT;
    }
    if (len > 5)
    {
        if (b[5] & 0x01) keys |= BUTTON_RC_UP;
        if (b[5] & 0x02) keys |= BUTTON_RC_DOWN;
    }
    return keys;
}

static void add_name(char *out, size_t size, const char *name)
{
    size_t n = strlen(out);
    if (n < size)
        snprintf(out + n, size - n, "%s%s", n ? "|" : "", name);
}

void remote_button_names(unsigned long keys, char *out, size_t size)
{
    static const struct { unsigned long bit; const char *name; } names[] = {
        { BUTTON_RC_MENU, "MENU" }, { BUTTON_RC_SELECT, "SELECT" },
        { BUTTON_RC_PLAY, "PLAY" }, { BUTTON_RC_LEFT, "LEFT" },
        { BUTTON_RC_RIGHT, "RIGHT" }, { BUTTON_RC_VOL_UP, "VOL+" },
        { BUTTON_RC_VOL_DOWN, "VOL-" }, { BUTTON_RC_UP, "UP" },
        { BUTTON_RC_DOWN, "DOWN" }, { BUTTON_RC_STOP, "STOP" },
        { BUTTON_RC_NEXT_ALBUM, "ALBUM+" },
        { BUTTON_RC_PREV_ALBUM, "ALBUM-" },
        { BUTTON_RC_NEXT_PLAYLIST, "LIST+" },
        { BUTTON_RC_PREV_PLAYLIST, "LIST-" }
    };
    if (!size) return;
    out[0] = '\0';
    for (unsigned int i = 0; i < sizeof(names) / sizeof(names[0]); i++)
        if (keys & names[i].bit)
        {
            add_name(out, size, names[i].name);
            keys &= ~names[i].bit;
        }
    if (keys) add_name(out, size, "?");
    if (!out[0]) snprintf(out, size, "(release)");
}

void remote_frame_names(const struct remote_frame *f, char *out, size_t size)
{
    const unsigned char *b = f->data;
    unsigned int len = remote_frame_stored(f);
    if (!size) return;
    out[0] = '\0';
    if (len < 3 || b[0] != 2 || b[1] != 0)
    {
        snprintf(out, size, "?");
        return;
    }
    remote_button_names(remote_context_buttons(len, b), out, size);
    if (!strcmp(out, "(release)")) out[0] = '\0';
    if (b[2] & 0x80) add_name(out, size, "STOP");
    if (len > 3)
    {
        if (b[3] & 1) add_name(out, size, "PLAY-ON");
        if (b[3] & 2) add_name(out, size, "PAUSE");
        if (b[3] & 8) add_name(out, size, "CHAPTER+");
        if (b[3] & 16) add_name(out, size, "CHAPTER-");
        if (b[3] & 128) add_name(out, size, "SHUFFLE");
        if (b[3] & 4) add_name(out, size, "?");
    }
    if (len > 4)
    {
        if (b[4] & 1) add_name(out, size, "REPEAT");
        if (b[4] & 2) add_name(out, size, "POWER-ON");
        if (b[4] & 4) add_name(out, size, "POWER-OFF");
        if (b[4] & 8) add_name(out, size, "?");
    }
    if (len > 5 && (b[5] & ~3)) add_name(out, size, "?");
    for (unsigned int i = 6; i < len; i++)
        if (b[i]) { add_name(out, size, "?"); break; }
    if (!out[0]) snprintf(out, size, "(release)");
}
