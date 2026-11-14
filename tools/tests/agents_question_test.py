"""Compile the actual FU21 view transitions/key handler with a fake link/UI."""
from pathlib import Path
import re,subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
S=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else R
s=(S/'apps-ipod/screens/system/agents_screen.c').read_text()
def function(name):
 m=re.search(r'(?m)^[\w *]+\b'+name+r'\([^;{}]*\)\s*\{',s);assert m,name
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
static char last_name[16],last_args[64];
#define SCREEN_MAIN 0
#define FONT_UI 1
#define USE_LINE 14
#define USE_TEXT 1
#define USE_DIM 2
#define MIN(a,b) ((a)<(b)?(a):(b))
struct viewport {int width,height,font;};
struct screen {void (*set_viewport)(struct viewport*);void (*clear_viewport)(void);void (*set_foreground)(int);void (*drawrect)(int,int,int,int);void (*update_viewport)(void);};
static int full_clears,input_draws,texts,yes_draws,no_draws;
static void setvp(struct viewport*v){if(v)assert(v->width==320&&v->height==240);}
static void clearvp(void){full_clears++;}
static void colour(int c){(void)c;}
static void rect(int x,int y,int w,int h){assert(x>=0&&y>=0&&x+w<=320&&y+h<=240);}
static void update(void){}
static struct screen screens[]={{setvp,clearvp,colour,rect,update}};
static void viewport_set_fullscreen(struct viewport*v,int screen){assert(!screen);*v=(struct viewport){320,240,1};}
static void text_at(struct screen*d,int x,int y,int colour,const char*t){(void)d;(void)colour;assert(x>=0&&x<320&&y>=0&&y+USE_LINE<=240);texts++;yes_draws+=strstr(t,"LEFT: Yes")!=NULL;no_draws+=strstr(t,"RIGHT: No")!=NULL;}
struct dialog_text_line {const char*str;int len;};
static int dialog_wrap_text(const char*t,int w,int font,struct dialog_text_line*out,int n){assert(w==308&&font==FONT_UI&&n==8);out[0]=(struct dialog_text_line){t,(int)strlen(t)};return 1;}
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
code=pre+'\n'.join(function(n) for n in ['current_question','question_binary_page','draw_question_overlay','open_question','leave_question','pop_question','enter_answer','answer_number','close_input','question_action'])
code+=r'''
static void reset(void){memset(&model,0,sizeof model);memset(&popped,0,sizeof popped);view=VIEW_AGENTS;question_id[0]=question_pane[0]=pending_req[0]=focus_req[0]=talk_req[0]=detail_pane[0]=0;talk_held=false;focused=sent=wakes=keys=voices=discarded=0;status=raw=0;
const char*q="Q\tid\tp1\tw1\t-\tAgent\t1\t-1\tTask\n?\tRun command?\no\t1\tYes\t-\no\t2\tAlways allow\t-\no\t3\tNo, and tell Codex differently\tc";assert(hr_model_apply(&model,q,strlen(q))=='Q');}
static void key(struct gui_synclist*l,int action,int button){raw=button;assert(question_action(l,action));}
int main(void){struct gui_synclist list={0};reset();
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
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(S/'apps-ipod/screens/system'),str(O/'questions.c'),str(S/'apps-ipod/screens/system/herdremote_model.c'),'-o',str(O/'questions')],check=True)
subprocess.run([str(O/'questions')],check=True)
