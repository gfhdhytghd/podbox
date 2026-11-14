"""Compile the actual FU21 view transitions/key handler with a fake link/UI."""
from pathlib import Path
import re,subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
S=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else R
s=(S/'apps-ipod/screens/system/agents_screen.c').read_text()
def function(name, source=None):
 s=source if source is not None else (S/'apps-ipod/screens/system/agents_screen.c').read_text()
 m=re.search(r'(?m)^[\w *]+\b'+re.escape(name)+r'\([^;{}]*\)\s*\{',s);assert m,name
 e=m.end();depth=1
 while depth:depth+=(s[e]=='{')-(s[e]=='}');e+=1
 return s[m.start():e]
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include "herdremote_model.h"
#define IPOD_ACCESSORY_PROTOCOL
#define BUTTON_LEFT 1
#define BUTTON_RIGHT 2
#define BUTTON_RC_LEFT 4
#define BUTTON_RC_RIGHT 8
#define ACTION_STD_CANCEL 1
#define ACTION_STD_OK 2
#define ACTION_STD_MENU 3
#define ACTION_AGENTS_TALK 4
#define ACTION_AGENTS_REMOTE_TALK 5
#define ACTION_AGENTS_DISCARD 6
#define ACTION_REPEAT 128
struct gui_synclist {int sel;};
struct row {unsigned char kind,index;char id[16];};
'''
pre+=re.search(r'enum view \{[^}]*\};',s).group()+'\n'
pre+=r'''
static struct hr_model model;
static struct hr_popped popped;
static enum view view,question_from;
static struct row rows[4],question_from_row;
static int nrows=1,question_choice;
static char question_pane[16],question_id[12],question_saved_detail[16],question_agent[44],question_tab[44],question_line[124];
static char detail_pane[16],pending_req[8],focus_req[8],focus_want[16],talk_req[8],key_req[8];
static struct {int nruns;} key_runs;
static bool question_custom,question_closing,detail_ready,pending_answer,talk_held;
static int raw,status,focused,sent,wakes,shown,keys,voices,discarded;
#define PODLINK_CLOSE 6
#define PODLINK_TEXT 2
struct podlink_message {int type;unsigned int length;const unsigned char *data;};
static bool cache_valid=true,talk_discarded,talk_failed,talk_req_up,focus_for_detail;
static long last_message,current_tick,usage_at;
static void talk_result(const char *text,bool failed){(void)text;(void)failed;assert(0);}
static char last_name[16],last_args[64];
#define SCREEN_MAIN 0
#define FONT_UI 1
#define USE_LINE 14
#define USE_TEXT 1
#define USE_DIM 2
#define MIN(a,b) ((a)<(b)?(a):(b))
#include <stddef.h>
#define LCDFN(x) x
#define FG_FALLBACK 1
#define BG_FALLBACK 0
#define DRMODE_SOLID 0
enum screen_type {main_screen=0};
struct frame_buffer_t {void *data;void *(*get_address_fn)(int,int);ptrdiff_t stride;size_t elems;};
struct viewport {int x,y,width,height,flags,font,drawmode;struct frame_buffer_t *buffer;unsigned fg_pattern,bg_pattern;};
static struct frame_buffer_t framebuffer_default={.elems=320*240,.stride=320};
static struct viewport default_vp;
INIT_VIEWPORT
static void set_default_align_flags(struct viewport*v){v->flags=0;}
static int getuifont(void){return FONT_UI;}
struct screen {void (*set_viewport)(struct viewport*);void (*clear_viewport)(void);void (*set_foreground)(int);void (*drawrect)(int,int,int,int);void (*update_viewport)(void);struct viewport *(*init_viewport)(struct viewport*);int lcdwidth,lcdheight;int (*getuifont)(void);};
static int full_clears,input_draws,texts,yes_draws,no_draws;
static void setvp(struct viewport*v){if(v)assert(v->width==320&&v->height==240);}
static void clearvp(void){full_clears++;}
static void colour(int c){(void)c;}
static void rect(int x,int y,int w,int h){assert(x>=0&&y>=0&&x+w<=320&&y+h<=240);}
static void update(void){}
static struct screen screens[]={{setvp,clearvp,colour,rect,update,init_viewport,320,240,getuifont}};
FULLSCREEN
static void text_at(struct screen*d,int x,int y,int colour,const char*t){(void)d;(void)colour;assert(x>=0&&x<320&&y>=0&&y+USE_LINE<=240);texts++;yes_draws+=strstr(t,"LEFT: Yes")!=NULL;no_draws+=strstr(t,"RIGHT: No")!=NULL;}
struct dialog_text_line {const char*str;int len;};
typedef unsigned short ucschar_t;
struct font {int unused;};
static struct font metric_font;
static struct font *font_get(int id){(void)id;return &metric_font;}
static void font_lock(int id,bool lock){(void)id;(void)lock;}
static int font_get_width(struct font *pf,ucschar_t ch){(void)pf;return ch>=0x3000?14:7;}
#define IS_DIACRITIC(ch) ((ch)>=0x300 && (ch)<=0x36f)
UTF8_DECODE
WRAP_FUNCTIONS
DIALOG_WRAP

