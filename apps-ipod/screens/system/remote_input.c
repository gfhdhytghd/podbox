/* Serial dock remote capture screen. Copyright (C) 2026. GPL version 2+. */
#include "config.h"
#include <stdio.h>
#include <string.h>
#include "system.h"
#include "kernel.h"
#include "button.h"
#include "lcd.h"
#include "font.h"
#include "file.h"
#include "iap.h"
#include "iap/iap-core.h"
#include "iap/remote_capture.h"
#include "input/action.h"
#include "system/app_util.h"
#include "system/shutdown.h"
#include "draw/viewport.h"
#include "skin/skin_engine.h"

/* One static snapshot keeps the UI thread's stack independent of wire length. */
static struct remote_frame frame;
bool remote_input_active;

static bool snapshot(unsigned long seq)
{
    int level = disable_irq_save();
    bool ok = remote_capture_get(&iap_remote_capture, seq, &frame);
    restore_irq(level);
    return ok;
}

static unsigned long counts(unsigned long *bad)
{
    int level = disable_irq_save();
    unsigned long n = iap_remote_capture.frames;
    *bad = iap_remote_capture.bad;
    restore_irq(level);
    return n;
}

static const char *action_name(int action)
{
    switch (action)
    {
        case ACTION_NONE: return "ACTION_NONE";
        case ACTION_STD_OK: return "ACTION_STD_OK";
        case ACTION_STD_MENU: return "ACTION_STD_MENU";
        case ACTION_STD_CANCEL: return "ACTION_STD_CANCEL";
        case ACTION_STD_CONTEXT: return "ACTION_STD_CONTEXT";
        case ACTION_STD_QUICKSCREEN: return "ACTION_STD_QUICKSCREEN";
        case ACTION_STD_PREV: return "ACTION_STD_PREV";
        case ACTION_STD_NEXT: return "ACTION_STD_NEXT";
        case ACTION_STD_PREVREPEAT: return "ACTION_STD_PREVREPEAT";
        case ACTION_STD_NEXTREPEAT: return "ACTION_STD_NEXTREPEAT";
        case ACTION_LISTTREE_PGUP: return "ACTION_LISTTREE_PGUP";
        case ACTION_LISTTREE_PGDOWN: return "ACTION_LISTTREE_PGDOWN";
        default: return "?";
    }
}

static void payload(char *out, size_t size, unsigned int limit)
{
    unsigned int hdr = frame.received && frame.data[0] == 4 ? 3 : 2;
    unsigned int pos = 0;
    out[0] = '\0';
    for (unsigned int i = hdr; i < frame.received && i < hdr + limit; i++)
        pos += snprintf(out + pos, size - pos, "%02X ", frame.data[i]);
    if (frame.received > hdr + limit)
        snprintf(out + pos, size - pos, "...");
}

static unsigned int command(void)
{
    if (frame.received < 2) return 0;
    if (frame.data[0] == 4 && frame.received > 2)
        return frame.data[1] * 256 + frame.data[2];
    return frame.data[1];
}

static bool put_log(int fd, const char *text)
{
    size_t len = strlen(text);
    return write(fd, text, len) == (ssize_t)len;
}

static bool log_frame(int fd)
{
    char line[256], names[160];
    remote_frame_names(&frame, names, sizeof(names));
    snprintf(line, sizeof(line),
             "%lu tick=%lu +%lu.%02lus %s%u L%X/%02X "
             "len=%u rx=%u stored=%u %s",
             frame.sequence, frame.tick, frame.delta / HZ,
             frame.delta % HZ * 100 / HZ,
             (frame.flags & REMOTE_BAD_MASK) ? "BAD:" : "flags=",
             frame.flags, frame.received ? frame.data[0] : 0,
             command(), frame.length, frame.received,
             remote_frame_stored(&frame),
             (frame.flags & REMOTE_TRUNC) ? "TRUNC " : "");
    if (!put_log(fd, line)) return false;
    /* Log the stored raw prefix; declared and received lengths remain intact. */
    for (unsigned int i = 0; i < remote_frame_stored(&frame); )
    {
        unsigned int n = 0;
        while (i < remote_frame_stored(&frame) && n + 4 < sizeof(line))
            n += snprintf(line + n, sizeof(line) - n, "%02X ", frame.data[i++]);
        if (!put_log(fd, line)) return false;
    }
    snprintf(line, sizeof(line), " %s\n", names);
    return put_log(fd, line);
}

