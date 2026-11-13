#!/usr/bin/env python3
"""Sanitized production consent callbacks/USB-thread events with RAM ports."""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else \
      ROOT / "build-usbconfirm-evidence/backend"
OUT.mkdir(parents=True, exist_ok=True)
source = (ROOT / "firmware/usb.c").read_text()
api = source[source.index("bool usb_confirm_blocked(void)"):
             source.index("#endif\n#ifdef USB_ENABLE_HID", source.index(
                 "bool usb_confirm_blocked(void)"))]
events = source[source.index("        case USB_CONFIRM_READY:"):
                source.index("#endif\n\n#ifdef HAVE_USB_PODLINK", source.index(
                    "        case USB_CONFIRM_READY:"))]
preamble = r'''
#include <assert.h>
#include <stdio.h>
#include <stdint.h>
#include "usb_confirm.h"
#define HZ 100
#define USB_ENABLE_IAP
#define IPOD_ACCESSORY_PROTOCOL
enum { USB_EXTRACTED, USB_INSERTED, USB_CONFIRM_READY, USB_CONFIRM_CONNECT,
       USB_CONFIRM_CHARGE, SYS_USB_CONFIRM };
struct event_queue { int unused; };
struct timeout { intptr_t data; bool active; };
struct event { int id; intptr_t data; } pending[32];
static unsigned head, tail;
static struct usb_consent usb_consent;
static struct timeout usb_confirm_tmo;
static struct event_queue usb_queue;
static bool usb_confirm_has_config, usb_host_present, cable, car, serial_iap;
static int prompts, detach_count, attach_count, normal_profiles;
static int usb_detect(void) { return cable ? USB_INSERTED : USB_EXTRACTED; }
static bool iap_accessory_present(void) { return serial_iap; }
static bool usb_iap2_car_seen(void) { return car; }
static void queue_post(struct event_queue *q, int id, intptr_t data)
{ (void)q; assert(tail < 32); pending[tail++] = (struct event){id, data}; }
static void timeout_register(struct timeout *t, int (*cb)(struct timeout *),
                             int ticks, intptr_t data)
{ (void)cb; assert(ticks == HZ); t->data = data; t->active = true; }
static void timeout_cancel(struct timeout *t) { t->active = false; }
static void button_queue_post(int id, intptr_t data)
{ assert(id == SYS_USB_CONFIRM); assert(data == usb_consent.epoch); ++prompts; }
static void usb_stack_enable(bool on)
{ if (on) ++attach_count; else ++detach_count; }
static void sleep(int ticks) { assert(ticks == HZ / 5); }
static void usb_configure_drivers(int state)
{ assert(state == USB_INSERTED); ++normal_profiles; }
'''
tests = r'''
static void drain(void)
{
    while (head < tail)
    {
        struct event ev = pending[head++];
        switch (ev.id) {
EVENTS
        default: assert(false);
        }
    }
    head = tail = 0;
}
static void begin(bool charge, bool leased)
{
    usb_consent_begin(&usb_consent, charge, leased);
    cable = true; usb_host_present = false;
    usb_confirm_has_config = false;
    usb_confirm_tmo.active = false;
    car = serial_iap = false;
    prompts = detach_count = attach_count = normal_profiles = 0;
}
static void tick(void)
{
    assert(usb_confirm_tmo.active);
    usb_confirm_tmo.active = false;
    usb_confirm_tmo_cb(&usb_confirm_tmo);
    drain();
}
int main(void)
{
    begin(false, false);
    assert(usb_confirm_blocked());
    usb_confirm_configured(false, false, true); drain();
    assert(!prompts); /* no host / wall charger */
    usb_host_present = true;
    usb_confirm_configured(true, false, true);
    assert(!prompts); tick();
    assert(prompts == 1 && usb_confirm_pending(usb_consent.epoch));
    usb_confirm_configured(true, false, true); drain();
    assert(prompts == 1); /* repeated config, no timeout for the choice */
    usb_confirm_reply(usb_consent.epoch - 1, true); drain();
    assert(!normal_profiles && usb_confirm_blocked());
    usb_confirm_reply(usb_consent.epoch, false); drain();
    assert(!usb_confirm_pending(usb_consent.epoch));
    assert(!detach_count && usb_confirm_blocked());
    usb_confirm_configured(true, false, false); drain();
    assert(prompts == 1); /* MENU lasts until unplug */
    uint32_t old_epoch = usb_consent.epoch;
    begin(false, false); usb_host_present = true;
    usb_confirm_configured(true, false, false); drain();
    usb_confirm_reply(old_epoch, true); drain();
    assert(!normal_profiles);
    usb_confirm_reply(usb_consent.epoch, true); drain();
    assert(normal_profiles == 1 && detach_count == 1 && attach_count == 1);
    assert(!usb_confirm_blocked());
    for (int accessory = 0; accessory < 3; ++accessory)
    {
        begin(false, false); usb_host_present = true;
        usb_confirm_configured(true, false, true);
        if (accessory == 0) { car = true; tick(); }
        if (accessory == 1) usb_confirm_configured(true, true, false);
        if (accessory == 2) {
            serial_iap = true; usb_confirm_configured(true, false, false);
        }
        drain();
        assert(!prompts && usb_confirm_blocked());
    }
    for (int bypass = 0; bypass < 2; ++bypass)
    {
        begin(bypass == 0, bypass == 1); usb_host_present = true;
        usb_confirm_configured(true, false, false); drain();
        assert(!prompts);
    }
    begin(false, false); usb_host_present = true;
    usb_confirm_configured(true, false, false); drain();
    cable = false;
    assert(!usb_confirm_pending(usb_consent.epoch));
    usb_confirm_reply(usb_consent.epoch, true); drain();
    assert(!normal_profiles); /* unplug wins over the queued choice */
    puts("production USB events: classify, bypass, MENU, SELECT, stale choices, unplug PASS");
}
'''.replace("EVENTS", events)
c = OUT / "backend.c"
c.write_text(preamble + api + tests)
cmd = ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-g",
       "-fsanitize=address,undefined", "-fno-pie", "-no-pie",
       "-I" + str(ROOT / "firmware/export"), str(c), "-o", str(OUT / "backend")]
