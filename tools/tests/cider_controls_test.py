"""FU26: long-press routing in wps.c, and the Cider context page's value cycling
(production cider_controls.c with list/lang stubs; no player, no USB)."""
from pathlib import Path
import re,subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
P=R/'apps-ipod/screens/playback'
wps=(P/'wps.c').read_text();qs=(P/'quick_screen.c').read_text()
def body(text,start):
    i=text.index(start)
    depth=0;k=text.index('{',i)
    e=k
    while True:
        depth+=(text[e]=='{')-(text[e]=='}');e+=1
        if depth==0:break
    return text[i:e]
# Key routing: both long presses branch to Cider before any local menu code.
ctx=body(wps,'case ACTION_WPS_CONTEXT:\n            {\n                long go_to;')
assert ctx.index('cider_active()')<ctx.index('context_menu_show'),'context routing'
assert 'cider_context_show()' in ctx.split('context_menu_show')[0]
quick=body(wps,'case ACTION_WPS_QUICKSCREEN:')
assert quick.index('cider_active()')<quick.index('quick_screen_quick'),'quick routing'
assert 'quick_screen_cider(button)' in quick.split('quick_screen_quick')[0]
# The Cider quick screen never touches local settings or the shortcuts menu.
cq=body(qs,'int quick_screen_cider(int button_enter)')
assert not re.search(r'settings_save|option_select|settings_mark|shortcut',cq)
assert 'qs->cider && (button == ACTION_QS_VOLUP' in qs and 'button == ACTION_STD_CONTEXT && !qs->cider' in qs
# FU26b: remote keys and MENU-leaves. Remote Vol is what the wheel is (QS volume) in the
# quick screen, the remote centre key leaves like Select, MENU leaves once armed.
keymap=(R/'apps-ipod/input/keymap_ipod.c').read_text()
rq=keymap[keymap.index('remote_button_context_quickscreen[]'):];rq=rq[:rq.index('}; /*')]
for want in ('ACTION_QS_LEFT,       BUTTON_RC_LEFT,','ACTION_QS_RIGHT,      BUTTON_RC_RIGHT,',
             'ACTION_STD_CANCEL,    BUTTON_RC_SELECT|BUTTON_REL','ACTION_STD_CANCEL,    BUTTON_RC_PLAY|BUTTON_REL',
             'ACTION_QS_VOLUP,     BUTTON_RC_VOL_UP,','ACTION_QS_VOLDOWN,   BUTTON_RC_VOL_DOWN,','ACTION_QS_TOP,        BUTTON_RC_MENU,'):
    assert want in rq,want
assert 'ACTION_QS_DOWN' not in rq
act=(R/'apps-ipod/input/action.c').read_text()
rv=body(act,'static bool remote_volume_action')
qi=rv.index('CONTEXT_QUICKSCREEN');assert 'ACTION_QS_VOLUP' in rv[qi:qi+200] and 'ACTION_QS_VOLDOWN' in rv[qi:qi+200]
assert qi<rv.index('ACTION_STD_PREV'),'quickscreen must be handled before the generic list mapping'
loop=body(qs,'static int gui_syncquickscreen_run')
assert 'bool menu_armed = false;' in loop
assert loop.index('menu_armed = true')<loop.index('button == ACTION_QS_TOP && !qs->items[QUICKSCREEN_TOP]')<loop.index('gui_quickscreen_do_button(qs, button')
assert 'BUTTON_MENU | BUTTON_RC_MENU' in loop and 'menu_armed)\n            break;' in loop
# Same loop serves the local quick screen (top slot empty by default): one rule for both.
assert loop.count('gui_quickscreen_do_button')==1 and 'qs->cider' in qs
# Model of the arming rule: opener's repeats keep MENU down; only a press after an observed release leaves.
def run(events,top=False):
    armed=False
    for held,act in events:
        if not held:armed=True
        if act=='TOP' and not top and armed:return 'left'
    return 'stay'
