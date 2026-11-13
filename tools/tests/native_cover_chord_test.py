"""SELECT+MENU one-shot cover chord; original SELECT+PLAY kept as production."""
from pathlib import Path
import re,subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
S=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else R
s=(S/'apps-ipod/input/agents_shortcut.c').read_text()
source=re.sub(r'^#include .*$', '',s,flags=re.M)
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#define HAVE_USB_PODLINK
#define IPOD_ACCESSORY_PROTOCOL
#include "firmware/target/arm/ipod/button-target.h"
#define BUTTON_NONE 0
#define BUTTON_REL 0x02000000
#define BUTTON_REPEAT 0x04000000
#define BUTTON_REDRAW 0x20000000
#define SYS_EVENT 0x80000000u
#define BUTTON_MULTIMEDIA 0x10000000
#include "firmware/export/button_chord.h"
#include "apps-ipod/input/agents_shortcut.h"
'''
pre=pre.replace('#include "firmware/export/button_chord.h"',re.sub(r'^#include .*$', '',(R/'firmware/export/button_chord.h').read_text(),flags=re.M))
if 'int agents_cover_chord_button(' not in s:
 pre+='''
int agents_cover_chord_button(struct agents_cover_chord_state*s,int button,int held,bool enabled,bool*fire){
 (void)s;(void)enabled;struct agents_shortcut_state old={0};
 return agents_shortcut_button(&old,button,held,false,fire);
}
'''
tests=r'''
int main(void){
 const int chord=BUTTON_SELECT|BUTTON_MENU;
 for(int screen=0;screen<3;screen++)for(int first=0;first<3;first++)for(int order=0;order<2;order++){
  (void)screen;struct agents_cover_chord_state s={0};bool fire=false;
  if(first){int b=first==1?BUTTON_SELECT:BUTTON_MENU;
   assert(agents_cover_chord_button(&s,b,b,true,&fire)==b&&!fire);}
  assert(agents_cover_chord_button(&s,chord,chord,true,&fire)==BUTTON_NONE&&fire);
  for(int tick=0;tick<1300;tick++){
   assert(agents_cover_chord_button(&s,chord|BUTTON_REPEAT,chord,true,&fire)==BUTTON_NONE&&!fire);
  }
  int up=order?BUTTON_MENU:BUTTON_SELECT,other=order?BUTTON_SELECT:BUTTON_MENU;
  assert(agents_cover_chord_button(&s,up|BUTTON_REL,other,true,&fire)==BUTTON_NONE&&!fire);
  assert(agents_cover_chord_button(&s,other|BUTTON_REPEAT,other,true,&fire)==BUTTON_NONE&&!fire);
  assert(agents_cover_chord_button(&s,other|BUTTON_REL,0,true,&fire)==BUTTON_NONE&&!fire);
  assert(!s.latched);
  assert(agents_cover_chord_button(&s,BUTTON_MENU,BUTTON_MENU,true,&fire)==BUTTON_MENU&&!fire);
  assert(agents_cover_chord_button(&s,chord,chord,true,&fire)==BUTTON_NONE&&fire);
  assert(agents_cover_chord_button(&s,chord|BUTTON_REL,0,false,&fire)==BUTTON_NONE&&!fire);
 }
 struct agents_cover_chord_state s={0};bool fire;
 assert(agents_cover_chord_button(&s,chord,chord,false,&fire)==chord&&!fire);
 assert(agents_cover_chord_button(&s,BUTTON_RC_MENU,chord,true,&fire)==BUTTON_RC_MENU&&!fire);
 assert(agents_cover_chord_button(&s,0x80000001u,chord,true,&fire)==(int)0x80000001u&&!fire);
 assert(agents_cover_chord_button(&s,BUTTON_REDRAW,chord,true,&fire)==BUTTON_REDRAW&&!fire);
 /* Two queued releases still suppress both when latest held is already 0. */
 s=(struct agents_cover_chord_state){0};
 assert(agents_cover_chord_button(&s,chord,chord,true,&fire)==BUTTON_NONE&&fire);
 assert(!agents_cover_chord_button(&s,BUTTON_SELECT|BUTTON_REL,0,true,&fire)&&s.latched);
 assert(!agents_cover_chord_button(&s,0,0,true,&fire)&&s.latched);
 assert(!agents_cover_chord_button(&s,BUTTON_MENU|BUTTON_REL,0,true,&fire)&&!s.latched);
 struct agents_shortcut_state old={0};
 int play=BUTTON_SELECT|BUTTON_PLAY;
 assert(agents_cover_chord_button(&s,play,play,true,&fire)==play&&!fire);
 assert(agents_shortcut_button(&old,play,play,false,&fire)==BUTTON_NONE&&fire);
 puts("native cover chord: both/order/one-shot/13s simulated repeat/partial release/final release/context exit/remote+system pass; SELECT+PLAY unchanged PASS");
}
'''
(O/'config.h').write_text('')
(O/'chord.c').write_text(pre+source+tests)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(O),'-I'+str(R),'-I'+str(R/'apps-ipod'),str(O/'chord.c'),'-o',str(O/'chord')],check=True)
subprocess.run([str(O/'chord')],check=True)
