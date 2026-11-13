"""Production USB close/profile/consent event regression; RAM-only ports."""
from pathlib import Path
import subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
S=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else R
s=(S/'firmware/usb.c').read_text()
api=s[s.index('bool usb_confirm_blocked(void)'):s.index('#endif\n#ifdef USB_ENABLE_HID',s.index('bool usb_confirm_blocked(void)'))]
profiles=s[s.index('static inline void usb_configure_drivers(int for_state)'):s.index('static inline void usb_slave_mode',s.index('static inline void usb_configure_drivers(int for_state)'))]
confirm=s[s.index('        case USB_CONFIRM_READY:'):s.index('#endif\n\n#ifdef HAVE_USB_PODLINK',s.index('        case USB_CONFIRM_READY:'))]
open_event=s[s.index('        case USB_PODLINK_OPEN:'):s.index('        case USB_PODLINK_CLOSE:')] if '        case USB_PODLINK_OPEN:' in s else ''
open_api=s[s.index('bool usb_podlink_open(void)'):s.index('void usb_podlink_close(void)')]
close=s[s.index('        case USB_PODLINK_CLOSE:'):s.index('#endif\n        case USB_INSERTED:',s.index('        case USB_PODLINK_CLOSE:'))]
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdint.h>
#include "usb_confirm.h"
#include "usb_profile.h"
#define HZ 100
#define HAVE_USB_CONFIRM
#define HAVE_USB_PODLINK
#define USB_ENABLE_STORAGE
#define USB_ENABLE_SERIAL
#define USB_ENABLE_HID
#define USB_ENABLE_AUDIO
#define USB_ENABLE_IAP
#define USB_ENABLE_CHARGING_ONLY
#define USB_MODE_DRIVE_ONLY 2
enum { USB_EXTRACTED, USB_INSERTED, USB_POWERED, USB_CONFIRM_READY,
       USB_CONFIRM_CONNECT, USB_CONFIRM_CHARGE, USB_PODLINK_CLOSE,SYS_USB_CONFIRM,USB_PODLINK_OPEN };
enum {USB_MODE_MASS_STORAGE,USB_MODE_CHARGE};
enum {USB_DRIVER_MASS_STORAGE,USB_DRIVER_HID,USB_DRIVER_AUDIO,USB_DRIVER_SERIAL,
      USB_DRIVER_IAP,USB_DRIVER_CHARGING_ONLY,USB_NUM_DRIVERS};
struct event_queue {int unused;};struct timeout {intptr_t data;bool active;};
struct event {int id;intptr_t data;} pending[64];
static int head,tail,prompts,attached,detached;
static struct event_queue usb_queue;static struct timeout usb_confirm_tmo;
static struct usb_consent usb_consent;
static bool usb_confirm_has_config,usb_host_present,cable,car,unplug_during_sleep;
static bool podlink_requested,usb_power_only,usb_drive_only_now;
static uint32_t podlink_epoch;
static bool exclusive_storage_enabled,exclusive_storage_requested,usb_host_probe_on;
static inline int disable_irq_save(void){return 0;}
static inline void restore_irq(int n){(void)n;}
static int usb_state,usb_mode;
static bool enabled[USB_NUM_DRIVERS],usb_hid,usb_serial,usb_iap=true,usb_car_no_audio;
static int usb_audio;static struct {int usb_audio;}global_settings;
static bool usb_podlink_active(void){return podlink_requested;}
static int usb_detect(void){return cable?USB_INSERTED:USB_EXTRACTED;}
static bool usb_iap2_car_seen(void){return car;}
static bool usb_iap_answer_iap2(void){return car;}
static bool usb_audio_wanted(void){return usb_audio!=0;}
static void usb_iap2_host_new(void){car=false;}
static bool usb_mode_drive_only(void){return usb_mode==USB_MODE_DRIVE_ONLY;}
static bool usb_drive_only(void){return usb_drive_only_now&&!usb_confirm_blocked();}
static void usb_attach(void){attached++;}
static void usb_core_enable_driver(int i,bool on){enabled[i]=on;}
static void queue_post(struct event_queue *q,int id,intptr_t data)
{(void)q;assert(tail<64);pending[tail++]=(struct event){id,data};}
static void timeout_cancel(struct timeout*t){t->active=false;}
static void timeout_register(struct timeout*t,int(*cb)(struct timeout*),int n,intptr_t d)
{(void)cb;assert(n==HZ);t->active=true;t->data=d;}
static void button_queue_post(int id,intptr_t data)
{assert(id==SYS_USB_CONFIRM&&data==usb_consent.epoch);prompts++;}
static void usb_stack_enable(bool on);
static void usb_configure_drivers(int state);
static void sleep(int ticks)
{assert(ticks==HZ/5);if(unplug_during_sleep){cable=false;usb_state=USB_EXTRACTED;}}
'''
# Forward declaration is needed by the real profile-selector port above.
pre=pre.replace('static bool usb_drive_only(void)', 'bool usb_confirm_blocked(void);\nstatic bool usb_drive_only(void)')
post=r'''
static void usb_stack_enable(bool on)
{if(!on){detached++;usb_confirm_configured(false,false,false);}}
static void event(int id,intptr_t data)
{queue_post(&usb_queue,id,data);}
static void drain(void){while(head<tail){struct event ev=pending[head++];switch(ev.id){
EVENTS
 default:assert(0);}}head=tail=0;}
