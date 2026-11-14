"""Captured IR -> host Menu down/up -> real EP0/button sampler -> keymaps."""
from pathlib import Path
import ast,re,subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(R/'tools/usb_remote_key/tests'))
import test_gestures as gestures
from test_usb_remote_key import k

def fn(text,name):
 m=re.search(r'(?m)^[\w *]+\b'+name+r'\([^;{}]*\)\s*\{',text);assert m,name
 end=m.end();depth=1
 while depth:depth+=(text[end]=='{')-(text[end]=='}');end+=1
 return text[m.start():end]
a=ast.parse((R/'tools/tests/usb_remote_key_test.py').read_text())
pre=next(ast.literal_eval(n.value) for n in a.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='preamble' for t in n.targets))
parts=[(R/'firmware/drivers/button_usb_remote.c').read_text()]
button=(R/'firmware/drivers/button.c').read_text();core=(R/'firmware/usbstack/usb_core.c').read_text()
parts +=[fn(button,'button_read'),fn(button,'button_tick'),fn(core,'usb_core_control_request_handler')]
source='\n'.join(re.sub(r'^#include .*$', '',p,flags=re.M) for p in parts)
keys=(R/'apps-ipod/input/keymap_ipod.c').read_text();tables=[]
for name in ('standard','wps','agents'):
 m=re.search(r'static const struct button_mapping remote_button_context_'+name+r'\[\].*?\n};',keys,re.S);assert m
 tables.append(m.group())
actions=sorted(set(re.findall(r'\bACTION_\w+', '\n'.join(tables))))
pre+='\n#include <string.h>\nenum {'+','.join(a+'='+str(i+1) for i,a in enumerate(actions))+'};\n'
pre+='''#define LAST_ITEM_IN_LIST {0,0,0}
#define LAST_ITEM_IN_LIST__NEXTLIST(c) {0,0,0}
struct button_mapping {int action_code,button_code,prereq_button_code;};
'''
cases=[];arrays='';count=0
for group in gestures.groups():
 if group[0][2]!=gestures.MENU_FRAME:continue
 events=gestures.replay(group);base=events[0][2]
 arr=[(round((t-base)*100),key,int(down)) for key,down,t in events]
 name='case_'+str(count);count+=1
 arrays+='static const struct event '+name+'[]={'+','.join('{%d,%d,%d}'%e for e in arr)+'};\n'
 cases.append('run_case(%s,sizeof(%s)/sizeof(*%s),false);'%(name,name,name))
 if len(group)>3:
  cases.append('run_case(%s,sizeof(%s)/sizeof(*%s)-1,true);'%(name,name,name))
post=r'''
static const struct button_mapping*maps[]={remote_button_context_standard,remote_button_context_wps,remote_button_context_agents};
static int lookup(const struct button_mapping*m,int b,int prev){for(;m->button_code;m++)if(m->button_code==b&&(!m->prereq_button_code||m->prereq_button_code==prev))return m->action_code;return 0;}
struct event {int tick,key,down;};
static unsigned long next_start=1000;
static int normal,quick,checked;
static void run_case(const struct event*events,unsigned n,bool drop){
 for(int context=0;context<3;context++){
  assert(!lastbtn);used=0;unsigned cursor=0;int prev=0,menu=0,qs=0;unsigned at=0;
  unsigned long base=next_start;next_start+=3000;
  int end=events[n-1].tick+(drop?105:8);
  for(int t=0;t<=end;t++){
   current_tick=base+t;
   while(at<n&&events[at].tick==t){
    struct usb_ctrlrequest req={0x40,0x5b,events[at].key,events[at].down,0};
    response=0;usb_core_control_request_handler(&req,NULL,0);assert(response==USB_CONTROL_ACK);at++;
   }
   button_tick();
   while(cursor<(unsigned)used){
    int b=posted[cursor++];assert((b&~(BUTTON_REPEAT|BUTTON_REL))==BUTTON_RC_MENU);
    int action=lookup(maps[context],b,prev);prev=b;
    menu+=action==ACTION_STD_MENU||action==ACTION_WPS_MENU;
    qs+=action==ACTION_STD_QUICKSCREEN||action==ACTION_WPS_QUICKSCREEN;
   }
  }
  assert(at==n&&!lastbtn&&!button_usb_remote_read());assert(menu+qs==1);
  if(drop)assert(qs==1&&menu==0);
  if(n==2&&!drop)assert(menu==1&&qs==0);
  normal+=menu;quick+=qs;checked++;
 }
}
'''
arrays='\n'+arrays
main='int main(void){'+''.join(cases)+'printf("captured IR -> EP0 -> native sampler -> standard/WPS/Agents keymaps: %d cases, %d MENU taps, %d native QuickScreen holds; drop timeout releases; zero SELECT PASS\\n",checked,normal,quick);}\n'
(O/'pipeline.c').write_text(pre+source+'\n'+'\n'.join(tables)+post+arrays+main)
(O/'config.h').write_text('')
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(O),'-I'+str(R),str(O/'pipeline.c'),'-o',str(O/'pipeline')],check=True)
subprocess.run([str(O/'pipeline')],check=True)
