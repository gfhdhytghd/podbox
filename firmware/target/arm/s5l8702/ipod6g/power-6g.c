/***************************************************************************
 *             __________               __   ___.
 *   Open      \______   \ ____   ____ |  | _\_ |__   _______  ___
 *   Source     |       _//  _ \_/ ___\|  |/ /| __ \ /  _ \  \/  /
 *   Jukebox    |    |   (  <_> )  \___|    < | \_\ (  <_> > <  <
 *   Firmware   |____|_  /\____/ \___  >__|_ \|___  /\____/__/\_ \
 *                     \/            \/     \/    \/            \/
 *
 * Copyright © 2009 Bertrik Sikken
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 *
 * This software is distributed on an "AS IS" basis, WITHOUT WARRANTY OF ANY
 * KIND, either express or implied.
 *
 ****************************************************************************/
#include <stdbool.h>
#include "config.h"
#include "inttypes.h"
#include "s5l87xx.h"
#include "power.h"
#include "panic.h"
#include "pmu-target.h"
#include "usb_core.h"   /* for usb_charging_maxcurrent_change */
#include "timeout.h"
#if defined(HAVE_COMPOSITE_VIDEO_OUT) && !defined(BOOTLOADER)
#include "videoout.h"
#endif

static int idepowered;

void power_off(void)
{
#if defined(HAVE_COMPOSITE_VIDEO_OUT) && !defined(BOOTLOADER)
    videoout_disable();
#endif

    /* USB inserted or EXTON1 */
    pmu_set_wake_condition(
            PCF5063X_OOCWAKE_EXTON2 | PCF5063X_OOCWAKE_EXTON1);
    pmu_enter_standby();

    while(1);
}

void power_init(void)
{
    pmu_init();

    idepowered = false;

    /* DOWN1CTL: CPU DVM step time = 30us (default: no DVM) */
    pmu_write(0x20, 2);

    /* USB power configuration:
     *
     * GPIO C0 is probably related to the LTC4066's CLPROG
     * pin (see datasheet). Setting it high allows to double
     * the maximum current selected by HPWR:
     *
     *  GPIO B6     GPIO C0     USB current
     *  HPWR        CLPROG ???  limit (mA)
     *  -------     ----------  -----------
     *  0           0           100
     *  1           0           500
     *  0           1           200
     *  1           1           1000 ??? (max.seen ~750mA)
     *
     * USB current limit includes battery charge and device
     * consumption. Battery charge has it's own limit at
     * 330~340 mA (configured using RPROG).
     *
     * Setting either of GPIO C1 or GPIO C2 disables battery
     * charge, power needed for device consumptiom is drained
     * from USB or AC adaptor when present. If external power
     * is not present or it is insufficient or limited,
     * additional required power is drained from battery.
     */
    PCON11 = (PCON11 & 0x000000ff)
          | (0xe << 8)      /* route D+ to ADC2: off */
          | (0xe << 12)     /* route D- to ADC2: off */
          | (0x0 << 16)     /* USB related input, POL pin ??? */
          | (0x0 << 20)     /* USB related input, !CHRG pin ??? */
          | (0xe << 24)     /* HPWR: 100mA */
          | (0xe << 28);    /* USB suspend: off */

    PCON12 = (PCON12 & 0xffff0000)
          | (0xe << 0)      /* double HPWR limit: off */
          | (0xe << 4)      /* disable battery charge: off */
          | (0xe << 8)      /* disable battery charge: off */
          | (0x0 << 12);    /* USB inserted/not inserted */
}

void ide_power_enable(bool on)
{
    idepowered = on;
    pmu_hdd_power(on);
}

bool ide_powered()
{
    return idepowered;
}

#if CONFIG_CHARGING

/* Eight completed short !CHRG-high pulses in 90 s match the old eight-
 * dropout threshold and its reported 5-10 s repetition. Steady high means
 * full/paused, not oscillation. Qualify each pulse with 1 s of preceding
 * charge, reject 10 ms glitches and >=500 ms pauses. These are policy
 * thresholds, not a source-identity or battery-current measurement. */
#define CHRG_DROPOUTS          8
#define CHRG_WINDOW            (90*HZ)
#define CHRG_PULSE_MIN         2
#define CHRG_PULSE_MAX         (HZ/2)
#define CHRG_LOW_MIN           HZ
#define CHRG_BACKOFF_FIRST     (60*HZ)
#define CHRG_BACKOFF_MAX       (30*60*HZ)
#define CHRG_STABLE_RESET      (10*60*HZ)

