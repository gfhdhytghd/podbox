/* Safe descriptor helpers. SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef USB_PROFILE_H
#define USB_PROFILE_H
#include <stdbool.h>
#include <stdint.h>
static inline char usb_profile_serial_prefix(unsigned int enabled_mask)
{
    /* The legacy prefix has one hexadecimal code unit; higher driver bits
     * must never index beyond its alphabet. The device suffix stays intact. */
    return "0123456789ABCDEF"[enabled_mask & 15];
}
static inline unsigned int
usb_profile_config_count(unsigned int total, bool drive_only, bool podlink)
{
    return drive_only || podlink ? 1 : total;
}
static inline bool usb_profile_close_matches(bool leased, uint32_t current,
                                             uint32_t queued)
{
    return leased && current == queued;
}
#endif
