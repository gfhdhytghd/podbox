#!/usr/bin/env python3
"""FU28 host-executed production CN parser and spinner pixel/timing tests."""
from pathlib import Path
import subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]) if len(sys.argv)>1 else R/'build-fu28/loading';O.mkdir(parents=True,exist_ok=True)
code=r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "screens/playback/cider_model.h"
static int apply(struct cider_state*s,const char*t){return cider_state_apply(s,t,strlen(t),100);}
int main(void){struct cider_state s={0};char b[512];
 assert(apply(&s,"CA\t7\t1\t"));
 for(int i=0;i<2;i++){
 snprintf(b,sizeof b,"CN\t7\t%d\t4000\t100000\t1000\t1000\t50\t1\t2\tsong\t-\t123456\tT\tA\tB\t0\tC\t4\t%d",i+1,i);
 assert(apply(&s,b)&&s.loading==i&&s.repeat==4);
 }
 const char*legacy="CN\t7\t3\t4000\t100000\t1000\t1000\t50\t1\t2\tsong\t-\t123456\tT\tA\tB";
 assert(apply(&s,legacy)&&!s.loading);
 const char*bad="CN\t7\t4\t4000\t100000\t1000\t1000\t50\t1\t2\tsong\t-\t123456\tT\tA\tB\t0\tC\t4\t2";
 assert(!apply(&s,bad)&&s.seq==3&&!s.loading);
 s.loading=true;assert(apply(&s,"CA\t7\t0\toffline")&&!s.loading);
 puts("CN loading parse, legacy restore, invalid transactional rejection PASS");}
'''
(O/'cn.c').write_text(code)
cmd=['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(R/'apps-ipod'),'-I'+str(R/'firmware/export'),str(O/'cn.c'),str(R/'apps-ipod/screens/playback/cider_model.c'),str(R/'firmware/usbstack/podlink_wire.c'),'-o',str(O/'cn')]
subprocess.run(cmd,check=True);subprocess.run([str(O/'cn')],check=True)

# Compile the real pixel renderer against a clipped framebuffer recorder.
import re
src=(R/'apps-ipod/skin/skin_render.c').read_text()
def function(name):
    m=re.search(r'(?m)^static [\w *]+\b'+name+r'\([^;{}]*\)\s*\{',src);assert m
    e=m.end();d=1
    while d:d+=(src[e]=='{')-(src[e]=='}');e+=1
    return src[m.start():e]
pre=r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "screens/playback/cider_spinner.h"
#define LCD_DEPTH 16
#define LCD_BLEND_OPAQUE 15
#define HZ 100
static uint32_t current_tick;
struct screen {void (*blendrect)(int,int,int,int,unsigned);};
static unsigned char pixels[32][32];static int boxw,boxh,draws;
static void blend(int x,int y,int w,int h,unsigned a){
 assert(w==1&&h==1&&x>=0&&y>=0&&x<boxw&&y<boxh);
 assert(a>0&&a<=15);pixels[y][x]=a;draws++;
}
'''
post=r'''
int main(int argc,char**argv){
 assert(argc==2);struct screen screen={blend};struct cider_dot dots[4];
 for(int size=1;size<=24;size++)for(unsigned frame=0;frame<8;frame++){
  boxw=boxh=size;memset(pixels,0,sizeof pixels);draws=0;
  assert(cider_spinner_dots(size,size,frame,dots)==4);
  assert(dots[0].quarter==4&&dots[1].quarter==3&&dots[2].quarter==2&&dots[3].quarter==1);
  for(int i=0;i<4;i++){assert(dots[i].x>=0&&dots[i].y>=0);
   assert(dots[i].x+dots[i].diameter<=size&&dots[i].y+dots[i].diameter<=size);}
  current_tick=(frame*HZ+7)/8;assert(cider_spinner_frame(current_tick,HZ)==frame);
  draw_cider_spinner(&screen,size,size);assert(draws>0);
  if(size>=8){
   char path[512];snprintf(path,sizeof path,"%s/spinner-%02d-%u.pgm",argv[1],size,frame);
   FILE*f=fopen(path,"wb");assert(f);fprintf(f,"P5\n%d %d\n255\n",size,size);
   for(int y=0;y<size;y++)for(int x=0;x<size;x++)fputc(pixels[y][x]*17,f);
   fclose(f);
  }
 }
 /* Clockwise head: top, upper right, right; tails go counterclockwise. */
 cider_spinner_dots(16,16,0,dots);assert(dots[0].y==0&&dots[1].x<dots[0].x);
 cider_spinner_dots(16,16,1,dots);assert(dots[0].x>dots[1].x);
 cider_spinner_dots(16,16,2,dots);assert(dots[0].x==13);
 /* Exact schedule: eight advances per 100 ticks; no cumulative 12-tick drift. */
 int changes=0;unsigned prev=cider_spinner_frame(0,HZ);
 for(unsigned t=1;t<=100;t++){unsigned f=cider_spinner_frame(t,HZ);if(f!=prev)changes++;prev=f;}
 assert(changes==8&&prev==0);
 assert(cider_spinner_frame(UINT32_MAX,HZ)<8);
 assert(!cider_spinner_show(false,true,1)); /* local stays normal */
 assert(!cider_spinner_show(true,false,1)); /* restore in the same frame */
 assert(cider_spinner_show(true,true,1)&&cider_spinner_show(true,true,2));
 assert(!cider_spinner_show(true,true,3)&&!cider_spinner_show(true,true,4));
 puts("Spinner geometry/pixels 1..24 px, 8..16 px all frames, timing, restore, FF/RW priority PASS");}
'''
(O/'spinner.c').write_text(pre+function('draw_cider_spinner')+post)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(R/'apps-ipod'),str(O/'spinner.c'),'-o',str(O/'spinner')],check=True)
subprocess.run([str(O/'spinner'),str(O)],check=True)
# Guard the critical integration points: blank line is cleared before the
# overlay, and the dedicated refresh never visits neighboring viewports.
assert 'needs_update || update_all || info.pending_loading' in src
assert src.index('write_line(display, align') < src.index('if (info.pending_loading)')
assert 'refresh_mode == SKIN_REFRESH_LOADING' in src
assert '!skin_viewport->has_loading_indicator' in src
assert 'VP_NEVER_VISIBLE | VP_DRAW_HIDDEN' in src
status=(R/'apps-ipod/screens/playback/cider.c').read_text().split('void cider_status(',1)[1].split('\n}',1)[0]
assert 'mutex_lock' not in status and 'status_word' in status
print('Skin isolated refresh, background clear-before-overlay, hidden slots, lock-free status PASS')

