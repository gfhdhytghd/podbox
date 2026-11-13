"""Real dock Play timings through the production remote gesture consumer."""
from pathlib import Path
import re
import subprocess
import sys

R = Path(__file__).resolve().parents[2]
O = Path(sys.argv[1]).resolve()
O.mkdir(parents=True, exist_ok=True)
evidence = R/'tools/tests/fixtures/remote-play/remote-log-2.txt'
events = []
down = False
for line in evidence.read_text().splitlines():
    match = re.search(r'tick=(\d+).*stored=4 02 00 (01|00) 00', line)
    if not match:
        continue
    tick, value = int(match[1]), match[2] == '01'
    if not value and not down:
        continue
    flag = '|BUTTON_REPEAT' if value and down else '|BUTTON_REL' if not value else ''
    events.append(f'{{{tick},BUTTON_RC_PLAY{flag},{"BUTTON_RC_PLAY" if value else "0"}}}')
    down = value
new_events=[]
new_log=R/'tools/tests/fixtures/remote-play/remote-log-3-new.txt'
down=False
for line in new_log.read_text().splitlines():
    m=re.search(r'tick=(\d+).*stored=4 02 00 (01|00) 00',line)
    assert m,line
    tick,value=int(m[1]),m[2]=='01'
    flag='|BUTTON_REPEAT' if value and down else '|BUTTON_REL' if not value else ''
    new_events.append(f'{{{tick},BUTTON_RC_PLAY{flag},{"BUTTON_RC_PLAY" if value else "0"}}}')
    down=value
