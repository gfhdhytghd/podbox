#include <assert.h>
#include "usb_profile.h"
int main(void)
{
    for (unsigned int mask = 0; mask < 256; ++mask)
    {
        char c = usb_profile_serial_prefix(mask);
        assert((c >= '0' && c <= '9') || (c >= 'A' && c <= 'F'));
    }
    assert(usb_profile_serial_prefix(2) == '2');
    assert(usb_profile_serial_prefix(41) == '9');
    assert(usb_profile_config_count(2, false, true) == 1);
    assert(usb_profile_config_count(2, true, false) == 1);
    assert(usb_profile_config_count(2, false, false) == 2);
    return 0;
}