enum {
    CHRG_INPUT_LOW_MA = 100,
    CHRG_INPUT_FALLBACK_MA = 500,
    CHRG_INPUT_HIGH_MA = 1000,
};
static volatile int chrg_input_requested = CHRG_INPUT_LOW_MA;
static volatile int chrg_input_applied = CHRG_INPUT_LOW_MA;
static volatile int chrg_input_ceiling = CHRG_INPUT_HIGH_MA;

/* Clear the double-current bit before changing HPWR, so lowering a tier
 * never passes through the unintended 200 mA combination. IRQs are masked. */
static void chrg_apply_input(int requested)
{
    int limit = MIN(requested, chrg_input_ceiling);
    GPIOCMD = 0xb070e | (limit < CHRG_INPUT_LOW_MA ? 1 : 0);
    GPIOCMD = 0xc000e;
    GPIOCMD = 0xb060e | (limit >= CHRG_INPUT_FALLBACK_MA ? 1 : 0);
    if (limit >= CHRG_INPUT_HIGH_MA)
        GPIOCMD = 0xc000f;
    chrg_input_applied = limit;
}

static struct timeout chrg_monitor_tmo;
/* ISR owns sampling; thread accesses/reset take the IRQ lock. */
static volatile bool chrg_monitoring, chrg_backoff, chrg_pulse_armed;
static volatile unsigned int chrg_low_ticks, chrg_high_ticks;
static volatile unsigned int chrg_stable_ticks;
static volatile unsigned int chrg_count, chrg_head, chrg_triggers;
static volatile long chrg_pulses[CHRG_DROPOUTS];
static volatile long chrg_retry_at, chrg_last_trigger;
static volatile int chrg_next_backoff = CHRG_BACKOFF_FIRST;

static void chrg_clear_trace(void)
{
    chrg_low_ticks = chrg_high_ticks = chrg_stable_ticks = 0;
    chrg_pulse_armed = false;
    chrg_count = chrg_head = 0;
}

static void chrg_stop_monitor(void)
{
    if (chrg_monitoring)
        timeout_cancel(&chrg_monitor_tmo);
    chrg_monitoring = false;
}

void usb_charging_guard_reset(void)
{
    int oldlevel = disable_irq_save();
    chrg_stop_monitor();
    chrg_clear_trace();
    chrg_backoff = false;
    chrg_input_ceiling = CHRG_INPUT_HIGH_MA;
    chrg_triggers = 0;
    chrg_last_trigger = chrg_retry_at = 0;
    chrg_next_backoff = CHRG_BACKOFF_FIRST;
    restore_irq(oldlevel);
}

/* Tick ISR: one !CHRG GPIO read while enabled. Never count the guard's own
 * inhibit pulse. Stop sampling during back-off, off, unplug and suspend. */
static int chrg_monitor_cb(struct timeout *tmo)
{
    (void)tmo;
    if (usb_detect() != USB_INSERTED ||
        usb_charging_get_mode() == USB_CHARGING_DISABLE)
    {
        usb_charging_guard_reset();
        return 0;
    }
    if (usb_charging_maxcurrent() < 100)
    {
        chrg_monitoring = false;
        chrg_clear_trace();
        return 0;
    }

    long now = current_tick;
    while (chrg_count &&
           TIME_BEFORE(chrg_pulses[chrg_head] + CHRG_WINDOW, now))
    {
        chrg_head = (chrg_head + 1) % CHRG_DROPOUTS;
        --chrg_count;
    }
    if (PDAT(11) & 0x10)
    {
        if (!chrg_high_ticks)
            chrg_pulse_armed = chrg_low_ticks >= CHRG_LOW_MIN;
        chrg_low_ticks = chrg_stable_ticks = 0;
        if (chrg_high_ticks < CHRG_PULSE_MAX)
            ++chrg_high_ticks;
        if (chrg_high_ticks == CHRG_PULSE_MAX)
        {
            /* Full battery/thermal pause: discard even partial evidence. */
            chrg_count = chrg_head = 0;
            chrg_pulse_armed = false;
        }
    }
    else
    {
        if (chrg_pulse_armed && chrg_high_ticks >= CHRG_PULSE_MIN &&
            chrg_high_ticks < CHRG_PULSE_MAX)
        {
            unsigned int tail = (chrg_head + chrg_count) % CHRG_DROPOUTS;
            chrg_pulses[tail] = now;
            if (++chrg_count == CHRG_DROPOUTS)
            {
                chrg_last_trigger = now;
                ++chrg_triggers;
                if (chrg_input_applied == CHRG_INPUT_HIGH_MA)
                {
                    /* First reduce load without inhibiting battery charge.
                     * Keep this ceiling until unplug/settings reset. */
                    chrg_input_ceiling = CHRG_INPUT_FALLBACK_MA;
                    chrg_apply_input(chrg_input_requested);
                    chrg_clear_trace();
                    return 1;
                }
                chrg_backoff = true;
                chrg_retry_at = now + chrg_next_backoff;
                chrg_next_backoff = MIN(chrg_next_backoff * 2,
                                        CHRG_BACKOFF_MAX);
                chrg_monitoring = false;
                GPIOCMD = 0xc010f; /* C1 only; USB still powers the load. */
                return 0;
            }
        }
        chrg_high_ticks = 0;
        chrg_pulse_armed = false;
        if (chrg_low_ticks < CHRG_LOW_MIN)
            ++chrg_low_ticks;
        if (chrg_stable_ticks < CHRG_STABLE_RESET &&
            ++chrg_stable_ticks == CHRG_STABLE_RESET)
        {
            chrg_next_backoff = CHRG_BACKOFF_FIRST;
            chrg_count = chrg_head = 0;
        }
    }
    return 1;
}

