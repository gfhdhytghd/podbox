"""Production shortcut routing/controller/lifetimes with instrumented screen bodies.
No UI or device: a stack/app-buffer model tests cleanup before each entry.
"""
from pathlib import Path
import re,subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
legacy=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else None
S=legacy or R

def fn(text,name):
 m=re.search(r'(?m)^(?:static[ \t]+)?(?:inline[ \t]+)?(?:int|long|bool|void)[ \t*]+'+name+r'\([^;{}]*\)\s*\{',text);assert m,name
 end=m.end();depth=1
 while depth:
  depth+=(text[end]=='{')-(text[end]=='}');end+=1
 return text[m.start():end]
root=(S/'apps-ipod/root_menu.c').read_text();action=(S/'apps-ipod/input/action.c').read_text()
current_wps=(R/'apps-ipod/screens/playback/wps.c').read_text()
current_agents=(R/'apps-ipod/screens/system/agents_screen.c').read_text()
current_covers=(R/'apps-ipod/screens/covers/carousel.c').read_text()
h=(R/'apps-ipod/input/agents_shortcut.h').read_text();c=(R/'apps-ipod/input/agents_shortcut.c').read_text()
api=h[h.index('enum agents_nav_screen'):h.rfind('#endif')]
nav=c[c.index('static bool cover_journey'):c.index('int agents_shortcut_button(')]
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdint.h>
#include <stddef.h>
#define HAVE_USB_PODLINK
#define GO_TO_PREVIOUS -1
#define GO_TO_ROOT -2
#define GO_TO_AGENTS 10
#define GO_TO_WPS 11
#define GO_TO_ALBUM_COVERS 12
#define GO_TO_ALBUM_COVERS_TRACKS 13
#define CAROUSEL_REMOTE_RETURN -22
#define CAROUSEL_CONTEXT -21
#define CAROUSEL_PLAY_ALBUM -20
#define AGENTS_SWITCH_WPS 2
#define AGENTS_SWITCH_COVERS 3
#define AGENTS_SWITCH_REMOTE_WPS 4
#define ACTION_NONE 0
#define ACTION_STD_CANCEL 1
#define ACTION_REDRAW 2
#define ACTION_AGENTS_SWITCH 3
#define ACTION_AGENTS_REMOTE_SWITCH 4
#define ACTION_AGENTS_COVERS 5
#define ACTION_AGENTS_REMOTE_COVERS 6
#define ACTION_REMOTE_WPS 7
#define ACTION_WPS_MENU 8
#define ACTION_WPS_STOP 9
#define ACTION_WPS_BROWSE 100
#define ACTION_WPS_QUICKSCREEN 101
#define AUDIO_STATUS_PLAY 1
#define WPS_MENU_PREVIOUS 1
#define ACTIVITY_UNKNOWN 0
#define ACTIVITY_AGENTS 1
#define ACTIVITY_WPS 2
#define ACTIVITY_ALBUMCOVERS 3
#define CONTEXT_AGENTS 1
#define CONTEXT_WPS 2
#define CONTEXT_PLUGIN 3
#define CONTEXT_SETTINGS 4
#define EXIT_TEST 50
#define CHILD_TEST 51
#define TRACKS_TEST 52
#define PLAY_TEST 53
#define STAY 99
static struct {int wps_menu_action;}global_settings;
static bool wps_return_agents,wps_menu_covers,poweroff=true;
static int owner,activity,depth,max_depth,enters[5],exits[5];
static unsigned long events;
static uint32_t rng=1;
static int fixed[16],nfixed,atfixed;
static unsigned int budget;
static int get_current_activity(void){return activity;}
static int __attribute__((unused)) app_buffer_is_claimed(void){return owner!=0;}
static bool button_get_sw_poweroff_state(void){return poweroff;}
static void button_set_sw_poweroff_state(bool on){poweroff=on;}
static int audio_status(void){return AUDIO_STATUS_PLAY;}
static void gwps_leave_wps(bool on){(void)on;}
static void finish_talk(bool discard){assert(discard);}
static bool native_play_pending;
static bool tracks_valid=true;
static void album_covers_cancel_resume(void){tracks_valid=false;}
static bool album_covers_reopen_tracks(void){return tracks_valid;}
static bool wait_for_album_tables(void){return true;}
static int cover_tracks_scrn(void*);
static int agents_screen(void);
static int wpsscrn(void*);
static int album_covers_scrn(void*);
static int album_covers_return(const char*,bool*);
static int carousel_body(int,const char*);
static int album_covers_scrn(void*);
void root_menu_agents_shortcut(void);
void root_menu_wps_shortcut(void);
void root_menu_cover_shortcut(void);
static int route_screen_shortcut(int,int);
static int next_action(void){
 if(nfixed){if(atfixed<nfixed)return fixed[atfixed++];return EXIT_TEST;}
 if(!budget)return EXIT_TEST;
 budget--;rng=rng*1664525u+1013904223u;
 static const int choices[]={ACTION_AGENTS_SWITCH,ACTION_AGENTS_REMOTE_SWITCH,
 ACTION_AGENTS_COVERS,ACTION_AGENTS_REMOTE_COVERS,ACTION_REMOTE_WPS,
 ACTION_WPS_MENU,TRACKS_TEST,PLAY_TEST,CHILD_TEST,EXIT_TEST};
 int action=choices[(rng>>16)%10];return action==EXIT_TEST?ACTION_NONE:action;
}
static void enter_model(int screen){
 if(screen==3&&owner){fputs("app_buffer: album covers wants it, album covers holds it\n",stderr);assert(!owner);}
 assert(!enters[screen]);enters[screen]=1;exits[screen]++;depth++;
 if(depth>max_depth)max_depth=depth;
 assert(depth<=2);events++;
}
static void leave_model(int screen){assert(enters[screen]);enters[screen]=0;depth--;}
'''
# Instrumented bodies use the real local action cases and deferred-request arms.
a=current_agents.index('            case ACTION_AGENTS_REMOTE_COVERS:');b=current_agents.index('            case ACTION_AGENTS_TALK_END:',a)
agent_cases=current_agents[a:b]
a=current_agents.index('        enum agents_nav_screen nav =',current_agents.index('static int agents_run(void)\n{'));b=current_agents.index('        bool changed',a)
agent_pending=current_agents[a:b]
a=current_wps.index('            case ACTION_AGENTS_REMOTE_SWITCH:');b=current_wps.index('            case ACTION_WPS_BROWSE:',a)
wps_double=current_wps[a:b].replace("#endif", "")
gui=current_wps[current_wps.index('static long wps_run(bool return_agents, bool menu_covers)\n{'):]
a=gui.index('            case ACTION_WPS_MENU:');b=gui.index('            case ACTION_WPS_QUICKSCREEN:',a);wps_menu=gui[a:b]
a=gui.index('            case ACTION_WPS_STOP:');b=gui.index('            /* Bound only',a);wps_cover=gui[a:b]
a=gui.index('        enum agents_nav_screen nav =');b=gui.index('        bool audio_paused',a);wps_pending=gui[a:b]
a=current_covers.index('        enum agents_nav_screen nav =',current_covers.index('static int album_covers_loop(void)'));b=current_covers.index('        case PF_QUIT:',a)
cover_cases=current_covers[a:b]+'        default: break;\n        }\n return STAY;\n'
helpers=fn(current_wps,'show_album_covers')
bodies=r'''
static void child_menu(void){
 int old_owner=owner;
 if(!owner)owner=2+(rng&1); /* playlist viewer / album charts model */
 int before=depth;
 int action=route_screen_shortcut(ACTION_AGENTS_REMOTE_COVERS,CONTEXT_SETTINGS);
 assert(action==ACTION_STD_CANCEL);assert(depth==before);
 assert(owner);owner=old_owner;
}
static int agents_run(void){
 bool done=false;int ret=0;
 while(!done){
AGENT_PENDING
 int action=next_action();events++;
 if(action==EXIT_TEST)return 0;
 if(action==CHILD_TEST){child_menu();continue;}
 action=route_screen_shortcut(action,CONTEXT_AGENTS);
 switch(action){
AGENT_CASES
 case ACTION_WPS_MENU: done=true;break;
 default:break;}
 }
 return ret;
}
static long wps_run(bool return_agents,bool menu_covers){
 bool restore=false,theme_enabled=true;
 while(1){
 WPS_PENDING
 int button=next_action();events++;
 if(button==EXIT_TEST)return GO_TO_ROOT;
 if(button==CHILD_TEST){child_menu();continue;}
 button=route_screen_shortcut(button,CONTEXT_WPS);
 switch(button){
 WPS_DOUBLE
 WPS_MENU
 WPS_COVER
 default:break;}
 (void)restore;(void)theme_enabled;
 }
}
static int cover_decide(int button){
COVER_CASES
}
static int carousel_body(int model,const char*file){
 (void)model;(void)file;assert(!owner);owner=1;
 for(;;){
 int action=next_action();events++;
 if(action==CHILD_TEST){child_menu();action=ACTION_NONE;}
 if(action==EXIT_TEST){owner=0;return GO_TO_PREVIOUS;}
 if(action==TRACKS_TEST||action==PLAY_TEST){
  tracks_valid=true;owner=0;cover_nav_set_playing(action==PLAY_TEST);
  return action==PLAY_TEST?GO_TO_WPS:GO_TO_ALBUM_COVERS_TRACKS;
 }
 action=route_screen_shortcut(action,CONTEXT_PLUGIN);
 int ret=cover_decide(action);
 if(ret!=STAY){assert(owner==1);owner=0;return ret;}
 }
}
static int agents_screen(void){
 ENTER_A
 enter_model(AGENTS_NAV_AGENTS);int old=activity;activity=ACTIVITY_AGENTS;
 int ret=agents_run();activity=old;leave_model(AGENTS_NAV_AGENTS);
 LEAVE_A
 return ret;
}
static int wpsscrn(void*p){
 (void)p;ENTER_W
 enter_model(AGENTS_NAV_WPS);int old=activity;activity=ACTIVITY_WPS;
 bool ret_agents=wps_return_agents,menu=wps_menu_covers;
 wps_return_agents=wps_menu_covers=false;
 int ret=wps_run(ret_agents,menu);activity=old;leave_model(AGENTS_NAV_WPS);
 LEAVE_W
 return ret;
}
static int album_covers_return(const char*file,bool*remote){
 ENTER_C
 enter_model(AGENTS_NAV_COVERS);int old=activity;activity=ACTIVITY_ALBUMCOVERS;
 int ret=carousel_body(0,file);activity=old;leave_model(AGENTS_NAV_COVERS);
 LEAVE_C
 if(remote)*remote=ret==CAROUSEL_REMOTE_RETURN;
 return ret==CAROUSEL_REMOTE_RETURN?GO_TO_PREVIOUS:ret;
}
static int browser(void*p){
 (void)p;enter_model(AGENTS_NAV_TRACKS);assert(!owner);int ret_val;
 for(;;){
  int action=next_action();events++;
  if(action==CHILD_TEST){child_menu();}
  action=route_screen_shortcut(action,CONTEXT_SETTINGS);
  if(action==EXIT_TEST||action==ACTION_WPS_MENU||agents_nav_pending()!=AGENTS_NAV_NONE){ret_val=GO_TO_PREVIOUS;break;}
  if(action==PLAY_TEST){ret_val=GO_TO_WPS;break;}
 }
 switch(0){case 0:
 LIST_RETURN
 }
 leave_model(AGENTS_NAV_TRACKS);return ret_val;
}

