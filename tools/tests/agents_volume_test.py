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
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdint.h>
#define HAVE_USB_PODLINK
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
static struct {bool mute;}device;
static int changes,acks;
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
tests=r'''
/* remote-log.txt frames 328/329 and 335/336, exact captured bytes. */
static const unsigned char up[][6]={{3,14,4,0,0x27,1},{3,14,4,0,0x2a,1}};
static const unsigned char down[][6]={{3,14,4,0,0x27,1},{3,14,4,0,0x24,1}};
static void pair(unsigned char level) {
 unsigned char b[]={3,14,4,0,level,1};frame(6,b,true);frame(6,b,true);
}
int main(void) {
 global_status.volume=0x27;iap_agents_volume_enable(true);
 frame(6,up[0],true);assert(!changes&&!iap_agents_volume_take());
 frame(6,up[1],true);assert(!changes&&global_status.volume==0x27);
 assert(iap_agents_volume_take()==PAGE_UP&&!iap_agents_volume_take());
 frame(6,down[0],true);assert(!iap_agents_volume_take());
 frame(6,down[1],true);assert(iap_agents_volume_take()==PAGE_DOWN);
 assert(!changes&&global_status.volume==0x27);
 for(int i=0;i<10;i++){frame(6,up[0],true);frame(6,up[1],true);}
 for(int i=0;i<10;i++)assert(iap_agents_volume_take()==PAGE_UP);
 assert(!iap_agents_volume_take());
 global_status.volume=255;for(int i=0;i<4;i++){pair(255);assert(iap_agents_volume_take()==PAGE_UP);}
 global_status.volume=0;for(int i=0;i<4;i++){pair(0);assert(iap_agents_volume_take()==PAGE_DOWN);}
 global_settings.volume_limit=100;global_status.volume=100;
 pair(100);assert(iap_agents_volume_take()==PAGE_UP);
 global_status.volume=0x27;global_settings.volume_limit=255;
 frame(6,up[0],true);iap_agents_volume_enable(false);iap_agents_volume_enable(true);
 frame(6,down[0],true);assert(!iap_agents_volume_take());
 frame(6,down[1],true);assert(iap_agents_volume_take()==PAGE_DOWN);
 unsigned char mute[]={3,14,4,1,0x24,1};frame(6,mute,true);assert(!changes);
 device.mute=true;iap_agents_volume_enable(true);
 unsigned char muted_echo[]={3,14,4,0,0,1};frame(6,muted_echo,true);
 assert(!iap_agents_volume_take());muted_echo[4]=3;frame(6,muted_echo,true);
 assert(iap_agents_volume_take()==PAGE_UP&&device.mute&&!changes);
 device.mute=false;
 iap_agents_volume_enable(true);
 unsigned char absolute[]={3,14,16,0,0,0x27,1};frame(7,absolute,true);
 absolute[5]=0x2a;frame(7,absolute,true);
 assert(iap_agents_volume_take()==PAGE_UP&&!changes);
 iap_agents_volume_enable(false);frame(7,absolute,true);
 assert(changes==1&&global_status.volume==0x2a);changes=0;
 iap_agents_volume_enable(false);frame(6,up[1],true);
 assert(changes==1&&global_status.volume==0x2a);
 iap_agents_volume_enable(true);activity=0;frame(6,down[1],true);
 assert(changes==2&&global_status.volume==0x24);
 activity=ACTIVITY_AGENTS;iap_agents_volume_enable(true);
 frame(6,up[1],false);frame(4,up[1],true);assert(!iap_agents_volume_take());
 assert(acks>0);
 puts("captured volume pairs: echo+one page/frozen volume/repeated min+max/configured limit/mute/lifecycle/outside unchanged/auth+length PASS");
}
'''
(O/'volume.c').write_text(pre+setter+handle+tests)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie',str(O/'volume.c'),'-o',str(O/'volume')],check=True)
subprocess.run([str(O/'volume')],check=True)
