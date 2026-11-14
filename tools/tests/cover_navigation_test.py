"""Any-screen native chord at the UI queue boundary; no hardware inputs."""
from pathlib import Path
import ast,re,subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
S=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else R
source=(S/'apps-ipod/input/action.c').read_text()
assert 'static long raw_ui_filter(long button)' in source, 'baseline has no any-screen raw UI hook'
def fn(t,n):
 m=re.search(r'(?m)^(?:static[ \t]+)?(?:long|void|int|bool)[ \t*]+'+n+r'\([^;{}]*\)\s*\{',t);assert m,n
 end=m.end();depth=1
 while depth:depth+=(t[end]=='{')-(t[end]=='}');end+=1
 return t[m.start():end]
a=ast.parse((R/'tools/tests/native_cover_chord_test.py').read_text())
pre=next(ast.literal_eval(n.value) for n in a.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='pre' for t in n.targets))
pre=pre.replace('#include "firmware/export/button_chord.h"',re.sub(r'^#include .*$', '',(R/'firmware/export/button_chord.h').read_text(),flags=re.M))
pre+='''
static bool ui_polling;
static unsigned int ui_thread=7,caller=7;
static int held,clock_pumps;
static struct agents_cover_chord_state cover_chord;
static unsigned int thread_self(void){return caller;}
static int button_status(void){return held;}
static void podlink_time_sync(void){clock_pumps++;}
'''
c=re.sub(r'^#include .*$', '',(S/'apps-ipod/input/agents_shortcut.c').read_text(),flags=re.M)
post=r'''
int main(void){
 int chord=BUTTON_SELECT|BUTTON_MENU;
 caller=8;held=chord;assert(raw_ui_filter(chord)==chord&&!clock_pumps&&!agents_nav_active());
 caller=7;ui_polling=true;assert(raw_ui_filter(chord)==chord&&!clock_pumps);ui_polling=false;
 for(int screen=0;screen<9;screen++){
  /* Root menu/browser/settings/playlist/chart/dialog/game/WPS/Agents all
     share this thread-gated raw boundary, independent of activity. */
  held=chord;int out=raw_ui_filter(chord);
  assert(out==BUTTON_MENU&&agents_nav_pending()==AGENTS_NAV_COVERS);
  held=0;
  out=raw_ui_filter(BUTTON_SELECT|BUTTON_REL);assert(!(out&BUTTON_SELECT));
  out=raw_ui_filter(BUTTON_MENU|BUTTON_REL);assert(!(out&BUTTON_SELECT));
  assert(!cover_chord.latched);assert(agents_nav_take(AGENTS_NAV_COVERS));
 }
 held=chord;raw_ui_filter(chord);assert(agents_nav_pending()==AGENTS_NAV_COVERS);
 long a=raw_ui_filter(BUTTON_NONE),b=raw_ui_filter(BUTTON_NONE);
 assert(a!=b&&(a&BUTTON_MENU)&&(b&BUTTON_MENU));
 assert(raw_ui_filter(SYS_EVENT|1)==(SYS_EVENT|1));
 puts("any-screen UI/raw SELECT+MENU: thread isolation, release suppression, repeat reuse, modal down/release cancellation PASS");
}
'''
(O/'config.h').write_text('')
(O/'raw.c').write_text(pre+c+fn(source,'raw_ui_cancel')+fn(source,'raw_ui_filter')+post)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(O),'-I'+str(R),'-I'+str(R/'apps-ipod'),str(O/'raw.c'),'-o',str(O/'raw')],check=True)
subprocess.run([str(O/'raw')],check=True)
root=(S/'apps-ipod/root_menu.c').read_text();q=(S/'firmware/drivers/button_queue.c').read_text()
assert 'agents_shortcut_init();' in root and 'if (ui_filter) return ui_filter(ev.id);' in q
route=fn(source,'route_screen_shortcut')
assert 'if (covers) { /* The owner/modal unwinds to the root dispatcher. */ }' in route
assert 'next_screen = GO_TO_ALBUM_COVERS;' in root
print('Raw button queue registration, deferred modal route, root dispatch wiring PASS')
