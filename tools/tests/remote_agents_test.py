"""Offline production-keymap regression; no device or serial access."""
from pathlib import Path
import re, subprocess, sys
ROOT=Path(__file__).resolve().parents[2]
OUT=Path(sys.argv[1]).resolve(); OUT.mkdir(parents=True,exist_ok=True)
text=(ROOT/'apps-ipod/input/keymap_ipod.c').read_text()
# Compile the real mapping arrays, including their context cascades.
text=text[text.index('static const struct button_mapping'):]
ids=sorted(set(re.findall(r'\b(?:ACTION_|CONTEXT_)[A-Z_0-9]+\b',text)))
preamble='''#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#define IPOD_ACCESSORY_PROTOCOL
#include "firmware/target/arm/ipod/button-target.h"
#define BUTTON_NONE 0
#define BUTTON_REL 0x02000000
#define BUTTON_REPEAT 0x04000000
struct button_mapping { int action_code, button_code, pre_button_code; };
struct { bool hold_lr_for_scroll_in_list; } global_settings;
'''
preamble+='\n'.join('#define %s %d'%(x,i+1) for i,x in enumerate(ids))
preamble+='''
#undef CONTEXT_CUSTOM
#undef CONTEXT_REMOTE
#define CONTEXT_CUSTOM 0x10000
#define CONTEXT_REMOTE 0x20000
#define LAST_ITEM_IN_LIST { ACTION_NONE, BUTTON_NONE, -1 }
#define LAST_ITEM_IN_LIST__NEXTLIST(x) { ACTION_NONE, BUTTON_NONE, x }
'''
# Baseline exercises the existing remote context directly; the candidate
# also exercises the production raw-button adapter before the same lookup.
helper=ROOT/'apps-ipod/input/agents_remote.c'
extra=''
if helper.exists():
    extra=helper.read_text()
    extra=re.sub(r'^#include .*$', '', extra, flags=re.M)
    preamble+='\n#include "apps-ipod/input/agents_remote.h"\n#define HZ 100\n'
    preamble+='\n#define HAVE_USB_PODLINK\n#define SYS_EVENT 0x80000000u\n#define BUTTON_MULTIMEDIA 0x10000000\n#define BUTTON_REDRAW 0x20000000\n#include "apps-ipod/input/agents_shortcut.h"\n'
    for path in ['firmware/export/button_chord.h', 'apps-ipod/input/agents_shortcut.c']:
        extra += re.sub(r'^#include .*$', '', (ROOT/path).read_text(), flags=re.M)
