"""Captured Universal Dock volume-set pairs through the production lingo-3 arm."""
from pathlib import Path
import re,subprocess,sys
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
#include <stdio.h>
#include <stdint.h>
#include <limits.h>
#define HAVE_USB_PODLINK
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
if module.exists():pre+=body(module)
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
tests=r'''
/* remote-log.txt frames 328/329 and 335/336, exact captured bytes. */
static const unsigned char up[][6]={{3,14,4,0,0x27,1},{3,14,4,0,0x2a,1}};
static const unsigned char down[][6]={{3,14,4,0,0x27,1},{3,14,4,0,0x24,1}};
static void pair(unsigned char echo,unsigned char target) {
 unsigned char b[]={3,14,4,0,echo,1};frame(6,b,true);b[4]=target;frame(6,b,true);
}
static void reported_is(int level,bool muted) {
 unsigned char v;bool m;iap_get_volume(&v,&m);assert(v==level&&m==muted);
 sent=0;query(4);query(16);iap_set_remote_volume();
 assert(sent==3&&sizes[0]==5&&sizes[1]==6&&sizes[2]==5);
 for(int i=0;i<2;i++){assert(packets[i][3]==muted&&packets[i][4]==level);}
 bool virtual=iap_agents_volume_get(&v,&m);
 assert(packets[2][3]==(virtual?muted:false));
 assert(packets[2][4]==(virtual?level:global_status.volume));
 assert(packets[1][5]==level);
}
int main(void) {
 global_status.volume=0x27;iap_agents_volume_enable(true);
 frame(6,up[0],true);assert(!changes&&!iap_agents_volume_take());
 frame(6,up[1],true);assert(!changes&&global_status.volume==0x27);
 assert(iap_agents_volume_take()==PAGE_UP&&!iap_agents_volume_take());
 reported_is(0x2a,false);
 device.notifications=BIT_N(4)|BIT_N(16);device.volume=0x27;
 sent=0;notify();assert(sent==2&&device.volume==0x2a);
 assert(packets[0][3]==0&&packets[0][4]==0x2a&&packets[1][5]==0x2a);
 notify();assert(sent==2); /* Stable virtual readback stops change notifications. */
 for(int i=0;i<1000;i++){frame(6,up[0],true);frame(6,up[1],true);}
 current_tick=100;assert(!iap_agents_volume_take());reported_is(0x2a,false);
 frame(6,down[0],true);assert(!iap_agents_volume_take());
 frame(6,down[1],true);assert(iap_agents_volume_take()==PAGE_DOWN);
 reported_is(0x24,false);assert(!changes&&global_status.volume==0x27);
 for(int i=0;i<1000;i++){frame(6,down[0],true);frame(6,down[1],true);}
 current_tick=200;assert(!iap_agents_volume_take());
 for(int which=0;which<3;which++) {
  int bound=which==0?255:which==1?0:100;
  global_settings.volume_limit=which==2?100:255;global_status.volume=bound;
  iap_agents_volume_enable(true);int pages=0;current_tick=0;
  for(int t=0;t<100;t++) {
   current_tick=t;pair(bound,bound);int b=iap_agents_volume_take();
   if(b){assert(b==(which==1?PAGE_DOWN:PAGE_UP));pages++;}
  }
  assert(pages==4);reported_is(bound,false);
 }
 /* A changing-target flood coalesces into at most four pages per second. */
 global_status.volume=0x27;global_settings.volume_limit=255;
 iap_agents_volume_enable(true);int pages=0;unsigned char v=0x27;
 for(int t=0;t<200;t++) {
  current_tick=t;unsigned char target=(v+3)%240;pair(v,target);v=target;
  if(iap_agents_volume_take())pages++;
 }
 assert(pages==8);reported_is(v,false);
 iap_agents_volume_enable(false);assert(!iap_agents_volume_take());
 reported_is(0x27,false);sent=0;notify();assert(sent==2&&device.volume==0x27);
 /* Reset a half pair and all pending/rate state between app sessions. */
 iap_agents_volume_enable(true);frame(6,up[0],true);
 iap_agents_volume_enable(false);iap_agents_volume_enable(true);
 pair(0x27,0x24);assert(iap_agents_volume_take()==PAGE_DOWN);
 unsigned char mute[]={3,14,4,1,0x24,1};frame(6,mute,true);
 reported_is(0,true);assert(!device.mute&&!changes);
 device.mute=true;iap_agents_volume_enable(true);reported_is(0,true);
 pair(0,3);assert(iap_agents_volume_take()==PAGE_UP);
 reported_is(3,false);assert(device.mute&&!changes);
 iap_agents_volume_enable(false);reported_is(0,true);device.mute=false;
 iap_agents_volume_enable(true);
 unsigned char absolute[]={3,14,16,0,0,0x27,1};frame(7,absolute,true);
 absolute[5]=0x2a;frame(7,absolute,true);
 assert(iap_agents_volume_take()==PAGE_UP&&!changes);reported_is(0x2a,false);
 iap_agents_volume_enable(false);frame(7,absolute,true);
 assert(changes==1&&global_status.volume==0x2a);changes=0;
 frame(6,down[1],true);assert(changes==1&&global_status.volume==0x24);
 iap_agents_volume_enable(true);activity=0;frame(6,up[1],true);
 assert(changes==2&&global_status.volume==0x2a);reported_is(0x2a,false);
 activity=ACTIVITY_AGENTS;iap_agents_volume_enable(true);
 frame(6,up[1],false);frame(4,up[1],true);assert(!iap_agents_volume_take());
 global_status.volume=255;iap_agents_volume_enable(true);current_tick=ULONG_MAX-10;
 pair(255,255);assert(iap_agents_volume_take()==PAGE_UP);
 current_tick=13;pair(255,255);assert(!iap_agents_volume_take());
 current_tick=14;assert(iap_agents_volume_take()==PAGE_UP);
 assert(acks>0);
 puts("captured pairs/resend flood/virtual replies+both notifications/real volume+mute frozen/exit restore PASS");
 puts("clamps/configured limit/4 pages per second/coalescing/lifecycle/absolute/outside/auth+length/wrap PASS");
}
'''
(O/'volume.c').write_text(pre+setter+handle+reported+tests)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie',str(O/'volume.c'),'-o',str(O/'volume')],check=True)
subprocess.run([str(O/'volume')],check=True)
