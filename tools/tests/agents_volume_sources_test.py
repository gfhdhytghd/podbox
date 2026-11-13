"""Real EP0, volume arbitration, ring and text formatter; no device access."""
from pathlib import Path
import re,subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
(O/'config.h').write_text('')
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <stddef.h>
#include <stdint.h>
#include <limits.h>
#include <string.h>
#define __CONFIG_H__
#define HAVE_USB_PODLINK
#define IPOD_6G
#define IPOD_ACCESSORY_PROTOCOL
#define HZ 100
#define TIME_BEFORE(a,b) ((long)((unsigned long)(a)-(unsigned long)(b))<0)
#include "firmware/target/arm/ipod/button-target.h"
#include "firmware/export/button_usb_remote.h"
#include "firmware/export/remote_log.h"
#include "apps-ipod/iap/remote_capture.h"
#include "apps-ipod/iap/agents_volume.h"
#define BUTTON_NONE 0
#define BUTTON_REL 0x02000000
#define BUTTON_REPEAT 0x04000000
#define ACTIVITY_AGENTS 7
#define UP (BUTTON_RC_VOL_UP|BUTTON_REL|BUTTON_REPEAT)
#define DOWN (BUTTON_RC_VOL_DOWN|BUTTON_REL|BUTTON_REPEAT)
static long current_tick;
static bool test_link;
bool podlink_host_connected(void){return test_link;}
bool remote_input_active;
static int wakeups;
static bool button_queue_try_post(long b,int data){(void)b;(void)data;wakeups++;return true;}
static int activity=ACTIVITY_AGENTS;
static struct {int volume;} global_status;
static struct {int volume_limit;} global_settings={255};
static struct {bool mute;} device;
static int get_current_activity(void){return activity;}
static unsigned char iap_volume_to_byte(int n){return n<0?0:n>255?255:n;}
static void (*before_irq_mask)(void);
static int disable_irq_save(void){if(before_irq_mask){void (*fn)(void)=before_irq_mask;before_irq_mask=NULL;fn();}return 0;}
static void restore_irq(int level){(void)level;}
struct usb_ctrlrequest {uint8_t bRequestType,bRequest;uint16_t wValue,wIndex,wLength;};
'''
parts=['firmware/usbstack/remote_log.c','firmware/drivers/button_usb_remote.c','apps-ipod/iap/remote_capture.c','apps-ipod/iap/agents_volume.c']
source=pre+'\n'.join(re.sub(r'^#include .*$', '',(R/p).read_text(),flags=re.M) for p in parts)
inc=(R/'tools/tests/agents_volume_sources_test.inc').read_text()
action=(R/'apps-ipod/input/action.c').read_text()
start=action.index('static int remote_poll(');brace=action.index('{',start);end=brace+1;depth=1
while depth:
    depth+=(action[end]=='{')-(action[end]=='}');end+=1
inc=inc.replace('int main(void)', action[start:end]+'\nint main(void)')
source+=inc
(O/'sources.c').write_text(source)
cmd=['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie','-I'+str(O),'-I'+str(R),str(O/'sources.c'),'-o',str(O/'sources')]
subprocess.run(cmd,check=True);subprocess.run([str(O/'sources')],check=True)