lookup='''
static int lookup(int context, int button, int prev)
{
    for (int depth=0; depth<10; depth++) {
        const struct button_mapping *m=get_context_mapping(context |
                              ((button & BUTTON_REMOTE) ? CONTEXT_REMOTE : 0));
        for (; m->button_code; m++)
            if (m->button_code==button &&
                (!m->pre_button_code || m->pre_button_code==prev))
                return m->action_code;
        if (m->pre_button_code==-1) return ACTION_NONE;
        context=m->pre_button_code;
    }
    assert(0); return 0;
}
'''
tests='''
int main(void) {
    assert(lookup(CONTEXT_AGENTS, BUTTON_RC_VOL_UP,0)==ACTION_LISTTREE_PGUP);
    assert(lookup(CONTEXT_AGENTS, BUTTON_RC_VOL_DOWN,0)==ACTION_LISTTREE_PGDOWN);
'''
if helper.exists():
    tests+='''
    struct agents_remote_state s={0};
    const int rc[]={BUTTON_RC_LEFT,BUTTON_RC_RIGHT,BUTTON_RC_PLAY};
    const int local[]={BUTTON_LEFT,BUTTON_RIGHT,BUTTON_PLAY};
    for (int i=0;i<3;i++) for(int flag=0;flag<3;flag++) {
        int f=flag==1?BUTTON_REPEAT:flag==2?BUTTON_REL:0;
        int b=agents_remote_button(&s,true,rc[i]|f,rc[i],10);
        assert(b==(local[i]|f));
        assert(lookup(CONTEXT_AGENTS,b,local[i])==
               lookup(CONTEXT_AGENTS,local[i]|f,local[i]));
    }
    for (int i=0;i<3;i++)
        assert(agents_remote_button(&s,false,rc[i],rc[i],10)==rc[i]);
    assert(lookup(CONTEXT_LIST,BUTTON_RC_VOL_UP,0)==ACTION_LIST_VOLUP);
    assert(lookup(CONTEXT_WPS,BUTTON_RC_PLAY|BUTTON_REL,BUTTON_RC_PLAY)==ACTION_WPS_PLAY);
    s=(struct agents_remote_state){0};
    assert(agents_remote_button(&s,true,BUTTON_RC_MENU,BUTTON_RC_MENU,100)==0);
    assert(agents_remote_button(&s,true,0,BUTTON_RC_MENU,159)==0);
    int b=agents_remote_button(&s,true,BUTTON_RC_MENU|BUTTON_REL,0,159);
    assert(lookup(CONTEXT_AGENTS,b,0)==ACTION_STD_MENU);
    s=(struct agents_remote_state){0};
    assert(!agents_remote_button(&s,true,BUTTON_RC_MENU,BUTTON_RC_MENU,200));
    assert(!agents_remote_button(&s,true,BUTTON_RC_MENU|BUTTON_REPEAT,BUTTON_RC_MENU,240));
    b=agents_remote_button(&s,true,0,BUTTON_RC_MENU,260);
    assert(lookup(CONTEXT_AGENTS,b,0)==ACTION_STD_OK);
    assert(!agents_remote_button(&s,true,0,BUTTON_RC_MENU,261));
    assert(!agents_remote_button(&s,true,BUTTON_RC_MENU|BUTTON_REL,0,270));
    /* A release at the threshold must choose long even without a poll. */
    assert(!agents_remote_button(&s,true,BUTTON_RC_MENU,BUTTON_RC_MENU,300));
    b=agents_remote_button(&s,true,BUTTON_RC_MENU|BUTTON_REL,0,360);
    assert(lookup(CONTEXT_AGENTS,b,0)==ACTION_STD_OK);
    /* Leaving the context clears pending Menu, never changes other contexts. */
    assert(!agents_remote_button(&s,true,BUTTON_RC_MENU,BUTTON_RC_MENU,400));
    assert(agents_remote_button(&s,false,BUTTON_RC_MENU|BUTTON_REL,0,450)==
           (BUTTON_RC_MENU|BUTTON_REL));
    assert(!s.menu_down);
'''
if helper.exists():
    tests+='''
    struct agents_shortcut_state cs={0}; bool fire=false;
    int cx=agents_shortcut_button(&cs,BUTTON_SELECT,BUTTON_SELECT,false,&fire);
    assert(cx==BUTTON_SELECT && !fire);
    assert(lookup(CONTEXT_AGENTS,cx,0)==ACTION_NONE);
    cx=agents_shortcut_button(&cs,BUTTON_SELECT|BUTTON_REPEAT,BUTTON_SELECT,false,&fire);
    assert(cx==(BUTTON_SELECT|BUTTON_REPEAT) && !fire);
    assert(lookup(CONTEXT_AGENTS,cx,BUTTON_SELECT)==ACTION_STD_CONTEXT);
    cx=agents_shortcut_button(&cs,BUTTON_SELECT|BUTTON_PLAY,BUTTON_SELECT|BUTTON_PLAY,false,&fire);
    assert(cx==(BUTTON_SELECT|BUTTON_PLAY) && !fire);
    assert(lookup(CONTEXT_WPS,cx,BUTTON_SELECT|BUTTON_REPEAT)==ACTION_WPS_HOTKEY);
    cx=agents_shortcut_button(&cs,BUTTON_SELECT|BUTTON_REL,0,false,&fire);
    assert(lookup(CONTEXT_AGENTS,cx,BUTTON_SELECT|BUTTON_REPEAT)==ACTION_NONE);
    cs=(struct agents_shortcut_state){0};
    cx=agents_shortcut_button(&cs,BUTTON_SELECT,BUTTON_SELECT,false,&fire);
    cx=agents_shortcut_button(&cs,BUTTON_SELECT|BUTTON_REL,0,false,&fire);
    assert(lookup(CONTEXT_AGENTS,cx,BUTTON_SELECT)==ACTION_STD_OK);
'''
tests+='puts("remote Agents mapping and native SELECT held/tap timing PASS");}\n'
# The target header's config include needs only this feature for this test.
(OUT/'config.h').write_text('')
source=OUT/'mapping.c'; source.write_text(preamble+'\n'+text+'\n'+extra+lookup+tests)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-Wno-implicit-fallthrough','-g','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(OUT),'-I'+str(ROOT),str(source),'-o',str(OUT/'mapping')],check=True)
subprocess.run([str(OUT/'mapping')],check=True)
