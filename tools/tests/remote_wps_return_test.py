"""Production WPS Menu arm and root modal routing, without a UI/device."""
from pathlib import Path
import re
import subprocess
import sys

R = Path(__file__).resolve().parents[2]
O = Path(sys.argv[1]).resolve()
O.mkdir(parents=True, exist_ok=True)
root = (R/'apps-ipod/root_menu.c').read_text()
wps = (R/'apps-ipod/screens/playback/wps.c').read_text()
a = wps.index('            case ACTION_WPS_MENU:')
b = wps.index('            case ACTION_WPS_QUICKSCREEN:', a)
menu = wps[a:b]
source = r'''
#include <assert.h>
#include <stdbool.h>
static bool cider_active(void){return false;}
static int cider_browse_covers(bool home){(void)home;return 999;}
#include <stdio.h>
#define GO_TO_AGENTS 1
#define GO_TO_PREVIOUS 2
#define GO_TO_ROOT 3
#define GO_TO_ALBUM_COVERS_TRACKS 13
static bool cover_nav_playing(void) {return false;}
static bool album_covers_reopen_tracks(void) {return false;}
#define ACTION_WPS_MENU 4
#define WPS_MENU_PREVIOUS 5
static struct {int wps_menu_action;}global_settings;
static void gwps_leave_wps(bool on){assert(on);}
static bool menu_covers=false,theme_enabled,restore;
static bool show_album_covers(long*go,bool*flag){(void)go;(void)flag;return false;}
static int menu(bool return_agents) {
 (void)return_agents;
 switch(ACTION_WPS_MENU) {
'''+menu+r'''
 }
 return -1;
}
int main(void) {
 global_settings.wps_menu_action=0;
 assert(menu(true)==GO_TO_AGENTS);assert(menu(false)==GO_TO_ROOT);
 global_settings.wps_menu_action=WPS_MENU_PREVIOUS;
 assert(menu(true)==GO_TO_AGENTS);assert(menu(false)==GO_TO_PREVIOUS);
 puts("WPS Menu: Agents provenance overrides menu setting; other entries unchanged PASS");
}
'''
(O/'return.c').write_text(source)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g',
                '-fsanitize=address,undefined','-fno-sanitize-recover=all',
                '-fno-pie','-no-pie',str(O/'return.c'),'-o',str(O/'return')],check=True)
subprocess.run([str(O/'return')],check=True)
# The flag is consumed before any no-audio/USB-audio early return.
wrapper = root[root.index('static int wpsscrn('):root.index('static int wpsscrn(')+1300]
assert wrapper.index('wps_return_agents = false') < wrapper.index('usb_audio_get_active()')
assert root.count('gui_wps_show(return_agents, menu_covers)') == 3
assert 'gui_wps_show(bool return_agents, bool menu_covers)' in wps
