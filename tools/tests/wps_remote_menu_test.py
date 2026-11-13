"""FU18 production WPS MENU arm and remote-entry lifetime; no UI/device."""
from pathlib import Path
import re,subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
S=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else R
wps=(S/'apps-ipod/screens/playback/wps.c').read_text()
a=wps.index('            case ACTION_WPS_MENU:');b=wps.index('            case ACTION_WPS_QUICKSCREEN:',a)
menu=wps[a:b]
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#define GO_TO_AGENTS 1
#define GO_TO_PREVIOUS 2
#define GO_TO_ROOT 3
#define ACTION_WPS_MENU 4
#define WPS_MENU_PREVIOUS 5
static struct {int wps_menu_action;}global_settings;
static int covers,leaves;
static bool remote_return,exit_covers;
static bool restore __attribute__((unused)),theme_enabled __attribute__((unused));
static void gwps_leave_wps(bool on){assert(on);leaves++;}
static bool __attribute__((unused)) show_album_covers(long *go,bool *menu_covers){
 covers++;if(remote_return)*menu_covers=true;*go=GO_TO_ROOT;return exit_covers;
}
static int menu(bool return_agents,bool menu_covers){
 (void)menu_covers;restore=false;
 switch(ACTION_WPS_MENU){
'''
post=r'''
 }return restore?77:-1;
}
int main(void){
 global_settings.wps_menu_action=0;
 assert(menu(true,true)==77&&covers==1&&!leaves);
 assert(menu(false,true)==77&&covers==2&&!leaves);
 assert(menu(false,false)==GO_TO_ROOT&&leaves==1);
 assert(menu(true,false)==GO_TO_AGENTS&&leaves==2);
 global_settings.wps_menu_action=WPS_MENU_PREVIOUS;
 assert(menu(false,false)==GO_TO_PREVIOUS&&leaves==3);
 assert(menu(true,true)==77&&covers==3&&leaves==3);
 exit_covers=true;assert(menu(false,true)==GO_TO_ROOT&&covers==4);
 puts("WPS MENU: remote provenance -> modal Cover Flow/restore; other entries retain base exits; child destinations propagate PASS");
}
'''
(O/'menu.c').write_text(pre+menu+post)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie',str(O/'menu.c'),'-o',str(O/'menu')],check=True)
subprocess.run([str(O/'menu')],check=True)

def function(text,name):
 m=re.search(r'(?m)^(?:static[ \t]+)?(?:int|long|bool)[ \t*]+'+name+r'\([^;{}]*\)\s*\{',text);assert m,name
 end=m.end();depth=1
 while depth:
  depth+=(text[end]=='{')-(text[end]=='}');end+=1
 return text[m.start():end]
root=(R/'apps-ipod/root_menu.c').read_text()
albums=(R/'apps-ipod/screens/covers/album_covers.c').read_text()
nav=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <stddef.h>
#define AGENTS_SWITCH_WPS 2
#define AGENTS_SWITCH_COVERS 3
#define AGENTS_SWITCH_REMOTE_WPS 4
#define GO_TO_PREVIOUS -1
#define GO_TO_WPS 10
#define GO_TO_AGENTS 11
#define GO_TO_ROOT 12
#define GO_TO_ALBUM_COVERS_TRACKS 13
#define CAROUSEL_REMOTE_RETURN -22
#define CAROUSEL_CONTEXT -21
#define CAROUSEL_PLAY_ALBUM -20
#define AUDIO_STATUS_PLAY 1
static bool wps_return_agents,wps_menu_covers;
static int agent_result,covers_result,playing=1;
static int agents_screen(void){return agent_result;}
static int album_covers_scrn(void*p){(void)p;return GO_TO_PREVIOUS;}
static int audio_status(void){return playing;}
static int false_leaves,true_leaves;
static void gwps_leave_wps(bool on){if(on)true_leaves++;else false_leaves++;}
static int album_model;
static int carousel_run(int*m,const char*file){(void)m;(void)file;return covers_result;}
static int carousel_cover_menu(void){return GO_TO_ROOT;}
static void album_covers_cancel_resume(void){}
static int db_summary_play_pending(void){return 1;}
int album_covers_return(const char*,bool*);
'''
nav+=function(albums,'album_covers')+function(albums,'album_covers_return')
nav+=function((R/'apps-ipod/screens/playback/wps.c').read_text(),'show_album_covers')
nav+=function(root,'agents_scrn')
a=root.index('    bool return_agents =',root.index('static int wpsscrn('));b=root.index('    int audstatus',a)
nav+='\nstatic int consume(void){'+root[a:b]+'return return_agents*2+menu_covers;}\n'
nav+=r'''
int main(void){
 agent_result=AGENTS_SWITCH_REMOTE_WPS;assert(agents_scrn(NULL)==GO_TO_WPS);
 assert(wps_return_agents&&wps_menu_covers);assert(consume()==3);assert(consume()==0);
 agent_result=AGENTS_SWITCH_WPS;assert(agents_scrn(NULL)==GO_TO_WPS);
 assert(wps_return_agents&&!wps_menu_covers);assert(consume()==2);assert(consume()==0);
 agent_result=0;assert(agents_scrn(NULL)==GO_TO_PREVIOUS&&!wps_menu_covers);
 bool flag=false,remote=true;long go=0;
 covers_result=GO_TO_PREVIOUS;assert(!show_album_covers(&go,&flag)&&!flag);
 covers_result=CAROUSEL_REMOTE_RETURN;assert(!show_album_covers(&go,&flag)&&flag);
 covers_result=GO_TO_PREVIOUS;assert(!show_album_covers(&go,&flag)&&flag);
 /* An ordinary later WPS invocation starts unmarked; no static child flag. */
 flag=false;assert(!show_album_covers(&go,&flag)&&!flag);
 covers_result=CAROUSEL_REMOTE_RETURN;assert(album_covers(NULL)==GO_TO_PREVIOUS);
 covers_result=GO_TO_PREVIOUS;assert(album_covers_return(NULL,&remote)==GO_TO_PREVIOUS&&!remote);
 covers_result=GO_TO_ROOT;assert(show_album_covers(&go,&flag)&&go==GO_TO_ROOT);
 covers_result=GO_TO_ALBUM_COVERS_TRACKS;assert(show_album_covers(&go,&flag)&&go==GO_TO_ALBUM_COVERS_TRACKS);
 covers_result=GO_TO_PREVIOUS;playing=0;assert(show_album_covers(&go,&flag)&&go==GO_TO_WPS);
 assert(false_leaves==7&&true_leaves==3);
 puts("production entry/child routes: remote double vs native chord; pending flag consumed; remote triple tags WPS; normal/native return does not; nested-safe output; old callers sanitized; audio/destination exits PASS");
}
'''
(O/'origins.c').write_text(nav)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie',str(O/'origins.c'),'-o',str(O/'origins')],check=True)
subprocess.run([str(O/'origins')],check=True)
assert root.count('gui_wps_show(return_agents, menu_covers)')==3
assert root.index('wps_menu_covers = false',root.index('static int wpsscrn('))<root.index('if (usb_audio_get_active',root.index('static int wpsscrn('))
keys=(R/'apps-ipod/input/keymap_ipod.c').read_text()
for button in ['BUTTON_MENU','BUTTON_RC_MENU']:
 assert re.search(r'ACTION_WPS_MENU,\s*'+button+r'\|BUTTON_REL',keys)
assert re.search(r'ACTION_WPS_QUICKSCREEN,\s*BUTTON_MENU\|BUTTON_REPEAT',keys)
print('wheel/remote MENU share action; long MENU remains QuickScreen; provenance cleared before early WPS refusal PASS')
# Only inspect the actual GUI input switch, not similarly named party filters.
gui=(R/'apps-ipod/screens/playback/wps.c').read_text().split('long gui_wps_show(',1)[1]
a=gui.index('            case ACTION_WPS_STOP:');b=gui.index('            /* Bound only',a)
assert 'case ACTION_AGENTS_REMOTE_COVERS:' in gui[a:b]
assert 'case ACTION_AGENTS_COVERS:' in gui[a:b]
assert 'show_album_covers(&go_to, &menu_covers)' in gui[a:b]
assert 'case ACTION_AGENTS_REMOTE_SWITCH:' in gui
print('WPS cover/double actions belong to the real input switch (not just party filtering) PASS')
