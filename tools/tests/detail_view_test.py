"""Render the production detail routine into an offline pixel-row recorder."""
from pathlib import Path
import re,subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
S=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else R
s=(S/'apps-ipod/screens/system/agents_screen.c').read_text()
def function(text,name):
 m=re.search(r'(?m)^[\w *]+\b'+name+r'\([^;{}]*\)\s*\{',text);assert m,name
 e=m.end();d=1
 while d:d+=(text[e]=='{')-(text[e]=='}');e+=1
 return text[m.start():e]
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include "herdremote_model.h"
#include "detail_layout.h"
#define SCREEN_MAIN 0
#define INPUT_LINES 3
#define USE_LINE 10
#define DET_GAP 2
#define DET_DOT 3
#define USE_DIM 1
#define USE_TEXT 2
#define USE_WARN 3
#define USE_SOFT 4
#define DET_ACCENT 5
#define DOT_RED 1
#define DOT_GREEN 2
#define DOT_TEAL 3
#define DOT_GREY 4
#define TASK_REST 100
#define MIN(a,b) ((a)<(b)?(a):(b))
#define MAX(a,b) ((a)>(b)?(a):(b))
#define IS_DIACRITIC(ch) ((ch)>=0x300&&(ch)<=0x36f)
typedef uint32_t ucschar_t;
static const unsigned char *utf8decode(const unsigned char*p,ucschar_t*c){*c=*p;int n=*p<128?1:*p<224?2:*p<240?3:4;return p+(*p?n:0);}
struct font {int height;};static struct font fixed={8};
static struct font *font_get(int n){(void)n;return &fixed;}
static int font_get_width(struct font*f,ucschar_t c){(void)f;return c?6:0;}
static void font_lock(int n,bool b){(void)n;(void)b;}
struct viewport {int font,width,height;unsigned fg_pattern;};
struct screen {void (*set_viewport)(struct viewport*);void (*clear_viewport)(void);void (*set_foreground)(unsigned);void (*update_viewport)(void);void(*hline)(int,int,int);};
static char rows[180][200];
static void setvp(struct viewport*v){(void)v;}static void clear(void){memset(rows,0,sizeof rows);}
static void fg(unsigned n){(void)n;}static void update(void){}static void hline(int a,int b,int c){(void)a;(void)b;(void)c;}
static struct screen screens[]={{setvp,clear,fg,update,hline}};
struct gui_synclist {struct viewport *parent[1];};
static struct hr_model model;static struct hr_agent detail_agent={.kind="codex",.name="test"};
static struct detail_layout detail_layout;
static int detail_body_y,detail_body_avail,detail_scroll,detail_scroll_max,detail_page_lines,detail_shown_age;
static char detail_pane[16]="p1",task_text[244];static int task_w,task_x,task_y,task_next,current_tick;static bool task_at_end;
static const struct hr_agent *find_agent(const char*p){(void)p;return &detail_agent;}
static int detail_age(void){return 1;}
static const char *trouble(char*b,size_t n){(void)b;(void)n;return NULL;}
static char agent_state(const struct hr_agent*a){(void)a;return 'w';}
static const char *status_word(char s){(void)s;return "working";}
static const char *fmt_age(char*b,size_t n,int s){snprintf(b,n,"%d",s);return b;}
static int text_w(struct screen*d,const char*s){(void)d;return strlen(s)*6;}
static void text_at(struct screen*d,int x,int y,unsigned c,const char*s){(void)d;(void)x;(void)c;assert(y>=0&&y<180);snprintf(rows[y],200,"%s",s);}
static void draw_disc(struct screen*d,int x,int y,unsigned c){(void)d;(void)x;(void)y;(void)c;}
static const char *fit(char*b,size_t n,const char*s,int w){(void)b;(void)n;(void)w;return s;}
static void draw_task(struct screen*d,struct viewport*v){(void)d;(void)v;}
static int draw_input(struct screen*d,struct viewport*v,int y,bool dim){(void)d;(void)v;(void)dim;return y;}
'''
wrap=(S/'apps-ipod/draw/text_wrap.c').read_text()
code='\n'.join(function(wrap,n) for n in ['is_punct_break','wrap_next','text_wrap_next_punct'])
for n in ['detail_steps','detail_glyph']:
 if re.search(r'\b'+n+r'\(',s):code+='\n'+function(s,n)
code+='\n'+function(s,'draw_detail')
post=r'''
static int glyph(const char*p,int*n,void*ctx){(void)p;(void)ctx;*n=1;return 1;}
int main(void){
 (void)detail_layout;(void)detail_body_y;(void)detail_body_avail;(void)glyph;
 struct viewport vp={.width=120,.height=88};struct gui_synclist list={.parent={&vp}};
 model.detail_valid=true;strcpy(model.detail_pane,"p1");strcpy(model.detail_revision,"1234567800000001");
 strcpy(model.detail.now,"working");
 for(int i=0;i<280;i++)model.detail.say[i]='a'+i%23;model.detail.say[280]=0;
 draw_detail(&list);assert(detail_scroll_max>1);
 detail_scroll=1;draw_detail(&list);char before[180][200];memcpy(before,rows,sizeof rows);
 memmove(model.detail.say,model.detail.say+7,273);memcpy(model.detail.say+273,"newdata",8);
 strcpy(model.detail_revision,"1234567800000002");draw_detail(&list);
 assert(detail_scroll==1);assert(!memcmp(before,rows,sizeof rows));
 puts("production draw: streaming rolling prefix while scrolled up keeps all rendered rows and offset PASS");
 detail_scroll=detail_scroll_max;draw_detail(&list);int maximum=detail_scroll_max;
 strcpy(model.detail.step[0],"test inProgress");draw_detail(&list);assert(detail_scroll_max==maximum);
 strcpy(model.detail.step[0],"test completed");draw_detail(&list);assert(detail_scroll_max==maximum);
 puts("production draw: tool start/finish cannot re-anchor message PASS");
 struct detail_layout c={0};struct hr_detail det={0};
 detail_layout_update(&c,"p1","id1","abcd ef",&det,8,false,glyph,NULL);
 char line[9];memcpy(line,c.text,7);line[7]=0;
 detail_layout_update(&c,"p1","id1","abcd efghijklmnop",&det,8,false,glyph,NULL);
 assert(!memcmp(c.text,line,7)&&c.end[0]==8);
 struct detail_layout frozen=c;
 detail_layout_update(&c,"p1","id1","replacement",&det,8,true,glyph,NULL);assert(!memcmp(&frozen,&c,sizeof c));
 for(int i=0;i<5000;i++){
  char wire[HR_DETAIL_SAY];snprintf(wire,sizeof wire,"%s%c",c.wire,'a'+i%26);
  if(strlen(wire)>500)memmove(wire,wire+200,strlen(wire)-199);
  detail_layout_update(&c,"p1","id1",wire,&det,8,false,glyph,NULL);
  assert(c.used<DETAIL_LAYOUT_BYTES&&c.count<=DETAIL_LAYOUT_LINES);
 }
 printf("bounded append-only layout: bytes=%zu / 5000 rolling updates / frozen snapshot PASS\n",sizeof c);
}
'''
(O/'view.c').write_text(pre+code+post)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-Wno-format-truncation','-g','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(R/'apps-ipod/screens/system'),str(O/'view.c'),str(R/'apps-ipod/screens/system/detail_layout.c'),str(S/'apps-ipod/screens/system/herdremote_model.c'),'-o',str(O/'view')],check=True)
subprocess.run([str(O/'view')],check=True)