'''
a=root.index('            if (cover_nav_active())',root.index('        case GO_TO_ALBUM_COVERS_TRACKS:',root.index('        case GO_TO_ALBUM_COVERS_TRACKS:')+1));b=root.index('            if (ret_val == GO_TO_ROOT)',a)
list_return=root[a:b]
bodies=bodies.replace(' LIST_RETURN',list_return)
tokens=['AGENT_PENDING','AGENT_CASES','WPS_PENDING','WPS_DOUBLE','WPS_MENU','WPS_COVER','COVER_CASES']
for key in tokens:bodies=re.sub(r'(?m)^\s*'+key+r'\s*$', '@'+key+'@',bodies)
for key,value in [('AGENT_PENDING',agent_pending),('AGENT_CASES',agent_cases),('WPS_PENDING',wps_pending),('WPS_DOUBLE',wps_double),('WPS_MENU',wps_menu),('WPS_COVER',wps_cover),('COVER_CASES',cover_cases)]:bodies=bodies.replace('@'+key+'@',value)
for char,name,ret in [('A','AGENTS','5'),('W','WPS','GO_TO_PREVIOUS'),('C','COVERS','GO_TO_PREVIOUS')]:
 enter='' if legacy else f'if(!agents_nav_enter(AGENTS_NAV_{name}))return {ret};'
 leave='' if legacy else f'agents_nav_leave(AGENTS_NAV_{name});'
 bodies=bodies.replace('ENTER_'+char,enter).replace('LEAVE_'+char,leave)
if legacy:
 # Exact pre-V2 global routing block, which invokes a new modal controller.
 t=fn(action,'get_custom_action');a=t.index('#ifdef HAVE_USB_PODLINK\n    if (action == ACTION_REMOTE_WPS');b=t.index('    return action;',a)
 route='static int route_screen_shortcut(int action,int context){\n'+t[a:b]+'return action;}\n'
 root_funcs=''.join(fn(root,n) for n in ['agents_scrn','root_menu_agents_shortcut','root_menu_wps_shortcut','root_menu_cover_shortcut'])
else:
 route=fn(action,'shortcut_screen_context')+fn(action,'route_screen_shortcut')
 root_funcs=''.join(fn(root,n) for n in ['album_covers_scrn','cover_tracks_scrn','agents_scrn','run_screen_shortcut','root_menu_agents_shortcut','root_menu_wps_shortcut','root_menu_cover_shortcut'])
main=r'''
int main(void){
 /* Exact reported panic path: Cover -> double -> Agents -> triple -> Cover. */
 fixed[0]=ACTION_AGENTS_REMOTE_SWITCH;fixed[1]=ACTION_AGENTS_REMOTE_COVERS;
 fixed[2]=EXIT_TEST;nfixed=3;atfixed=0;root_menu_cover_shortcut();
 assert(!owner&&!depth&&!agents_nav_active());
 agents_nav_take(agents_nav_pending());
 fixed[0]=ACTION_REMOTE_WPS;fixed[1]=ACTION_AGENTS_REMOTE_COVERS;
 fixed[2]=EXIT_TEST;nfixed=3;atfixed=0;root_menu_cover_shortcut();
 assert(!owner&&!depth&&!agents_nav_active());
 agents_nav_take(agents_nav_pending());
 fixed[0]=ACTION_AGENTS_REMOTE_SWITCH;fixed[1]=ACTION_WPS_MENU;
 fixed[2]=ACTION_AGENTS_REMOTE_SWITCH;fixed[3]=ACTION_AGENTS_COVERS;
 fixed[4]=EXIT_TEST;nfixed=5;atfixed=0;root_menu_agents_shortcut();
 assert(!owner&&!depth&&!agents_nav_active());
 agents_nav_take(agents_nav_pending());
 /* Actual list back/play branch and WPS MENU arm, with logical history. */
 for(int origin=0;origin<3;origin++){
  fixed[0]=TRACKS_TEST;fixed[1]=PLAY_TEST;fixed[2]=ACTION_WPS_MENU;
  fixed[3]=ACTION_WPS_MENU;fixed[4]=EXIT_TEST;nfixed=5;atfixed=0;
  int lists=exits[AGENTS_NAV_TRACKS];
  cover_nav_begin(origin==0?GO_TO_PREVIOUS:origin==1?GO_TO_AGENTS:GO_TO_WPS);
  root_menu_cover_shortcut();
  assert(exits[AGENTS_NAV_TRACKS]==lists+2&&!cover_nav_active());
  assert(!depth&&!owner&&!agents_nav_active());
 }
 fixed[0]=TRACKS_TEST;fixed[1]=ACTION_REMOTE_WPS;fixed[2]=ACTION_WPS_MENU;
 fixed[3]=ACTION_WPS_MENU;fixed[4]=EXIT_TEST;nfixed=5;atfixed=0;
 int lists=exits[AGENTS_NAV_TRACKS];root_menu_cover_shortcut();
 assert(exits[AGENTS_NAV_TRACKS]==lists+2&&!cover_nav_active()&&!depth&&!owner);
 fixed[0]=PLAY_TEST;fixed[1]=ACTION_WPS_MENU;fixed[2]=ACTION_WPS_MENU;
 fixed[3]=EXIT_TEST;nfixed=4;atfixed=0;root_menu_cover_shortcut();
 assert(!cover_nav_active()&&!depth&&!owner);nfixed=0;
 for(unsigned int seed=1;seed<=2000;seed++){
  rng=seed;atfixed=0;budget=512;
  while(budget){
   if(seed%3==0)root_menu_cover_shortcut();
   else if(seed%3==1)root_menu_agents_shortcut();else root_menu_wps_shortcut();
   assert(!owner&&!depth&&!agents_nav_active());
   assert(agents_nav_pending()==AGENTS_NAV_NONE);
  }
 }
 /* An unrelated owner must unwind before a controller enters any screen. */
 owner=3;unsigned long before=events;root_menu_cover_shortcut();
 assert(owner==3&&events==before&&agents_nav_pending()==AGENTS_NAV_COVERS);
 owner=0;budget=100;root_menu_cover_shortcut();assert(!owner&&!depth);
 printf("production router/controller + stack/app-buffer model: 2000 long seeded sequences (512 generated actions each), %lu events, max live screen depth=%d, no duplicate screen/owner, panic path + album-list/WPS/origin history + child-owner cancellation PASS\n",events,max_depth);
}
'''
(O/'nav.c').write_text(pre+api+nav+helpers+bodies+route+root_funcs+main)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie',str(O/'nav.c'),'-o',str(O/'nav')],check=True)
subprocess.run([str(O/'nav')],check=True,timeout=30)

if not legacy:
 for filename,name,screen in [('screens/system/agents_screen.c','agents_screen','AGENTS'),('screens/playback/wps.c','gui_wps_show','WPS'),('screens/covers/carousel.c','carousel_run','COVERS')]:
  body=fn((R/'apps-ipod'/filename).read_text(),name)
  assert 'agents_nav_enter(AGENTS_NAV_'+screen+')' in body
  assert 'agents_nav_leave(AGENTS_NAV_'+screen+')' in body
 for name in ('get_action','get_custom_action'):
  body=fn(action,name)
  assert body.index('agents_nav_pending()')<body.index('init_act_cur(')
 assert 'agents_nav_pending() != AGENTS_NAV_NONE && !app_buffer_is_claimed()' in root
 print('Actual public lifetime wrappers, child-menu cancellation and root-owner-unwind wiring PASS')
