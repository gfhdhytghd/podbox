#!/usr/bin/env python3
"""Exercise the production profile selector with RAM-only USB ports.

Pass a source root to run the identical disk-hiding regression on a baseline.
No controller, device node, service or real USB connection is used.
"""
import pathlib
import re
import subprocess
import sys

root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else
                    pathlib.Path(__file__).resolve().parents[2]).resolve()
out = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else
                   root / "build-usb-confirm-profile").resolve()
out.mkdir(parents=True, exist_ok=True)
source = (root / "firmware/usb.c").read_text()
start = source.index("static inline void usb_configure_drivers(int for_state)")
end = source.index("static inline void usb_slave_mode", start)
function = source[start:end]
preamble = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include "usb_confirm.h"
#define HAVE_USB_CONFIRM
#define HAVE_USB_PODLINK
#define USB_ENABLE_STORAGE
#define USB_ENABLE_SERIAL
#define USB_ENABLE_HID
#define USB_ENABLE_AUDIO
#define USB_ENABLE_IAP
#define USB_ENABLE_CHARGING_ONLY
enum { USB_EXTRACTED, USB_INSERTED, USB_POWERED };
enum { USB_DRIVER_MASS_STORAGE, USB_DRIVER_HID, USB_DRIVER_AUDIO,
       USB_DRIVER_SERIAL, USB_DRIVER_IAP, USB_DRIVER_CHARGING_ONLY,
       USB_NUM_DRIVERS };
static struct usb_consent consent;
static bool podlink_requested, drive, car;
static bool enabled[USB_NUM_DRIVERS];
static bool usb_hid = true, usb_serial = true, usb_iap = true;
static int usb_audio;
static struct { int usb_audio; } global_settings = {1};
static bool usb_confirm_blocked(void) { return usb_consent_blocks(&consent); }
static bool usb_drive_only(void) { return drive && !usb_confirm_blocked(); }
static bool usb_iap_answer_iap2(void) { return car; }
static bool usb_audio_wanted(void) { return usb_audio != 0; }
static void usb_iap2_host_new(void) { car = false; }
static bool usb_car_no_audio;
static void usb_attach(void) {}
static void usb_core_enable_driver(int d, bool on) { enabled[d] = on; }
'''
tests = r'''
int main(void)
{
    for (int drive_only = 0; drive_only < 2; ++drive_only)
    {
        drive = drive_only;
        usb_consent_begin(&consent, false, false);
        usb_configure_drivers(USB_INSERTED);
        assert(!enabled[USB_DRIVER_MASS_STORAGE]);
        assert(enabled[USB_DRIVER_CHARGING_ONLY]);
        assert(enabled[USB_DRIVER_IAP]);
        assert(!enabled[USB_DRIVER_AUDIO]);
        assert(!enabled[USB_DRIVER_HID]);
        assert(usb_consent_observe(&consent, true, true, false));
        usb_consent_reply(&consent, consent.epoch, false);
        usb_configure_drivers(USB_INSERTED);
        assert(!enabled[USB_DRIVER_MASS_STORAGE]);
        usb_consent_begin(&consent, false, false);
        usb_consent_observe(&consent, true, true, false);
        usb_consent_reply(&consent, consent.epoch, true);
        usb_configure_drivers(USB_INSERTED);
        assert(enabled[USB_DRIVER_MASS_STORAGE]);
        assert(!enabled[USB_DRIVER_CHARGING_ONLY]);
        assert(enabled[USB_DRIVER_IAP] == !drive_only);
        assert(enabled[USB_DRIVER_HID] == !drive_only);
        assert(enabled[USB_DRIVER_AUDIO] == !drive_only);
    }
    podlink_requested = true;
    usb_consent_begin(&consent, false, true);
    usb_configure_drivers(USB_INSERTED);
    for (int i = 0; i < USB_NUM_DRIVERS; ++i)
        assert(enabled[i] == (i == USB_DRIVER_SERIAL));
    podlink_requested = false;
    usb_consent_begin(&consent, true, false);
    usb_configure_drivers(USB_POWERED);
    assert(!enabled[USB_DRIVER_MASS_STORAGE]);
    assert(enabled[USB_DRIVER_CHARGING_ONLY]);
    puts("production USB profiles: consent hides disk; SELECT restores mode; PodLink/Charge PASS");
}
'''
cfile = out / "profile.c"
cfile.write_text(preamble + function + tests)
# Use the final shared model for the baseline's unchanged profile selector.
headers = pathlib.Path(__file__).resolve().parents[2] / "firmware/export"
cmd = ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-g",
       "-fsanitize=address,undefined", "-fno-pie", "-no-pie",
       "-I" + str(headers), str(cfile), "-o", str(out / "profile")]
print(" ".join(cmd), flush=True)
subprocess.run(cmd, check=True)
subprocess.run([str(out / "profile")], check=True)
