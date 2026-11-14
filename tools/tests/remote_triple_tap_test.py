"""Production remote tap state and Cover Flow origin routes, offline only."""
from pathlib import Path
import re,subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
def function(text,name):
 m=re.search(r'(?m)^[\w *]+\b'+name+r'\([^;{}]*\)\s*\{',text);assert m,name
 end=m.end();depth=1
 while depth:
  depth+=(text[end]=='{')-(text[end]=='}');end+=1
 return text[m.start():end]
S=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else R
(O/'config.h').write_text('')
source=re.sub(r'^#include .*$', '',(S/'apps-ipod/input/agents_remote.c').read_text(),flags=re.M)
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <limits.h>
#define REMOTE_LOG_DISABLE
#include "firmware/export/remote_log.h"
#define IPOD_ACCESSORY_PROTOCOL
#include "firmware/target/arm/ipod/button-target.h"
#define BUTTON_NONE 0
#define BUTTON_REL 0x02000000
#define BUTTON_REPEAT 0x04000000
#define BUTTON_REDRAW 0x20000000
#define HZ 100
#include "apps-ipod/input/agents_remote.h"
static struct agents_remote_state state;
static int output[128],used;
static void reset(void){state=(struct agents_remote_state){0};used=0;}
static void feed(int context,int button,int held,unsigned long now){
 int result=agents_remote_button(&state,context,button,held,now);
 if(result)output[used++]=result;
 while(state.queued){result=agents_remote_button(&state,context,0,held,now);if(result)output[used++]=result;}
 assert(used<128);
}
static int count(int button){int n=0;for(int i=0;i<used;i++)n+=output[i]==button;return n;}
static void tap(int c,unsigned long at,int length){feed(c,BUTTON_RC_PLAY,BUTTON_RC_PLAY,at);feed(c,BUTTON_RC_PLAY|BUTTON_REL,0,at+length);}
'''
post=r'''
int main(void){
 for(int mode=REMOTE_OTHER;mode<=REMOTE_COVERS;mode++){
  reset();tap(mode,100,13);feed(mode,0,0,133);assert(!count(BUTTON_SELECT)&&!count(REMOTE_SWITCH_BUTTON));
  feed(mode,0,0,134);
  assert(count(mode==REMOTE_AGENTS||mode==REMOTE_COVERS?BUTTON_SELECT:BUTTON_RC_PLAY)==1);
  assert(!count(REMOTE_SWITCH_BUTTON)&&!count(REMOTE_COVERS_BUTTON));
  /* First two tap durations and 150 ms gap are from remote-log-2.txt.
   * The third is a modeled 130 ms tap with the same observed gap. */
  reset();tap(mode,100,29);tap(mode,144,13);
  assert(!count(REMOTE_SWITCH_BUTTON));feed(mode,0,0,177);assert(!count(REMOTE_SWITCH_BUTTON));
  feed(mode,0,0,178);assert(count(REMOTE_SWITCH_BUTTON)==1&&!count(REMOTE_COVERS_BUTTON));
  reset();tap(mode,100,29);tap(mode,144,13);tap(mode,172,13);
  assert(count(REMOTE_COVERS_BUTTON)==1&&!count(REMOTE_SWITCH_BUTTON));
  feed(mode,0,0,250);assert(count(REMOTE_COVERS_BUTTON)==1&&!count(REMOTE_SWITCH_BUTTON));
  for(int gap=19;gap<=21;gap++){
   reset();tap(mode,100,13);tap(mode,118,13);tap(mode,131+gap,13);
   feed(mode,0,0,200);
   assert(count(REMOTE_COVERS_BUTTON)==(gap<=20));
   assert(count(REMOTE_SWITCH_BUTTON)==(gap>20));
  }
  /* Holds keep the old 300/600 ms threshold and never become triples. */
  reset();feed(mode,BUTTON_RC_PLAY,BUTTON_RC_PLAY,300);
  int threshold=mode==REMOTE_AGENTS?30:60;
  feed(mode,0,BUTTON_RC_PLAY,300+threshold);
  feed(mode,BUTTON_RC_PLAY|BUTTON_REL,0,400);
  assert(!count(REMOTE_COVERS_BUTTON)&&!count(REMOTE_SWITCH_BUTTON));
  if(mode==REMOTE_OTHER||mode==REMOTE_COVERS)assert(count(REMOTE_WPS_BUTTON)==1);
  reset();tap(mode,ULONG_MAX-70,13);tap(mode,ULONG_MAX-47,13);tap(mode,ULONG_MAX-24,13);
  assert(count(REMOTE_COVERS_BUTTON)==1&&!count(REMOTE_SWITCH_BUTTON));
 }
 puts("tap/double/triple: all contexts; real 290/130ms taps+150ms gaps; modeled third; 200ms inclusive; no premature double; hold/wrap PASS");
}
'''
(O/'gestures.c').write_text(pre+source+post)
cmd=['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(O),'-I'+str(R)]
subprocess.run(cmd+[str(O/'gestures.c'),'-o',str(O/'gestures')],check=True)
subprocess.run([str(O/'gestures')],check=True)
root=(R/'apps-ipod/root_menu.c').read_text()
nav_h=(R/'apps-ipod/input/agents_shortcut.h').read_text()
nav_c=(R/'apps-ipod/input/agents_shortcut.c').read_text()
nav_production=nav_c[nav_c.index('static bool cover_journey'):nav_c.index('int agents_shortcut_button(')]
nav_production=nav_h[nav_h.index('enum agents_nav_screen'):nav_h.rfind('#endif')]+nav_production
wps=(R/'apps-ipod/screens/playback/wps.c').read_text();covers=(R/'apps-ipod/screens/covers/carousel.c').read_text()
nav=r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#define AGENTS_SWITCH_WPS 2
#define AGENTS_SWITCH_COVERS 3
#define AGENTS_SWITCH_REMOTE_WPS 4
#define GO_TO_PREVIOUS -1
#define GO_TO_WPS 10
#define GO_TO_ALBUM_COVERS 14
#define GO_TO_AGENTS 11
#define GO_TO_ROOT 12
static bool wps_return_agents,wps_menu_covers,poweroff=true;
static int a_calls,calls,covers_result=GO_TO_PREVIOUS;
static int agents_screen(void){return ++a_calls==1?AGENTS_SWITCH_COVERS:0;}
static int album_covers_scrn(void*p){(void)p;calls++;return covers_result;}
static bool button_get_sw_poweroff_state(void){return poweroff;}
static void button_set_sw_poweroff_state(bool on){poweroff=on;}
static int wpsscrn(void*p){(void)p;return GO_TO_PREVIOUS;}
static void album_covers_cancel_resume(void) {}
static int cover_tracks_scrn(void *p) {(void)p;return GO_TO_PREVIOUS;}
#define GO_TO_ALBUM_COVERS_TRACKS 111
static int app_buffer_is_claimed(void){return 0;}

'''
nav='#include <stdbool.h>\n'+nav_production+nav
nav+=function(root,'agents_scrn')+function(root,'run_screen_shortcut')+function(root,'root_menu_cover_shortcut')+r'''
int main(void){assert(agents_scrn(NULL)==GO_TO_PREVIOUS);assert(a_calls==2&&calls==1);
 root_menu_cover_shortcut();assert(calls==2&&poweroff);
 a_calls=0;covers_result=GO_TO_ROOT;assert(agents_scrn(NULL)==GO_TO_ROOT);
 puts("Agents triple closes then reopens cached client after Cover return; generic caller resumes; destinations propagate PASS");}
'''
(O/'nav.c').write_text(nav)
subprocess.run(cmd+[str(O/'nav.c'),'-o',str(O/'nav')],check=True);subprocess.run([str(O/'nav')],check=True)
# WPS shares its pre-existing album modal helper; returning restores WPS,
# not a new root destination, so the origin is its existing caller frame.
assert re.search(r'case ACTION_WPS_STOP:\s*#ifdef HAVE_USB_PODLINK\s*case ACTION_AGENTS_COVERS:',wps)
assert 'case ACTION_AGENTS_COVERS:\n            return GO_TO_PREVIOUS;' in covers
assert 'case ACTION_AGENTS_SWITCH:\n                gwps_leave_wps(true);\n                return GO_TO_AGENTS;' in wps
assert 'action == ACTION_AGENTS_COVERS' in (R/'apps-ipod/input/action.c').read_text()
assert 'shortcut_screen_context(context)' in (R/'apps-ipod/input/action.c').read_text()
print('WPS existing album modal origin; Cover triple returns caller; double remains Agents; custom action bypasses nested dispatch PASS')
