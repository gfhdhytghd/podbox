"""Cable-owned resident USB profile: production selector/events, fake controller."""
from pathlib import Path
import subprocess, sys
R=Path(__file__).resolve().parents[2]
O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
S=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else R
s=(S/'firmware/usb.c').read_text()
api=s[s.index('bool usb_confirm_blocked(void)'):s.index('#endif\n#ifdef USB_ENABLE_HID',s.index('bool usb_confirm_blocked(void)'))]
profiles=s[s.index('static inline void usb_configure_drivers(int for_state)'):s.index('static inline void usb_slave_mode',s.index('static inline void usb_configure_drivers(int for_state)'))]
events=s[s.index('        case USB_CONFIRM_READY:'):s.index('#endif\n\n#ifdef HAVE_USB_PODLINK',s.index('        case USB_CONFIRM_READY:'))]
clients=s[s.index('bool usb_podlink_open(void)'):s.index('bool usb_podlink_active(void)')]
mode_events=s[s.index('        case USB_PODLINK_OPEN:'):s.index('#endif\n        case USB_INSERTED:',s.index('        case USB_PODLINK_OPEN:'))]

import re
screen=(S/'apps-ipod/screens/system/agents_screen.c').read_text()
widget=(S/'apps-ipod/widgets/usb_confirm.c').read_text()
def function(text,name):
 m=re.search(r'(?m)^[\w *]+\b'+name+r'\([^;{}]*\)\s*\{',text)
 assert m,name
 end=m.end();depth=1
 while depth:
  depth+=(text[end]=='{')-(text[end]=='}');end+=1
 return text[m.start():end]
exit_hook=function(screen,'agents_usb_exit') if 'static void agents_usb_exit(' in screen else 'static void agents_usb_exit(int result){(void)result;}'
if 'static void agents_usb_exit(' in screen:
 assert re.search(r'viewportmanager_theme_undo\(i, false\);\s*agents_usb_exit\(ret\);\s*return ret;',screen)
 assert re.search(r'case ACTION_AGENTS_SWITCH:.*?ret = AGENTS_SWITCH_WPS;.*?done = true;',screen,re.S)
