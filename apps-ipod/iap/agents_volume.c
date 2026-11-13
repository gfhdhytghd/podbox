/* Dock volume-set pairs become local page keys in Agents. GPL version 2+. */
#include "config.h"
#include "button.h"
#include "system.h"
#include "sound.h"
#include "iap/iap-core.h"
#include "iap/agents_volume.h"
#include "settings/settings.h"
#include "system/activity.h"

#define PAGE_QUEUE 16
static struct {
    bool enabled, echo;
    unsigned char anchor;
    unsigned int read, write;
    signed char pages[PAGE_QUEUE];
} volume_keys;

void iap_agents_volume_enable(bool enabled)
{
    int level = disable_irq_save();
    volume_keys.enabled = enabled;
    volume_keys.echo = false;
    volume_keys.read = volume_keys.write = 0;
    restore_irq(level);
}

bool iap_agents_volume(unsigned char requested, bool mute)
{
    if (!volume_keys.enabled || get_current_activity() != ACTIVITY_AGENTS)
    {
        volume_keys.echo = false;
        return false;
    }
    unsigned char current = device.mute ? 0 :
                            iap_volume_to_byte(global_status.volume);
    unsigned char upper = iap_volume_to_byte(global_settings.volume_limit);
    int direction = 0;
    if (mute)
        volume_keys.echo = false;
    else if (volume_keys.echo && volume_keys.anchor == current)
    {
        volume_keys.echo = false;
        direction = requested > current ? 1 : requested < current ? -1 :
                    current == 0 ? -1 : current >= upper ? 1 : 0;
    }
    else if (requested == current)
    {
        volume_keys.echo = true;
        volume_keys.anchor = current;
    }
    else
    {
        volume_keys.echo = false;
        direction = requested > current ? 1 : -1;
    }
    if (direction)
    {
        int level = disable_irq_save();
        if (volume_keys.write - volume_keys.read == PAGE_QUEUE)
            volume_keys.read++;
        volume_keys.pages[volume_keys.write++ % PAGE_QUEUE] = direction;
        restore_irq(level);
    }
    return true;
}

int iap_agents_volume_take(void)
{
    int button = BUTTON_NONE;
    int level = disable_irq_save();
    if (volume_keys.enabled && get_current_activity() == ACTIVITY_AGENTS &&
        volume_keys.read != volume_keys.write)
        button = (volume_keys.pages[volume_keys.read++ % PAGE_QUEUE] > 0 ?
                  BUTTON_RC_VOL_UP : BUTTON_RC_VOL_DOWN) |
                 BUTTON_REL | BUTTON_REPEAT;
    restore_irq(level);
    return button;
}
