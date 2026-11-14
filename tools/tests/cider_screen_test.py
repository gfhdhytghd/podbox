"""Production Cider screen actions and root shortcut destinations, offline."""
from pathlib import Path
import re,subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
def fn(text,name):
 m=re.search(r'(?m)^(?:static[ \t]+)?(?:int|void|bool)[ \t*]+'+name+r'\([^;{}]*\)\s*\{',text);assert m,name
 e=m.end();d=1
 while d:d+=(text[e]=='{')-(text[e]=='}');e+=1
 return text[m.start():e]
s=(R/'apps-ipod/screens/playback/cider_screen.c').read_text();body=fn(s,'cider_screen')
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include "screens/playback/cider_model.h"
#define HZ 100
#define CONTEXT_WPS 1
#define ACTIVITY_WPS 1
#define MIN(a,b) ((a)<(b)?(a):(b))
#define MAX(a,b) ((a)>(b)?(a):(b))
#define SYS_USB_CONNECTED 400
#define GO_TO_WPS 10
#define GO_TO_AGENTS 11
#define GO_TO_PREVIOUS -1
#define GO_TO_ALBUM_COVERS 12
#define GO_TO_CIDER 13
#define AGENTS_NAV_NONE 0
#define AGENTS_NAV_AGENTS 1
#define AGENTS_NAV_WPS 2
#define AGENTS_NAV_COVERS 3
enum agents_nav_screen { NAV_TEST };
static unsigned short cover[240*240];
static unsigned long current_tick;
static bool available,on,owned,falloff;
static struct cider_state model;
static int actions[32],na,at,commands,draws,cancels,origin,nav;
static char keys[32][16];static int values[32];
static bool cider_available(void){return available;}
static bool agents_nav_enter(int n){assert(n==AGENTS_NAV_WPS);assert(!owned);owned=true;return true;}
static void agents_nav_leave(int n){assert(n==AGENTS_NAV_WPS&&owned);owned=false;}
static int agents_nav_pending(void){return nav;}
static void agents_nav_take(int n){if(nav==n)nav=0;}
static void cider_set_active(bool b){on=b;}
static void push_current_activity(int a){assert(a==ACTIVITY_WPS);}
static void pop_current_activity(void){}
static void cider_snapshot(struct cider_state*s){*s=model;}
static void cider_request_art(const char*k,const char*t){assert(strlen(k)==64&&!strcmp(t,"L"));}
static bool cider_copy_art(const char*k,const char*t,void*p,unsigned int n){(void)k;(void)t;(void)p;assert(n==115200);return true;}
static void draw(const struct cider_state*s,bool art,long seek){(void)s;(void)seek;assert(art);draws++;}
static int get_action(int c,int timeout){assert(c==CONTEXT_WPS&&timeout==10);assert(at<na);current_tick+=10;if(falloff)available=false;return actions[at++];}
static void cider_control(const char*k,int value){strcpy(keys[commands],k);values[commands++]=value;if(!strcmp(k,"volume"))model.volume=value;}
static void cover_nav_begin(int n){origin=n;}
static int default_event_handler(int a){return a;}
static void cider_cancel_art(void){cancels++;}
'''
for i,a in enumerate(sorted(set(re.findall(r'\bACTION_[A-Z_]+',body)))):pre+=f'#define {a} {i+1}\n'
post=r'''
static void reset(void){available=true;on=owned=falloff=false;commands=draws=cancels=at=na=nav=0;model=(struct cider_state){.duration=100000,.position=12000,.volume=50,.rate=1000,.available=true};memset(model.art,'a',64);current_tick=0;}
int main(void){reset();available=false;assert(cider_screen()==GO_TO_WPS&&!on&&!owned);
reset();int seq[]={ACTION_WPS_PLAY,ACTION_WPS_SKIPPREV,ACTION_WPS_SKIPNEXT,ACTION_WPS_VOLUP,ACTION_WPS_VOLUP,ACTION_WPS_VOLDOWN,ACTION_WPS_SEEKFWD,ACTION_WPS_SEEKFWD,ACTION_WPS_STOPSEEK,ACTION_WPS_MENU};memcpy(actions,seq,sizeof seq);na=sizeof seq/sizeof *seq;
assert(cider_screen()==GO_TO_PREVIOUS&&!on&&!owned&&cancels==1&&commands==7);
assert(!strcmp(keys[0],"toggle")&&!strcmp(keys[1],"previous")&&!strcmp(keys[2],"next"));assert(values[3]==51&&values[4]==52&&values[5]==51);assert(!strcmp(keys[6],"seek")&&values[6]>22000);
reset();actions[na++]=ACTION_AGENTS_REMOTE_SWITCH;assert(cider_screen()==GO_TO_AGENTS&&!owned);
reset();actions[na++]=ACTION_AGENTS_SWITCH;assert(cider_screen()==GO_TO_AGENTS&&!owned);
reset();actions[na++]=ACTION_AGENTS_REMOTE_COVERS;assert(cider_screen()==GO_TO_ALBUM_COVERS&&origin==GO_TO_CIDER&&!owned);
reset();falloff=true;actions[na++]=ACTION_NONE;assert(cider_screen()==GO_TO_WPS&&!owned);
puts("Cider production screen: controls, one volume step/report, seek preview/commit, double/chord Agents, native triple Cover Flow, MENU and unavailable fallback PASS");}
'''
(O/'screen.c').write_text(pre+body+post)
cmd=['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(R/'apps-ipod'),'-I'+str(R/'firmware/export')]
subprocess.run(cmd+[str(O/'screen.c'),str(R/'apps-ipod/screens/playback/cider_model.c'),str(R/'firmware/usbstack/podlink_wire.c'),'-o',str(O/'screen')],check=True);subprocess.run([str(O/'screen')],check=True)
root=(R/'apps-ipod/root_menu.c').read_text()
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#define GO_TO_CIDER 13
#define GO_TO_WPS 10
#define GO_TO_AGENTS 11
#define GO_TO_PREVIOUS -1
#define AGENTS_NAV_NONE 0
#define AGENTS_NAV_WPS 2
#define AGENTS_SWITCH_WPS 2
#define AGENTS_SWITCH_COVERS 3
#define AGENTS_SWITCH_REMOTE_WPS 4
static bool available,wps_return_agents,wps_menu_covers;
static int result,target;
static bool cider_available(void){return available;}
static int agents_screen(void){return result;}
static int agents_nav_pending(void){return 0;}
static void agents_nav_request(int a,bool remote){assert(a==2&&!remote);}
static void run_screen_shortcut(int screen){target=screen;}
static void cover_nav_begin(int origin){assert(origin==GO_TO_AGENTS);}
static int album_covers_scrn(void*p){(void)p;return GO_TO_PREVIOUS;}
'''
post=r'''
int main(void){for(int a=0;a<2;a++)for(int remote=0;remote<2;remote++){available=a;result=remote?AGENTS_SWITCH_REMOTE_WPS:AGENTS_SWITCH_WPS;assert(agents_scrn(NULL)==(a?GO_TO_CIDER:GO_TO_WPS));root_menu_wps_shortcut();assert(target==(a?GO_TO_CIDER:GO_TO_WPS));}puts("Cider routing: both Play double/chord WPS destinations -> Cider available / native unavailable PASS");}
'''
(O/'route.c').write_text(pre+fn(root,'agents_scrn')+fn(root,'root_menu_wps_shortcut')+post)
subprocess.run(cmd+[str(O/'route.c'),'-o',str(O/'route')],check=True);subprocess.run([str(O/'route')],check=True)