print(" ".join(cmd), flush=True)
subprocess.run(cmd, check=True)
subprocess.run([str(OUT / "backend")], check=True)

ui = (ROOT / "apps-ipod/widgets/usb_confirm.c").read_text()
callbacks = ui[ui.index("static int confirm_action("):
               ui.index("void gui_usb_confirm_run(")]
ui_ports = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#define SYS_EVENT (1u << 31)
enum { ACTION_STD_OK = 1, ACTION_STD_CANCEL, ACTION_WPS_PLAY,
       BUTTON_MULTIMEDIA_PLAYPAUSE, DIALOG_ABORT, DIALOG_ACCEPT,
       DIALOG_CONTINUE, BUTTON_SELECT, BUTTON_MENU, BUTTON_PLAY,
       BUTTON_NONE, BUTTON_REL = 0x100, AUDIO_STATUS_PLAY = 0x200,
       AUDIO_STATUS_PAUSE = 0x400 };
#define LAST_ITEM_IN_LIST {0, 0, 0}
struct button_mapping { int action, button, prerequisite; };
struct dialog { bool backlight_on; };
static bool pending = true;
static int reply_count, pause_count, resume_count, system_events;
static int status = AUDIO_STATUS_PLAY;
static bool connect;
static bool usb_confirm_pending(uint32_t epoch)
{ assert(epoch == 42); return pending; }
static void usb_confirm_reply(uint32_t epoch, bool yes)
{ assert(epoch == 42); ++reply_count; connect = yes; }
static int audio_status(void) { return status; }
static void pause_action(bool update)
{ assert(update); ++pause_count; status |= AUDIO_STATUS_PAUSE; }
static void unpause_action(bool update)
{ assert(update); ++resume_count; status &= ~AUDIO_STATUS_PAUSE; }
static void default_event_handler(int action)
{ assert(action & SYS_EVENT); ++system_events; }
'''
ui_tests = r'''
int main(void)
{
    struct dialog d = {0}; uint32_t epoch = 42;
    const struct button_mapping *keys = confirm_keymap(0);
    assert(keys[0].button == BUTTON_SELECT && keys[0].action == ACTION_STD_OK);
    assert(keys[1].button == BUTTON_MENU && keys[1].action == ACTION_STD_CANCEL);
    assert(keys[2].button == (BUTTON_PLAY | BUTTON_REL));
    assert(keys[3].action == 0); /* no fallback mapping for wheel / other keys */
    for (int ignored = 20; ignored < 100; ++ignored)
        assert(confirm_action(&d, ignored, &epoch) == DIALOG_CONTINUE);
    assert(!reply_count && !pause_count && !resume_count && !system_events);
    /* A dark-screen SELECT/MENU only wakes the light, never chooses. */
    assert(!d.backlight_on);
    assert(confirm_action(&d, ACTION_STD_OK, &epoch) == DIALOG_CONTINUE);
    assert(confirm_action(&d, ACTION_STD_CANCEL, &epoch) == DIALOG_CONTINUE);
    assert(!reply_count && pending);
    /* Play/Pause keeps its behavior even when the light was off. */
    assert(confirm_action(&d, ACTION_WPS_PLAY, &epoch) == DIALOG_CONTINUE);
    assert(pause_count == 1 && (status & AUDIO_STATUS_PAUSE));
    confirm_action(&d, BUTTON_MULTIMEDIA_PLAYPAUSE, &epoch);
    assert(resume_count == 1 && !(status & AUDIO_STATUS_PAUSE));
    d.backlight_on = true;
    assert(confirm_action(&d, ACTION_STD_CANCEL, &epoch) == DIALOG_ACCEPT);
    assert(reply_count == 1 && !connect);
    assert(confirm_action(&d, ACTION_STD_OK, &epoch) == DIALOG_ACCEPT);
    assert(reply_count == 2 && connect);
    pending = false;
    assert(confirm_action(&d, ACTION_STD_OK, &epoch) == DIALOG_ABORT);
    assert(reply_count == 2);
    puts("production popup callbacks: dark wake-only SELECT/MENU, lit choices, ignored keys, dark Play/Pause, unplug PASS");
}
'''
c = OUT / "ui.c"
c.write_text(ui_ports + callbacks + ui_tests)
cmd[-3:] = [str(c), "-o", str(OUT / "ui")]
print(" ".join(cmd), flush=True)
subprocess.run(cmd, check=True)
subprocess.run([str(OUT / "ui")], check=True)