ir=(R/'tools/tests/fixtures/remote-play/ir-dbl.log').read_text()
presses=[float(x) for x in re.findall(r't=([\d.]+) NEC ee 87 04 59',ir)]
assert sum(.16<y-x<.18 for x,y in zip(presses,presses[1:]))==3
source = re.sub(r'^#include .*$', '', (R/'apps-ipod/input/agents_remote.c').read_text(), flags=re.M)
pre = r'''
#include <assert.h>
#include <stdbool.h>
#define REMOTE_LOG_DISABLE
#include "firmware/export/remote_log.h"
#include <stdio.h>
#include <limits.h>
#define IPOD_ACCESSORY_PROTOCOL
#include "firmware/target/arm/ipod/button-target.h"
#define BUTTON_NONE 0
#define BUTTON_REL 0x02000000
#define BUTTON_REPEAT 0x04000000
#define BUTTON_REDRAW 0x20000000
#define HZ 100
#include "apps-ipod/input/agents_remote.h"
#define SWITCH (BUTTON_REDRAW|BUTTON_SELECT|BUTTON_PLAY)
static struct agents_remote_state state;
static int output[32], count;
static void feed(int mode,int button,int held,unsigned long tick) {
    int b=agents_remote_button(&state,mode,button,held,tick);
    if(mode==REMOTE_AGENTS&&(b==BUTTON_RC_PLAY||b==(BUTTON_RC_PLAY|BUTTON_REL|BUTTON_REPEAT)))b=0;
    if(b){assert(count<32);output[count++]=b;}
    /* Production polling drains queued output before fetching more input. */
    for(int i=0;i<6;i++) {
        b=agents_remote_button(&state,mode,0,held,tick);
        if(mode==REMOTE_AGENTS&&(b==BUTTON_RC_PLAY||b==(BUTTON_RC_PLAY|BUTTON_REL|BUTTON_REPEAT)))b=0;
        if(!b) continue;
        assert(count<32);output[count++]=b;
    }
}
static void reset(void){state=(struct agents_remote_state){0};count=0;}
struct event {unsigned long tick;int button,held;};
'''
tests = r'''
int main(void) {
    const int modes[]={REMOTE_AGENTS,REMOTE_OTHER,REMOTE_WPS};
    for(int c=0;c<3;c++) {
        reset();
        /* Captured 290 ms + 130 ms taps, with a 150 ms release-to-press gap. */
        for(int i=0;i<11;i++) feed(modes[c],captured[i].button,captured[i].held,captured[i].tick);
        assert(count==1&&output[0]==SWITCH);
        feed(modes[c],0,0,64860);assert(count==1);
        /* New 130 ms taps in Agents; old 400 ms taps elsewhere, slow gaps. */
        unsigned long first=c==0?153358:64879,length=c==0?13:40;
        unsigned long gap=c==0?27:25,up=first+length,second=up+gap;
        reset();feed(modes[c],BUTTON_RC_PLAY,BUTTON_RC_PLAY,first);
        feed(modes[c],BUTTON_RC_PLAY|BUTTON_REL,0,up);
        assert(!count);feed(modes[c],0,0,up+20);assert(!count);
        feed(modes[c],0,0,up+21);assert(count==2);
        feed(modes[c],BUTTON_RC_PLAY,BUTTON_RC_PLAY,second);
        feed(modes[c],BUTTON_RC_PLAY|BUTTON_REL,0,second+length);
        feed(modes[c],0,0,second+length+21);assert(count==4);
        for(int i=0;i<count;i++) assert(output[i]!=SWITCH);
        for(int gap=19;gap<=21;gap++) {
            reset();feed(modes[c],BUTTON_RC_PLAY,BUTTON_RC_PLAY,100);
            feed(modes[c],BUTTON_RC_PLAY|BUTTON_REL,0,129);
            feed(modes[c],BUTTON_RC_PLAY,BUTTON_RC_PLAY,129+gap);
            feed(modes[c],BUTTON_RC_PLAY|BUTTON_REL,0,142+gap);
            if(gap<=20) assert(count==1&&output[0]==SWITCH);
            else {feed(modes[c],0,0,180+gap);assert(count==4);}
        }
    }
    reset();feed(REMOTE_AGENTS,BUTTON_RC_PLAY,BUTTON_RC_PLAY,100);
    feed(REMOTE_AGENTS,0,BUTTON_RC_PLAY,129);assert(!count);
    feed(REMOTE_AGENTS,0,BUTTON_RC_PLAY,130);assert(!count);
    feed(REMOTE_AGENTS,BUTTON_RC_PLAY|BUTTON_REPEAT,BUTTON_RC_PLAY,160);assert(!count);
    feed(REMOTE_AGENTS,BUTTON_RC_PLAY|BUTTON_REL,0,180);
    assert(count==1&&output[0]==(BUTTON_PLAY|BUTTON_REL));
    feed(REMOTE_AGENTS,BUTTON_RC_PLAY,BUTTON_RC_PLAY,190);
    feed(REMOTE_AGENTS,BUTTON_RC_PLAY|BUTTON_REL,0,203);
    feed(REMOTE_AGENTS,0,0,224);assert(count==3);
    reset();feed(REMOTE_AGENTS,BUTTON_RC_PLAY,BUTTON_RC_PLAY,100);
    feed(REMOTE_AGENTS,BUTTON_RC_PLAY|BUTTON_REL,0,129);
    feed(REMOTE_AGENTS,BUTTON_RC_PLAY,BUTTON_RC_PLAY,139);
    feed(REMOTE_AGENTS,0,BUTTON_RC_PLAY,169);assert(count==2);
    feed(REMOTE_AGENTS,BUTTON_RC_PLAY|BUTTON_REL,0,180);
    assert(count==3&&output[2]==(BUTTON_PLAY|BUTTON_REL));
    for(int mode=REMOTE_OTHER;mode<=REMOTE_WPS;mode+=2) {
        reset();feed(mode,BUTTON_RC_PLAY,BUTTON_RC_PLAY,300);
        feed(mode,0,BUTTON_RC_PLAY,359);assert(!count);
        feed(mode,0,BUTTON_RC_PLAY,360);
        if(mode==REMOTE_OTHER)assert(count==1&&output[0]==REMOTE_WPS_BUTTON);
        else assert(count==2&&output[0]==BUTTON_RC_PLAY&&output[1]==(BUTTON_RC_PLAY|BUTTON_REPEAT));
        feed(mode,BUTTON_RC_PLAY|BUTTON_REL,0,380);
        for(int i=0;i<count;i++)assert(output[i]!=SWITCH);
    }
    /* Replay every frame and intervening timer tick from the new device log. */
    for(int c=0;c<3;c++) {
        reset();unsigned long previous=new_capture[0].tick;int held=0;
        for(unsigned int i=0;i<sizeof(new_capture)/sizeof(new_capture[0]);i++) {
            struct event e=new_capture[i];
            for(unsigned long t=previous+1;t<e.tick;t++)feed(modes[c],0,held,t);
            feed(modes[c],e.button,e.held,e.tick);previous=e.tick;held=e.held;
        }
        feed(modes[c],0,0,previous+21);int switches=0;
        for(int i=0;i<count;i++)switches+=output[i]==SWITCH;
        assert(switches==3&&count==(c==0?17:c==1?17:21));
    }
    /* An unpolled second hold can emit five queued WPS events at release. */
    reset();feed(REMOTE_WPS,BUTTON_RC_PLAY,BUTTON_RC_PLAY,100);
    feed(REMOTE_WPS,BUTTON_RC_PLAY|BUTTON_REL,0,113);
    feed(REMOTE_WPS,BUTTON_RC_PLAY,BUTTON_RC_PLAY,123);
    feed(REMOTE_WPS,BUTTON_RC_PLAY|BUTTON_REL,0,183);assert(count==5);
    reset();feed(REMOTE_OTHER,BUTTON_RC_PLAY,BUTTON_RC_PLAY,ULONG_MAX-30);
    feed(REMOTE_OTHER,BUTTON_RC_PLAY|BUTTON_REL,0,ULONG_MAX-10);
    feed(REMOTE_OTHER,BUTTON_RC_PLAY,BUTTON_RC_PLAY,9);
    feed(REMOTE_OTHER,BUTTON_RC_PLAY|BUTTON_REL,0,19);
    assert(count==1&&output[0]==SWITCH);
    reset();feed(REMOTE_AGENTS,BUTTON_PLAY,BUTTON_PLAY,100);
    feed(REMOTE_AGENTS,BUTTON_PLAY|BUTTON_REPEAT,BUTTON_PLAY,115);
    feed(REMOTE_AGENTS,BUTTON_PLAY|BUTTON_REL,0,120);assert(count==3);
    puts("captured Play timings: double/single/slow/200ms inclusive/300ms remote classification/600ms other hold/native isolation/wrap PASS");
}
'''
(O/'config.h').write_text('')
(O/'double.c').write_text(pre+source+'\nstatic const struct event captured[]={'+','.join(events)+'};\nstatic const struct event new_capture[]={'+','.join(new_events)+'};\n'+tests)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g',
                '-fsanitize=address,undefined','-fno-sanitize-recover=all',
                '-fno-pie','-no-pie','-I'+str(O),'-I'+str(R),
                '-I'+str(R/'apps-ipod'),str(O/'double.c'),'-o',str(O/'double')],check=True)
subprocess.run([str(O/'double')],check=True)