assert run([(True,'NONE'),(True,'TOP'),(True,'TOP'),(False,'NONE')])=='stay'
assert run([(True,'TOP'),(False,'NONE'),(True,'TOP')])=='left'
assert run([(False,'NONE'),(True,'TOP')],top=True)=='stay'
# FU26c: songs picked in a list play that list; both play sites hand over the list route and row.
br=(P/'cider_browser.c').read_text()
assert 'cider_play_in(selected_row.route, search_route,' in br and 'search_index' in br
assert 'cider_play_in(selected_row.route,\n                                      depth ? history[depth].route : "",\n                                      history[depth].selected)' in br
assert br.count('cider_play(selected_row.route)')==1  # only Random/Shuffle Songs keep the plain play
# Page value cycling on the production page source.
src=re.sub(r'(?m)^#include .*\n','',(P/'cider_controls.c').read_text())
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
#define CIDER_MODE_SHUFFLE 0
#define CIDER_MODE_REPEAT 1
#define CIDER_MODE_COUNT 2
enum {LANG_REPEAT,LANG_SHUFFLE,LANG_REPEAT_ONE,LANG_ALL,LANG_OFF,LANG_ON,LANG_NOW_PLAYING,LANG_REPEAT_AB};
static const char*names[]={"Repeat","Shuffle","One","All","Off","On","Now Playing","A-B"};
static const char*str(int id){return names[id];}
enum {ACTION_NONE,ACTION_REDRAW,ACTION_STD_OK,ACTION_STD_MENU,ACTION_STD_CANCEL};
enum list_row_kind{LIST_ROW_PLAIN,LIST_ROW_TOGGLE,LIST_ROW_SETTING};
struct gui_synclist{int sel;};
struct simplelist_info{const char*title;int count;bool speak_onshow;int(*action_callback)(int,struct gui_synclist*);
 const char*(*get_name)(int,void*,char*,size_t);const char*(*get_value)(int,void*,char*,size_t);enum list_row_kind(*get_kind)(int,void*);};
static int gui_synclist_get_sel_pos(struct gui_synclist*l){return l->sel;}
static void action_wait_for_release(void){}
static void simplelist_info_init(struct simplelist_info*i,const char*t,int c,void*d){(void)d;memset(i,0,sizeof*i);i->title=t;i->count=c;}
static struct simplelist_info shown;
static bool simplelist_show_list(struct simplelist_info*i){shown=*i;return false;}
static int live[2],want[2];static bool pend[2];
static int cider_mode_get(int m){return pend[m]?want[m]:live[m];}
static int sets;
static void cider_mode_set(int m,int v){pend[m]=true;want[m]=v;sets++;}
static int cider_mode_step(int m,int v,bool p){int n=m?5:2;return (v+(p?n-1:1))%n;}
'''
post=r'''
int main(void){
 struct gui_synclist l={0};char b[8];
 assert(!cider_context_show()&&shown.count==2&&!shown.speak_onshow&&!strcmp(shown.title,"Now Playing"));
 assert(!strcmp(shown.get_name(0,0,b,8),"Shuffle")&&!strcmp(shown.get_name(1,0,b,8),"Repeat"));
 assert(shown.get_kind(0,0)==LIST_ROW_TOGGLE&&shown.get_kind(1,0)==LIST_ROW_SETTING);
 assert(!strcmp(shown.get_value(0,0,b,8),"Off")&&!strcmp(shown.get_value(1,0,b,8),"Off"));
 int(*act)(int,struct gui_synclist*)=shown.action_callback;
 assert(act(ACTION_STD_MENU,&l)==ACTION_STD_CANCEL&&!sets);
 l.sel=0;assert(act(ACTION_STD_OK,&l)==ACTION_REDRAW&&sets==1&&!strcmp(shown.get_value(0,0,b,8),"On"));
 assert(act(ACTION_STD_OK,&l)==ACTION_REDRAW&&!strcmp(shown.get_value(0,0,b,8),"Off"));
 l.sel=1;const char*seq[]={"All","One","Shuffle","A-B","Off","All"};
 for(int i=0;i<6;i++){assert(act(ACTION_STD_OK,&l)==ACTION_REDRAW);assert(!strcmp(shown.get_value(1,0,b,8),seq[i]));}
 /* Nothing but the two rows can be changed */
 int n=sets;l.sel=5;act(ACTION_STD_OK,&l);assert(sets==n);
 /* Cider's own change repaints on the next idle tick; a quiet tick does not */
 act(ACTION_REDRAW,&l);assert(act(ACTION_NONE,&l)==ACTION_NONE);
 pend[0]=false;live[0]=1;assert(act(ACTION_NONE,&l)==ACTION_REDRAW&&act(ACTION_NONE,&l)==ACTION_NONE);
 /* every native value is reachable and named as Rockbox names it (settings_list.c repeat_mode) */
 const char*native[]={"Off","All","One","Shuffle","A-B"};
 for(int v=0;v<5;v++)assert(!strcmp(cider_mode_label(1,v),native[v]));
 assert(!strcmp(cider_mode_label(0,0),"Off")&&!strcmp(cider_mode_label(0,1),"On"));
 { int v=0,seen=0; for(int i=0;i<5;i++){seen|=1<<v;v=cider_mode_step(1,v,false);} assert(seen==31&&!v);
   for(int i=0;i<5;i++){seen|=1<<v;v=cider_mode_step(1,v,true);} assert(!v&&cider_mode_step(1,0,true)==4); }
 puts("Cider controls routing and page cycling PASS");}