# Execute the production WPS action loop with a deterministic kernel clock.
loop=(R/'apps-ipod/skin/skin_display.c').read_text()
start=loop.index('int skin_wait_for_action(');end=loop.index('\n}',start)+2
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include "screens/playback/cider_spinner.h"
#define HZ 100
#define TIMEOUT_NOBLOCK 0
#define MIN(a,b) ((a)<(b)?(a):(b))
#define TIME_BEFORE(a,b) ((long)((a)-(b))<0)
#define TIME_AFTER(a,b) TIME_BEFORE(b,a)
#define FOR_NB_SCREENS(i) for(int i=0;i<1;i++)
#define SCREEN_MAIN 0
#define ACTION_NONE 0
#define BUTTON_NONE 0
#define PEAK_METER_FPS 20
#define SPECTRUM_FPS 25
#define SKIN_REFRESH_LOADING 256
#define SKIN_REFRESH_PEAK_METER 16
#define SKIN_REFRESH_SPECTRUM 128
#define SKIN_REFRESH_SCROLL 4
#define SKIN_REFRESH_ALL 65535
enum skinnable_screens {CUSTOM_STATUSBAR,WPS};
struct wps_data {bool loading_indicator,loading_drawn;unsigned char loading_frame;bool peak_meter_enabled,spectrum_enabled,textbox_moving;long textbox_due;};
struct gui_wps {struct wps_data *data;};
static struct wps_data data={.loading_indicator=true};static struct gui_wps gwps={&data};
static long current_tick,clear_at=-1,changed_at=-1,last_draw=-1;static int draws,flushes,restores;
static bool active=true,visible=true,asleep,inhibited;
static struct gui_wps*skin_get_gwps(int s,int i){(void)s;(void)i;return &gwps;}
static bool cider_active(void){return active;}
static bool skin_cider_loading(void){return active&&visible;}
static bool lcd_active(void){return !asleep;}
static bool dynamic_colors_needs_repaint(void){return false;}
static bool dynamic_colors_pending(void){return false;}
static bool dynamic_colors_needs_full_update(void){return false;}
static bool skin_flush_inhibited(void){return inhibited;}
static void skin_update(int s,int i,unsigned r){(void)s;(void)i;assert(r==SKIN_REFRESH_LOADING);
 if(last_draw>=0&&visible)assert(current_tick-last_draw<=13);
 data.loading_drawn=skin_cider_loading();data.loading_frame=cider_spinner_frame(current_tick,HZ);
 last_draw=current_tick;draws++;if(!skin_cider_loading()){restores++;assert(current_tick-changed_at<=13);}}
