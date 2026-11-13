/* USB disk consent for PodBox. GNU General Public License, version 2+. */
#ifndef USB_CONFIRM_H
#define USB_CONFIRM_H

#include <stdbool.h>
#include <stdint.h>

enum usb_consent_state
{
    USB_CONSENT_BYPASS,
    USB_CONSENT_WAIT,
    USB_CONSENT_PROMPT,
    USB_CONSENT_CHARGE,
    USB_CONSENT_CONNECT,
    USB_CONSENT_ACCESSORY,
};

struct usb_consent
{
    enum usb_consent_state state;
    uint32_t epoch;
};

static inline void usb_consent_begin(struct usb_consent *s, bool charge,
                                     bool leased)
{
    if (!++s->epoch)
        ++s->epoch;
    s->state = charge || leased ? USB_CONSENT_BYPASS : USB_CONSENT_WAIT;
}

static inline bool usb_consent_blocks(const struct usb_consent *s)
{
    return s->state != USB_CONSENT_BYPASS &&
           s->state != USB_CONSENT_CONNECT;
}

static inline bool usb_consent_observe(struct usb_consent *s, bool host,
                                       bool configured, bool accessory)
{
    if (!host)
    {
        s->state = USB_CONSENT_BYPASS;
        return false;
    }
    if (accessory && usb_consent_blocks(s))
        s->state = USB_CONSENT_ACCESSORY;
    if (!configured && s->state == USB_CONSENT_PROMPT)
        s->state = USB_CONSENT_WAIT;
    if (configured && s->state == USB_CONSENT_WAIT)
    {
        s->state = USB_CONSENT_PROMPT;
        return true;
    }
    return false;
}

static inline bool usb_consent_reply(struct usb_consent *s, uint32_t epoch,
                                     bool connect)
{
    if (epoch != s->epoch || s->state != USB_CONSENT_PROMPT)
        return false;
    s->state = connect ? USB_CONSENT_CONNECT : USB_CONSENT_CHARGE;
    return true;
}

#endif
