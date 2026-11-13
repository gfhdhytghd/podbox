"""Captured Universal Dock volume-set pairs through the production lingo-3 arm."""
from pathlib import Path
import re,subprocess,sys,os
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
S=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else R
s=(S/'apps-ipod/iap/iap-lingo3.c').read_text()
a=re.search(r'^        case 0x0E:',s,re.M).start();m=re.search(r'^                case 0x04:',s[a:],re.M);start=a+m.start();brace=s.index('{',start);end=brace+1;d=1
while d:
 if s[end]=='{':d+=1
 if s[end]=='}':d-=1
 end+=1
case=s[start:end]
a=s.index('static void set_accessory_volume');brace=s.index('{',a);end=brace+1;d=1
while d:
 if s[end]=='{':d+=1
 if s[end]=='}':d-=1
 end+=1
setter=s[a:end]
a=re.search(r'^        case 0x0E:',s,re.M).start();m=re.search(r'^                case 0x10:',s[a:],re.M)
br=s.index('{',a+m.start());en=br+1;depth=1
while depth:
 if s[en]=='{':depth+=1
 if s[en]=='}':depth-=1
 en+=1
case += '\n'+s[a+m.start():en]
def body(p):return re.sub(r'^#include .*$', '',p.read_text(),flags=re.M)
core=(S/'apps-ipod/iap/iap-core.c').read_text()
def function(text,name):
 a=text.index('void '+name+'(');br=text.index('{',a);en=br+1;depth=1
 while depth:
  depth+=(text[en]=='{')-(text[en]=='}');en+=1
 return text[a:en]
def get_case(tag):
 a=s.index('        case 0x0C:');a=s.index(tag,a);br=s.index('{',a);en=br+1;depth=1
 while depth:
  depth+=(s[en]=='{')-(s[en]=='}');en+=1
 return s[a:en]
replies=get_case('                case 0x04:')+'\n'+get_case('                case 0x10:')
a=core.index('    /* Volume: looked at every 100ms');b=core.index('    /* All other events',a)
notify=core[a:b]
pre=r'''
#include <assert.h>
#include <stdbool.h>
#define REMOTE_LOG_DISABLE
#include "firmware/export/remote_log.h"
static bool streaming;
#define remote_log_enabled() streaming
#include <stdio.h>
#include <stdint.h>
#include <limits.h>
#include <stdlib.h>
static bool remote_input_active;
#define HAVE_USB_PODLINK
#define IPOD_ACCESSORY_PROTOCOL
#define HZ 100
static unsigned long current_tick;
#define TIME_BEFORE(a,b) ((long)((unsigned long)(a)-(unsigned long)(b))<0)
#define BUTTON_NONE 0
#define BUTTON_REL 0x02000000
#define BUTTON_REPEAT 0x04000000
#define BUTTON_RC_VOL_UP 1
#define BUTTON_RC_VOL_DOWN 2
#define PAGE_UP (BUTTON_RC_VOL_UP|BUTTON_REL|BUTTON_REPEAT)
#define PAGE_DOWN (BUTTON_RC_VOL_DOWN|BUTTON_REL|BUTTON_REPEAT)
#define ACTIVITY_AGENTS 7
static int activity=ACTIVITY_AGENTS;
static struct {int volume;}global_status;
static struct {int volume_limit;}global_settings={255};
static struct {bool mute,volume_mute;unsigned char volume;unsigned int notifications,changed_notifications;}device;
static int changes,acks;
static unsigned char tx[16],packets[16][16];static int txlen,sent,sizes[16];
#define BIT_N(n) (1u<<(n))
#define IAP_TX_INIT(l,c) do{txlen=0;tx[txlen++]=(l);tx[txlen++]=(c);}while(0)
#define IAP_TX_PUT(n) do{assert(txlen<16);tx[txlen++]=(n);}while(0)
static void iap_send_tx(void){assert(sent<16);sizes[sent]=txlen;for(int i=0;i<txlen;i++)packets[sent][i]=tx[i];sent++;}

static inline int disable_irq_save(void){return 0;}
static inline void restore_irq(int n){(void)n;}
static inline int get_current_activity(void){return activity;}
static inline unsigned char iap_volume_to_byte(int n){return n<0?0:n>255?255:n;}
static int iap_byte_to_volume(unsigned char n){return n;}
static void setvol(void){changes++;}
static void iap_set_mute(bool mute){device.mute=mute;changes++;}
#define CHECKLEN(n) do{if(len<(n))return;}while(0)
#define cmd_ok(cmd) do{(void)cmd;acks++;}while(0)
'''
module=S/'apps-ipod/iap/agents_volume.c'
if module.exists():
 trace=(S/'apps-ipod/iap/remote_capture.c').read_text()
 pre+=body(S/'apps-ipod/iap/remote_capture.h')
 pre+=trace[trace.index('static struct remote_volume_trace'):trace.index('void remote_capture_begin')]
 pre+=body(module)
