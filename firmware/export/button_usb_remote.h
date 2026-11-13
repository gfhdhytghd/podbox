/* USB remote key input. Copyright (C) 2026. GPL version 2 or later. */
#ifndef BUTTON_USB_REMOTE_H
#define BUTTON_USB_REMOTE_H

#include "config.h"
#include <stdbool.h>

#if defined(IPOD_6G) && !defined(BOOTLOADER) && !defined(SIMULATOR)
#define USB_REMOTE_KEY_ENABLED
#define USB_REMOTE_KEY_REQUEST 0x5b
#define USB_REMOTE_KEY_MENU 1
#define USB_REMOTE_KEY_SELECT 2

struct usb_ctrlrequest;
bool button_usb_remote_request(const struct usb_ctrlrequest *req);
int button_usb_remote_read(void);
#endif
#endif
