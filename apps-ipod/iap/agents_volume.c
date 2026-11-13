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
#include "button_usb_remote.h"

enum volume_source { VOLUME_NONE, VOLUME_WAIT, VOLUME_PC, VOLUME_DOCK };
struct volume_step {
    int direction;
    bool pc, waiting;
    unsigned long deadline, press;
};
static struct {
    enum volume_source owner;
    int direction;
    unsigned long deadline, press, pc_at[2];
    bool pc_seen[2];
    struct remote_volume_pair pair;
    struct volume_step steps[AGENTS_VOLUME_STEPS];
    unsigned int used;
} source;

/* a: direction in bit 0, PC in bit 1; b: used/fallback/duplicate/ignored. */
static void source_log_pair(int direction, bool pc, unsigned int decision,
                            unsigned long now,
                            const struct remote_volume_pair *pair)
{
    remote_log_event(REMOTE_LOG_VOLUME_KEY, now,
                     (direction > 0) | (pc ? 2 : 0), decision,
                     pair, sizeof(*pair));
}

static void source_log(int direction, bool pc, unsigned int decision,
                       unsigned long now)
{
    struct remote_volume_pair pair = pc ?
        (struct remote_volume_pair){.pc_tick = now, .present = 1} : source.pair;
    source_log_pair(direction, pc, decision, now, &pair);
}

static void pc_volume(int direction, bool frame, unsigned long now);

static bool queue_step(int direction, bool pc, bool waiting,
                       unsigned long deadline, unsigned long now)
{
    if (source.used == AGENTS_VOLUME_STEPS)
    {
        remote_log_event(REMOTE_LOG_LOST, now, 1, 0, NULL, 0);
        return false;
    }
    source.steps[source.used++] = (struct volume_step){
        direction, pc, waiting, deadline, source.press};
    if (source.used == 1) button_queue_try_post(BUTTON_NONE, 0);
    return true;
}

static void remove_step(unsigned int index)
{
    for (unsigned int i = index + 1; i < source.used; i++)
        source.steps[i - 1] = source.steps[i];
    source.used--;
}

static int volume_page(int direction, bool pc, unsigned long now)
{
    int button = (direction > 0 ? BUTTON_RC_VOL_UP : BUTTON_RC_VOL_DOWN) |
                 BUTTON_REL | BUTTON_REPEAT;
    remote_log_event(REMOTE_LOG_PAGE, now,
                     (direction > 0) | (pc ? 2 : 0), button, NULL, 0);
    return button;
}

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
    button_usb_remote_volume_handler(pc_volume);
    int level = disable_irq_save();
    /* Changing list/detail/Usage mode must not discard already received PC
     * reports or forget ownership of the current Dock press. Entry/exit and
     * stale bridges retain the original decoder reset behavior. */
    if (get_current_activity() == ACTIVITY_AGENTS &&
        button_usb_remote_bridge_fresh(current_tick))
    {
        volume_keys.enabled = enabled;
        restore_irq(level);
        return;
    }
    volume_keys.enabled = enabled;
    volume_keys.volume = device.mute ? 0 :
                         iap_volume_to_byte(global_status.volume);
    volume_keys.mute = device.mute;
    volume_keys.pending = 0;
    volume_keys.pressing = false;
    source = (typeof(source)){0};
    restore_irq(level);
}

/* Called under the EP0 driver's IRQ mask. No allocation or UI/file work. */
static void pc_volume(int direction, bool frame, unsigned long now)
{
    if (get_current_activity() != ACTIVITY_AGENTS ||
        !button_usb_remote_bridge_fresh(now))
    {
        source_log(direction, true, 3, now);
        return;
    }
    /* Both report types are immediate steps, with no gesture classifier. */
    (void)frame;
    source.pc_seen[direction > 0] = true;
    source.pc_at[direction > 0] = now;
    /* Match one pending press, including an older opposite-key candidate.
     * A new press must not overwrite an unexpired earlier candidate. */
    for (unsigned int i = source.used; i > 0; i--)
    {
        struct volume_step *step = &source.steps[i - 1];
        if (step->waiting && step->direction == direction &&
            !TIME_BEFORE(step->deadline, now))
        {
            struct remote_volume_pair pair = {
                .press = step->press, .pc_tick = now,
                .dock_tick = step->deadline -
                    (AGENTS_VOLUME_ARBITRATION_MS * HZ + 999) / 1000,
                .present = 3};
            if (step->press == source.press)
            {
                source.owner = VOLUME_PC;
                source.pair = pair;
            }
            /* Replace in place to retain physical order when opposite
             * candidates overlap. The PC report counts exactly once. */
            step->pc = true;
            step->waiting = false;
            source_log_pair(direction, false, 2, now, &pair);
            source_log_pair(direction, true, 0, now, &pair);
            button_queue_try_post(BUTTON_NONE, 0);
            return;
        }
    }
    if (queue_step(direction, true, false, now, now))
        source_log(direction, true, 0, now);
}