else:pre+='''
static void iap_agents_volume_enable(bool enabled){(void)enabled;}
static int iap_agents_volume_take(void){return 0;}
'''
handle='''
static void frame(unsigned int len,const unsigned char *buf,bool auth) {
 unsigned int cmd=0x0e;
 if(!auth)return;
 switch(buf[2]) {
'''+case+'''\n }
}
'''
reported=function(core,'iap_get_volume')+'\n'+function(core,'iap_set_remote_volume')
reported+='\nstatic void notify(void){'+notify+'}\n'
reported+='\nstatic void query(int type){IAP_TX_INIT(3,13);IAP_TX_PUT(type);switch(type){'+replies+'}}\n'
screen=(S/'apps-ipod/screens/system/agents_screen.c').read_text()
policy=function(screen,'update_volume_mode') if 'static void update_volume_mode' in screen else 'static void update_volume_mode(void){iap_agents_volume_enable(true);}'
pre+='\nstatic int view,confirm_from;static bool volume_paging __attribute__((unused));\n#define VIEW_USAGE 4\n#define VIEW_CONFIRM 5\n'+policy
trace=[]
for line in (R/'tools/tests/fixtures/remote-volume/2026-11-13.txt').read_text().splitlines():
 m=re.search(r'tick=(\d+).*03 0E 04 00 ([0-9A-F]{2}) 01',line)
 if m:trace.append('{'+m[1]+',0x'+m[2]+'}')