'''
(O/'page.c').write_text(pre+'const char *cider_mode_title(int);const char *cider_mode_label(int,int);bool cider_context_show(void);\n'+src+post)
subprocess.run(['cc','-std=gnu11','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-fsanitize=address,undefined','-fno-pie','-no-pie',str(O/'page.c'),'-o',str(O/'page')],check=True)
subprocess.run([str(O/'page')],check=True)

# CN parsing: Rockbox repeat numbering, with the older host's numbering still understood.
(O/'cn.c').write_text(r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "screens/playback/cider_model.h"
static int apply(struct cider_state*s,const char*t){return cider_state_apply(s,t,strlen(t),100);}
int main(void){struct cider_state s={0};
 assert(apply(&s,"CA\t7\t1\t"));
 char m[400];
 /* n=16 / 18: legacy 0 none, 1 one, 2 all -> native 0, 2, 1 */
 for(int v=0;v<3;v++){snprintf(m,sizeof m,"CN\t7\t%d\t4000\t100000\t1000\t1000\t50\t1\t%d\tsong\t-\t123456\tT\tA\tB",v+1,v);assert(apply(&s,m)&&s.repeat==(v==1?2:v==2?1:0)&&s.shuffle==1);}
 snprintf(m,sizeof m,"CN\t7\t10\t4000\t100000\t1000\t1000\t50\t0\t2\tsong\t-\t123456\tT\tA\tB\t0\tC");assert(apply(&s,m)&&s.repeat==1);
 /* n=19: field 19 is already native, 0..4; the legacy field is ignored */
 for(int v=0;v<5;v++){snprintf(m,sizeof m,"CN\t7\t%d\t4000\t100000\t1000\t1000\t50\t1\t2\tsong\t-\t123456\tT\tA\tB\t0\tC\t%d",20+v,v);assert(apply(&s,m)&&s.repeat==v);}
 snprintf(m,sizeof m,"CN\t7\t30\t4000\t100000\t1000\t1000\t50\t1\t2\tsong\t-\t123456\tT\tA\tB\t0\tC\t5");assert(!apply(&s,m));
 snprintf(m,sizeof m,"CN\t7\t31\t4000\t100000\t1000\t1000\t50\t1\t2\tsong\t-\t123456\tT\tA\tB\t0\tC\tx");assert(!apply(&s,m));
 snprintf(m,sizeof m,"CN\t7\t32\t4000\t100000\t1000\t1000\t50\t1\t3\tsong\t-\t123456\tT\tA\tB\t0\tC\t4");assert(!apply(&s,m));
 puts("Cider CN repeat numbering PASS");}
''')
subprocess.run(['cc','-std=gnu11','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(R/'apps-ipod'),'-I'+str(R/'firmware/export'),str(O/'cn.c'),str(P/'cider_model.c'),str(R/'firmware/usbstack/podlink_wire.c'),'-o',str(O/'cn')],check=True)
subprocess.run([str(O/'cn')],check=True)
