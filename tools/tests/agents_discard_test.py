"""Production voice queue, gesture and native chord probes; no microphone/bus."""
from pathlib import Path
import re
import subprocess
import sys

R = Path(__file__).resolve().parents[2]
O = Path(sys.argv[1]).resolve()
O.mkdir(parents=True, exist_ok=True)

def function(text, name):
    match = re.search(r'(?m)^[\w *]+\b'+name+r'\([^;{}]*\)\s*\{', text)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[match.start():end]

agents = (R/'apps-ipod/screens/system/agents_screen.c').read_text()
action = (R/'apps-ipod/input/action.c').read_text()
remote = re.sub(r'^#include .*$', '', (R/'apps-ipod/input/agents_remote.c').read_text(), flags=re.M)
chord = re.sub(r'^#include .*$', '', (R/'apps-ipod/input/agents_shortcut.c').read_text(), flags=re.M)
arms = agents[agents.index('            case ACTION_AGENTS_TALK:'):agents.index('            case ACTION_STD_CONTEXT:',agents.index('            case ACTION_AGENTS_TALK:'))]
events = []
down = False
for line in (R/'tools/tests/fixtures/remote-play/remote-log-3-new.txt').read_text().splitlines():
    m = re.search(r'tick=(\d+).*stored=4 02 00 (01|00) 00', line)
    assert m
    value = m[2] == '01'
    flag = '|BUTTON_REPEAT' if down and value else '|BUTTON_REL' if not value else ''
    events.append('{'+m[1]+',BUTTON_RC_PLAY'+flag+','+('BUTTON_RC_PLAY' if value else '0')+'}')
    down = value
pre = r'''
#include <assert.h>
#include <stdio.h>
#include <stdbool.h>
#include <string.h>
#include <stdint.h>
#define IPOD_ACCESSORY_PROTOCOL
#define HAVE_USB_PODLINK
#define HZ 100
#define BUTTON_NONE 0
#define BUTTON_REL 0x02000000
#define BUTTON_REPEAT 0x04000000
#define BUTTON_REDRAW 0x20000000
#define BUTTON_MULTIMEDIA 0x10000000
#define SYS_EVENT 0x80000000u
#include "firmware/target/arm/ipod/button-target.h"
#include "apps-ipod/input/agents_remote.h"
#include "apps-ipod/input/agents_shortcut.h"
#define TIME_BEFORE(a,b) ((long)((unsigned long)(a)-(unsigned long)(b))<0)
#define OUTBOX 4
#define TALK_REPLY (8*HZ)
#define PODLINK_CONNECTED 1
#define VIEW_CONFIRM 1
#define ACTION_AGENTS_TALK 10
#define ACTION_AGENTS_REMOTE_TALK 11
#define ACTION_AGENTS_DISCARD 12
#define ACTION_AGENTS_SWITCH 13
#define ACTION_AGENTS_TALK_END 14
#define AGENTS_SWITCH_WPS 2
static unsigned long current_tick,shortcut_talking_at;
static bool shortcut_talking,talk_held,talk_remote,talk_discarded,talk_req_up,talk_failed;
static long talk_since,talk_until;
static char talk_req[8],talk_note[64],outbox[OUTBOX][96];
static int music;
#define BUTTON_MULTIMEDIA_PLAYPAUSE 0x10000100
static void __attribute__((unused)) default_event_handler(int b){assert(b==BUTTON_MULTIMEDIA_PLAYPAUSE);music++;}
static bool native_play_pending;static unsigned long native_play_at __attribute__((unused));
static int nout,view,ret,switches;static bool done;
static unsigned int next_req=1;
static struct {bool hello,result_new,result_ok;int voice;char result_req[8],result_text[64];}model;
struct gui_synclist {int unused;};
static int podlink_state(void){return PODLINK_CONNECTED;}
static void talk_target(struct gui_synclist*l,char*b,size_t n){(void)l;snprintf(b,n,"-");}
static char commands[64][96];static int ncommands;
static unsigned long sent_at[64];
static void flush(void){for(int i=0;i<nout;i++){assert(ncommands<64);strcpy(commands[ncommands],outbox[i]);sent_at[ncommands++]=current_tick;}nout=0;}
'''
code = re.sub(r'^#include .*$', '', (R/'firmware/export/button_chord.h').read_text(), flags=re.M)
code += function(action,'agents_shortcut_talking') + chord + remote
for name in ['outbox_slot','take_req','talk_result','start_talk','finish_talk','talk_is_short','talk_wanted','take_talk_result']:
    code += '\n'+function(agents,name)
