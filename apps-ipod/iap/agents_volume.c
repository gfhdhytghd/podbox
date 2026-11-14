/* PC/Dock volume sources and UI navigation. GPL version 2+. */
#include "config.h"
#include "button.h"
#include "system.h"
#include "sound.h"
#include "kernel.h"
#include "iap/iap-core.h"
#include "iap/agents_volume.h"
#include "iap/remote_capture.h"
#include "settings/settings.h"
#include "system/activity.h"
#include "remote_log.h"
#include "button_usb_remote.h"
#include "podlink.h"

/* The only connection policy: resident 2AA session up on current configured
 * USB, OR the bridge's existing three-second receiver heartbeat. Both reads
 * are nonblocking and safe under the EP0 callback's IRQ mask. */
static bool computer_connected(unsigned long now)
{
    return podlink_host_connected() || button_usb_remote_bridge_fresh(now);
}

static struct {
    signed char steps[AGENTS_VOLUME_STEPS];
    unsigned int first, used;
    int activity;
    bool wps;
} source;

static struct {
    bool enabled, mute, host_keys;
    unsigned char volume;
    signed char pending;
    unsigned long next_page, step_at;
    signed char direction;
    bool pressing, wps;
    int activity;
} volume_keys;

static void pc_volume(int direction, bool frame, unsigned long now);

static bool real_volume(void)
{
    return volume_keys.wps && volume_keys.activity == ACTIVITY_WPS &&
           get_current_activity() == ACTIVITY_WPS;
}

/* Called with IRQs masked. A new screen never inherits an old input batch. */
static void volume_scope(void)
{
    int activity = get_current_activity();
    if (volume_keys.activity == activity) return;
    volume_keys.activity = activity;
    volume_keys.wps = false;
    volume_keys.pending = 0;
    volume_keys.pressing = false;
    source.first = source.used = 0;
    volume_keys.volume = device.mute ? 0 : iap_volume_to_byte(global_status.volume);
    volume_keys.mute = device.mute;
}

void iap_volume_context(bool wps)
{
    button_usb_remote_volume_handler(pc_volume);
    int level = disable_irq_save();
    volume_scope();
    if (volume_keys.wps != wps)
    {
        volume_keys.wps = wps;
        volume_keys.pending = 0;
        volume_keys.pressing = false;
        source.first = source.used = 0;
        volume_keys.volume = device.mute ? 0 : iap_volume_to_byte(global_status.volume);
        volume_keys.mute = device.mute;
    }
    restore_irq(level);
}

/* a: direction in bit 0, PC in bit 1; b: used/fallback/ignored. */
static void source_log(int direction, bool pc, unsigned int decision,
                       unsigned long now)
{
    struct remote_volume_pair pair = pc ?
        (struct remote_volume_pair){.pc_tick = now, .present = 1} :
        (struct remote_volume_pair){.dock_tick = now, .present = 2};
    remote_log_event(REMOTE_LOG_VOLUME_KEY, now,
                     (direction > 0) | (pc ? 2 : 0), decision,
                     &pair, sizeof(pair));
}

static int volume_page(int direction, bool pc, unsigned long now)
{
    int button = (direction > 0 ? BUTTON_RC_VOL_UP : BUTTON_RC_VOL_DOWN) |
                 BUTTON_REL | BUTTON_REPEAT;
    remote_log_event(REMOTE_LOG_PAGE, now,
                     (direction > 0) | (pc ? 2 : 0), button, NULL, 0);
    return button;
}

/* Called under the EP0 driver's IRQ mask. One accepted report = one step. */
static void pc_volume(int direction, bool frame, unsigned long now)
{
    (void)frame;
    volume_scope();
    if (remote_input_active || !computer_connected(now))
    {
        source_log(direction, true, 3, now);
        return;
    }
    if (get_current_activity() == ACTIVITY_AGENTS && volume_keys.host_keys)
    {
        /* Usage/detail are handled on the computer, never by iPod page keys. */
        source_log(direction, true, 5, now);
        return;
    }
    bool wps = real_volume();
    if (source.used && (source.activity != get_current_activity() || source.wps != wps))
        source.first = source.used = 0;
    source.activity = get_current_activity();
    source.wps = wps;
    if (source.used == AGENTS_VOLUME_STEPS)
    {
        remote_log_event(REMOTE_LOG_LOST, now, 1, 0, NULL, 0);
        return;
    }
    source.steps[(source.first + source.used++) % AGENTS_VOLUME_STEPS] =
        direction;
    if (source.used == 1) button_queue_try_post(BUTTON_NONE, 0);
    source_log(direction, true, 0, now);
}

void iap_agents_volume_enable(bool enabled)
{
    button_usb_remote_volume_handler(pc_volume);
    int level = disable_irq_save();
    volume_keys.enabled = enabled;
    volume_keys.volume = device.mute ? 0 :
                         iap_volume_to_byte(global_status.volume);
    volume_keys.mute = device.mute;
    volume_keys.pending = 0;
    volume_keys.pressing = false;
    if (!enabled) source.first = source.used = 0;
    /* View changes keep accepted PC reports. Entry/exit and Debug clear them. */
    if (get_current_activity() != ACTIVITY_AGENTS)
        source.first = source.used = 0;
    restore_irq(level);
}

