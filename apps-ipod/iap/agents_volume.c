/* Dock volume-set pairs become local page keys in Agents. GPL version 2+. */
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

static struct {
    bool enabled, echo, mute;
    unsigned char anchor, volume;
    signed char pending;
    unsigned long next_page, pair_at, echo_at;
    signed char direction;
    bool pressing;
} volume_keys;

void iap_agents_volume_enable(bool enabled)
{
    int level = disable_irq_save();
    volume_keys.enabled = enabled;
    volume_keys.echo = false;
    volume_keys.volume = device.mute ? 0 :
                         iap_volume_to_byte(global_status.volume);
    volume_keys.mute = device.mute;
    volume_keys.pending = 0;
    volume_keys.pressing = false;
    restore_irq(level);
}

bool iap_agents_volume(unsigned char requested, bool mute)
{
    if (!volume_keys.enabled || get_current_activity() != ACTIVITY_AGENTS)
    {
        volume_keys.echo = false;
        return false;
    }
    if (volume_keys.echo && (unsigned long)current_tick - volume_keys.echo_at >=
        (35 * HZ + 99) / 100)
        volume_keys.echo = false;
    unsigned char upper = iap_volume_to_byte(global_settings.volume_limit);
    int direction = 0;
    if (mute)
    {
        volume_keys.echo = false;
        volume_keys.mute = true;
        volume_keys.volume = 0;
    }
    else if (volume_keys.echo)
    {
        volume_keys.echo = false;
        direction = requested > volume_keys.anchor ? 1 :
                    requested < volume_keys.anchor ? -1 :
                    requested == 0 ? -1 : requested >= upper ? 1 : 0;
        volume_keys.volume = requested;
        volume_keys.mute = false;
    }
    else
    {
        /* Echo/target order retains the direction of retransmitted pairs;
         * the press gap and hold threshold below coalesce their pages. */
        volume_keys.echo = true;
        volume_keys.anchor = requested;
        volume_keys.echo_at = current_tick;
    }
    if (!mute && !volume_keys.echo)
    {
        int level = disable_irq_save();
        unsigned long now = current_tick;
        bool fresh = !volume_keys.pressing ||
                     now - volume_keys.pair_at >= (35 * HZ + 99) / 100;
        char decision = 'S';
        if (direction)
        {
            fresh |= direction != volume_keys.direction;
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
            volume_keys.pair_at = now;
        }
        remote_volume_log(now, volume_keys.anchor, requested,
                          direction, decision);
        restore_irq(level);
    }
    return true;
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
        remote_volume_log(current_tick, volume_keys.anchor, volume_keys.volume,
                          volume_keys.pending, 'E');
        volume_keys.pending = 0;
    }
    restore_irq(level);
    return button;
}
