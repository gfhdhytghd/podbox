"""Production native menu loop with instrumented widgets/provider/navigation."""
from pathlib import Path
import re,subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
s=(R/'apps-ipod/screens/playback/cider_browser.c').read_text();s=re.sub(r'^#include .*\n','',s,flags=re.M)
tokens=sorted(set(re.findall(r'\b(?:ACTION_|Icon_|LANG_|CONTEXT_|ACTIVITY_|GO_TO_)[A-Z_a-z0-9]+',s)))
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include "screens/playback/cider_library.h"
#define ARRAYLEN(x) (sizeof(x)/sizeof((x)[0]))
#define HZ 100
#define MAX(a,b) ((a)>(b)?(a):(b))
#define MIN(a,b) ((a)<(b)?(a):(b))
#define SEARCH_MAX_QUERY 32
#define P2STR(s) ((const char*)(s))
#define FORMAT_NATIVE 1
#define SYS_USB_CONNECTED 0xffff
'''
tokens=sorted(set(tokens)|{'ACTION_STD_MENU'})
pre+='enum {ACTION_NONE,'+','.join(tokens)+'};\n'
pre+=r'''
enum themable_icons {icon_dummy=1000};
enum list_row_kind {LIST_ROW_PLAIN,LIST_ROW_TRACK,LIST_ROW_CONTAINER};
enum agents_nav_screen {AGENTS_NAV_NONE,AGENTS_NAV_TRACKS,AGENTS_NAV_WPS,AGENTS_NAV_AGENTS,AGENTS_NAV_COVERS,AGENTS_NAV_BACK};
typedef uint16_t fb_data;
struct dim {int width,height;};struct img_filter{int dummy;};
struct bitmap{int width,height,format;unsigned char*data;};
struct browser_db_header {char name[128],artist[128];int tracks,year;long length_ms;};
static void browser_set_external_header(const struct browser_db_header*h,const struct bitmap*(*f)(struct dim*)){(void)h;(void)f;}
struct gui_synclist{int selected,count;};
struct search_provider{const char*title;int activity;int(*scan)(const char*,void*);const char*(*row_text)(int,void*);bool(*refresh)(void*);};
static struct {bool db_albumart;int db_art_row_height;}global_settings={true,46};
static bool origin_agents;
static bool cover_nav_origin_is(int s){return origin_agents&&s==GO_TO_AGENTS;}
static bool fail_play;
static int cider_play_status(void){return 1;}
static void splash(int t,const char*s){(void)t;(void)s;}
static void cider_warm_art(const char*const*k,unsigned int n,const char*t){(void)k;(void)n;(void)t;}
static bool available=true;static int live,max_live,plays,draws,step,mode;static uint32_t rng;
static char route[380];
static const char*str(int n){(void)n;return "Native";}
static bool cider_available(void){return available;}
static bool cider_active(void){return false;}
static int audio_status(void){return 1;}
static bool agents_nav_enter(int n){(void)n;assert(live==0);live++;return true;}
static void agents_nav_leave(int n){(void)n;assert(live==1);live--;}
static void agents_nav_take(int n){(void)n;}
static int agents_nav_pending(void){return AGENTS_NAV_NONE;}
static void push_current_activity(int n){(void)n;}
static void pop_current_activity(void){}
static int browser_db_root_row_count(void){return 3;}
static bool browser_db_get_root_row(int n,int*index,const unsigned char**name){static const char*names[]={"Album","Artist","Genre"};*index=n;*name=(const unsigned char*)names[n];return true;}
static bool browser_db_root_row_is_hidden(int index){return index==1;}
static const char*browser_db_root_row_key(int index){static const char*names[]={"Album","Artist","Genre"};return names[index];}
static void browser_set_showing_art(bool b){(void)b;}
static void cider_page_open(const char*p){snprintf(route,sizeof route,"%s",p);}
static int cider_page_total(void){return 3;}
static bool cider_page_stale(void){return false;}
static bool cider_page_row(int i,struct cider_row*r){if(i<0||i>=3)return false;memset(r,0,sizeof*r);snprintf(r->title,sizeof r->title,"Album %d",i);if(!strncmp(route,"album/",6)||!strcmp(route,"random")){r->kind=CIDER_ROW_SONG;snprintf(r->route,sizeof r->route,"play/library-songs/s%d",i);}else{r->kind=CIDER_ROW_ALBUM;snprintf(r->route,sizeof r->route,"album/a%d",i);}return true;}
static void cider_cancel_art(void){}
static int pins;static bool pinned;
static void cider_pin_art(const char*k){assert(strlen(k)==64);pins++;pinned=true;}
static void cider_unpin_art(void){pinned=false;}
static bool cider_copy_art(const char*k,const char*t,void*p,unsigned int n){(void)k;(void)t;(void)p;(void)n;return false;}
static void cider_prefetch_art(const char*k,const char*t){(void)k;(void)t;}
static void img_filter_apply(void*p,int w,int h,const void*f){(void)p;(void)w;(void)h;(void)f;}
static bool cider_play(const char*p){assert(available&&live==1&&strstr(p,"play/"));if(fail_play)return false;plays++;return true;}
int cider_covers(void);
static int default_event_handler(int a){(void)a;return 0;}
static int search_dialog_run(const struct search_provider*p,void*c,char*q,size_t n){(void)n;p->scan(q,c);p->refresh(c);p->row_text(0,c);return -1;}
static int dialog_input(char*p,int n){snprintf(p,n,"text");return 0;}
static void gui_synclist_init(struct gui_synclist*l,const char*(*n)(int,void*,char*,size_t),void*d,bool s,int c,void*v){(void)n;(void)d;(void)s;(void)c;(void)v;memset(l,0,sizeof*l);}
static void gui_synclist_set_icon_callback(struct gui_synclist*l,enum themable_icons(*f)(int,void*)){(void)l;(void)f;}
static void gui_synclist_set_row_callbacks(struct gui_synclist*l,enum list_row_kind(*f)(int,void*),void*a,void*b){(void)l;(void)f;(void)a;(void)b;}
static void gui_synclist_set_albumart_callback(struct gui_synclist*l,const struct bitmap*(*f)(int,void*,struct dim*,const struct img_filter*,uint32_t)){(void)l;(void)f;}
static void gui_synclist_set_row_height(struct gui_synclist*l,int h){(void)l;(void)h;}
static void gui_synclist_set_title(struct gui_synclist*l,const char*t,int i){(void)l;(void)t;(void)i;}
static void gui_synclist_set_nb_items(struct gui_synclist*l,int n){l->count=n;}
static void gui_synclist_select_item(struct gui_synclist*l,int i){l->selected=MAX(0,MIN(i,l->count-1));}
static int gui_synclist_get_sel_pos(struct gui_synclist*l){return l->selected;}
static void gui_synclist_draw(struct gui_synclist*l){(void)l;draws++;}
static void gui_synclist_scroll_stop(struct gui_synclist*l){(void)l;}
static void list_do_action(int context,int timeout,struct gui_synclist*l,int*a){(void)context;(void)timeout;step++;
if(mode==4){*a=ACTION_STD_MENU;return;}if(mode==3){*a=step==1?ACTION_STD_OK:ACTION_STD_CANCEL;return;}if(mode==0){*a=ACTION_STD_OK;return;}if(mode==1){*a=ACTION_STD_MENU;return;}
if(step>128){*a=ACTION_REMOTE_WPS;return;}rng=rng*1664525+1013904223;l->selected=(rng>>16)%MAX(1,l->count);*a=(rng&3)==0?ACTION_STD_CANCEL:(rng&3)==1?ACTION_STD_OK:ACTION_NONE;}
'''
post=r'''
int cider_covers(void){if(mode==4)return CIDER_COVERS_REMOTE_RETURN;if(mode==1)return GO_TO_PREVIOUS;assert(live==1);live++;if(live>max_live)max_live=live;struct cider_row r={0};strcpy(r.route,"album/a0");strcpy(r.title,"Album 0");cider_browse_album(&r);live--;return GO_TO_PREVIOUS;}
int main(void){
mode=0;depth=0;assert(cider_browse()==GO_TO_WPS&&plays==1&&!live&&depth==1);assert(root_count==5);assert(!strcmp(root_rows[3].route,"root/Album")&&!strcmp(root_rows[4].route,"root/Genre"));
mode=1;assert(cider_browse()==GO_TO_PREVIOUS&&!live&&depth==0);
depth=1;strcpy(history[1].route,"random");assert(cider_browse()==GO_TO_WPS&&depth==0&&!live);assert(cider_browse()==GO_TO_PREVIOUS&&!live);
fail_play=true;depth=1;strcpy(history[1].route,"album/a0");memset(history[1].art,'c',64);history[1].art[64]=0;pins=0;mode=3;step=0;int raced=plays;assert(cider_browse()==GO_TO_PREVIOUS&&plays==raced&&!live);assert(pins>0&&!pinned);fail_play=false;
available=false;mode=2;step=0;int before=plays;cider_browse();assert(plays==before&&!live&&empty_list);available=true;
for(int i=1;i<=2000;i++){rng=i;step=0;mode=2;cider_browse();assert(!live);}
depth=0;mode=4;cider_browse_covers(true);assert(cider_browse()==GO_TO_WPS&&cider_browse_remote_return()&&!cider_browse_remote_return());
mode=1;cider_browse_covers(true);assert(cider_browse()==GO_TO_ROOT&&!cider_browse_remote_return());
mode=4;cider_browse_covers(false);assert(cider_browse()==GO_TO_PREVIOUS&&!cider_browse_remote_return());
origin_agents=true;cider_browse_covers(false);assert(cider_browse()==GO_TO_AGENTS&&!cider_browse_remote_return());mode=1;cider_browse_covers(false);assert(cider_browse()==GO_TO_AGENTS);origin_agents=false;
assert(max_live==2&&draws>2000);printf("Production Cider native list controller: hidden rows, empty/unavailable, album/track play, 2000 seeded sequences, max instances=%d PASS\n",max_live);}
'''
(O/'browser.c').write_text(pre+s+post)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-Wno-enum-conversion','-g','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(R/'apps-ipod'),str(O/'browser.c'),'-o',str(O/'browser')],check=True)
subprocess.run([str(O/'browser')],check=True,timeout=30)
