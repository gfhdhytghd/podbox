/* USB remote key input. Copyright (C) 2026. GPL version 2 or later. */
#include "button_usb_remote.h"
#include "button.h"
#include "system.h"
#include "kernel.h"
#include "usb_ch9.h"
#include "remote_log.h"

static int remote_button;
static unsigned long pressed_at;
static unsigned long menu_until;

bool button_usb_remote_request(const struct usb_ctrlrequest *req)
{
    if (req->bRequestType != 0x40 ||
        req->bRequest != USB_REMOTE_KEY_REQUEST ||
        (req->wValue != USB_REMOTE_KEY_MENU &&
         req->wValue != USB_REMOTE_KEY_SELECT) || req->wIndex > 1 ||
        req->wLength != 0)
        return false;

    int level = disable_irq_save();
    remote_log_event(REMOTE_LOG_EP0, current_tick, req->wValue, req->wIndex,
                     NULL, 0);
    int key = req->wValue == USB_REMOTE_KEY_MENU ? BUTTON_RC_MENU :
                                                  BUTTON_RC_SELECT;
    if (req->wIndex)
    {
        if (remote_button != key) pressed_at = current_tick;
        remote_button = key;
        menu_until = (unsigned long)current_tick + HZ;
    }
    else if (remote_button == key)
    {
        /* Back-to-back EP0 press/release must survive button debounce.
         * Never synthesize a repeat; the bridge already chose the key. */
        unsigned long minimum = pressed_at + (HZ + 19) / 20;
        if (TIME_BEFORE(current_tick, minimum))
            menu_until = minimum;
        else
            remote_button = BUTTON_NONE;
    }
    restore_irq(level);
    return true;
}

int button_usb_remote_read(void)
{
    int level = disable_irq_save();
    if (remote_button && !TIME_BEFORE(current_tick, menu_until))
        remote_button = BUTTON_NONE;
    int button = remote_button;
    restore_irq(level);
    return button;
}