bool iap_agents_volume(unsigned char requested, bool mute)
{
    bool active = volume_keys.enabled &&
                  get_current_activity() == ACTIVITY_AGENTS;
    bool pc_mode = get_current_activity() == ACTIVITY_AGENTS &&
                   button_usb_remote_bridge_fresh(current_tick);
    /* Debug runs outside Agents. Decode a shadow stream there too, while
     * lingo 3 continues to set the real volume and reports the real value. */
    if (!active && !pc_mode && !remote_input_active && !remote_log_enabled())
        return false;
    unsigned char previous = volume_keys.volume;
    unsigned char upper = iap_volume_to_byte(global_settings.volume_limit);
    int direction = requested > previous ? 1 : requested < previous ? -1 :
                    requested == 0 ? -1 : requested >= upper ? 1 : 0;
    int level = disable_irq_save();
    unsigned long now = current_tick;
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
            source.press++;
            source.pair = (struct remote_volume_pair){
                .press = source.press, .dock_tick = now, .present = 2};
            volume_keys.pending = direction;
            volume_keys.next_page = now + (3 * HZ + 4) / 5;
            decision = 'P';
            if (pc_mode)
            {
                source.direction = direction;
                source.deadline = now +
                    (AGENTS_VOLUME_ARBITRATION_MS * HZ + 999) / 1000;
                source.owner = source.pc_seen[direction > 0] &&
                    now - source.pc_at[direction > 0] <=
                    (AGENTS_VOLUME_ARBITRATION_MS * HZ + 999) / 1000 ?
                    VOLUME_PC : VOLUME_WAIT;
                if (source.owner == VOLUME_PC)
                {
                    source.pair.pc_tick = source.pc_at[direction > 0];
                    source.pair.present |= 1;
                }
            }
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
        if (pc_mode && source.owner == VOLUME_PC &&
            source.direction == direction)
        {
            volume_keys.pending = 0;
            source_log(direction, false, 2, now);
        }
        else if (pc_mode && decision == 'S')
            source_log(direction, false, 2, now);
        if (pc_mode && volume_keys.pending)
        {
            queue_step(volume_keys.pending, false,
                       source.owner == VOLUME_WAIT,
                       source.pair.dock_tick +
                           (AGENTS_VOLUME_ARBITRATION_MS * HZ + 999) / 1000,
                       now);
            volume_keys.pending = 0;
        }
    }
    if (!active && !pc_mode) volume_keys.pending = 0;
    remote_volume_log(now, previous, requested, direction, decision);
    restore_irq(level);
    return active || pc_mode;
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
    bool agents = get_current_activity() == ACTIVITY_AGENTS;
    bool pc_mode = agents && button_usb_remote_bridge_fresh(current_tick);
    if (agents && source.used)
    {
        struct volume_step step = source.steps[0];
        if (step.waiting && pc_mode &&
            TIME_BEFORE(current_tick, step.deadline))
        {
            restore_irq(level);
            return BUTTON_NONE;
        }
        remove_step(0);
        if (!step.pc)
        {
            if (step.press == source.press) source.owner = VOLUME_DOCK;
            struct remote_volume_pair pair = {
                .press = step.press,
                .dock_tick = step.deadline -
                    (AGENTS_VOLUME_ARBITRATION_MS * HZ + 999) / 1000,
                .present = 2};
            source_log_pair(step.direction, false, 1, current_tick, &pair);
        }
        button = volume_page(step.direction, step.pc, current_tick);
        restore_irq(level);
        return button;
    }
    if (agents && (volume_keys.enabled || pc_mode) &&
        volume_keys.pending)
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

int iap_agents_volume_timeout(int timeout)
{
    int level = disable_irq_save();
    if (get_current_activity() == ACTIVITY_AGENTS)
    {
        bool fresh = button_usb_remote_bridge_fresh(current_tick);
        if (source.used)
        {
            struct volume_step *step = &source.steps[0];
            int left = step->waiting && fresh &&
                       TIME_BEFORE(current_tick, step->deadline) ?
                       step->deadline - (unsigned long)current_tick : 0;
            if (timeout < 0 || timeout > left) timeout = left;
        }
    }
    restore_irq(level);
    return timeout;
}
