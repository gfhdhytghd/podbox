"""FU29: real consent/UI/profile/storage decisions, with RAM USB/queue ports.

Unlike the profile-only test, run the production exclusive-storage gate and
ACK handler, and model the controller's disable/init/configure sequence.
No USB device, serial port, host service, or installed files are accessed.
"""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(sys.argv[1]).resolve()
SOURCE = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else ROOT
OUT.mkdir(parents=True, exist_ok=True)


def fn(text, name):
    m = re.search(r"(?m)^[A-Za-z_][\w \t*]*\b" + name +
                  r"\([^;{}]*\)\s*\{", text)
    assert m, name
    end, depth = m.end(), 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[m.start():end]


usb = (SOURCE / "firmware/usb.c").read_text()
ui = (SOURCE / "apps-ipod/widgets/usb_confirm.c").read_text()
screen = (SOURCE / "apps-ipod/screens/system/agents_screen.c").read_text()
events = usb[usb.index("        case USB_CONFIRM_READY:"):
             usb.index("#endif\n\n#ifdef HAVE_USB_PODLINK",
                       usb.index("        case USB_CONFIRM_READY:"))]
ack = usb[usb.index("        case SYS_USB_CONNECTED_ACK:"):
          usb.index("        case USB_EXTRACTED:",
                    usb.index("        case SYS_USB_CONNECTED_ACK:"))]
api = usb[usb.index("bool usb_confirm_blocked(void)"):
          usb.index("#endif\n#ifdef USB_ENABLE_HID",
                    usb.index("bool usb_confirm_blocked(void)"))]
profile = fn(usb, "usb_configure_drivers")
keymap = fn(ui, "confirm_keymap")
# Only enum identities are mocked; the complete production key table is used.
identities = sorted(set(re.findall(r"\b(?:ACTION_|BUTTON_)[A-Z_0-9]+\b",
                                   keymap + fn(ui, "confirm_action"))))
identities.remove("BUTTON_NONE")
identities.remove("BUTTON_MULTIMEDIA")
pre = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include "usb_confirm.h"
#include "usb_profile.h"
#define HAVE_USB_CONFIRM
#define HAVE_USB_PODLINK
#define USB_ENABLE_STORAGE
#define USB_ENABLE_SERIAL
#define USB_ENABLE_HID
#define USB_ENABLE_AUDIO
#define USB_ENABLE_IAP
#define USB_ENABLE_CHARGING_ONLY
#define IPOD_ACCESSORY_PROTOCOL
#define HZ 100
#define BUTTON_NONE 0
#define SYS_EVENT 0x80000000u
#define BUTTON_MULTIMEDIA 0x10000000u
#define AUDIO_STATUS_PLAY 1
#define AUDIO_STATUS_PAUSE 2
#define LAST_ITEM_IN_LIST {0,0,0}
#define DEBUGF(...) do {} while (0)
enum { USB_EXTRACTED, USB_INSERTED, USB_POWERED, USB_CONFIRM_READY,
       USB_CONFIRM_CONNECT, USB_CONFIRM_CHARGE, USB_CONFIRM_REOPEN,
       SYS_USB_CONFIRM, SYS_USB_CONNECTED, SYS_USB_CONNECTED_ACK,
       SYS_USB_DISCONNECTED };
enum { USB_MODE_MASS_STORAGE, USB_MODE_CHARGE, USB_MODE_DRIVE_ONLY };
enum { USB_DRIVER_MASS_STORAGE, USB_DRIVER_SERIAL, USB_DRIVER_CHARGING_ONLY,
       USB_DRIVER_HID, USB_DRIVER_AUDIO, USB_DRIVER_IAP, USB_NUM_DRIVERS };