static void skin_flush_dirty(void){flushes++;}
static void peak_meter_peek(void){}static void spectrum_meter_peek(void){}
static void sleep(int t){(void)t;current_tick++;}
static int get_action(int c,int t){(void)c;assert(t>0);current_tick+=t;
 if(clear_at>=0&&current_tick>=clear_at&&visible){visible=false;changed_at=clear_at;}
 return ACTION_NONE;}
'''
post=r'''
int main(void){
 clear_at=51;assert(!skin_wait_for_action(WPS,0,100));
 assert(draws>=5&&restores==1&&draws==flushes); /* only the slot, including restore */
 /* A full render drew a spinner, but loading cleared before the first tick. */
 visible=false;data.loading_drawn=true;changed_at=current_tick;last_draw=-1;
 skin_wait_for_action(WPS,0,13);assert(restores==2&&!data.loading_drawn);
 int old=draws;active=false;visible=true;skin_wait_for_action(WPS,0,20);assert(draws==old);
 active=true;visible=true;clear_at=-1;asleep=true;skin_wait_for_action(WPS,0,20);assert(draws==old);
 asleep=false;inhibited=true;skin_wait_for_action(WPS,0,20);assert(draws==old);
 puts("Production WPS tick: bounded blocking wait, slot-only frames, restore <=13 ticks, local/LCD-off/inhibition PASS");}
'''
(O/'tick.c').write_text(pre+loop[start:end]+post)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(R/'apps-ipod'),str(O/'tick.c'),'-o',str(O/'tick')],check=True)
subprocess.run([str(O/'tick')],check=True)

# Exercise the actual status publisher and lock-free UI reader.
source=(R/'apps-ipod/screens/playback/cider.c').read_text()
def source_function(name):
    m=re.search(r'(?m)^(?:static )?[\w *]+\b'+name+r'\([^;{}]*\)\s*\{',source);assert m
    e=m.end();d=1
    while d:d+=(source[e]=='{')-(source[e]=='}');e+=1
    return source[m.start():e]
pre=r'''
#include <assert.h>
#include <stdio.h>
#include "screens/playback/cider_model.h"
#define HZ 100
static struct cider_state state;
static volatile uint32_t status_word;
static unsigned long current_tick;
static bool connected=true;
static bool podlink_host_connected(void){return connected;}
'''
header=(R/'apps-ipod/screens/playback/cider.h').read_text()
status=re.search(r'struct cider_status \{[^}]+\};',header).group()
post=r'''
int main(void){struct cider_status s;
 state.available=true;state.loading=true;state.rate=1000;state.muted=true;
 state.volume=100;state.shuffle=1;state.repeat=4;state.received=10;current_tick=11;
 publish_status();cider_status(&s);assert(s.available&&s.loading&&s.muted&&s.rate==1000&&s.volume==100&&s.shuffle==1&&s.repeat==4);
 state.loading=false;publish_status();cider_status(&s);assert(!s.loading&&s.rate==1000);
 state.loading=true;publish_status();connected=false;cider_status(&s);assert(!s.loading&&!s.available);
 connected=true;current_tick=3010;cider_status(&s);assert(!s.loading&&!s.available);
 state.available=false;publish_status();current_tick=11;cider_status(&s);assert(!s.available&&!s.loading);
 puts("Production compact status: fields, loading transitions, disconnect, expiry; no lock symbol PASS");}
'''
(O/'status.c').write_text(pre+status+source_function('publish_status')+source_function('cider_status')+post)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(R/'apps-ipod'),str(O/'status.c'),'-o',str(O/'status')],check=True)
subprocess.run([str(O/'status')],check=True)
