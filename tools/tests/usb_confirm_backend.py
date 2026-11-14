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
static bool usb_disk_ejected;
static int prompts, detach_count, attach_count, normal_profiles;
static int usb_detect(void) { return cable ? USB_INSERTED : USB_EXTRACTED; }
bool iap_accessory_present(void) { return serial_iap; }
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
static void serial_case(int arrival, bool connect)
{
    begin(false, false);
    serial_iap = arrival == 0;
    usb_confirm_configured(false, false, false);
    usb_host_present = true;
    usb_confirm_configured(true, false, true);
    if (arrival == 1)
        serial_iap = true;
    printf("serial case %d, connect %d: classification\n", arrival, connect);
    fflush(stdout);
    tick();
    assert(prompts == 1);
    if (arrival == 2)
        serial_iap = true;
    assert(usb_confirm_pending(usb_consent.epoch));
    /* More frames and repeated SET_CONFIG must not close the question. */
    usb_confirm_configured(true, false, false); drain();
    assert(usb_confirm_pending(usb_consent.epoch));
    assert(prompts == 1 && usb_confirm_blocked());
    usb_confirm_reply(usb_consent.epoch, connect); drain();
    assert(!usb_confirm_pending(usb_consent.epoch));
    assert(usb_confirm_blocked() == !connect);
    assert(normal_profiles == (int)connect);
    assert(detach_count == (int)connect && attach_count == (int)connect);
    assert(serial_iap); /* USB consent does not turn the serial transport off. */
}
int main(void)
{
#ifdef SERIAL_CASE
    serial_case(SERIAL_CASE, false);
    serial_case(SERIAL_CASE, true);
    return 0;
#else
    for (int arrival = 0; arrival < 3; ++arrival)
    {
        serial_case(arrival, false);
        serial_case(arrival, true);
    }
#endif
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
    for (int accessory = 0; accessory < 2; ++accessory)
    {
        begin(false, false); usb_host_present = true;
        usb_confirm_configured(true, false, true);
        if (accessory == 0) { car = true; tick(); }
        if (accessory == 1) usb_confirm_configured(true, true, false);
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
failures = []
def run_test(binary):
    result = subprocess.run([str(binary)])
    if result.returncode:
        failures.append((binary.name, result.returncode))
run_test(OUT / "backend")
for arrival in range(3):
    binary = OUT / ("serial-" + str(arrival))
    variant = cmd[:-3] + ["-DSERIAL_CASE=" + str(arrival), str(c), "-o", str(binary)]
    print(" ".join(variant), flush=True)
    subprocess.run(variant, check=True)
    run_test(binary)

ui = (ROOT / "apps-ipod/widgets/usb_confirm.c").read_text()
callbacks = ui[ui.index("static int confirm_action("):
               ui.index("void gui_usb_confirm_run(")]
ui_ports = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#define SYS_EVENT (1u << 31)
#define IPOD_ACCESSORY_PROTOCOL
#define BUTTON_MULTIMEDIA (1u << 28)
enum { ACTION_STD_OK = 1, ACTION_STD_CANCEL, ACTION_WPS_PLAY,
       DIALOG_ABORT, DIALOG_ACCEPT,
       DIALOG_CONTINUE, BUTTON_SELECT, BUTTON_MENU, BUTTON_PLAY,
       BUTTON_NONE, BUTTON_REL = 0x100, AUDIO_STATUS_PLAY = 0x200,
       AUDIO_STATUS_PAUSE = 0x400, BUTTON_REPEAT = 0x800,
       BUTTON_RC_PLAY = 0x10000, BUTTON_RC_STOP = 0x20000,
       BUTTON_RC_LEFT = 0x40000, BUTTON_RC_RIGHT = 0x80000,
       BUTTON_RC_VOL_UP = 0x100000, BUTTON_RC_VOL_DOWN = 0x200000,
       BUTTON_RC_MENU = 0x400000, BUTTON_RC_SELECT = 0x800000,
       BUTTON_MULTIMEDIA_PLAYPAUSE = BUTTON_MULTIMEDIA | 1,
       BUTTON_MULTIMEDIA_STOP = BUTTON_MULTIMEDIA | 2,
       BUTTON_MULTIMEDIA_PREV = BUTTON_MULTIMEDIA | 4,
       BUTTON_MULTIMEDIA_NEXT = BUTTON_MULTIMEDIA | 8,
       BUTTON_MULTIMEDIA_VOLUME_UP = BUTTON_MULTIMEDIA | 0x40,
       BUTTON_MULTIMEDIA_VOLUME_DOWN = BUTTON_MULTIMEDIA | 0x80 };
#include "usb_confirm.h"
#define LAST_ITEM_IN_LIST {0, 0, 0}
struct button_mapping { int action, button, prerequisite; };
struct dialog { bool backlight_on; };
static bool cable = true, serial_iap;
static struct usb_consent usb_consent = { USB_CONSENT_PROMPT, 42 };
static int reply_count, pause_count, resume_count, system_events, remote_events;
static int status = AUDIO_STATUS_PLAY;
static bool connect;
static int usb_detect(void) { return cable ? 1 : 0; }
#define USB_INSERTED 1
bool iap_accessory_present(void) { return serial_iap; }
PRODUCTION_PENDING
static void usb_confirm_reply(uint32_t epoch, bool yes)
{ assert(epoch == 42); ++reply_count; connect = yes; }
static int audio_status(void) { return status; }
static void pause_action(bool update)
{ assert(update); ++pause_count; status |= AUDIO_STATUS_PAUSE; }
static void unpause_action(bool update)
{ assert(update); ++resume_count; status &= ~AUDIO_STATUS_PAUSE; }
static void default_event_handler(int action)
{
    if (action & SYS_EVENT) ++system_events;
    else { assert(action & BUTTON_MULTIMEDIA); ++remote_events; }
}
'''
pending_start = source.index("bool usb_confirm_pending(uint32_t epoch)")
pending_end = source.index("void usb_confirm_reply(", pending_start)
ui_ports = ui_ports.replace("PRODUCTION_PENDING", source[pending_start:pending_end])
ui_tests = r'''
static int key_action(int button, int prerequisite)
{
    const struct button_mapping *keys = confirm_keymap(0);
    for (; keys->action; ++keys)
        if (keys->button == button &&
            (keys->prerequisite == BUTTON_NONE ||
             keys->prerequisite == prerequisite))
            return keys->action;
    return 0;
}
int main(void)
{
    struct dialog d = {0}; uint32_t epoch = 42;
    const struct button_mapping *keys = confirm_keymap(0);
    assert(keys[0].button == BUTTON_SELECT && keys[0].action == ACTION_STD_OK);
    assert(keys[1].button == BUTTON_MENU && keys[1].action == ACTION_STD_CANCEL);
    assert(keys[2].button == (BUTTON_PLAY | BUTTON_REL));
    assert(key_action(BUTTON_RC_MENU, BUTTON_NONE) == ACTION_STD_CANCEL);
    assert(key_action(BUTTON_RC_MENU | BUTTON_REL, BUTTON_RC_MENU) == 0);
    d.backlight_on = true;
    assert(confirm_action(&d, key_action(BUTTON_RC_MENU, BUTTON_NONE), &epoch)
           == DIALOG_ACCEPT);
    assert(reply_count == 1 && !connect);
    reply_count = 0; d.backlight_on = false;
    /* Serial frames arriving during the actual popup must not abort it. */
    serial_iap = true;
    assert(usb_confirm_pending(epoch));
    for (int ignored = 20; ignored < 100; ++ignored)
        assert(confirm_action(&d, ignored, &epoch) == DIALOG_CONTINUE);
    assert(!reply_count && !pause_count && !resume_count && !system_events);
    /* A dark-screen SELECT/MENU only wakes the light, never chooses. */
    assert(!d.backlight_on);
    assert(confirm_action(&d, ACTION_STD_OK, &epoch) == DIALOG_CONTINUE);
    assert(confirm_action(&d, ACTION_STD_CANCEL, &epoch) == DIALOG_CONTINUE);
    assert(!reply_count && usb_confirm_pending(epoch));
    /* Play/Pause keeps its behavior even when the light was off. */
    assert(confirm_action(&d, ACTION_WPS_PLAY, &epoch) == DIALOG_CONTINUE);
    assert(pause_count == 1 && (status & AUDIO_STATUS_PAUSE));
    confirm_action(&d, BUTTON_MULTIMEDIA_PLAYPAUSE, &epoch);
    assert(resume_count == 1 && !(status & AUDIO_STATUS_PAUSE));
    /* A remote Play tap is SELECT: on a dark screen it only wakes. */
    assert(key_action(BUTTON_RC_PLAY | BUTTON_REL, BUTTON_RC_PLAY) ==
           ACTION_STD_OK);
    assert(confirm_action(&d, key_action(BUTTON_RC_PLAY | BUTTON_REL,
                                         BUTTON_RC_PLAY), &epoch) ==
           DIALOG_CONTINUE);
    assert(!reply_count && pause_count == 1 && resume_count == 1);
    const struct button_mapping remote[] = {
        { BUTTON_MULTIMEDIA_STOP, BUTTON_RC_PLAY | BUTTON_REPEAT, BUTTON_RC_PLAY },
        { BUTTON_MULTIMEDIA_STOP, BUTTON_RC_STOP, BUTTON_NONE },
        { BUTTON_MULTIMEDIA_NEXT, BUTTON_RC_RIGHT | BUTTON_REL, BUTTON_RC_RIGHT },
        { BUTTON_MULTIMEDIA_PREV, BUTTON_RC_LEFT | BUTTON_REL, BUTTON_RC_LEFT },
        { BUTTON_MULTIMEDIA_VOLUME_UP, BUTTON_RC_VOL_UP, BUTTON_NONE },
        { BUTTON_MULTIMEDIA_VOLUME_UP, BUTTON_RC_VOL_UP | BUTTON_REPEAT, BUTTON_RC_VOL_UP },
        { BUTTON_MULTIMEDIA_VOLUME_DOWN, BUTTON_RC_VOL_DOWN, BUTTON_NONE },
        { BUTTON_MULTIMEDIA_VOLUME_DOWN, BUTTON_RC_VOL_DOWN | BUTTON_REPEAT, BUTTON_RC_VOL_DOWN },
    };
    for (unsigned i = 0; i < sizeof(remote) / sizeof(remote[0]); ++i)
    {
        int action = key_action(remote[i].button, remote[i].prerequisite);
        assert(action == remote[i].action);
        assert(confirm_action(&d, action, &epoch) == DIALOG_CONTINUE);
    }
    assert(remote_events == 8 && !reply_count && usb_confirm_pending(epoch));
    assert(key_action(BUTTON_RC_MENU, BUTTON_NONE) == ACTION_STD_CANCEL);
    assert(!key_action(BUTTON_RC_SELECT, BUTTON_NONE));
    d.backlight_on = true;
    assert(confirm_action(&d, ACTION_STD_CANCEL, &epoch) == DIALOG_ACCEPT);
    assert(reply_count == 1 && !connect);
    assert(confirm_action(&d, ACTION_STD_OK, &epoch) == DIALOG_ACCEPT);
    assert(reply_count == 2 && connect);
    cable = false;
    assert(confirm_action(&d, ACTION_STD_OK, &epoch) == DIALOG_ABORT);
    assert(reply_count == 2);
    puts("production popup callbacks: serial frames, remote playback keys, dark wake-only SELECT/MENU, lit choices, unplug PASS");
}
'''
c = OUT / "ui.c"
c.write_text(ui_ports + callbacks + ui_tests)
cmd[-3:] = [str(c), "-o", str(OUT / "ui")]
print(" ".join(cmd), flush=True)
subprocess.run(cmd, check=True)
run_test(OUT / "ui")
if failures:
    print("FAILED:", failures, flush=True)
    sys.exit(1)
