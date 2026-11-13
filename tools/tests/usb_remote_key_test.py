"""Compile production EP0/button code with offline hardware stubs."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
helper = ROOT / 'firmware/drivers/button_usb_remote.c'
assert helper.exists(), 'baseline: USB remote key helper is absent'

def function(text, name):
    m = re.search(r'(?m)^[\w *]+\b' + name + r'\([^;{}]*\)\s*\{', text)
    assert m, name
    end, depth = m.end(), 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[m.start():end]

# Full helper, real button sampling/tick routines, and the EP0 dispatcher.
parts = [helper.read_text()]
button = (ROOT / 'firmware/drivers/button.c').read_text()
core = (ROOT / 'firmware/usbstack/usb_core.c').read_text()
parts += [function(button, 'button_read'), function(button, 'button_tick')]
parts += [function(core, 'usb_core_control_request_handler')]
parts += [(ROOT / 'apps-ipod/input/agents_remote.h').read_text(),
          (ROOT / 'apps-ipod/input/agents_remote.c').read_text()]
source = '\n'.join(re.sub(r'^#include .*$', '', p, flags=re.M) for p in parts)
preamble = r'''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdio.h>
#include <limits.h>
#define IPOD_6G
#define USB_REMOTE_KEY_ENABLED
#define IPOD_ACCESSORY_PROTOCOL
#include "firmware/target/arm/s5l8702/ipod6g/button-target.h"
#define BUTTON_NONE 0
#define BUTTON_REL 0x02000000
#define BUTTON_REPEAT 0x04000000
#define HZ 100
#define REPEAT_START 30
#define REPEAT_INTERVAL_START 16
#define REPEAT_INTERVAL_FINISH 5
#define USB_CONTROL_ACK 1
#define USB_CONTROL_STALL 2
#define USB_RECIP_MASK 31
#define USB_RECIP_DEVICE 0
#define USB_RECIP_INTERFACE 1
#define USB_RECIP_ENDPOINT 2
#define CONFIGURED 2
#define USB_REMOTE_KEY_REQUEST 0x5b
#define USB_REMOTE_KEY_MENU 1
#define TIME_BEFORE(a,b) ((long)((unsigned long)(a)-(unsigned long)(b)) < 0)
struct usb_ctrlrequest { uint8_t bRequestType, bRequest; uint16_t wValue, wIndex, wLength; };
static unsigned long current_tick;
static long lastbtn, last_read;
static int lastdata, usb_state=CONFIGURED, response, used;
static long posted[500];
static int disable_irq_save(void) { return 0; }
static void restore_irq(int level) { (void)level; }
static int button_read_device(void) { return 0; }
static bool button_queue_try_post(long b,int data) { (void)data; posted[used++]=b; return true; }
static void button_remote_post(void) {}
static void check_audio_peripheral_state(void) {}
static void reset_poweroff_timer(void) {}
static void usb_core_init_interfaces(void) {}
static void usb_core_control_response(int r,void *p,int n) { (void)p;(void)n;response=r; }
static void request_handler_device(struct usb_ctrlrequest *r,uint8_t *p,size_t n) { (void)r;(void)p;(void)n;response=3; }
#define request_handler_interface request_handler_device
#define request_handler_endpoint request_handler_device
#define logf(...)
'''
tests = r'''
static void tick(unsigned long t) { current_tick=t; button_tick(); }
static void request(int type,int id,int down,int len,int expected) {
    struct usb_ctrlrequest r={type,0x5b,id,down,len};
    response=0; usb_core_control_request_handler(&r,NULL,0);
    assert(response==expected);
}
int main(void) {
    for(int type=0;type<256;type++) if(type!=0x40)
        request(type,1,1,0,USB_CONTROL_STALL);
    for(int key=0;key<65536;key++) if(key!=1)
        request(0x40,key,1,0,USB_CONTROL_STALL);
    request(0x40,1,2,0,USB_CONTROL_STALL);
    request(0x40,1,1,1,USB_CONTROL_STALL);
    usb_state=0;request(0x40,1,1,0,USB_CONTROL_STALL);usb_state=CONFIGURED;
    tick(0);request(0x40,1,1,0,USB_CONTROL_ACK);
    tick(1);tick(2);assert(used==1 && posted[0]==BUTTON_RC_MENU);
    for(int t=3;t<60;t++) { if(t%10==0) request(0x40,1,1,0,USB_CONTROL_ACK);tick(t); }
    assert(lastbtn==BUTTON_RC_MENU);
    for(int i=1;i<used;i++) assert(posted[i]==(BUTTON_RC_MENU|BUTTON_REPEAT));
    request(0x40,1,0,0,USB_CONTROL_ACK);tick(60);tick(61);
    assert(lastbtn==0 && posted[used-1]==(BUTTON_RC_MENU|BUTTON_REL));
    int before=used;request(0x40,1,0,0,USB_CONTROL_ACK);tick(62);assert(used==before);
    current_tick=100;request(0x40,1,1,0,USB_CONTROL_ACK);tick(101);tick(102);
    for(int t=103;t<200;t++) tick(t);
    assert(lastbtn==BUTTON_RC_MENU);tick(200);tick(201);
    assert(!lastbtn && posted[used-1]==(BUTTON_RC_MENU|BUTTON_REL));
    /* Unsigned addition and signed comparisons remain valid across wrap. */
    current_tick=ULONG_MAX-50;request(0x40,1,1,0,USB_CONTROL_ACK);
    tick(ULONG_MAX-49);tick(ULONG_MAX-48);tick(48);assert(lastbtn);
    tick(49);tick(50);assert(!lastbtn);
    struct agents_remote_state state={0};
    current_tick=400;request(0x40,1,1,0,USB_CONTROL_ACK);
    int first=used;tick(401);tick(402);
    assert(!agents_remote_button(&state,true,posted[first],lastbtn,402));
    for(int t=403;t<462;t++) {
        if(t%20==0) request(0x40,1,1,0,USB_CONTROL_ACK);
        tick(t);assert(!agents_remote_button(&state,true,0,lastbtn,t));
    }
    tick(462);
    assert(agents_remote_button(&state,true,0,lastbtn,462)==BUTTON_RC_SELECT);
    request(0x40,1,0,0,USB_CONTROL_ACK);tick(463);tick(464);
    assert(!agents_remote_button(&state,true,posted[used-1],lastbtn,464));
    current_tick=500;request(0x40,1,1,0,USB_CONTROL_ACK);
    first=used;tick(501);tick(502);
    assert(!agents_remote_button(&state,true,posted[first],lastbtn,502));
    current_tick=520;request(0x40,1,0,0,USB_CONTROL_ACK);tick(521);tick(522);
    assert(agents_remote_button(&state,true,posted[used-1],lastbtn,522)==
           (BUTTON_RC_MENU|BUTTON_REL));
    current_tick=600;request(0x40,1,1,0,USB_CONTROL_ACK);tick(601);tick(602);
    current_tick=699;request(0x40,1,1,0,USB_CONTROL_ACK);
    tick(700);assert(lastbtn);
    current_tick=798;request(0x40,2,1,0,USB_CONTROL_STALL);
    tick(798);assert(lastbtn);tick(799);tick(800);assert(!lastbtn);
    puts("0.6s production remote hold consumer + short tap + renewed watchdog PASS");
    puts("EP0 strict parsing + real button queue press/repeat/release + 1s safety/wrap PASS");
}
'''
(out/'config.h').write_text('')
(out/'probe.c').write_text(preamble+source+tests)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror',
                '-fsanitize=address,undefined','-fno-pie','-no-pie',
                '-I'+str(ROOT), '-I'+str(out),
                str(out/'probe.c'),'-o',str(out/'probe')],check=True)
subprocess.run([str(out/'probe')],check=True)
