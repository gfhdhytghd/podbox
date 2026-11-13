"""Execute the production direct Usage lease sender; no serial/device access."""
from pathlib import Path
import re
import subprocess
import sys
R=Path(__file__).resolve().parents[2]
O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
s=(R/'apps-ipod/screens/system/agents_screen.c').read_text()
a=s.index('#define USAGE_VIEW_SIGNAL_TICKS');b=s.index('\nstatic const struct hr_question',a)
lease=s[a:b]
assert 'signal_usage_view(view == VIEW_USAGE ? 1 : view == VIEW_DETAIL ? 2 : 0, false);' in s
assert 'signal_usage_view(false, true);\n    finish_talk(false);' in s
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <limits.h>
static bool detail_ready;static char focus_req[8],detail_pane[16]="w1:p1";
#define HZ 100
#define MAX(a,b) ((a)>(b)?(a):(b))
#define TIME_BEFORE(a,b) ((long)((unsigned long)(a)-(unsigned long)(b))<0)
#define PODLINK_CONNECTED 3
#define PODLINK_TEXT 2
#define PODLINK_SENT 0
static long current_tick;
static bool connected=true,busy;
static int sent,revokes;
static char last[32];
static int podlink_state(void){return connected?PODLINK_CONNECTED:0;}
static int podlink_send(int type,const char*data,unsigned int len){
 assert(type==PODLINK_TEXT);if(busy)return -1;
 assert(len<32);memcpy(last,data,len);last[len]=0;sent++;if(!strcmp(last,"vw\t0"))revokes++;return PODLINK_SENT;
}
'''
post=r'''
int main(void){
 signal_usage_view(false,true);assert(sent==1&&!strcmp(last,"vw\t0"));
 current_tick=1;signal_usage_view(true,false);assert(sent==2&&!strcmp(last,"vw\t1"));
 current_tick=25;signal_usage_view(true,false);assert(sent==2);
 current_tick=26;signal_usage_view(true,false);assert(sent==3);
 current_tick=27;signal_usage_view(false,false);assert(sent==4&&!strcmp(last,"vw\t0"));
 busy=true;current_tick=28;signal_usage_view(true,false);assert(sent==4);
 busy=false;signal_usage_view(true,false);assert(sent==5&&!strcmp(last,"vw\t1"));
 connected=false;signal_usage_view(false,true);assert(sent==5);
 connected=true;signal_usage_view(false,true);assert(sent==6&&!strcmp(last,"vw\t0"));
 current_tick=LONG_MAX-2;signal_usage_view(true,true);assert(sent==7);
 current_tick=LONG_MIN+2;signal_usage_view(true,false);assert(sent==7);
 current_tick=LONG_MIN+23;signal_usage_view(true,false);assert(sent==8);
 detail_ready=false;current_tick=100;signal_usage_view(2,true);assert(!strcmp(last,"vw\t0"));
 detail_ready=true;strcpy(focus_req,"f1");signal_usage_view(2,true);assert(!strcmp(last,"vw\t0"));
 focus_req[0]=0;int old_revokes=revokes;signal_usage_view(2,true);assert(revokes==old_revokes+1);assert(!strcmp(last,"vw\tdetail\tw1:p1"));
 strcpy(detail_pane,"w1:p2");signal_usage_view(2,false);assert(!strcmp(last,"vw\tdetail\tw1:p2"));
 signal_usage_view(0,true);assert(!strcmp(last,"vw\t0"));
 puts("S2/FU16 production view lease: direct enter/leave/250ms refresh/busy retry/disconnect/exit/wrap PASS");
}
'''
p=O/'lease.c';p.write_text(pre+lease+post)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g',
 '-fsanitize=address,undefined','-fno-pie','-no-pie',str(p),'-o',str(O/'lease')],check=True)
subprocess.run([str(O/'lease')],check=True)