enum { AGENTS_EXIT_MENU, AGENTS_SWITCH_WPS = 2 };
enum { DIALOG_CONTINUE, DIALOG_ACCEPT, DIALOG_ABORT };
struct dialog { bool backlight_on; };
struct button_mapping {int action,button,pre;};
struct event_queue {int unused;};
struct queue_event {int id;intptr_t data;};
struct timeout {intptr_t data;bool active;};
static struct event_queue usb_queue;
static struct timeout usb_confirm_tmo;
static struct usb_consent usb_consent;
static struct queue_event pending[128];
static unsigned int head,tail;
static bool usb_host_present,usb_confirm_has_config,podlink_requested;
static bool exclusive_storage_enabled,exclusive_storage_requested;
static bool usb_disk_ejected,usb_host_probe_on,usb_power_only,usb_drive_only_now;
static bool car,unplug_during_sleep,cable,enabled[USB_NUM_DRIVERS];
static bool usb_hid,usb_iap=true,usb_car_no_audio;
static int usb_mode,usb_audio,usb_num_acks_to_expect;
static uint32_t usb_broadcast_seqnum;
static int current_tick,prompts,detaches,attaches,broadcasts,ui_events,handovers;
static struct {int usb_audio;} global_settings;
static struct {int acks_expected,broadcast_tick;} usb_record;
static void queue_post(struct event_queue*q,int id,intptr_t data)
{(void)q;assert(tail<128);pending[tail++]=(struct queue_event){id,data};}
static int usb_detect(void){return cable?USB_INSERTED:USB_EXTRACTED;}
static bool usb_mode_drive_only(void){return usb_mode==USB_MODE_DRIVE_ONLY;}
static bool usb_confirm_blocked_unused __attribute__((unused));
bool usb_confirm_blocked(void);
static bool usb_drive_only(void){return usb_drive_only_now&&!usb_confirm_blocked();}
static bool usb_iap2_car_seen(void){return car;}
static bool usb_iap_answer_iap2(void){return car;}
static bool usb_audio_wanted(void){return usb_audio!=0;}
static void usb_iap2_host_new(void){car=false;}
static int audio_status(void){return 0;}
static void pause_action(bool on){(void)on;}
static void unpause_action(bool on){(void)on;}
static void default_event_handler(int action){(void)action;}
static void timeout_cancel(struct timeout*t){t->active=false;}
static void timeout_register(struct timeout*t,int(*cb)(struct timeout*),int n,intptr_t d)
{(void)cb;assert(n==HZ);t->data=d;t->active=true;}
static void button_queue_post(int id,intptr_t data)
{assert(id==SYS_USB_CONFIRM&&data==usb_consent.epoch);prompts++;}
static void usb_slave_mode(bool on)
{if(on){assert(!podlink_requested&&!usb_confirm_blocked());handovers++;}}
static void usb_signal_class_notify(int driver,int value)
{assert(driver==USB_DRIVER_MASS_STORAGE&&value==0);}
/* USB queue itself is counted but does not ACK. UI + Cider + another worker
 * stand in for the broadcast subscribers; a handover waits for all three. */
