"""Production WPS MENU/list/cover-origin branches and saved album re-entry."""
from pathlib import Path
import re,subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
S=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else R
root=(S/'apps-ipod/root_menu.c').read_text();wps=(S/'apps-ipod/screens/playback/wps.c').read_text();albums=(S/'apps-ipod/screens/covers/album_covers.c').read_text()
def fn(t,n):
 m=re.search(r'(?m)^(?:static[ \t]+)?(?:long|void|int|bool)[ \t*]+'+n+r'\([^;{}]*\)\s*\{',t);assert m,n
 end=m.end();depth=1
 while depth:depth+=(t[end]=='{')-(t[end]=='}');end+=1
 return t[m.start():end]
assert 'cover_nav_playing() && album_covers_reopen_tracks()' in wps,'baseline WPS skips the album list'
nav=(R/'apps-ipod/input/agents_shortcut.c').read_text();nav=nav[nav.index('static bool cover_journey'):nav.index('static unsigned int nav_live')]
a=wps.index('            case ACTION_WPS_MENU:');b=wps.index('            case ACTION_WPS_QUICKSCREEN:',a);menu=wps[a:b]
a=root.index('            if (cover_nav_active())',root.index('        case GO_TO_ALBUM_COVERS_TRACKS:',root.index('        case GO_TO_ALBUM_COVERS_TRACKS:')+1));b=root.index('            if (ret_val == GO_TO_ROOT)',a);listing=root[a:b]
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#define GO_TO_ROOT -2
#define GO_TO_PREVIOUS -1
#define GO_TO_WPS 10
#define GO_TO_AGENTS 11
#define GO_TO_ALBUM_COVERS 12
#define GO_TO_ALBUM_COVERS_TRACKS 13
#define ACTION_WPS_MENU 1
#define WPS_MENU_PREVIOUS 1
static struct {int wps_menu_action;}global_settings;
static bool restore,theme_enabled,menu_covers,return_agents;
static int covers,cover_result,clears,prepared;
static long tracks_album_seek=42,tracks_artist_seek=7;
static char tracks_album_name[32]="Selected album";
static int tracks_album_index=19,pf_resume_album_index;
static bool tracks_album_valid=true,pf_resume_last_album;
static void browser_db_enter_artist_album_tracks_on_next_load(long a,long b,const char*c){assert(a==42&&b==7&&!strcmp(c,"Selected album"));prepared++;}
static void album_covers_cancel_resume(void){clears++;}
static void gwps_leave_wps(bool on){assert(on);}
static bool show_album_covers(long*g,bool*f){(void)g;(void)f;covers++;return false;}
static bool wait_for_album_tables(void){return true;}
static int album_covers_return(const char*p,bool*r){(void)p;(void)r;return cover_result;}
'''
post=r'''
int main(void){
 for(int origin=-1;origin<16;origin++){
  cover_nav_begin(origin);cover_result=GO_TO_ALBUM_COVERS_TRACKS;
  assert(album_covers_scrn(NULL)==GO_TO_ALBUM_COVERS_TRACKS&&cover_nav_active());
  assert(list_result(GO_TO_WPS)==GO_TO_WPS&&cover_nav_playing());
  menu_covers=true;int before=covers;assert(menu()==GO_TO_ALBUM_COVERS_TRACKS&&covers==before);
  assert(pf_resume_last_album&&pf_resume_album_index==19);
  assert(list_result(GO_TO_ROOT)==GO_TO_ALBUM_COVERS&&!cover_nav_playing());
  cover_result=GO_TO_PREVIOUS;assert(album_covers_scrn(NULL)==origin&&!cover_nav_active());
 }
 cover_nav_begin(GO_TO_AGENTS);cover_nav_set_playing(true);cover_result=GO_TO_WPS;
 assert(album_covers_scrn(NULL)==GO_TO_WPS&&cover_nav_playing());
 assert(menu()==GO_TO_ALBUM_COVERS_TRACKS);assert(list_result(GO_TO_PREVIOUS)==GO_TO_ALBUM_COVERS);
 cover_result=GO_TO_PREVIOUS;assert(album_covers_scrn(NULL)==GO_TO_AGENTS);
 assert(!cover_nav_active()&&prepared==18);
 puts("production album/WPS MENU back stack: 17 origins, direct play, V1 precedence, saved album identity/centering PASS");
}
'''
pre+='#define ONPLAY_OK 90\n#define AUDIO_STATUS_PLAY 1\nstatic int left_for;static int leave(int r){left_for=r;return 77;}static int audio_status(void){return AUDIO_STATUS_PLAY;}\n'
context=(R/'apps-ipod/screens/context_menu.c').read_text()
code=pre+nav+fn(albums,'album_covers_reopen_tracks')+fn(root,'album_covers_scrn')+fn(albums,'album_covers')+fn(context,'hotkey_album_covers')
post=post.replace('assert(!cover_nav_active()&&prepared==18);','''assert(!cover_nav_active()&&prepared==18);
 int destinations[]={GO_TO_WPS,GO_TO_AGENTS,GO_TO_ALBUM_COVERS_TRACKS,GO_TO_ROOT};
 for(unsigned i=0;i<4;i++){
  cover_nav_begin(GO_TO_PREVIOUS);cover_nav_set_playing(true);cover_result=destinations[i];
  assert(hotkey_album_covers()==77&&left_for==destinations[i]);
  if(i==1||i==3)assert(!cover_nav_active());else cover_nav_finish();
 }''')
code+='\nstatic int menu(void){switch(ACTION_WPS_MENU){'+menu+'}return restore?99:0;}\n'
code+='static int list_result(int ret_val){switch(0){case 0:'+listing+'}return ret_val;}\n'+post
(O/'return.c').write_text(code)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie',str(O/'return.c'),'-o',str(O/'return')],check=True)
subprocess.run([str(O/'return')],check=True)
assert 'tracks_artist_seek = carousel_idx.album_index[index].artist_seek;' in albums
assert 'tracks_album_seek = carousel_idx.album_index[index].seek;' in albums and 'strmemccpy(tracks_album_name' in albums
print('Album identity copied before carousel cleanup; logical history retained independently of app_buffer PASS')
