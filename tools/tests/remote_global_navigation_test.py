"""Global volume source policy and action routing, production C, no device."""
from pathlib import Path
import ast,re,subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
S=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else R
(O/'config.h').write_text('')
t=ast.parse((R/'tools/tests/agents_volume_sources_test.py').read_text())
pre=next(ast.literal_eval(n.value) for n in t.body if isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='pre' for x in n.targets))
parts=['firmware/usbstack/remote_log.c','firmware/drivers/button_usb_remote.c','apps-ipod/iap/remote_capture.c','apps-ipod/iap/agents_volume.c']
source=pre+'\n'.join(re.sub(r'^#include .*$', '',(S/p).read_text(),flags=re.M) for p in parts)
if 'void iap_volume_context(' not in source:
 source+='\nvoid iap_volume_context(bool wps){(void)wps;}\nint iap_volume_filter(int b){return b;}\n'
inc=(R/'tools/tests/agents_volume_sources_test.inc').read_text();inc=inc[:inc.index('#include "apps-ipod/input/agents_remote.h"')]
post=r'''
static void screen(int a,bool wps){activity=a;iap_volume_context(wps);}
int main(void){
 reset_sources(false);screen(0,false);test_link=true;assert(dock(100,15)&&global_status.volume==12);
 const int screens[]={ACTIVITY_AGENTS,ACTIVITY_WPS,0,9,10,11,12,13,14};
 for(unsigned i=0;i<sizeof(screens)/sizeof(*screens);i++){
  reset_sources(true);screen(screens[i],screens[i]==ACTIVITY_WPS);test_link=true;
  for(int n=0;n<10;n++){assert(dock(100+n*10,15+n*3));assert(!take_at(100+n*10));}
  assert(global_status.volume==12);
  unsigned char value;bool mute;
  assert(iap_agents_volume_get(&value,&mute)==(screens[i]!=ACTIVITY_WPS));
  pc(201,1,true);pc(202,-1,false);
  assert(iap_volume_filter(take_at(202))==(screens[i]==ACTIVITY_WPS?BUTTON_RC_VOL_UP:UP));
  assert(iap_volume_filter(take_at(202))==(screens[i]==ACTIVITY_WPS?BUTTON_RC_VOL_DOWN:DOWN));
  assert(!iap_volume_filter(BUTTON_RC_VOL_UP));assert(!iap_volume_filter(BUTTON_RC_VOL_DOWN|BUTTON_REL));
  assert(global_status.volume==12);
 }
 reset_sources(false);screen(0,false);heartbeat(100);pc(101,1,true);assert(take_at(101)==UP);
 assert(dock(102,15)&&!take_at(102));assert(dock(400,18)&&take_at(400)==UP);
 reset_sources(false);screen(ACTIVITY_WPS,true);
 unsigned long rows=remote_volume_count();
 assert(!dock(100,15)&&global_status.volume==15&&!take_at(100));
 assert(remote_volume_count()==rows+1);
 assert(iap_volume_filter(BUTTON_RC_VOL_UP)==BUTTON_RC_VOL_UP);
 /* WPS child/QuickScreen contexts cannot change real volume. */
 iap_volume_context(false);assert(dock(110,18)&&global_status.volume==15);
 assert(take_at(110)==UP);
 /* No computer: same Dock grouping and 600/250ms hold as Agents. */
 reset_sources(false);screen(0,false);assert(dock(100,15)&&take_at(100)==UP);
 for(int t=110;t<160;t+=10){dock(t,18+(t-110)*3/10);assert(!take_at(t));}
 dock(160,33);assert(take_at(160)==UP);dock(170,36);assert(!take_at(170));
 dock(190,39);assert(take_at(190)==UP);assert(global_status.volume==12);
 /* Usage/detail delegate only while connected; disconnected navigation is silent. */
 reset_sources(false);iap_agents_volume_host_keys(true);test_link=true;
 pc(100,1,true);assert(!take_at(100));assert(dock(101,15)&&!take_at(101));
 test_link=false;assert(dock(102,18)&&take_at(102)==UP&&global_status.volume==12);
 /* A changed screen/mode or lost link cannot replay an old PC batch. */
 reset_sources(true);screen(0,false);test_link=true;pc(100,1,true);
 screen(ACTIVITY_WPS,true);assert(!take_at(101));pc(102,-1,true);iap_volume_context(false);assert(!take_at(103));
 pc(104,1,true);test_link=false;assert(!take_at(105));assert(dock(106,15)&&take_at(106)==UP);
 remote_input_active=true;
 assert(!iap_volume_filter(UP)&&!iap_volume_filter(BUTTON_RC_VOL_UP));
 assert(dock(200,18)&&!take_at(200)&&global_status.volume==12);
 remote_input_active=false;
 /* Old firmware mode had no non-Agents PC input and allowed Dock audio. */
 puts("nine screen policies, PC-only connected, Dock disconnected 300/600/250ms, WPS-only audio, stale-scope drop, Usage/detail host delegation PASS");
}
'''
(O/'global.c').write_text(source+inc+post)
cmd=['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(O),'-I'+str(R)]
subprocess.run(cmd+[str(O/'global.c'),'-o',str(O/'global')],check=True);subprocess.run([str(O/'global')],check=True)
def fn(text,name):
 m=re.search(r'(?m)^(?:static[ \t]+)?(?:bool|int)[ \t*]+'+name+r'\([^;{}]*\)\s*\{',text);assert m,name
 end=m.end();depth=1
 while depth:depth+=(text[end]=='{')-(text[end]=='}');end+=1
 return text[m.start():end]
action=(S/'apps-ipod/input/action.c').read_text()
prefix=pre+'''
#define CONTEXT_AGENTS 1
#define CONTEXT_WPS 2
#define CONTEXT_PLUGIN 3
#define ACTIVITY_ALBUMCOVERS 9
#define ACTION_STD_PREV 501
#define ACTION_STD_NEXT 502
#define ACTION_WPS_VOLUP 503
#define ACTION_WPS_VOLDOWN 504
static bool test_cider_mode;
static bool cider_active(void){return test_cider_mode;}
static int __attribute__((unused)) disable_irq_save_unused;
typedef struct {int button,context,action;} action_cur_t;
'''
# Unused source-harness mocks are irrelevant to this pure action probe.
prefix=prefix.replace('static ', 'static __attribute__((unused)) ')
test=r'''
int main(void){
 for(int c=0;c<20;c++)for(int d=0;d<2;d++){
  activity=0;action_cur_t cur={.button=d?DOWN:UP,.context=c};bool routed=remote_volume_action(&cur);
  if(c==CONTEXT_AGENTS||c==CONTEXT_WPS)assert(!routed&&cur.button==(d?DOWN:UP));
  else assert(routed&&cur.action==(d?ACTION_STD_NEXT:ACTION_STD_PREV)&&cur.button==(d?DOWN:UP));
 }
 activity=ACTIVITY_ALBUMCOVERS;action_cur_t cur={.button=UP,.context=CONTEXT_PLUGIN};assert(!remote_volume_action(&cur)&&cur.button==BUTTON_SCROLL_BACK);
 cur=(action_cur_t){.button=DOWN,.context=CONTEXT_PLUGIN};assert(!remote_volume_action(&cur)&&cur.button==BUTTON_SCROLL_FWD);
 test_cider_mode=true;
 action_cur_t cider={.button=BUTTON_RC_VOL_UP|BUTTON_REL|BUTTON_REPEAT,.context=CONTEXT_WPS};
 assert(remote_volume_action(&cider)&&cider.action==ACTION_WPS_VOLUP);
 cider.button=BUTTON_RC_VOL_DOWN|BUTTON_REL|BUTTON_REPEAT;
 assert(remote_volume_action(&cider)&&cider.action==ACTION_WPS_VOLDOWN);
 test_cider_mode=false;
 puts("production action routing: all generic contexts list-only, WPS/Agents native arms, carousel previous/next PASS");
}
'''
(O/'action.c').write_text(prefix+fn(action,'remote_volume_action')+test)
subprocess.run(cmd+[str(O/'action.c'),'-o',str(O/'action')],check=True);subprocess.run([str(O/'action')],check=True)

# The production worker must return the routed action while retaining raw RC
# provenance for first-key backlight handling and zero wheel acceleration.
worker_pre=prefix+'''
#define ACTION_UNKNOWN -1
#define GUI_EVENT_ACTIONUPDATE 1
typedef struct {int unused;} action_last_t;
static void send_event(int id,void*p){(void)id;(void)p;}
static bool action_poll_button(action_last_t*l,action_cur_t*c){(void)l;(void)c;return true;}
static void update_screen_has_lock(action_last_t*l,action_cur_t*c){(void)l;(void)c;}
static bool get_action_touchscreen(action_last_t*l,action_cur_t*c){(void)l;(void)c;return false;}
static void do_softlock(action_last_t*l,action_cur_t*c){(void)l;(void)c;}
static void button_flip_horizontally(int c,int*b){(void)c;(void)b;}
static void action_code_lookup(action_last_t*l,action_cur_t*c){(void)l;(void)c;}
static int update_action_last(action_last_t*l,action_cur_t*c){(void)l;return c->action;}
'''
worker_test='''
int main(void){action_last_t last={0};action_cur_t cur={.button=UP,.action=ACTION_STD_PREV};
assert(get_action_worker(&last,&cur)==ACTION_STD_PREV&&cur.button==UP);
cur=(action_cur_t){.button=12345,.action=ACTION_UNKNOWN};assert(get_action_worker(&last,&cur)==12345);
puts("production worker: routed action return, raw remote provenance, legacy early actions PASS");}
'''
(O/'worker.c').write_text(worker_pre+fn(action,'get_action_worker')+worker_test)
subprocess.run(cmd+[str(O/'worker.c'),'-o',str(O/'worker')],check=True);subprocess.run([str(O/'worker')],check=True)