popup=function(widget,'confirm_action')
pre=r"""
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
       USB_CONFIRM_CONNECT, USB_CONFIRM_CHARGE,USB_CONFIRM_REOPEN, USB_PODLINK_CLOSE,SYS_USB_CONFIRM,USB_PODLINK_OPEN };
enum {USB_MODE_MASS_STORAGE,USB_MODE_CHARGE};
enum {USB_DRIVER_MASS_STORAGE,USB_DRIVER_SERIAL,USB_DRIVER_CHARGING_ONLY,
      USB_DRIVER_HID,USB_DRIVER_AUDIO,USB_DRIVER_IAP,USB_NUM_DRIVERS};
struct event_queue {int unused;};struct timeout {intptr_t data;bool active;};
struct event {int id;intptr_t data;} pending[64];
static int head,tail,prompts,attached,detached;
static struct event_queue usb_queue;static struct timeout usb_confirm_tmo;
static struct usb_consent usb_consent;
static bool usb_confirm_has_config,usb_host_present,cable,car,unplug_during_sleep;
static bool podlink_requested,usb_power_only,usb_drive_only_now;

static bool exclusive_storage_enabled,exclusive_storage_requested,usb_host_probe_on;
static inline int disable_irq_save(void){return 0;}
static inline void restore_irq(int n){(void)n;}
static int usb_state,usb_mode;
static uint32_t podlink_epoch __attribute__((unused)), podlink_charge_epoch __attribute__((unused));
static bool enabled[USB_NUM_DRIVERS],usb_hid,usb_iap=true,usb_car_no_audio;
static bool usb_serial __attribute__((unused));
static int usb_audio;static struct {int usb_audio;}global_settings;
static bool usb_podlink_active(void){return podlink_requested;}
static int usb_detect(void){return cable?USB_INSERTED:USB_EXTRACTED;}
static bool usb_iap2_car_seen(void){return car;}
static bool usb_iap_answer_iap2(void){return car;}
static bool usb_audio_wanted(void){return usb_audio!=0;}
static void usb_iap2_host_new(void){car=false;}
static bool usb_mode_drive_only(void){return usb_mode==USB_MODE_DRIVE_ONLY;}
bool usb_confirm_blocked(void);
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
"""
post=r"""
static void usb_stack_enable(bool on)
{if(!on){detached++;usb_confirm_configured(false,false,false);}}
static void drain(void){while(head<tail){struct event ev=pending[head++];switch(ev.id){
EVENTS
 default:assert(0);}}head=tail=0;}
CLIENTS
static void begin(int mode)
{
 head=tail=prompts=attached=detached=0;car=unplug_during_sleep=false;
 usb_mode=mode;usb_power_only=mode==USB_MODE_CHARGE;
 usb_drive_only_now=usb_mode_drive_only();usb_state=USB_INSERTED;
 cable=usb_host_present=true;podlink_requested=false;
 exclusive_storage_enabled=exclusive_storage_requested=usb_host_probe_on=false;
 usb_consent_begin(&usb_consent,usb_power_only,false);
 usb_confirm_has_config=false;usb_confirm_tmo.active=false;
 usb_configure_drivers(usb_power_only?USB_POWERED:USB_INSERTED);
}
static char serial(void){unsigned int mask=0;for(int i=0;i<USB_NUM_DRIVERS;i++)if(enabled[i])mask|=1u<<i;return usb_profile_serial_prefix(mask);}
int main(void)
{

 /* S1: confirmed MENU exit reopens consent without resetting resident USB. */
 begin(0);usb_confirm_configured(true,false,false);drain();
 usb_confirm_reply(usb_consent.epoch,false);drain();
 int old_prompts=prompts,old_attach=attached,old_detach=detached;
 agents_usb_exit(0);agents_usb_exit(0);drain();assert(prompts==old_prompts+1);
 assert(usb_confirm_pending(usb_consent.epoch));
 struct dialog dialog={true};uint32_t answer=usb_consent.epoch;
 assert(confirm_action(&dialog,ACTION_STD_CANCEL,&answer)==DIALOG_ACCEPT);drain();
 assert(usb_consent.state==USB_CONSENT_CHARGE&&serial()=='2');
 assert(podlink_requested&&attached==old_attach&&detached==old_detach);
 old_prompts=prompts;assert(usb_podlink_open());drain();usb_podlink_close();drain();
 assert(prompts==old_prompts&&serial()=='2'&&podlink_requested);
 assert(attached==old_attach&&detached==old_detach);
 old_prompts=prompts;agents_usb_exit(AGENTS_SWITCH_WPS);drain();
 agents_usb_exit(AGENTS_SWITCH_WPS);drain();assert(prompts==old_prompts);
 agents_usb_exit(0);drain();answer=usb_consent.epoch;
 assert(confirm_action(&dialog,ACTION_STD_OK,&answer)==DIALOG_ACCEPT);drain();
 assert(enabled[USB_DRIVER_MASS_STORAGE]&&!podlink_requested&&serial()=='1');
 begin(0);usb_confirm_configured(true,false,false);drain();
 usb_confirm_reply(usb_consent.epoch,false);drain();
 agents_usb_exit(0);cable=false;old_prompts=prompts;drain();assert(prompts==old_prompts);
 begin(0);usb_confirm_configured(true,false,false);drain();
 usb_confirm_reply(usb_consent.epoch,false);drain();old_prompts=prompts;
 queue_post(&usb_queue,USB_CONFIRM_REOPEN,usb_consent.epoch-1);drain();
 assert(prompts==old_prompts&&usb_consent.state==USB_CONSENT_CHARGE);
 car=true;agents_usb_exit(0);drain();assert(prompts==old_prompts);car=false;
 usb_host_present=false;agents_usb_exit(0);drain();assert(prompts==old_prompts);
 for(int mode=0;mode<3;mode++)for(int probe=0;probe<2;probe++){
  begin(mode);assert(!enabled[USB_DRIVER_MASS_STORAGE]);
  assert(serial()==(mode==USB_MODE_CHARGE?'4':'2'));
  usb_confirm_configured(true,false,probe);
  if(usb_confirm_tmo.active){usb_confirm_tmo_cb(&usb_confirm_tmo);}
  drain();
  if(mode==USB_MODE_CHARGE){assert(!prompts&&!usb_podlink_open());continue;}
  assert(prompts==1&&usb_confirm_pending(usb_consent.epoch));
  usb_confirm_reply(usb_consent.epoch,false);drain();
  unsigned int epoch=usb_consent.epoch;int a=attached,d=detached;
  for(int i=0;i<500;i++){
   assert(usb_podlink_open());usb_podlink_close();
   assert(serial()=='2'&&!enabled[USB_DRIVER_MASS_STORAGE]);
  }
  /* Profile selection is tested above; clients themselves never attach USB. */
  for(int i=0;i<500;i++){assert(usb_podlink_open());usb_podlink_close();}
  drain();assert(attached==a&&detached==d&&usb_consent.epoch==epoch&&prompts==1);
  usb_confirm_configured(true,false,false);drain();assert(prompts==1);
  begin(mode);usb_confirm_configured(true,false,false);drain();
  epoch=usb_consent.epoch;usb_confirm_reply(epoch-1,true);drain();assert(!enabled[USB_DRIVER_MASS_STORAGE]);
  usb_confirm_reply(epoch,true);drain();
  assert(enabled[USB_DRIVER_MASS_STORAGE]&&!enabled[USB_DRIVER_SERIAL]);
  assert(detached==1&&!usb_podlink_active());
  exclusive_storage_enabled=true;assert(!usb_podlink_open());
 }
 begin(0);usb_confirm_configured(true,false,false);drain();
 unplug_during_sleep=true;usb_confirm_reply(usb_consent.epoch,true);drain();assert(usb_state==USB_EXTRACTED);
 begin(0);usb_host_probe_on=true;assert(!usb_podlink_open());
 begin(0);exclusive_storage_requested=true;assert(!usb_podlink_open());
 begin(0);usb_mode=USB_MODE_CHARGE;queue_post(&usb_queue,USB_PODLINK_OPEN,usb_mode);drain();
 assert(serial()=='4'&&!podlink_requested&&!enabled[USB_DRIVER_SERIAL]);
 int a=attached;queue_post(&usb_queue,USB_PODLINK_OPEN,0);drain();assert(attached==a);
 usb_mode=USB_MODE_MASS_STORAGE;queue_post(&usb_queue,USB_PODLINK_OPEN,usb_mode);drain();
 assert(serial()=='2'&&!enabled[USB_DRIVER_MASS_STORAGE]);
 usb_confirm_configured(true,false,false);drain();assert(prompts==1);
 puts("S1: MENU re-prompt; duplicate/stale/unplug safety; WPS switch silent; popup MENU keeps resident 2AA; SELECT enters disk PASS");
 puts("resident USB: first consent/MENU/500 switches stable 2AA/no attach or detach/no second popup/disk exclusive/pure Charge/stale reply/unplug PASS");
}
""".replace('EVENTS',events+'\n'+mode_events).replace('CLIENTS',clients)
post=post.replace('int main(void)',exit_hook+'\n'+popup+'\nint main(void)')
f=O/'lease.c';f.write_text(pre+'\n#define AGENTS_SWITCH_WPS 2\n#define AGENTS_EXIT_MENU 0\n#define ACTION_STD_OK 100\n#define ACTION_STD_CANCEL 101\n#define ACTION_WPS_PLAY 102\n#define BUTTON_MULTIMEDIA_PLAYPAUSE 103\n#define AUDIO_STATUS_PLAY 1\n#define AUDIO_STATUS_PAUSE 2\n#define DIALOG_ABORT -1\n#define DIALOG_ACCEPT 1\n#define DIALOG_CONTINUE 0\n#define SYS_EVENT 0x80000000u\n#define BUTTON_MULTIMEDIA 0x10000000u\nstruct dialog {bool backlight_on;};\nstatic int audio_status(void){return 0;}\nstatic void unpause_action(bool on){(void)on;}\nstatic void pause_action(bool on){(void)on;}\nstatic void default_event_handler(int action){(void)action;}\n'+api+profiles+post)
cmd=['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(R/'firmware/export'),str(f),'-o',str(O/'lease')]
subprocess.run(cmd,check=True);subprocess.run([str(O/'lease')],check=True)
