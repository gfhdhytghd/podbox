"""Execute production Agents cache initialization and its first render, offline."""
from pathlib import Path
import subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
s=(R/'apps-ipod/screens/system/agents_screen.c').read_text()
start=s.index('static void background_link(');end=s.index('/* A free outbox slot',start)
background=s[start:end]
a=s.index('    bool resume =',s.index('int agents_screen(void)'))
b=s.index('    FOR_NB_SCREENS(i)',a);initial=s[a:b]
a=s.index('    if (resume)',b);b=s.index('    long frame_at',a);render=s[a:b]
pre=r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "herdremote_model.h"
#include "detail_layout.h"
#define PODLINK_CONNECTED 3
#define PODLINK_CLOSE 6
#define PODLINK_TEXT 2
struct podlink_message {int type;unsigned int length;const unsigned char *data;};
#define SCREEN_MAIN 0
#define HZ 100
#define TIME_BEFORE(a,b) ((a)<(b))
enum view {VIEW_SPACES,VIEW_DETAIL,VIEW_USAGE,VIEW_CONFIRM,VIEW_ALERT,VIEW_INPUT};
static enum view view;
static char question_id[12];static int question_choice;
static bool question_custom,question_closing;
static struct hr_model model;
static struct detail_layout detail_layout;static int detail_body_avail;
struct row {int kind;char id[16];};
struct gui_synclist {int start_item[1];};
static struct row cache_row;
static int cache_top=4,frames,round_trips,resets;
static uint32_t cache_generation=7,generation=7;
static bool cache_valid=true,pending_answer,was_connected,detail_ready;
static bool talk_held,talk_shown,talk_discarded,talk_req_up,talk_failed,focus_for_detail;
static char key_req[8];
static char space_id[16],space_tab[16],detail_pane[16],question_pane[16];
static char pending_req[8],focus_req[8],talk_req[8],talk_note[64],task_text[244];
static int detail_shown_age,nout,usage_serial,usage_scroll,detail_scroll,detail_scroll_max,detail_page_lines;
static long last_message,current_tick,usage_at;
static uint32_t podlink_generation(void){return generation;}
static int podlink_state(void){return PODLINK_CONNECTED;}
static void reset_focus(void){resets++;}
static void forget_keys(void){}
static void agents_shortcut_talking(bool b){(void)b;}
static void show_row(struct gui_synclist*l,const struct row*r){(void)l;assert(!strcmp(r->id,"p1"));frames++;assert(!round_trips);}
static void gui_synclist_set_top_item(struct gui_synclist*l,int top){l->start_item[0]=top;}
static void show_same(struct gui_synclist*l){(void)l;frames++;}
static void draw_question_overlay(void){frames++;}
static void draw_usage(struct gui_synclist*l){(void)l;frames++;}
static void draw_detail(struct gui_synclist*l){(void)l;assert(model.detail_valid);assert(!strcmp(model.detail.say,"cached message"));assert(detail_scroll==3);frames++;}
static void show(struct gui_synclist*l,int row){(void)l;assert(row==0);frames++;}
static void talk_result(const char *text,bool failed){snprintf(talk_note,sizeof talk_note,"%s",text);talk_failed=failed;}
BACKGROUND
static void enter(void){struct gui_synclist list={0};
'''
post=r'''
 assert(list.start_item[0]==(resume?cache_top:0));
}
int main(void){
 model.hello=model.detail_valid=true;model.nws=2;
 strcpy(model.detail.say,"cached message");strcpy(cache_row.id,"p1");
 view=VIEW_DETAIL;detail_scroll=3;usage_scroll=7;was_connected=true;
 for(int i=0;i<500;i++){enter();assert(model.nws==2&&view==VIEW_DETAIL&&detail_scroll==3&&usage_scroll==7&&!resets);}
 assert(frames==1000&&!round_trips);
 generation++;enter();assert(!model.hello&&view==VIEW_SPACES&&detail_scroll==0&&resets==1);
 assert(hr_detail_scroll(3,10,15)==3);assert(hr_detail_scroll(10,10,15)==15);
 assert(hr_detail_scroll(3,10,2)==2);
 cache_valid=true;strcpy(focus_req,"f1");focus_for_detail=true;detail_ready=false;
 const char *reply="R\tf1\tok";struct podlink_message message={PODLINK_TEXT,strlen(reply),(const unsigned char *)reply};
 background_link(&message);assert(!focus_req[0]&&detail_ready);
 strcpy(talk_req,"v1");talk_req_up=false;talk_discarded=true;talk_note[0]=0;
 reply="R\tv1\tok\tDiscarded";message.length=strlen(reply);message.data=(const unsigned char *)reply;
 background_link(&message);assert(!talk_req[0]&&!talk_note[0]);
 reply="V\ttalk";message.length=strlen(reply);message.data=(const unsigned char *)reply;
 background_link(&message);assert(!model.voice);
 hr_model_reset(&model);
 const char *d="D\tp1\t1\ttask\tworking\tmessage\t\t\t\t\t0\t1";
 assert(hr_model_apply(&model,d,strlen(d))=='D');
 assert(hr_model_apply(&model,d,strlen(d))==0);
 const char *age="D\tp1\t9\ttask\tworking\tmessage\t\t\t\t\t0\t1";
 assert(hr_model_apply(&model,age,strlen(age))==0);
 const char *revision="D\tp1\t9\ttask\tworking\tmessage\t\t\t\t\t0\t2";
 assert(hr_model_apply(&model,revision,strlen(revision))==0);
 puts("production cache entry/render: 500 warm returns keep detail/selection/scroll; zero host round trips before first frame; generation loss cold fetch; anchor up/bottom; identical/age-only D no redraw PASS");
 printf("hr_model cache bytes=%zu; selection snapshot bytes=%zu\n",sizeof(model),sizeof(cache_row)+sizeof(cache_top)+sizeof(cache_generation)+sizeof(cache_valid));
}
'''
(O/'cache.c').write_text(pre.replace('BACKGROUND',background)+initial+render+post)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(R/'apps-ipod/screens/system'),str(O/'cache.c'),str(R/'apps-ipod/screens/system/herdremote_model.c'),'-o',str(O/'cache')],check=True)
subprocess.run([str(O/'cache')],check=True)
