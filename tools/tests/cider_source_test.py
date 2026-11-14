"""Production source switches and WPS adapter; no audio/USB/Cider endpoints."""
from pathlib import Path
import re,subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
source=(R/'apps-ipod/screens/playback/cider_wps.c').read_text()
source=re.sub(r'^#include .*\n','',source,flags=re.M)
resident=(R/'apps-ipod/screens/playback/cider.c').read_text()
playback=(R/'apps-ipod/audio/playback.c').read_text()
wps=(R/'apps-ipod/screens/playback/wps.c').read_text()
def fn(name,text=None):
 text=resident if text is None else text
 m=re.search(r'(?m)^(?:static )?(?:void|bool|int) '+name+r'\([^;{}]*\)\s*\{',text);assert m,name
 e=m.end();d=1
 while d:d+=(text[e]=='{')-(text[e]=='}');e+=1
 return text[m.start():e]
seek=fn('ffwd_rew',wps)
actions=sorted(set(re.findall(r'\bACTION_[A-Z_0-9]+',source+seek)))
pre=r'''
#include <assert.h>
#include <string.h>
#include <stdio.h>
#include <stdbool.h>
#include <stdint.h>
#include "screens/playback/cider_model.h"
#include "screens/playback/cider_wps.h"
#define HAVE_USB_PODLINK
#define Q_AUDIO_PAUSE 1
#define Q_AUDIO_PLAY 2
#define HZ 100
#define TIME_AFTER(a,b) ((long)((a)-(b))>0)
#define logf(...) do{}while(0)
#define LOGFQUEUE(...) do{}while(0)
#define FF_REWIND_MAX_PERCENT 3
#define MIN_FF_REWIND_STEP 500
#define STATUS_FASTFORWARD 3
#define STATUS_FASTBACKWARD 4
#define SKIN_REFRESH_PLAYER_PROGRESS 1
#define SKIN_REFRESH_DYNAMIC 2
#define FOR_NB_SCREENS(i) for(int i=0;i<1;i++)
#define CONTEXT_WPS 1
#define ALLOW_SOFTLOCK 0
#define TIMEOUT_BLOCK -1
#define SYS_USB_CONNECTED -100
#define IS_SYSEVENT(x) ((x)<0)
#define UNIT_TIME 1
#define ARRAYLEN(x) (sizeof(x)/sizeof((x)[0]))
#define MAX(a,b) ((a)>(b)?(a):(b))
#define MIN(a,b) ((a)<(b)?(a):(b))
#define FORMAT_NATIVE 1
#define WPS 1
#define ACTIVITY_WPS 1
#define AUDIO_STATUS_PLAY 1
#define SKIN_REFRESH_ALL 0xffff
struct mp3entry {char *title,*artist,*albumartist,*album,*composer;unsigned long length,elapsed;char path[260];};
struct wps_state {struct mp3entry *id3,*nid3;int ff_rewind_count;bool paused;};
struct bitmap {int width,height,format;unsigned char *data;};
typedef uint16_t fb_data;
static long current_tick;
static struct {long last_volume_change;} global_status;
static bool active;
static struct cider_state incoming;
static struct {int ff_rewind_accel,ff_rewind_min_step;bool talk_menu;}global_settings={0,2,false};
static struct wps_state testing_wps;
static struct wps_state*get_wps_state(void){return &testing_wps;}
static int seek_actions;
static int get_action(int c,int t);
static void play_status_set_ffmode(int m){(void)m;}
static void audio_pre_ff_rewind(void){assert(!active);}
static void audio_ff_rewind(unsigned long pos){(void)pos;assert(!active);}
static void skin_update(int s,int i,int f){(void)s;(void)i;(void)f;}
static int default_event_handler(int a){(void)a;return 0;}
static void talk_value(long p,int u,bool e){(void)p;(void)u;(void)e;}
static void audio_pause_at_chapter_end(bool b){(void)b;}
static void talk_force_shutup(void){}
struct audio_resume_info {unsigned long elapsed,offset;};
static bool initialized=true;
static int cider_mutex,local_playing,pauses;
static unsigned int ncommands,next_request,cancel_request,play_request;
static int play_reply;static long play_at;
static void cider_log(const char*f,...){(void)f;}
static struct cider_transfer transfer;
static bool disk_lookup,request_ready,cancel_all;
static void cider_warm_art(const char*const*k,unsigned int n,const char*t){(void)k;(void)n;(void)t;}
static int request_calls;
static bool cider_has_art(const char*k,const char*t){(void)k;(void)t;return false;}
static char commands[12][384];
static struct cider_state state;
static void mutex_lock(int*m){(void)m;}
static void mutex_unlock(int*m){(void)m;}
static int disable_irq_save(void){return 0;}
static void restore_irq(int level){(void)level;}
static bool cider_active(void){return active;}
static bool cider_available(void){return state.available;}
static void cider_snapshot(struct cider_state*out){*out=state;}
struct cider_status { bool available, muted; int rate, volume, shuffle, repeat; };
static void cider_status(struct cider_status*o){o->available=state.available;o->muted=state.muted;o->rate=state.rate;o->volume=state.volume;o->shuffle=state.shuffle;o->repeat=state.repeat;}
static int local_starts;
static int audio_status(void){return local_playing;}
static void audio_queue_send(int q,intptr_t data){(void)q;(void)data;assert(!active);local_starts++;}
static void audio_pause(void){pauses++;local_playing=0;}
static void cider_request_art(const char*k,const char*t){(void)k;(void)t;request_calls++;}
static bool bitmap_ready;
static bool cider_copy_art(const char*k,const char*t,void*p,unsigned int n){(void)k;(void)t;if(!bitmap_ready)return false;memset(p,0x17,n);return true;}
static void skin_request_full_update(int x){(void)x;}
static void skin_albumart_art_opened(int x){assert(x==CIDER_ART_HANDLE);}
static int get_current_activity(void){return ACTIVITY_WPS;}
static int playback_current_aa_hid(int slot){return slot+10;}
static int bufgetdata(int h,int size,void*out){(void)h;(void)size;(void)out;return 0;}
static bool buf_pin_handle(int h,bool p){(void)h;(void)p;return true;}
'''
post=r'''
int main(void){
struct wps_state w={0};assert(!active);
strcpy(state.title,"Cider song");strcpy(state.artist,"Artist");strcpy(state.album,"Album");strcpy(state.art,"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
state.available=state.have=true;state.generation=7;state.duration=100000;state.position=1000;state.rate=1000;state.volume=42;
state.available=false;assert(!cider_play("play/library-songs/failed")&&!active&&!pauses&&!ncommands);state.available=true;
local_playing=1;cider_play("play/library-songs/s1");assert(!active&&!pauses&&ncommands==1);assert(!cider_play_status());play_reply=1;assert(cider_play_status()==1);assert(active&&pauses==1&&ncommands==1);assert(cider_play_status()==1&&pauses==1);assert(strstr(commands[0],"native-play\tplay/library-songs/s1"));
current_tick=100;bitmap_ready=true;cider_wps_update(&w);assert(!strcmp(w.id3->title,"Cider song")&&w.id3->elapsed==2000&&!w.paused);assert(wps_art_handle(0)==CIDER_ART_HANDLE);
struct bitmap*b=NULL;assert(wps_art_bitmap(CIDER_ART_HANDLE,&b)&&b->width==240&&b->height==240);
assert(cider_wps_action(ACTION_WPS_PLAY)&&strstr(commands[ncommands-1],"toggle"));
assert(cider_wps_action(ACTION_WPS_VOLUP)&&strstr(commands[ncommands-1],"volume-step\t1"));
unsigned int before=ncommands;state.available=false;cider_wps_update(&w);assert(w.paused&&!strcmp(w.id3->title,"Cider song"));assert(cider_wps_action(ACTION_WPS_PLAY)&&ncommands==before&&active);
state.available=true;cider_wps_update(&w);assert(active&&!w.paused);
cider_local_play();assert(!active&&strstr(commands[ncommands-1],"source-local"));before=ncommands;cider_local_play();assert(ncommands==before);assert(wps_art_handle(2)==12);
assert(!cider_wps_action(ACTION_WPS_PLAY));
ncommands=0;cider_play("play/library-songs/s2");play_reply=1;assert(cider_play_status()==1);audio_resume();assert(!active&&local_starts==1);
cider_play("play/library-songs/s3");play_reply=1;assert(cider_play_status()==1);audio_play(0,0);assert(!active&&local_starts==2);
ncommands=0;cider_play("play/library-songs/s4");play_reply=1;assert(cider_play_status()==1);cider_wps_update(&testing_wps);seek_actions=0;unsigned long old_position=testing_wps.id3->elapsed;
assert(!ffwd_rew(ACTION_WPS_SEEKFWD,false));assert(testing_wps.id3->elapsed>old_position&&strstr(commands[ncommands-1],"seek"));
transfer.active=true;transfer.request=77;transfer.received=4000;strcpy(transfer.key,"kept");strcpy(transfer.tier,"M");
const char*visible[]={"near","kept"};cider_retain_art(visible,2,"M");assert(transfer.active&&transfer.request==77&&transfer.received==4000&&!cancel_request);
request_calls=0;cider_prefetch_art("near","M");assert(!request_calls);cider_retain_art(visible,1,"M");assert(!transfer.active&&cancel_request==77);cider_prefetch_art("near","M");assert(request_calls==1);
cider_local_play();ncommands=0;local_playing=1;int old_pauses=pauses;
assert(cider_play("play/library-songs/fail"));play_reply=-1;assert(cider_play_status()==-1&&!active&&local_playing&&pauses==old_pauses);
assert(cider_play("play/library-songs/timeout"));current_tick+=601;assert(cider_play_status()==-1&&!active);
ncommands=12;assert(!cider_play("play/library-songs/full")&&!active);ncommands=0;
assert(cider_play("play/library-songs/cancel"));cider_local_play();assert(!play_request&&cider_play_status()==-1&&!active);
puts("Production Local/Cider, disconnect/reconnect, native WPS bitmap/actions PASS");}
'''
(O/'source.c').write_text(pre+'\nenum {'+','.join(actions)+'};\n'+fn('cider_control')+'\n'+fn('cider_play')+'\n'+fn('cider_play_status')+'\n'+fn('cider_local_play')+'\n'+fn('cider_retain_art')+'\n'+fn('cider_prefetch_art')+'\n'+fn('audio_play',playback)+'\n'+fn('audio_resume',playback)+'\n'+source+'\n'+seek+'\nstatic int get_action(int c,int t){(void)c;(void)t;return seek_actions++ ? ACTION_WPS_STOPSEEK : ACTION_WPS_SEEKFWD;}\n'+post)
(O/'config.h').write_text('#define HAVE_USB_PODLINK\n')
cmd=['cc','-std=gnu11','-g','-Wall','-Wextra','-Wno-unused-variable','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(O),'-I'+str(R/'apps-ipod'),'-I'+str(R/'firmware/export'),str(O/'source.c'),str(R/'apps-ipod/screens/playback/cider_model.c'),str(R/'firmware/usbstack/podlink_wire.c'),'-o',str(O/'source')]
subprocess.run(cmd,check=True);subprocess.run([str(O/'source')],check=True)