OPEN_API
static uint32_t begin(int mode)
{
 head=tail=prompts=attached=detached=0;car=unplug_during_sleep=false;
 usb_mode=mode;usb_power_only=mode==USB_MODE_CHARGE;
 usb_drive_only_now=usb_mode_drive_only();usb_state=USB_INSERTED;
 cable=usb_host_present=podlink_requested=true;podlink_epoch++;
 usb_consent_begin(&usb_consent,usb_power_only,true);
 usb_confirm_has_config=true;usb_confirm_tmo.active=false;
 usb_configure_drivers(USB_INSERTED);
 assert(enabled[USB_DRIVER_SERIAL]&&!enabled[USB_DRIVER_MASS_STORAGE]);
 return usb_consent.epoch;
}
int main(void)
{
 for(int mode=0;mode<3;mode++)for(int connect=0;connect<2;connect++)for(int probe=0;probe<2;probe++){
  uint32_t prior=begin(mode);
  event(USB_PODLINK_CLOSE,podlink_epoch);drain();
  assert(!enabled[USB_DRIVER_MASS_STORAGE]);
  assert(!podlink_requested&&usb_consent.epoch!=prior);
  assert(!prompts&&!usb_confirm_has_config);
  event(USB_CONFIRM_CONNECT,prior);event(USB_CONFIRM_READY,prior);drain();
  assert(!prompts&&!enabled[USB_DRIVER_MASS_STORAGE]);
  usb_confirm_configured(true,false,probe);
  if(usb_confirm_tmo.active){usb_confirm_tmo.active=false;usb_confirm_tmo_cb(&usb_confirm_tmo);}
  drain();
  if(mode==USB_MODE_CHARGE){assert(!prompts&&enabled[USB_DRIVER_CHARGING_ONLY]);continue;}
  assert(prompts==1&&usb_confirm_pending(usb_consent.epoch));
  event(connect?USB_CONFIRM_CONNECT:USB_CONFIRM_CHARGE,usb_consent.epoch);drain();
  assert(enabled[USB_DRIVER_MASS_STORAGE]==(bool)connect);
  usb_confirm_configured(true,false,false);drain();assert(prompts==1);
 }
 uint32_t old=begin(0);event(USB_PODLINK_CLOSE,podlink_epoch-1);drain();
 assert(podlink_requested&&usb_consent.epoch==old);
 unplug_during_sleep=true;event(USB_PODLINK_CLOSE,podlink_epoch);drain();
 assert(!prompts&&!enabled[USB_DRIVER_MASS_STORAGE]&&usb_state==USB_EXTRACTED);
 old=begin(0);event(USB_PODLINK_CLOSE,podlink_epoch);drain();
 int count=attached;event(USB_PODLINK_CLOSE,podlink_epoch);drain();assert(count==attached);
 assert(usb_consent.epoch!=old);
 for(int mode=0;mode<3;mode++)for(int state=USB_CONSENT_BYPASS;state<=USB_CONSENT_ACCESSORY;state++)for(int link=USB_INSERTED;link<=USB_POWERED;link++) {
  begin(mode);podlink_requested=false;usb_consent.state=state;usb_state=link;
  usb_host_present=link==USB_INSERTED;
  usb_configure_drivers(USB_POWERED);int prior_epoch=usb_consent.epoch;
  uint32_t old_lease=podlink_epoch;
  event(USB_CONFIRM_CONNECT,prior_epoch);event(USB_CONFIRM_READY,prior_epoch);
  assert(usb_podlink_open());assert(podlink_requested&&podlink_epoch!=old_lease);
  assert(!usb_confirm_pending(prior_epoch));
  event(USB_CONFIRM_CONNECT,prior_epoch);event(USB_CONFIRM_READY,prior_epoch);drain();
  assert(!enabled[USB_DRIVER_MASS_STORAGE]&&enabled[USB_DRIVER_SERIAL]);
  assert(usb_consent.epoch!=(uint32_t)prior_epoch&&!prompts);
  assert(detached==1);usb_host_present=true;usb_state=USB_INSERTED;usb_confirm_configured(true,false,false);drain();assert(!prompts);
  assert(!usb_podlink_open());
  event(USB_PODLINK_CLOSE,podlink_epoch);drain();assert(!enabled[USB_DRIVER_MASS_STORAGE]);
  usb_confirm_configured(true,false,false);drain();
  assert(prompts==(mode==USB_MODE_CHARGE?0:1));
 }
 begin(0);podlink_requested=false;exclusive_storage_enabled=true;
 assert(!usb_podlink_open());exclusive_storage_enabled=false;
 exclusive_storage_requested=true;assert(!usb_podlink_open());exclusive_storage_requested=false;
 usb_host_probe_on=true;assert(!usb_podlink_open());usb_host_probe_on=false;
 begin(0);podlink_requested=false;unplug_during_sleep=true;
 assert(usb_podlink_open());drain();assert(usb_state==USB_EXTRACTED&&!enabled[USB_DRIVER_MASS_STORAGE]);
 begin(0);podlink_requested=false;assert(usb_podlink_open());uint32_t first=podlink_epoch;
 event(USB_PODLINK_CLOSE,first);drain();
 assert(usb_podlink_open());event(USB_PODLINK_OPEN,first);event(USB_PODLINK_CLOSE,first);drain();
 assert(podlink_requested&&enabled[USB_DRIVER_SERIAL]&&!enabled[USB_DRIVER_MASS_STORAGE]);
 puts("open while plugged: all consent states/Charge/Drive-only/lease+disk refusal/stale events/unplug PASS");
 puts("lease release: disk hidden/fresh epoch/SELECT/MENU/Charge/Drive-only/stale close+choice/unplug PASS");
}
'''.replace('EVENTS',confirm+'\n'+open_event+'\n'+close)
post=post.replace('OPEN_API',open_api)
f=O/'lease.c';f.write_text(pre+api+profiles+post)
cmd=['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie','-I'+str(R/'firmware/export'),str(f),'-o',str(O/'lease')]
subprocess.run(cmd,check=True);subprocess.run([str(O/'lease')],check=True)
