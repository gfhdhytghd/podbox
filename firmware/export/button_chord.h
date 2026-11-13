/* Native Agents chord poweroff guard. Copyright (C) 2026. GPL version 2+. */
#ifndef BUTTON_CHORD_H
#define BUTTON_CHORD_H
#include "config.h"
#include "button.h"
#if defined(HAVE_USB_PODLINK) && defined(BUTTON_SELECT) && !defined(BOOTLOADER)
extern volatile bool button_agents_chord_latched;
static inline void button_chord_begin(void)
{
    button_agents_chord_latched = true;
}
#endif
static inline bool button_chord_blocks_poweroff(int buttons)
{
#if defined(HAVE_USB_PODLINK) && defined(BUTTON_SELECT) && !defined(BOOTLOADER)
    if (!(buttons & (BUTTON_SELECT | BUTTON_PLAY)))
        button_agents_chord_latched = false;
    return button_agents_chord_latched;
#else
    (void)buttons;
    return false;
#endif
}
#endif