static int queue_broadcast(int id,intptr_t data)
{if(id==SYS_USB_CONNECTED){assert(data==usb_broadcast_seqnum);broadcasts++;ui_events++;}return 4;}
static void usb_core_enable_driver(int driver,bool on){enabled[driver]=on;}
static void usb_attach(void){attaches++;}
static void usb_stack_enable(bool on);
static void sleep(int ticks)
{assert(ticks==HZ/5);current_tick+=ticks;if(unplug_during_sleep)cable=false;}
'''
pre += "\nenum { MOCK_ID_START, " + ",".join(identities) + "};\n"
post = r'''
static void usb_stack_enable(bool on)
{
    if(!on){detaches++;usb_confirm_configured(false,false,false);usb_release_exclusive_storage();}
    else for(int i=0;i<USB_NUM_DRIVERS;i++)enabled[i]=false;
}
static void drain(void)
{
    while(head<tail){struct queue_event ev=pending[head++];switch(ev.id){
@EVENTS@
@ACK@
        default:assert(false);
    }}head=tail=0;
}
/* Host selects storage after the soft reconnect; do not stub the gate out. */
static void host_configure(void)
{
    usb_confirm_configured(true,false,false);drain();
    if(enabled[USB_DRIVER_MASS_STORAGE])usb_request_exclusive_storage();
}
static char serial(void)
{unsigned int mask=0;for(int i=0;i<USB_NUM_DRIVERS;i++)if(enabled[i])mask|=1u<<i;return usb_profile_serial_prefix(mask);}
static void begin(int mode,bool ejected)
{
    head=tail=prompts=attaches=detaches=broadcasts=ui_events=handovers=0;
    current_tick=0;usb_mode=mode;usb_disk_ejected=ejected;
    usb_host_present=cable=true;car=unplug_during_sleep=false;
    usb_host_probe_on=exclusive_storage_enabled=exclusive_storage_requested=false;
    usb_power_only=mode==USB_MODE_CHARGE;usb_drive_only_now=usb_mode_drive_only();
    usb_consent_begin(&usb_consent,usb_power_only,false);
    usb_configure_drivers(USB_INSERTED);host_configure();
}
static void press(int button)
{
    const struct button_mapping*m=confirm_keymap(0);
    for(;m->button;m++)if(m->button==button)break;
    assert(m->button);struct dialog d={true};uint32_t epoch=usb_consent.epoch;
    assert(confirm_action(&d,m->action,&epoch)==DIALOG_ACCEPT);drain();
}
static void select_case(bool ejected)
{
    begin(USB_MODE_MASS_STORAGE,ejected);
    press(BUTTON_MENU); /* resident Agents session after the original MENU */
    assert(serial()=='2'&&podlink_requested&&!broadcasts);
    assert(!exclusive_storage_requested); /* no implicit disk access */
    agents_usb_exit(AGENTS_SWITCH_WPS);drain();assert(prompts==1);
    agents_usb_exit(AGENTS_EXIT_MENU);drain();
    assert(prompts==2&&usb_confirm_pending(usb_consent.epoch));
    press(BUTTON_SELECT);
    assert(usb_consent.state==USB_CONSENT_CONNECT);
    assert(serial()=='1'&&!podlink_requested&&detaches==1);
    host_configure();
    printf("FU29 re-ask SELECT: prior eject=%d, broadcasts=%d, UI events=%d\n",ejected,broadcasts,ui_events);fflush(stdout);
    assert(broadcasts==1&&ui_events==1&&exclusive_storage_requested);
    assert(!handovers); /* late Cider ACK cannot suppress the USB screen */
    queue_post(&usb_queue,SYS_USB_CONNECTED_ACK,usb_broadcast_seqnum-1);drain();
    assert(usb_num_acks_to_expect==3&&!handovers);
    for(int n=0;n<3;n++){
        queue_post(&usb_queue,SYS_USB_CONNECTED_ACK,usb_broadcast_seqnum);drain();
        assert(handovers==(n==2));
    }
    assert(exclusive_storage_enabled&&current_tick<=2*HZ);
    usb_request_exclusive_storage();assert(broadcasts==1); /* repeated config */
}
int main(int argc,char**argv)
{
    (void)argv;
    select_case(false);
    if(argc>1)select_case(true);
    begin(USB_MODE_MASS_STORAGE,true);press(BUTTON_MENU);
    agents_usb_exit(AGENTS_EXIT_MENU);drain();press(BUTTON_MENU);
    assert(usb_disk_ejected&&!broadcasts&&podlink_requested&&serial()=='2'&&!detaches);
    begin(USB_MODE_MASS_STORAGE,false);press(BUTTON_MENU);
    agents_usb_exit(AGENTS_EXIT_MENU);drain();
    usb_confirm_reply(usb_consent.epoch-1,true);drain();assert(!broadcasts&&!detaches);
    unplug_during_sleep=true;press(BUTTON_SELECT);assert(!broadcasts&&!cable);
    puts("FU29 physical SELECT/MENU mapping, fresh/ejected re-ask, silent WPS, stale choice, unplug, all-ACK gate PASS");
}
'''.replace("@EVENTS@", events).replace("@ACK@", ack)
parts = [api, profile, fn(usb, "usb_request_exclusive_storage"),
         fn(usb, "usb_release_exclusive_storage"), fn(screen, "agents_usb_exit"),
         fn(ui, "confirm_action"), keymap]
cfile = OUT / "after-agents.c"
cfile.write_text(pre + "\n".join(parts) + post)
binary = OUT / "after-agents"
cmd = ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-g",
       "-fsanitize=address,undefined", "-fno-pie", "-no-pie",
       "-I" + str(SOURCE / "firmware/export"), str(cfile), "-o", str(binary)]
print(" ".join(cmd), flush=True)
subprocess.run(cmd, check=True)
subprocess.run([str(binary), "with-prior-eject"], check=True)