void iap_agents_volume_host_keys(bool host_keys)
{
    int level = disable_irq_save();
    volume_keys.host_keys = host_keys;
    if (host_keys) source.first = source.used = 0;
    restore_irq(level);
}

bool iap_agents_volume(unsigned char requested, bool mute)
{
    int level = disable_irq_save();
    unsigned long now = current_tick;
    volume_scope();
    bool active = !real_volume();
    bool connected = computer_connected(now);
    if (!active && !connected && !remote_input_active && !remote_log_enabled())
    {
        restore_irq(level);
        return false;
    }
    unsigned char previous = volume_keys.volume;
    unsigned char upper = iap_volume_to_byte(global_settings.volume_limit);
    int direction = requested > previous ? 1 : requested < previous ? -1 :
                    requested == 0 ? -1 : requested >= upper ? 1 : 0;
    volume_keys.volume = mute ? 0 : requested;
    volume_keys.mute = mute;
    if (connected)
    {
        /* No Dock press grouping, hold timing, WAIT or fallback in this mode.
         * Keep virtual readback but never reach the real lingo-3 volume setter. */
        volume_keys.pending = 0;
        volume_keys.pressing = false;
        remote_volume_log(now, previous, requested, direction, 'I');
        source_log(direction, false, 4, now);
        restore_irq(level);
        return true;
    }

    /* FU13 Dock navigation when neither computer signal is present. */
    char decision = 'S';
    if (mute)
    {
        volume_keys.pending = 0;
        volume_keys.pressing = false;
    }
    if (!mute && direction)
    {
        bool fresh = !volume_keys.pressing ||
                     now - volume_keys.step_at >= (3 * HZ + 9) / 10 ||
                     direction != volume_keys.direction;
        if (fresh)
        {
            volume_keys.pending = direction;
            volume_keys.next_page = now + (3 * HZ + 4) / 5;
            decision = 'P';
        }
        else if (!TIME_BEFORE(now, volume_keys.next_page))
        {
            volume_keys.pending = direction;
            volume_keys.next_page = now + (HZ + 3) / 4;
            decision = 'R';
        }
        volume_keys.pressing = true;
        volume_keys.direction = direction;
        volume_keys.step_at = now;
    }
    if (!active || remote_input_active) volume_keys.pending = 0;
    else if (volume_keys.pending) button_queue_try_post(BUTTON_NONE, 0);
    remote_volume_log(now, previous, requested, direction, decision);
    restore_irq(level);
    return active || remote_input_active;
}

bool iap_agents_volume_get(unsigned char *volume, bool *mute)
{
    int level = disable_irq_save();
    volume_scope();
    bool active = !real_volume();
    if (active)
    {
        *volume = volume_keys.volume;
        *mute = volume_keys.mute;
    }
    restore_irq(level);
    return active;
}

int iap_agents_volume_take(void)
{
    int button = BUTTON_NONE;
    int level = disable_irq_save();
    volume_scope();
    bool connected = computer_connected(current_tick);
    if (remote_input_active) { source.used = 0; volume_keys.pending = 0; }
    if (source.used && (!connected || source.activity != get_current_activity() ||
                        source.wps != real_volume())) source.first = source.used = 0;
    if (connected)
    {
        /* Drop pre-connection Dock input even if a PC report is ready. */
        volume_keys.pending = 0;
        volume_keys.pressing = false;
    }
    if (source.used)
    {
        int direction = source.steps[source.first];
        source.first = (source.first + 1) % AGENTS_VOLUME_STEPS;
        source.used--;
        button = volume_page(direction, true, current_tick);
        restore_irq(level);
        return button;
    }
    if (!connected && !real_volume() && volume_keys.pending)
    {
        button = volume_page(volume_keys.pending, false, current_tick);
        remote_volume_log(current_tick, volume_keys.volume, volume_keys.volume,
                          volume_keys.pending, 'E');
        source_log(volume_keys.pending, false, 1, current_tick);
        volume_keys.pending = 0;
    }
    restore_irq(level);
    return button;
}

/* Synthetic reports carry REL|REPEAT. Real Dock button releases do not step. */
int iap_volume_filter(int button)
{
    int key = button & BUTTON_REMOTE;
    if (button & (SYS_EVENT | BUTTON_MULTIMEDIA | BUTTON_REDRAW) ||
        (key != BUTTON_RC_VOL_UP && key != BUTTON_RC_VOL_DOWN)) return button;
    int level = disable_irq_save();
    bool report = (button & (BUTTON_REL | BUTTON_REPEAT)) == (BUTTON_REL | BUTTON_REPEAT);
    bool ignore = remote_input_active ||
                  (!report && (computer_connected(current_tick) || (button & BUTTON_REL)));
    bool wps = real_volume();
    restore_irq(level);
    if (ignore) return BUTTON_NONE;
    return wps ? key : key | BUTTON_REL | BUTTON_REPEAT;
}