/* C1 policy is shared by both callers. Input tier changes belong to the
 * budget setter and the qualified-dropout ISR, not this status query. */
static bool usb_charge_apply(int maxcurrent)
{
    int oldlevel = disable_irq_save();
    bool enabled = usb_detect() == USB_INSERTED && maxcurrent >= 100 &&
                   usb_charging_get_mode() != USB_CHARGING_DISABLE;
    if (usb_detect() != USB_INSERTED ||
        usb_charging_get_mode() == USB_CHARGING_DISABLE)
        usb_charging_guard_reset();
    else if (!enabled)
    {
        chrg_stop_monitor();
        chrg_clear_trace();
    }
    else
    {
        if (chrg_backoff && !TIME_BEFORE(current_tick, chrg_retry_at))
        {
            chrg_backoff = false;
            chrg_clear_trace();
        }
        enabled = !chrg_backoff;
        if (enabled && !chrg_monitoring)
        {
            chrg_monitoring = true;
            timeout_register(&chrg_monitor_tmo, chrg_monitor_cb, 1, 0);
        }
    }
    GPIOCMD = 0xc010e | (enabled ? 0 : 1);
    restore_irq(oldlevel);
    return enabled;
}

void usb_charging_guard_get_status(struct usb_charge_guard_status *status)
{
    int oldlevel = disable_irq_save();
    status->input_limit_ma = chrg_input_applied;
    status->input_ceiling_ma = chrg_input_ceiling;
    status->dropouts = chrg_count;
    status->triggers = chrg_triggers;
    status->last_trigger_tick = chrg_last_trigger;
    status->backoff_seconds = chrg_backoff &&
        TIME_BEFORE(current_tick, chrg_retry_at) ?
        (chrg_retry_at - current_tick + HZ - 1) / HZ : 0;
    status->next_backoff_seconds = chrg_next_backoff / HZ;
    restore_irq(oldlevel);
}

#ifdef HAVE_USB_CHARGING_ENABLE
void usb_charging_maxcurrent_change(int maxcurrent)
{
    int oldlevel = disable_irq_save();
#ifndef BOOTLOADER
    /* Keep the USB descriptor/grant unchanged. The target input setting
     * doubles the former 500 mA tier; the weak-source ceiling can lower it. */
    if (maxcurrent >= CHRG_INPUT_FALLBACK_MA)
        maxcurrent = CHRG_INPUT_HIGH_MA;
#endif
    chrg_input_requested = maxcurrent < CHRG_INPUT_LOW_MA ? 0 :
        maxcurrent >= CHRG_INPUT_HIGH_MA ? CHRG_INPUT_HIGH_MA :
        maxcurrent >= CHRG_INPUT_FALLBACK_MA ? CHRG_INPUT_FALLBACK_MA :
                                             CHRG_INPUT_LOW_MA;
    chrg_apply_input(chrg_input_requested);
    usb_charge_apply(maxcurrent);
    restore_irq(oldlevel);
}
#endif

unsigned int power_input_status(void)
{
    unsigned int status = POWER_INPUT_NONE;
    bool enabled = usb_charge_apply(usb_charging_maxcurrent());
    if (usb_detect() == USB_INSERTED)
    {
        status |= POWER_INPUT_USB;
        if (enabled)
            status |= POWER_INPUT_USB_CHARGER;
    }
    if (pmu_firewire_present())
        status |= POWER_INPUT_MAIN_CHARGER;
    return status;
}

bool charging_state(void)
{
    return (PDAT(11) & 0x10) ? 0 : 1;
}
#endif /* CONFIG_CHARGING */
