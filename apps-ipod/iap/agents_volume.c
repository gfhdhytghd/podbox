/* Dock absolute-volume steps become local page keys in Agents. GPL version 2+. */
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

static struct {
    bool enabled, mute;
    unsigned char volume;
    signed char pending;
    unsigned long next_page, step_at;
    signed char direction;
    bool pressing;
} volume_keys;

void iap_agents_volume_enable(bool enabled)
{
    int level = disable_irq_save();
    volume_keys.enabled = enabled;
    volume_keys.volume = device.mute ? 0 :
                         iap_volume_to_byte(global_status.volume);
    volume_keys.mute = device.mute;
    volume_keys.pending = 0;
    volume_keys.pressing = false;
    restore_irq(level);
}

bool iap_agents_volume(unsigned char requested, bool mute)
{
    bool active = volume_keys.enabled &&
                  get_current_activity() == ACTIVITY_AGENTS;
    /* Debug runs outside Agents. Decode a shadow stream there too, while
     * lingo 3 continues to set the real volume and reports the real value. */
    if (!active && !remote_input_active && !remote_log_enabled())
        return false;
    unsigned char previous = volume_keys.volume;
    unsigned char upper = iap_volume_to_byte(global_settings.volume_limit);
    int direction = requested > previous ? 1 : requested < previous ? -1 :
                    requested == 0 ? -1 : requested >= upper ? 1 : 0;
    unsigned long now = current_tick;
    int level = disable_irq_save();
    volume_keys.volume = mute ? 0 : requested;
    volume_keys.mute = mute;
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
    if (!active) volume_keys.pending = 0;
    remote_volume_log(now, previous, requested, direction, decision);
    restore_irq(level);
    return active;
}

bool iap_agents_volume_get(unsigned char *volume, bool *mute)
{
    int level = disable_irq_save();
    bool active = volume_keys.enabled &&
                  get_current_activity() == ACTIVITY_AGENTS;
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
    if (volume_keys.enabled && get_current_activity() == ACTIVITY_AGENTS &&
        volume_keys.pending)
    {
        button = (volume_keys.pending > 0 ?
                  BUTTON_RC_VOL_UP : BUTTON_RC_VOL_DOWN) |
                 BUTTON_REL | BUTTON_REPEAT;
        remote_volume_log(current_tick, volume_keys.volume, volume_keys.volume,
                          volume_keys.pending, 'E');
        volume_keys.pending = 0;
    }
    restore_irq(level);
    return button;
}
