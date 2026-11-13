/* Dock volume-set pairs become local page keys in Agents. GPL version 2+. */
#include "config.h"
#include "button.h"
#include "system.h"
#include "sound.h"
#include "kernel.h"
#include "iap/iap-core.h"
#include "iap/agents_volume.h"
#include "settings/settings.h"
#include "system/activity.h"

static struct {
    bool enabled, echo, mute, cooling;
    unsigned char anchor, volume;
    signed char pending;
    unsigned long next_page;
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
    volume_keys.cooling = false;
    restore_irq(level);
}

bool iap_agents_volume(unsigned char requested, bool mute)
{
    if (!volume_keys.enabled || get_current_activity() != ACTIVITY_AGENTS)
    {
        volume_keys.echo = false;
        return false;
    }
    unsigned char current = volume_keys.volume;
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
        direction = requested > current ? 1 : requested < current ? -1 :
                    volume_keys.anchor != requested ? 0 :
                    current == 0 ? -1 : current >= upper ? 1 : 0;
        volume_keys.volume = requested;
        volume_keys.mute = false;
    }
    else
    {
        /* The dock sends current-value echo, then requested value. Comparing
         * the target to our reported value also discards resends of a pair. */
        volume_keys.echo = true;
        volume_keys.anchor = requested;
    }
    if (direction)
    {
        int level = disable_irq_save();
        volume_keys.pending = direction;
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
        volume_keys.pending && (!volume_keys.cooling ||
        !TIME_BEFORE((unsigned long)current_tick, volume_keys.next_page)))
    {
        button = (volume_keys.pending > 0 ?
                  BUTTON_RC_VOL_UP : BUTTON_RC_VOL_DOWN) |
                 BUTTON_REL | BUTTON_REPEAT;
        volume_keys.pending = 0;
        volume_keys.cooling = true;
        volume_keys.next_page = (unsigned long)current_tick + (HZ + 3) / 4;
    }
    restore_irq(level);
    return button;
}