bool dbg_remote_input(void)
{
    int fd = -1;
    long observed_tick = -1;
    unsigned long cursor = 0, lost = 0, bad, total;
    unsigned long volume_cursor = remote_volume_count() + 1;
    bool log_error = false, usb = false;
    char line[320], hex[40], names[160], decoded[160] = "(none)";
    const char *made = "ACTION_NONE";
    bool inhibited = skin_flush_inhibited();
    skin_inhibit_flush(true);
    remote_input_active = true;
    lcd_set_viewport(NULL);
    lcd_setfont(FONT_SYSFIXED);
    button_clear_queue();
    while (true)
    {
        total = counts(&bad);
        if (fd >= 0)
        {
            if (total >= cursor && total - cursor >= REMOTE_HISTORY)
            {
                unsigned long skipped = total - cursor - REMOTE_HISTORY + 1;
                lost += skipped;
                cursor += skipped;
                snprintf(line, sizeof(line), "LOST %lu frames (ring overrun)\n", skipped);
                log_error = !put_log(fd, line);
            }
            /* A fixed batch lets native MENU run even during sustained RX. */
            unsigned long end = total;
            for (; cursor <= end && !log_error; cursor++)
                if (snapshot(cursor)) log_error = !log_frame(fd);
                else { lost++; log_error = true; }
            unsigned long volume_end = remote_volume_count();
            if (volume_end >= volume_cursor &&
                volume_end - volume_cursor >= REMOTE_VOLUME_HISTORY)
            {
                snprintf(line, sizeof(line), "VOL LOST %lu\n",
                         volume_end - volume_cursor - REMOTE_VOLUME_HISTORY + 1);
                log_error |= !put_log(fd, line);
                volume_cursor = volume_end - REMOTE_VOLUME_HISTORY + 1;
            }
            for (; volume_cursor <= volume_end && !log_error; volume_cursor++)
            {
                struct remote_volume_trace v;
                int level = disable_irq_save();
                bool have = remote_volume_get(volume_cursor, &v);
                restore_irq(level);
                if (!have) continue;
                snprintf(line, sizeof(line),
                         "VOL %lu tick=%lu echo=%02X target=%02X dir=%d "
                         "decision=%c\n", v.sequence, v.tick, v.echo, v.target,
                         v.direction, v.decision);
                log_error = !put_log(fd, line);
            }
            if (log_error) { close(fd); fd = -1; }
        }
        lcd_clear_display();
        snprintf(line, sizeof(line), "Remote input frames %lu bad %lu %s",
                 total, bad, fd >= 0 ? "LOG" : log_error ? "LOG ERROR" : "");
        lcd_puts(0, 0, line);
        remote_button_names(iap_remotebtn, names, sizeof(names));
        snprintf(line, sizeof(line), "held: %s", names);
        lcd_puts(0, 1, line);
        if (snapshot(total))
        {
            payload(hex, sizeof(hex), 8);
            snprintf(line, sizeof(line), "last: L%X cmd %02X %s%s%s",
                     frame.received ? frame.data[0] : 0, command(), hex,
                     (frame.flags & REMOTE_BAD_MASK) ? " BAD" : "",
                     (frame.flags & REMOTE_TRUNC) ? " TRUNC" : "");
            lcd_puts(0, 2, line);
            if ((frame.flags & REMOTE_BAD_MASK) || frame.received < 3 ||
                frame.data[0] != 2 || frame.data[1] != 0)
            {
                snprintf(decoded, sizeof(decoded), "?");
                made = (frame.flags & REMOTE_BAD_MASK) ? "ACTION_NONE" : "?";
            }
        }
        snprintf(line, sizeof(line), "-> RC_%s -> %s", decoded, made);
        lcd_puts(0, 3, line);
        snprintf(line, sizeof(line), "---- newest first ---- lost %lu", lost);
        lcd_puts(0, 4, line);
        for (unsigned int i = 0; i < REMOTE_HISTORY && total > i; i++)
        {
            if (!snapshot(total - i)) continue;
            remote_frame_names(&frame, names, sizeof(names));
            payload(hex, sizeof(hex), 4);
            snprintf(line, sizeof(line), "+%lu.%02lus L%X/%02X %s %s%s%s",
                     frame.delta / HZ, frame.delta % HZ * 100 / HZ,
                     frame.received ? frame.data[0] : 0, command(), hex,
                     (frame.flags & REMOTE_BAD_MASK) ? "BAD " : "",
                     (frame.flags & REMOTE_TRUNC) ? "TRUNC " : "", names);
            lcd_puts(0, 5 + i, line);
        }
        lcd_update();
        int action = get_action(CONTEXT_STD, MAX(1, HZ / 10));
        int button;
        get_action_statuscode(&button);
        if (button & BUTTON_REMOTE)
        {
            if (action != ACTION_NONE || get_action_tick() != observed_tick)
            {
                remote_button_names(button & BUTTON_REMOTE, decoded,
                                    sizeof(decoded));
                made = action_name(action);
                observed_tick = get_action_tick();
            }
            if (!(action & SYS_EVENT)) continue;
        }
        if ((button & BUTTON_MENU) && !(action & SYS_EVENT)) break;
        if (action == ACTION_STD_OK &&
            button == (BUTTON_SELECT | BUTTON_REL))
        {
            if (fd >= 0) { close(fd); fd = -1; }
            else
            {
                fd = open(ROCKBOX_DIR "/remote-log.txt",
                          O_WRONLY | O_CREAT | O_APPEND, 0666);
                log_error = fd < 0;
                cursor = counts(&bad) + 1;
                volume_cursor = remote_volume_count() + 1;
            }
        }
        if (action & SYS_EVENT)
        {
            if (default_event_handler(action) == SYS_USB_CONNECTED)
            { usb = true; break; }
        }
    }
    if (fd >= 0) close(fd);
    remote_input_active = false;
    button_clear_queue();
    lcd_setfont(FONT_UI);
    skin_inhibit_flush(inhibited);
    return usb;
}