if 'static void finish_native_play' in agents:code += '\n'+function(agents,'finish_native_play')
code += '\nstatic void perform(int button) {struct gui_synclist list={0};int action=0;\n'
code += r'''
 if(button==BUTTON_PLAY)action=ACTION_AGENTS_TALK;
 else if(button==BUTTON_RC_PLAY)action=ACTION_AGENTS_REMOTE_TALK;
 else if(button==(BUTTON_RC_PLAY|BUTTON_REL|BUTTON_REPEAT))action=ACTION_AGENTS_DISCARD;
 else if(button==(BUTTON_PLAY|BUTTON_REL))action=ACTION_AGENTS_TALK_END;
 else if(button==REMOTE_SWITCH_BUTTON)action=ACTION_AGENTS_SWITCH;
 switch(action) {
'''+arms+r'''
 default:break;
 }
 if(done){switches++;done=false;assert(ret==AGENTS_SWITCH_WPS);}
 flush();
 if(action==ACTION_AGENTS_DISCARD)assert(!talk_wanted());
}
static struct agents_remote_state rstate;
static struct agents_shortcut_state nstate;
static void reset(void){
 memset(&rstate,0,sizeof rstate);memset(&nstate,0,sizeof nstate);
 talk_held=talk_discarded=talk_failed=shortcut_talking=false;
 talk_note[0]=talk_req[0]=0;model.hello=true;model.voice=0;
 nout=ncommands=switches=music=0;native_play_pending=false;done=false;
}
static void rc(int b,int held,unsigned long tick){
 current_tick=tick;perform(agents_remote_button(&rstate,REMOTE_AGENTS,b,held,tick));
 while(rstate.queued)perform(agents_remote_button(&rstate,REMOTE_AGENTS,0,held,tick));
}
static void native(int b,int held,unsigned long tick){
 current_tick=tick;bool fire;
 int result=agents_shortcut_button(&nstate,b,held,shortcut_talking&&
             tick-shortcut_talking_at>(3*HZ+19)/20,&fire);
 perform(fire?REMOTE_SWITCH_BUTTON:result);
}
static void pair_is(int index,const char*end){
 assert(!strncmp(commands[index],"vd\t",3));
 assert(!strncmp(commands[index+1],end,3));
}
struct event {unsigned long tick;int button,held;};
'''
tests = r'''
int main(void){
 reset();native(BUTTON_PLAY,BUTTON_PLAY,100);assert(ncommands==1&&sent_at[0]==100);
 assert(!talk_wanted());model.voice='t';current_tick=114;assert(!talk_wanted());
 current_tick=115;assert(talk_wanted());current_tick=100;model.voice=0;
 native(BUTTON_PLAY|BUTTON_REL,0,114);pair_is(0,"vc\t");assert(!talk_wanted());assert(music==1);
 reset();model.hello=false;native(BUTTON_PLAY,BUTTON_PLAY,100);assert(!talk_wanted());
 native(BUTTON_PLAY|BUTTON_REL,0,114);assert(music==1&&!ncommands&&!talk_wanted());
 reset();native(BUTTON_PLAY,BUTTON_PLAY,100);native(BUTTON_PLAY|BUTTON_REL,0,115);
 pair_is(0,"vu\t");assert(!music);
 reset();native(BUTTON_PLAY,BUTTON_PLAY,100);
 native(BUTTON_SELECT|BUTTON_PLAY,BUTTON_SELECT|BUTTON_PLAY,110);
 assert(switches==1&&ncommands==2);pair_is(0,"vc\t");
 native(BUTTON_PLAY|BUTTON_SELECT|BUTTON_REL,0,120);assert(ncommands==2);
 reset();native(BUTTON_PLAY,BUTTON_PLAY,100);
 native(BUTTON_SELECT|BUTTON_PLAY,BUTTON_SELECT|BUTTON_PLAY,115);
 assert(switches==1&&ncommands==2);pair_is(0,"vc\t");
 reset();native(BUTTON_SELECT,BUTTON_SELECT,100);
 native(BUTTON_SELECT|BUTTON_PLAY,BUTTON_SELECT|BUTTON_PLAY,105);
 assert(switches==1&&!ncommands);
 reset();native(BUTTON_PLAY,BUTTON_PLAY,100);
 native(BUTTON_SELECT|BUTTON_PLAY,BUTTON_SELECT|BUTTON_PLAY,116);
 assert(!switches&&ncommands==1&&talk_held);
 native(BUTTON_PLAY|BUTTON_REL,0,125);pair_is(0,"vu\t");
 reset();unsigned long previous=captured[0].tick;int held=0;
 for(unsigned int i=0;i<sizeof(captured)/sizeof(captured[0]);i++){
  struct event e=captured[i];
  for(unsigned long t=previous+1;t<e.tick;t++)rc(0,held,t);
  rc(e.button,e.held,e.tick);previous=e.tick;held=e.held;
 }
 rc(0,0,previous+21);assert(ncommands==28&&switches==3);
 for(int i=0;i<12;i++)pair_is(i*2,"vc\t");
 pair_is(24,"vu\t");pair_is(26,"vu\t");
 assert(sent_at[0]==153358&&sent_at[1]==153371);
 /* UI samples, including early host V replies, stay dark until hold. */
 for(int remote=0;remote<2;remote++) {
  reset();current_tick=100;struct gui_synclist list={0};start_talk(&list,remote);
  flush();assert(ncommands==1&&!strncmp(commands[0],"vd\t",3));model.voice='t';
  int threshold=remote?30:15,band_frames=0;
  for(int t=0;t<threshold;t++){current_tick=100+t;if(talk_wanted())band_frames++;}
  assert(!band_frames);current_tick=100+threshold;assert(talk_wanted());
  finish_talk(false);flush();pair_is(0,"vu\t");
  reset();current_tick=100;start_talk(&list,remote);flush();model.voice='t';
  current_tick=100+threshold-1;assert(!talk_wanted());finish_talk(true);flush();
  assert(!talk_wanted()&&!talk_note[0]);pair_is(0,"vc\t");
 }
 reset();rc(BUTTON_RC_PLAY,BUTTON_RC_PLAY,100);model.voice='t';assert(!talk_wanted());
 rc(BUTTON_RC_PLAY|BUTTON_REL,0,113);assert(!talk_wanted());
 rc(BUTTON_RC_PLAY,BUTTON_RC_PLAY,118);model.voice='t';assert(!talk_wanted());
 rc(BUTTON_RC_PLAY|BUTTON_REL,0,131);assert(switches==1&&!talk_wanted());
 /* A delayed successful vc result cannot display Sent or Talking. */
 reset();rc(BUTTON_RC_PLAY,BUTTON_RC_PLAY,100);rc(BUTTON_RC_PLAY|BUTTON_REL,0,113);
 strcpy(model.result_req,talk_req);strcpy(model.result_text,"Discarded");model.result_ok=true;
 assert(take_talk_result());model.voice='t';assert(!talk_wanted()&&!talk_note[0]);
 /* A full command burst always leaves a termination slot. */
 reset();current_tick=100;struct gui_synclist list={0};start_talk(&list,true);
 assert(nout==1);while(outbox_slot()){}assert(nout==OUTBOX-1);
 current_tick=113;finish_talk(true);assert(nout==OUTBOX&&!strncmp(outbox[OUTBOX-1],"vc\t",3));
 reset();nout=OUTBOX-1;start_talk(&list,true);assert(!talk_held&&nout==OUTBOX-1);
 puts("immediate vd / native 150ms discard+chord / remote real taps vc, holds vu / no Sent / termination slot PASS");
}
'''
(O/'config.h').write_text('')
(O/'discard.c').write_text(pre+code+'\nstatic const struct event captured[]={'+','.join(events)+'};\n'+tests)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g',
                '-fsanitize=address,undefined','-fno-sanitize-recover=all',
                '-fno-pie','-no-pie','-I'+str(O),'-I'+str(R),
                '-I'+str(R/'apps-ipod'),str(O/'discard.c'),'-o',str(O/'discard')],check=True)
subprocess.run([str(O/'discard')],check=True)
