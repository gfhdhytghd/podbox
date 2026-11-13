/* USB remote key input. Copyright (C) 2026. GPL version 2 or later. */
#include "button_usb_remote.h"
#include "button.h"
#include "system.h"
#include "kernel.h"
#include "usb_ch9.h"

static bool menu_down;
static unsigned long menu_until;

bool button_usb_remote_request(const struct usb_ctrlrequest *req)
{
    if (req->bRequestType != 0x40 ||
        req->bRequest != USB_REMOTE_KEY_REQUEST ||
        req->wValue != USB_REMOTE_KEY_MENU || req->wIndex > 1 ||
        req->wLength != 0)
        return false;

    int level = disable_irq_save();
    menu_down = req->wIndex != 0;
    menu_until = (unsigned long)current_tick + HZ;
    restore_irq(level);
    return true;
}

int button_usb_remote_read(void)
{
    int level = disable_irq_save();
    if (menu_down && !TIME_BEFORE(current_tick, menu_until))
        menu_down = false;
    int button = menu_down ? BUTTON_RC_MENU : BUTTON_NONE;
    restore_irq(level);
    return button;
}
