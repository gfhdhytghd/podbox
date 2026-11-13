"""Sanitized production remote MENU adapters and native-only UI readers."""
from pathlib import Path
import re
import subprocess
import sys

R = Path(__file__).resolve().parents[2]
O = Path(sys.argv[1]).resolve()
O.mkdir(parents=True, exist_ok=True)

def function(text, name):
    m = re.search(r'(?m)^[\w *]+\b' + name + r'\([^;{}]*\)\s*\{', text)
    assert m, name
    end, depth = m.end(), 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[m.start():end]

def body(path):
    return re.sub(r'^#include .*$', '', (R / path).read_text(), flags=re.M)

action = (R / 'apps-ipod/input/action.c').read_text()
arcade = (R / 'apps-ipod/games/arcade/shell.c').read_text()
image = (R / 'apps-ipod/viewers/image_viewer/image_viewer.c').read_text()
carousel = (R / 'apps-ipod/screens/covers/carousel.c').read_text()
keys = carousel[carousel.index('const struct button_mapping pf_context_buttons[]'):]
keys = keys[:keys.index('\n};') + 3]
pre = r'''
#include <assert.h>
#include <stdbool.h>
#define REMOTE_LOG_DISABLE
#include "firmware/export/remote_log.h"
#include <stdio.h>
#define IPOD_ACCESSORY_PROTOCOL
#define HAVE_USB_PODLINK
#define HZ 100
#define BUTTON_NONE 0
#define BUTTON_REL 0x02000000
#define BUTTON_REPEAT 0x04000000
#define BUTTON_REDRAW 0x20000000
#define BUTTON_MULTIMEDIA 0x10000000
#define SYS_EVENT 0x80000000u
#include "firmware/target/arm/ipod/button-target.h"
#include "apps-ipod/input/agents_shortcut.h"
#define TIME_BEFORE(a,b) ((long)((unsigned long)(a)-(unsigned long)(b)) < 0)
static struct agents_shortcut_state shortcut;
static bool shortcut_talking, remote_input_active;
static unsigned long shortcut_talking_at, current_tick;
static struct agents_remote_state remote;
enum {ACTIVITY_AGENTS,ACTIVITY_WPS,ACTIVITY_OTHER};
static int raw, held, activity=ACTIVITY_OTHER;
static int get_current_activity(void) {return activity;}
static int button_status(void) {return held;}
static int remote_poll(int timeout,int context)
{(void)timeout;(void)context;return raw;}
struct vm {bool claims_menu;};
static bool arcade_menu_is_game(struct vm *vm) {return vm->claims_menu;}
static bool menu_down,menu_paused;
static long menu_since;
#define ARCADE_PREV 1
#define ARCADE_NEXT 2
#define ARCADE_PLAY 4
#define ARCADE_SELECT 8
#define ARCADE_MENU 16
static int iv_decode_thread=1,iv_decode_stop;
static int thread_self(void) {return 1;}
static void cb_progress(int a,int b) {(void)a;(void)b;}
static int button_get(bool block) {(void)block;return raw;}
#define SYS_USB_CONNECTED ((int)SYS_EVENT|1)
#define PLUGIN_USB_CONNECTED 2
#define PLUGIN_ABORT 3
static void default_event_handler(int b) {(void)b;}
enum {PF_MENU=1,PF_QUIT,PF_SORTING_NEXT,PF_SORTING_PREV,PF_RANDOM};
struct button_mapping {int action,button,prerequisite;};
#define CONTEXT_TREE 1
#define LAST_ITEM_IN_LIST__NEXTLIST(x) {0,0,x}
'''
source = pre + body('apps-ipod/input/remote_menu.h')
source += body('firmware/export/button_chord.h')
source += body('apps-ipod/input/agents_shortcut.c')
source += body('apps-ipod/input/agents_remote.c')
source += function(action, 'agents_shortcut_get_button')
source += function(arcade, 'menu_held_long') + function(arcade, 'buttons')
source += function(image, 'iv_decode_row') + keys
source += r'''
static int mapping(int b,int previous) {
 const struct button_mapping *k=pf_context_buttons;
 for(;k->button;k++)if(k->button==b&&(!k->prerequisite||k->prerequisite==previous))return k->action;
 return 0;
}
int main(void) {
 for(int f=0;f<3;f++) {
  int flag=f==1?BUTTON_REPEAT:f==2?BUTTON_REL:0;
  raw=BUTTON_RC_MENU|flag;held=f==2?0:BUTTON_RC_MENU;
  assert(agents_shortcut_get_button(0)==(BUTTON_MENU|flag));
  remote_input_active=true;
  assert(agents_shortcut_get_button(0)==raw);
  remote_input_active=false;
  assert(remote_menu_button(raw)==(BUTTON_MENU|flag));
  assert(remote_menu_button(BUTTON_LEFT|BUTTON_RC_MENU|flag)==(BUTTON_LEFT|BUTTON_MENU|flag));
 }
 assert(remote_menu_button(SYS_USB_CONNECTED)==SYS_USB_CONNECTED);
 assert(remote_menu_button(BUTTON_MULTIMEDIA|1)==(BUTTON_MULTIMEDIA|1));
 assert(remote_menu_button(AGENTS_SHORTCUT_BUTTON)==AGENTS_SHORTCUT_BUTTON);
 assert(mapping(BUTTON_RC_MENU|BUTTON_REPEAT,BUTTON_RC_MENU)==PF_MENU);
 assert(mapping(BUTTON_RC_MENU|BUTTON_REL,BUTTON_RC_MENU)==PF_QUIT);
 assert(mapping(BUTTON_RC_MENU|BUTTON_REPEAT,BUTTON_RC_MENU|BUTTON_REPEAT)==0);
 raw=BUTTON_RC_MENU;iv_decode_stop=0;assert(!iv_decode_row(1,2)&&iv_decode_stop==PLUGIN_ABORT);
 raw=BUTTON_MENU;iv_decode_stop=0;assert(!iv_decode_row(1,2)&&iv_decode_stop==PLUGIN_ABORT);
 raw=SYS_USB_CONNECTED;iv_decode_stop=0;assert(!iv_decode_row(1,2)&&iv_decode_stop==PLUGIN_USB_CONNECTED);
 struct vm vm={true};current_tick=10;
 assert(!menu_held_long(BUTTON_RC_MENU));
 current_tick=109;assert(!menu_held_long(BUTTON_RC_MENU));
 current_tick=110;assert(menu_held_long(BUTTON_RC_MENU));
 assert(!menu_held_long(BUTTON_RC_MENU));assert(!buttons(&vm,0));
 current_tick=200;assert(!menu_held_long(BUTTON_RC_MENU));
 assert(buttons(&vm,0)==ARCADE_MENU);assert(!buttons(&vm,0));
 puts("native-only raw readers: remote MENU press/repeat/release; capture isolation; image cancel; carousel tap/hold; Arcade claimed tap/hold PASS");
}
'''
(O / 'probe.c').write_text(source)
(O / 'config.h').write_text('')
subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-g',
                '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                '-fno-pie', '-no-pie', '-I'+str(O), '-I'+str(R), '-I'+str(R/'apps-ipod'),
                str(O/'probe.c'), '-o', str(O/'probe')], check=True)
subprocess.run([str(O/'probe')], check=True)
# Raw navigation uses the shared reader; ordinary action contexts keep their
# existing remote keymaps and Remote input keeps its explicit capture bypass.
for path in ('screens/system/podlink_screen.c',
             'viewers/image_viewer/image_viewer.c',
             'viewers/playback_viewer/pv_row.c',
             'viewers/text_viewer/text_viewer.c'):
    assert 'agents_shortcut_get_button(' in (R/'apps-ipod'/path).read_text(), path
assert 'remote_menu_button(button)' not in function(action, 'action_poll_button')
assert 'remote_menu_button(button) == IMGVIEW_MENU' in image
assert 'remote_menu_button(button_status()) & BUTTON_MENU' in image
assert arcade.count('remote_menu_button(b) == BUTTON_MENU') == 2
assert 'b = remote_menu_button(b);' in function(arcade, 'beat_stamp')
pv = (R/'apps-ipod/viewers/playback_viewer/pv_row.c').read_text()
assert 'remote_menu_button(button_status()) &' in pv
assert 'remote_menu_button(button_status()), pos' in arcade
print('Production raw-reader wiring and unchanged ordinary remote action path PASS')
