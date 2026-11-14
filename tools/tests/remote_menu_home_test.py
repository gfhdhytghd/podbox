"""MENU versus triple/back origin table, production carousel arm/state."""
from pathlib import Path
import re,subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
S=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else R
s=(S/'apps-ipod/screens/covers/carousel.c').read_text();a=s.index('        case PF_QUIT:',s.index('static int album_covers_loop'));b=s.index('        case PF_WPS:',a);arm=s[a:b]
n=(R/'apps-ipod/input/agents_shortcut.c').read_text();n=n[n.index('static bool cover_journey'):n.index('static unsigned int nav_live;')]
pre='''#include <assert.h>\n#include <stdbool.h>\n#include <stdio.h>\n#define GO_TO_ROOT -2\n#define GO_TO_PREVIOUS -1\n#define GO_TO_WPS 10\n#define PF_QUIT 1\n#define AGENTS_NAV_WPS 2\nstatic bool wps_live;\nstatic bool agents_nav_has(int screen){assert(screen==AGENTS_NAV_WPS);return wps_live;}\n'''
post='''
static int menu(void){switch(PF_QUIT){ARM}return 9;}
int main(void){
 for(int origin=-1;origin<17;origin++)for(int live=0;live<2;live++){
  wps_live=live;
  cover_nav_begin(origin);assert(menu()==(origin==GO_TO_WPS||live?GO_TO_ROOT:GO_TO_PREVIOUS));
  cover_nav_set_playing(true);assert(menu()==(origin==GO_TO_WPS||live?GO_TO_ROOT:GO_TO_PREVIOUS));
  cover_nav_set_playing(false);assert(cover_nav_finish()==origin);
 }
 assert(!cover_nav_active());puts("MENU state table: WPS triple/V1/hotkey -> home; other origins -> previous; X2 journey and triple/back retain origin PASS");
}
'''.replace('ARM',arm)
(O/'home.c').write_text(pre+n+post)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie',str(O/'home.c'),'-o',str(O/'home')],check=True)
subprocess.run([str(O/'home')],check=True)
