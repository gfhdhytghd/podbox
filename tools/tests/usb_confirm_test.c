/* Offline USB consent state checks. GPL version 2+. */
#include <assert.h>
#include <stdio.h>
#include "usb_confirm.h"

int main(void)
{
    struct usb_consent s = {0};
    for (int mode = 0; mode < 3; ++mode)
    for (int lease = 0; lease < 2; ++lease)
    {
        usb_consent_begin(&s, mode == 1, lease);
        bool ask = mode != 1 && !lease;
        assert(usb_consent_blocks(&s) == ask);
        assert(!usb_consent_observe(&s, true, false, false));
        assert(usb_consent_observe(&s, true, true, false) == ask);
        uint32_t epoch = s.epoch;
        assert(!usb_consent_reply(&s, epoch - 1, true));
        assert(usb_consent_reply(&s, epoch, false) == ask);
        assert(!usb_consent_observe(&s, true, true, false));
        assert(!usb_consent_reply(&s, epoch, true));
        usb_consent_observe(&s, false, false, false);
        assert(s.state == USB_CONSENT_BYPASS);
        assert(!usb_consent_reply(&s, epoch, true));
        usb_consent_begin(&s, mode == 1, lease);
        assert(usb_consent_observe(&s, true, true, false) == ask);
        assert(!usb_consent_reply(&s, epoch, true));
        assert(usb_consent_reply(&s, s.epoch, true) == ask);
        assert(!usb_consent_blocks(&s));
    }
    /* Wall charger: an insertion without a host never opens the prompt. */
    usb_consent_begin(&s, false, false);
    assert(s.state == USB_CONSENT_WAIT);
    assert(usb_consent_blocks(&s));
    usb_consent_observe(&s, false, false, false);
    assert(s.state == USB_CONSENT_BYPASS);
    /* Both a dock's config and a car's probe suppress consent for this cable. */
    for (int configured = 0; configured < 2; ++configured)
    {
        usb_consent_begin(&s, false, false);
        assert(!usb_consent_observe(&s, true, configured, true));
        assert(s.state == USB_CONSENT_ACCESSORY);
        assert(!usb_consent_observe(&s, true, true, false));
        assert(!usb_consent_reply(&s, s.epoch, true));
    }
    usb_consent_begin(&s, false, false);
    assert(usb_consent_observe(&s, true, true, false));
    usb_consent_observe(&s, true, false, true);
    assert(s.state == USB_CONSENT_ACCESSORY);
    usb_consent_begin(&s, false, false);
    assert(usb_consent_observe(&s, true, true, false));
    usb_consent_observe(&s, true, false, false);
    assert(!usb_consent_reply(&s, s.epoch, true));
    assert(usb_consent_observe(&s, true, true, false));
    s.epoch = UINT32_MAX;
    usb_consent_begin(&s, false, false);
    assert(s.epoch == 1);
    puts("usb consent: modes, lease, host, accessory, choices, reset, replug PASS");
}
