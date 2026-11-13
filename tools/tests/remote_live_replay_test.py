"""FU17: replay exact fast Dock timelines through the unchanged FU16 decoder."""
from pathlib import Path
import re,subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
lines=(R/'tools/tests/fixtures/remote-play/fu17/remote-live-extract.log').read_text().splitlines()
def trace(kind,start,end):
 out=[];previous=False
 for line in lines:
  pattern=(r'LOG \d+ tick=(\d+).*L2/00.*stored=4 02 00 (01|00) 00' if kind=='raw' else r'LOG HELD tick=(\d+) (PLAY|\(release\))$')
  m=re.search(pattern,line)
  if not m:continue
  tick=int(m[1]);down=m[2] in ('01','PLAY')
  if not start<=tick<=end:continue
  if down!=previous:out.append((tick,down));previous=down
 assert out and not previous,(kind,start,end,out)
 return out
cases=[('double-60ms','raw',25122,25154,2),('double-40ms','raw',25256,25286,2),
 ('double-30ms','raw',25379,25408,2),('double-30ms-again','raw',25614,25644,2),
 ('single-130ms','raw',25494,25507,1),('hold-1600ms','raw',31856,32016,0),
 ('new-double-40ms','raw',236239,236269,2),('new-double-30ms','raw',236342,236376,2),
 ('actual-triple-raw','raw',236470,236516,3),('actual-triple-held','held',236471,236522,3)]
arrays=[];inits=[];rows=[]
for i,(name,kind,a,b,expected) in enumerate(cases):
 events=trace(kind,a,b);arrays.append('static const struct event e%d[]={%s};'%(i,','.join('{%d,%d}'%p for p in events)))
 inits.append('{e%d,sizeof(e%d)/sizeof(e%d[0]),%d,"%s"}'%(i,i,i,expected,name));rows.append((name,events))
# User-requested 20 ms gap is also replayed using captured 130 ms tap lengths.
for gap in range(2,7):
 i=len(inits);events=[(100,True),(113,False),(113+gap,True),(126+gap,False),(128+gap,True),(141+gap,False)]
 arrays.append('static const struct event e%d[]={%s};'%(i,','.join('{%d,%d}'%p for p in events)))
 inits.append('{e%d,sizeof(e%d)/sizeof(e%d[0]),3,"user-gap-%dms"}'%(i,i,i,gap*10))
print('Exact traces:',rows)
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#define REMOTE_LOG_DISABLE
#include "firmware/export/remote_log.h"
#define IPOD_ACCESSORY_PROTOCOL
#include "firmware/target/arm/ipod/button-target.h"
#define BUTTON_NONE 0
#define BUTTON_REL 0x02000000
#define BUTTON_REPEAT 0x04000000
#define BUTTON_REDRAW 0x20000000
#define HZ 100
#include "apps-ipod/input/agents_remote.h"
struct event {unsigned long tick;bool down;};
struct scenario {const struct event *events;unsigned int n;int taps;const char *name;};
static struct agents_remote_state s;
static int singles,doubles,triples;
static void output(int b){
 singles+=b==BUTTON_SELECT||b==BUTTON_RC_PLAY;
 doubles+=b==REMOTE_SWITCH_BUTTON;triples+=b==REMOTE_COVERS_BUTTON;
}
static void feed(int context,int button,int held,unsigned long now){
 output(agents_remote_button(&s,context,button,held,now));
 while(s.queued)output(agents_remote_button(&s,context,0,held,now));
}
'''
source=re.sub(r'^#include .*$', '',(R/'apps-ipod/input/agents_remote.c').read_text(),flags=re.M)
post=r'''
int main(void){
 for(int c=REMOTE_OTHER;c<=REMOTE_COVERS;c++)for(unsigned int i=0;i<sizeof(cases)/sizeof(cases[0]);i++){
  struct scenario sc=cases[i];s=(struct agents_remote_state){0};singles=doubles=triples=0;
  unsigned int next=0;bool down=false;
  for(unsigned long tick=sc.events[0].tick;tick<=sc.events[sc.n-1].tick+21;tick++){
   int button=0;
   if(next<sc.n&&sc.events[next].tick==tick){down=sc.events[next++].down;button=BUTTON_RC_PLAY|(down?0:BUTTON_REL);}
   feed(c,button,down?BUTTON_RC_PLAY:0,tick);
  }
  assert(doubles==(sc.taps==2));assert(triples==(sc.taps==3));
  if(sc.taps==1&&c!=REMOTE_AGENTS)assert(singles==1);
  printf("%s context=%d: double=%d triple=%d PASS\n",sc.name,c,doubles,triples);
 }
 puts("FU16 production decoder: captured 130ms taps/20-60ms gaps/third RAW and HELD pulses/tap-double-triple-hold PASS");
}
'''
(O/'config.h').write_text('')
(O/'replay.c').write_text(pre+source+'\n'.join(arrays)+'\nstatic const struct scenario cases[]={'+','.join(inits)+'};\n'+post)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(O),'-I'+str(R),str(O/'replay.c'),'-o',str(O/'replay')],check=True)
subprocess.run([str(O/'replay')],check=True)
