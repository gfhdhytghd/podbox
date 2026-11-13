/* Remote MENU for native-button readers. GPL version 2 or later. */
#ifndef REMOTE_MENU_H
#define REMOTE_MENU_H
#include "config.h"
#include "button.h"
#include "kernel.h"

static inline int remote_menu_button(int button)
{
#ifdef IPOD_ACCESSORY_PROTOCOL
    if (!(button & (SYS_EVENT | BUTTON_MULTIMEDIA | BUTTON_REDRAW)) &&
        (button & BUTTON_RC_MENU))
        button = (button & ~BUTTON_RC_MENU) | BUTTON_MENU;
#endif
    return button;
}
#endif
