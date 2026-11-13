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
static bool bridge_seen;
static unsigned long bridge_at;
static void (*volume_handler)(int, bool, unsigned long);

void button_usb_remote_volume_handler(void (*handler)(int, bool,
                                                       unsigned long))
{
    int level = disable_irq_save();
    volume_handler = handler;
    restore_irq(level);
}

bool button_usb_remote_bridge_fresh(unsigned long now)
{
    return bridge_seen && now - bridge_at < USB_REMOTE_BRIDGE_TIMEOUT;
}

bool button_usb_remote_request(const struct usb_ctrlrequest *req)
{
    if (req->bRequestType != 0x40 ||
        req->bRequest != USB_REMOTE_KEY_REQUEST ||
        req->wValue < USB_REMOTE_KEY_MENU ||
        req->wValue > USB_REMOTE_KEY_HEARTBEAT ||
        req->wLength != 0)
        return false;
    bool volume = req->wValue == USB_REMOTE_KEY_VOL_UP ||
                  req->wValue == USB_REMOTE_KEY_VOL_DOWN;
    if (volume ? (req->wIndex != 1 && req->wIndex != 2) :
        req->wValue == USB_REMOTE_KEY_HEARTBEAT ? req->wIndex != 1 :
        req->wIndex > 1)
        return false;

    int level = disable_irq_save();
    remote_log_event(REMOTE_LOG_EP0, current_tick, req->wValue, req->wIndex,
                     NULL, 0);
    if (req->wValue == USB_REMOTE_KEY_HEARTBEAT)
    {
        bridge_seen = true;
        bridge_at = current_tick;
    }
    else if (volume)
    {
        if (volume_handler)
            volume_handler(req->wValue == USB_REMOTE_KEY_VOL_UP ? 1 : -1,
                           req->wIndex == 1, current_tick);
    }
    else
    {
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