pre+='\nstatic const struct {unsigned long tick;unsigned char volume;} trace[]={'+','.join(trace)+'};\n'
tests=r'''
static void step(unsigned char v){unsigned char b[]={3,14,4,0,v,1};frame(6,b,true);}
static void reported_is(int n){unsigned char v;bool m;iap_get_volume(&v,&m);assert(v==n&&!m);}
int main(int argc,char **argv) {
 (void)argc;(void)argv;
 global_status.volume=12;iap_agents_volume_enable(true);volume_paging=true;
 if(getenv("VOLUME_POLICY")) {
  view=VIEW_USAGE;update_volume_mode();unsigned char v;bool m;
  assert(!iap_agents_volume_get(&v,&m));step(15);
  assert(global_status.volume==15&&!iap_agents_volume_take());
  view=2;update_volume_mode();step(18);
  assert(iap_agents_volume_take()==PAGE_UP&&global_status.volume==15);
  view=VIEW_CONFIRM;confirm_from=VIEW_USAGE;update_volume_mode();
  assert(!iap_agents_volume_get(&v,&m));puts("Usage real volume / return paging PASS");return 0;
 }
 int ups=0,downs=0;
 for(unsigned int i=0;i<sizeof(trace)/sizeof(trace[0]);i++) {
  current_tick=trace[i].tick;step(trace[i].volume);int b=iap_agents_volume_take();
  if(i==0)assert(b==PAGE_UP);
  if(b==PAGE_UP)ups++;else if(b==PAGE_DOWN)downs++;
 }
 printf("recorded L3 steps: up=%d down=%d (expected 6/8)\n",ups,downs);fflush(stdout);
 assert(ups==6&&downs==8&&global_status.volume==12);reported_is(0);
 /* Clamp keeps its sign; fresh presses are separated by 300ms or more. */
 for(int bound=0;bound<=255;bound+=255) {
  global_status.volume=bound;iap_agents_volume_enable(true);int pages=0;
  for(int t=0;t<200;t+=10){current_tick=1000+t;step(bound);int b=iap_agents_volume_take();if(b){pages++;assert(b==(bound?PAGE_UP:PAGE_DOWN));}}
  assert(pages==6);current_tick=1230;step(bound);assert(iap_agents_volume_take());
 }
 /* Every packet updates virtual readback; reversals start another press. */
 global_status.volume=39;iap_agents_volume_enable(true);
 current_tick=2000;step(42);assert(iap_agents_volume_take()==PAGE_UP);reported_is(42);
 current_tick=2010;step(45);assert(!iap_agents_volume_take());reported_is(45);
 current_tick=2020;step(42);assert(iap_agents_volume_take()==PAGE_DOWN);
 current_tick=2030;step(39);assert(!iap_agents_volume_take());
 current_tick=2060;step(36);assert(iap_agents_volume_take()==PAGE_DOWN);
 iap_agents_volume_enable(false);step(33);assert(global_status.volume==33&&changes==1);
 /* Diagnostic screen logs the same decisions without virtualizing real audio. */
 activity=0;remote_input_active=true;global_status.volume=12;iap_agents_volume_enable(true);
 unsigned long before=remote_volume_count();current_tick=3000;step(15);
 assert(global_status.volume==15&&!iap_agents_volume_take());
 struct remote_volume_trace trace_row;assert(remote_volume_count()>before&&remote_volume_get(remote_volume_count(),&trace_row));assert(trace_row.decision=='P'&&trace_row.direction==1);
 remote_input_active=false;iap_agents_volume_enable(false);
 /* Authentication, lengths and both absolute-volume encodings stay gated. */
 activity=ACTIVITY_AGENTS;global_status.volume=39;changes=0;iap_agents_volume_enable(true);
 unsigned char absolute[]={3,14,16,0,0,42,1};
 frame(7,absolute,false);frame(4,absolute,true);assert(!iap_agents_volume_take());
 current_tick=4000;frame(7,absolute,true);assert(iap_agents_volume_take()==PAGE_UP&&!changes);reported_is(42);
 device.notifications=BIT_N(4)|BIT_N(16);device.volume=39;sent=0;notify();
 assert(sent==2&&device.volume==42&&packets[0][4]==42&&packets[1][5]==42);
 sent=0;query(4);query(16);iap_set_remote_volume();
 assert(sent==3&&packets[0][4]==42&&packets[1][4]==42&&packets[2][4]==42);
 unsigned char muted[]={3,14,4,1,42,1};frame(6,muted,true);
 unsigned char v;bool m;iap_get_volume(&v,&m);assert(v==0&&m&&!device.mute&&!iap_agents_volume_take());
 current_tick=4010;step(3);assert(iap_agents_volume_take()==PAGE_UP&&!changes);reported_is(3);
 iap_agents_volume_enable(false);frame(7,absolute,true);assert(global_status.volume==42&&changes==1);
 global_settings.volume_limit=100;global_status.volume=100;iap_agents_volume_enable(true);
 current_tick=ULONG_MAX-10;step(100);assert(iap_agents_volume_take()==PAGE_UP);
 current_tick=9;step(100);assert(!iap_agents_volume_take());
 current_tick=49;step(100);assert(iap_agents_volume_take()==PAGE_UP);
 /* Resident streaming observes volume outside Debug/Agents without paging
  * or changing the normal audio-volume setter's result. */
 activity=0;remote_input_active=false;global_status.volume=12;
 iap_agents_volume_enable(false);streaming=true;changes=0;
 before=remote_volume_count();current_tick=5000;step(15);
 assert(global_status.volume==15&&changes==1&&!iap_agents_volume_take());
 assert(remote_volume_count()==before+1);
 assert(remote_volume_get(remote_volume_count(),&trace_row));
 assert(trace_row.direction==1&&trace_row.decision=='P');
 streaming=false;before=remote_volume_count();step(18);
 assert(global_status.volume==18&&changes==2);
 assert(remote_volume_count()==before);
 puts("single-message decode/readback/6 up + 8 down/gap/hold/clamps/reversal/Usage/Debug/resident stream outside Agents PASS");
}
'''
(O/'volume.c').write_text(pre+setter+handle+reported+tests)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie','-I'+str(R),str(O/'volume.c'),'-o',str(O/'volume')],check=True)
subprocess.run([str(O/'volume')],check=True)