static int draw_input(struct screen*d,struct viewport*v,int y,bool dim){(void)d;(void)v;(void)dim;assert(y<240-3*USE_LINE);input_draws++;return y+3*USE_LINE;}

static int gui_synclist_get_sel_pos(struct gui_synclist*l){return l->sel;}
static void show(struct gui_synclist*l,int i){l->sel=i;shown++;}
static void show_row(struct gui_synclist*l,const struct row*r){(void)r;show(l,0);}
static void forget_keys(void){keys=0;}
static void subscribe_detail(const char*p){(void)p;}
static void queue_focus(const char*n,const char*p,bool detail){assert(!strcmp(n,"fp"));assert(detail);assert(!strcmp(p,detail_pane));strcpy(focus_req,"focus");focused++;}
static void queue_request(const char*n,const char*a){strcpy(last_name,n);strcpy(last_args,a);strcpy(pending_req,"answer");sent++;}
static void backlight_on(void){wakes++;}
static int get_action_statuscode(int*b){*b=raw;return status;}
static void send_key(const char*k){assert(!strcmp(k,"bs"));keys++;}
static void start_talk(struct gui_synclist*l,bool remote){(void)l;(void)remote;voices++;talk_held=true;}
static void finish_talk(bool discard){assert(discard);discarded++;talk_held=false;}
'''
pre=pre.replace('INIT_VIEWPORT',function('LCDFN(init_viewport)',(S/'firmware/drivers/lcd-bitmap-common.c').read_text()))
pre=pre.replace('FULLSCREEN',function('viewport_set_fullscreen',(S/'apps-ipod/draw/viewport.c').read_text()))
pre=pre.replace('UTF8_DECODE',function('utf8decode',(S/'firmware/common/unicode.c').read_text()))
w=(S/'apps-ipod/draw/text_wrap.c').read_text()
pre=pre.replace('WRAP_FUNCTIONS','\n'.join(function(n,w) for n in ['is_punct_break','wrap_next','text_wrap_next']))
pre=pre.replace('DIALOG_WRAP',function('dialog_wrap_text',(S/'apps-ipod/widgets/dialog.c').read_text()))
code=pre+'\n'.join(function(n) for n in ['background_link','current_question','question_binary_page','draw_question_overlay','open_question','leave_question','pop_question','enter_answer','answer_number','close_input','question_action'])
code+=r'''
static void reset(void){memset(&model,0,sizeof model);memset(&popped,0,sizeof popped);view=VIEW_AGENTS;question_id[0]=question_pane[0]=pending_req[0]=focus_req[0]=talk_req[0]=detail_pane[0]=0;talk_held=false;focused=sent=wakes=keys=voices=discarded=0;status=raw=0;
const char*q="Q\tid\tp1\tw1\t-\tAgent\t1\t-1\tTask\n?\tRun command?\no\t1\tYes\t-\no\t2\tAlways allow\t-\no\t3\tNo, and tell Codex differently\tc";assert(hr_model_apply(&model,q,strlen(q))=='Q');}
static void key(struct gui_synclist*l,int action,int button){raw=button;assert(question_action(l,action));}
static void real_payload(struct gui_synclist *list){
FILE *f=fopen(REAL_PAYLOAD,"rb");assert(f);char payload[2048];size_t len=fread(payload,1,sizeof payload,f);assert(!ferror(f)&&feof(f));fclose(f);
/* Menus, Now Playing and Cover Flow all use the resident callback with
 * no Agents foreground client. This tests that callback, not those screens. */
for(int outside=0;outside<3;outside++){
 reset();int before=wakes;hr_model_reset(&model);
 struct podlink_message m={PODLINK_TEXT,len,(const unsigned char *)payload};
 background_link(&m);assert(view==VIEW_AGENTS&&wakes==before&&model.nquestions==1);
 assert(!strcmp(model.questions[0].id,"e0298fe2"));
 pop_question(list);assert(view==VIEW_ALERT);draw_question_overlay();
}
for(int start=VIEW_SPACES;start<=VIEW_DETAIL;start++){
 reset();view=start;hr_model_reset(&model);assert(hr_model_apply(&model,payload,len)=='Q');
 assert(model.questions[0].noptions==3&&model.questions[0].nlines==2&&model.questions[0].options[2].custom);
 struct dialog_text_line lines[8];
 for(int i=0;i<2;i++)assert(dialog_wrap_text(model.questions[0].lines[i],308,FONT_UI,lines,8)>0);
 for(int i=0;i<3;i++)assert(dialog_wrap_text(model.questions[0].options[i].label,308,FONT_UI,lines,8)>0);
 talk_held=true;for(int i=0;i<1000;i++)pop_question(list);assert(view==(enum view)start);
 talk_held=false;pop_question(list);assert(view==VIEW_ALERT);draw_question_overlay();
 leave_question(list);assert(view==(enum view)start);
}
puts("Exact device Q, production viewport init/wrap/UTF8: spaces/agents/detail, held Play deferral PASS");
}
int main(void){struct gui_synclist list={0};real_payload(&list);full_clears=yes_draws=no_draws=texts=0;reset();
talk_held=true;pop_question(&list);assert(view==VIEW_AGENTS);talk_held=false;strcpy(talk_req,"voice");pop_question(&list);assert(view==VIEW_AGENTS);talk_req[0]=0;
pop_question(&list);assert(view==VIEW_ALERT&&wakes==1&&focused==0&&sent==0);
draw_question_overlay();assert(full_clears==1&&yes_draws&&no_draws&&texts>=5);
key(&list,99,0);assert(focused==0&&sent==0);key(&list,ACTION_STD_MENU,0);assert(view==VIEW_AGENTS&&model.nquestions==1);pop_question(&list);assert(view==VIEW_AGENTS);
open_question(&list,"p1");assert(view==VIEW_ALERT);key(&list,ACTION_STD_OK,0);assert(view==VIEW_QUESTION&&focused==1&&!detail_ready);answer_number(1);assert(sent==0);focus_req[0]=0;detail_ready=true;answer_number(3);assert(question_custom&&sent==1&&!strcmp(last_args,"id\t3"));
reset();view=VIEW_DETAIL;strcpy(detail_pane,"p1");detail_ready=true;pop_question(&list);key(&list,ACTION_STD_MENU,0);assert(view==VIEW_DETAIL&&focused==0&&detail_ready);
reset();pop_question(&list);key(&list,ACTION_STD_CANCEL,BUTTON_LEFT);assert(question_choice==1&&focused==1);
reset();pop_question(&list);key(&list,ACTION_STD_OK,BUTTON_RIGHT);assert(question_choice==-1&&focused==1);
reset();pop_question(&list);key(&list,ACTION_STD_CANCEL,BUTTON_RC_LEFT);assert(view==VIEW_QUESTION&&question_choice==1&&sent==0);focus_req[0]=0;detail_ready=true;answer_number(question_choice);assert(!strcmp(last_args,"id\t1"));
reset();pop_question(&list);key(&list,ACTION_STD_OK,BUTTON_RC_RIGHT);assert(question_choice==-1&&sent==0);focus_req[0]=0;detail_ready=true;answer_number(question_choice);assert(!strcmp(last_args,"id\t-1"));
reset();pop_question(&list);strcpy(focus_req,"old");key(&list,ACTION_STD_OK,BUTTON_RIGHT);assert(question_choice==0&&view==VIEW_ALERT);
reset();pop_question(&list);model.questions[0].yes=model.questions[0].no=0;key(&list,ACTION_STD_OK,BUTTON_RIGHT);assert(view==VIEW_ALERT&&sent==0&&focused==0);key(&list,ACTION_STD_OK,0);assert(view==VIEW_QUESTION);assert(!question_action(&list,ACTION_STD_OK));
reset();pop_question(&list);view=VIEW_INPUT;detail_ready=false;key(&list,ACTION_AGENTS_REMOTE_TALK,0);assert(voices==0);key(&list,ACTION_STD_OK,0);assert(sent==0);detail_ready=true;key(&list,ACTION_AGENTS_TALK,0);assert(voices==1);key(&list,ACTION_STD_OK,0);assert(sent==0);key(&list,ACTION_AGENTS_DISCARD,0);key(&list,ACTION_STD_CANCEL,BUTTON_RC_LEFT);assert(keys==1);key(&list,ACTION_STD_MENU,0);assert(sent==1&&!strcmp(last_name,"qi")&&!strcmp(last_args,"id\tesc")&&discarded>=1);
pending_req[0]=0;question_closing=false;
strcpy(key_req,"edit");key(&list,ACTION_STD_OK,0);assert(!pending_req[0]);key_req[0]=0;
key_runs.nruns=1;key(&list,ACTION_STD_OK,0);assert(!pending_req[0]);key_runs.nruns=0;
key(&list,ACTION_STD_OK,0);assert(!strcmp(last_args,"id\tenter"));
model.detail_valid=true;strcpy(model.detail_pane,"p1");view=VIEW_INPUT;draw_question_overlay();assert(input_draws==1);
view=VIEW_QUESTION;model.questions[0].noptions=2;model.questions[0].yes=1;model.questions[0].no=2;assert(question_binary_page());
strcpy(model.questions[0].id,"new");assert(!current_question());sent=0;answer_number(1);assert(!sent);
// Legacy Q works without binary/custom metadata; malformed binary Q is atomic.
struct hr_model old={0};const char*legacy="Q\told\tp\tw\t-\tAgent\n?\tChoose\no\t1\tOne\no\t2\tTwo";assert(hr_model_apply(&old,legacy,strlen(legacy))=='Q');assert(!old.questions[0].yes&&!old.questions[0].options[0].custom);
const char*bad="Q\tbad\tp\tw\t-\tAgent\t3\t2\tTask\no\t1\tYes\t-\no\t2\tNo\t-";assert(!hr_model_apply(&old,bad,strlen(bad)));assert(old.nquestions==1);
printf("FU21 production transitions: alert/defer/ignore/reopen, native+remote binary, focus gate, multi entry, voice/review/BS/submit/Esc, replacement ID, old Q: PASS\n");return 0;}
'''
(O/'questions.c').write_text(code)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie','-ftrivial-auto-var-init=pattern','-DREAL_PAYLOAD=\"'+str(R/'tools/tests/fixtures/fu21-hang-q.bin')+'\"','-I'+str(S/'apps-ipod/screens/system'),str(O/'questions.c'),str(S/'apps-ipod/screens/system/herdremote_model.c'),'-o',str(O/'questions')],check=True)
subprocess.run([str(O/'questions')],check=True)
