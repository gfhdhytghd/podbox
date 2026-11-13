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
    const int contexts[]={REMOTE_AGENTS,REMOTE_OTHER,REMOTE_WPS};
    const int maps[]={CONTEXT_AGENTS,CONTEXT_LIST,CONTEXT_WPS};
    const int rc[]={BUTTON_RC_RIGHT,BUTTON_RC_LEFT};
    const int local[]={BUTTON_RIGHT,BUTTON_LEFT};
    for(int c=0;c<3;c++)for(int i=0;i<2;i++) {
        struct agents_remote_state st={0};
        int expected=c==0?local[i]:rc[i];
        assert(agents_remote_button(&st,contexts[c],rc[i],rc[i],100)==expected);
        assert(agents_remote_button(&st,contexts[c],rc[i]|BUTTON_REPEAT,rc[i],160)==(expected|BUTTON_REPEAT));
        assert(agents_remote_button(&st,contexts[c],rc[i]|BUTTON_REL,0,180)==(expected|BUTTON_REL));
        assert(!agents_remote_button(&st,contexts[c],0,0,200));
        assert(!agents_remote_pending(&st));
        assert(lookup(maps[c],expected,0)==lookup(maps[c],expected,0));
    }
    struct agents_remote_state st={0};
    assert(agents_remote_button(&st,REMOTE_AGENTS,BUTTON_RC_MENU,BUTTON_RC_MENU,400)==BUTTON_RC_MENU);
    assert(!agents_remote_button(&st,REMOTE_AGENTS,0,BUTTON_RC_MENU,460));
    assert(agents_remote_button(&st,REMOTE_AGENTS,BUTTON_RC_MENU|BUTTON_REL,0,470)==(BUTTON_RC_MENU|BUTTON_REL));
    assert(lookup(CONTEXT_AGENTS,BUTTON_RC_MENU|BUTTON_REL,0)==ACTION_STD_MENU);
    assert(lookup(CONTEXT_AGENTS,BUTTON_RC_SELECT|BUTTON_REL,0)==ACTION_STD_OK);
    st=(struct agents_remote_state){0};
    for(int f=0;f<3;f++) {
        int flag=f==1?BUTTON_REPEAT:f==2?BUTTON_REL:0;
        assert(agents_remote_button(&st,REMOTE_AGENTS,BUTTON_LEFT|flag,0,500)==(BUTTON_LEFT|flag));
        assert(agents_remote_button(&st,REMOTE_AGENTS,BUTTON_RIGHT|flag,0,500)==(BUTTON_RIGHT|flag));
    }
    assert(lookup(CONTEXT_AGENTS,BUTTON_RC_PLAY,0)==ACTION_AGENTS_REMOTE_TALK);
    assert(lookup(CONTEXT_AGENTS,BUTTON_RC_PLAY|BUTTON_REL|BUTTON_REPEAT,0)==ACTION_AGENTS_DISCARD);
    assert(lookup(CONTEXT_AGENTS,BUTTON_PLAY|BUTTON_REL,0)==ACTION_AGENTS_TALK_END);
    st=(struct agents_remote_state){0};
    assert(!agents_remote_button(&st,REMOTE_OTHER,BUTTON_RC_PLAY,BUTTON_RC_PLAY,600));
    assert(!agents_remote_button(&st,REMOTE_OTHER,BUTTON_RC_PLAY|BUTTON_REPEAT,BUTTON_RC_PLAY,659));
    assert(agents_remote_button(&st,REMOTE_OTHER,0,BUTTON_RC_PLAY,660)==REMOTE_WPS_BUTTON);
    assert(!agents_remote_button(&st,REMOTE_WPS,BUTTON_RC_PLAY|BUTTON_REPEAT,BUTTON_RC_PLAY,700));
    assert(!agents_remote_button(&st,REMOTE_WPS,BUTTON_RC_PLAY|BUTTON_REL,0,710));
    assert(lookup(CONTEXT_WPS,BUTTON_RC_PLAY|BUTTON_REPEAT,BUTTON_RC_PLAY)==ACTION_WPS_STOP);
    struct agents_shortcut_state cs={0};bool fire=false;
    int b=agents_shortcut_button(&cs,BUTTON_SELECT,BUTTON_SELECT,false,&fire);
    assert(lookup(CONTEXT_AGENTS,b,0)==ACTION_NONE);
    b=agents_shortcut_button(&cs,BUTTON_SELECT|BUTTON_REPEAT,BUTTON_SELECT,false,&fire);
    assert(!fire&&lookup(CONTEXT_AGENTS,b,BUTTON_SELECT)==ACTION_STD_CONTEXT);
    b=agents_shortcut_button(&cs,BUTTON_SELECT|BUTTON_PLAY,BUTTON_SELECT|BUTTON_PLAY,false,&fire);
    assert(!fire&&lookup(CONTEXT_WPS,b,BUTTON_SELECT|BUTTON_REPEAT)==ACTION_WPS_HOTKEY);
    puts("plain Left/Right press+repeat+release everywhere; host Menu/Select; Play scopes; native keys PASS");
}
'''
# The target header's config include needs only this feature for this test.
(OUT/'config.h').write_text('')
source=OUT/'mapping.c'; source.write_text(preamble+'\n'+text+'\n'+extra+lookup+tests)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-Wno-implicit-fallthrough','-g','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(OUT),'-I'+str(ROOT),'-I'+str(ROOT/'apps-ipod'),str(source),'-o',str(OUT/'mapping')],check=True)
subprocess.run([str(OUT/'mapping')],check=True)
