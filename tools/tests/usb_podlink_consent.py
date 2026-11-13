"""Production USB close/profile/consent event regression; RAM-only ports."""
from pathlib import Path
import subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
s=(R/'firmware/usb.c').read_text()
api=s[s.index('bool usb_confirm_blocked(void)'):s.index('#endif\n#ifdef USB_ENABLE_HID',s.index('bool usb_confirm_blocked(void)'))]
profiles=s[s.index('static inline void usb_configure_drivers(int for_state)'):s.index('static inline void usb_slave_mode',s.index('static inline void usb_configure_drivers(int for_state)'))]
confirm=s[s.index('        case USB_CONFIRM_READY:'):s.index('#endif\n\n#ifdef HAVE_USB_PODLINK',s.index('        case USB_CONFIRM_READY:'))]
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
       USB_CONFIRM_CONNECT, USB_CONFIRM_CHARGE, USB_PODLINK_CLOSE,SYS_USB_CONFIRM };
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
static int usb_state,usb_mode;
static bool enabled[USB_NUM_DRIVERS],usb_hid,usb_serial,usb_iap=true,usb_car_no_audio;
static int usb_audio;static struct {int usb_audio;}global_settings;
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
 puts("lease release: disk hidden/fresh epoch/SELECT/MENU/Charge/Drive-only/stale close+choice/unplug PASS");
}
'''.replace('EVENTS',confirm+'\n'+close)
f=O/'lease.c';f.write_text(pre+api+profiles+post)
cmd=['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie','-I'+str(R/'firmware/export'),str(f),'-o',str(O/'lease')]
subprocess.run(cmd,check=True);subprocess.run([str(O/'lease')],check=True)
