"""Sanitized serial RX/capture and native chord probes. No hardware I/O."""
from pathlib import Path
import os, re, subprocess, sys
R=Path(__file__).resolve().parents[2]; O=Path(sys.argv[1]).resolve(); O.mkdir(parents=True,exist_ok=True)
def function(text,name):
    m=re.search(r'(?m)^[\w *]+\b'+name+r'\([^;{}]*\)\s*\{',text)
    assert m,name
    p=m.end(); depth=1
    # These production functions have no braces embedded in string literals.
    while depth:
        if text[p]=='{':depth+=1
        if text[p]=='}':depth-=1
        p+=1
    return text[m.start():p]
def body(path):
    return re.sub(r'^#include "[^\n]+"\s*$', '', (R/path).read_text(),flags=re.M)
(O/'config.h').write_text('')
pre='''#include <assert.h>
#include <stdio.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdbool.h>
#define REMOTE_LOG_DISABLE
#include "firmware/export/remote_log.h"
#define IPOD_ACCESSORY_PROTOCOL
#define HAVE_USB_PODLINK
#include "firmware/target/arm/ipod/button-target.h"
#define BUTTON_NONE 0
#define BUTTON_REL 0x02000000
#define BUTTON_REPEAT 0x04000000
#define SYS_EVENT 0x80000000u
#define BUTTON_MULTIMEDIA 0x10000000
#define BUTTON_REDRAW 0x20000000
#define HZ 100
#include "apps-ipod/iap/remote_capture.h"
#include "apps-ipod/input/agents_shortcut.h"
#ifndef REMOTE_TRUNC
#define REMOTE_TRUNC 8
#endif
'''
pre += body('firmware/export/button_chord.h')
core=(R/'apps-ipod/iap/iap-core.c').read_text()
a=core.index('enum fsm_state');b=core.index('enum interface_state interface_state',a)
states=core[a:b]
rxpre='''
#define IF_IAP_MP(...)
#define IAP_PKT_TIMEOUT 3
#define TIME_AFTER(a,b) ((int32_t)((uint32_t)(a)-(uint32_t)(b))>0)
#define IAP_EV_MSG_RCVD 1
#define panicf(...) assert(0)
static long current_tick;
static bool iap_setupflag=true, iap_enabled=true, iap_started=false, iap_running=true;
static long last_frame_tick;
static unsigned char rx[200000], *iap_rxpayload=rx, *iap_rxnext=rx;
static unsigned int iap_rxlen=sizeof(rx);
static int iap_queue, queued;
static void iap_tick_start(void) {}
static void iap_start(void) {iap_running=true;}
static void queue_post(int *q,int event,int data) {(void)q;(void)event;(void)data;queued++;}
static void put_u16(unsigned char *b,unsigned int n) {b[0]=n>>8;b[1]=n;}
'''
test='''
static struct remote_frame f;
static void send_frame(unsigned int len, const unsigned char *b, bool bad) {
    iap_getc(0xff); iap_getc(0x55);
    unsigned int sum=0;
    if (len && len<=252) {iap_getc(len);sum=len;}
    else {iap_getc(0);iap_getc(len>>8);iap_getc(len);sum=(len>>8)+(len&255);}
    for(unsigned int i=0;i<len;i++) {iap_getc(b[i]);sum+=b[i];}
    iap_getc((unsigned char)(-sum+(bad?1:0)));
    current_tick+=12;
}
int main(void) {
    assert(REMOTE_FRAME_MAX == 128);
    assert(sizeof(struct remote_capture) + 2 * sizeof(struct remote_frame) < 16384);
    unsigned char menu[]={2,0,0,0,0x40,0};
    unsigned char release[]={2,0,0,0,0,0};
    char names[180];
    send_frame(sizeof(menu),menu,false);
    assert(queued==1 && last_frame_tick==1);
    assert(remote_capture_get(&iap_remote_capture,1,&f));
    assert(!f.flags && f.length==6 && f.received==6);
    assert(remote_context_buttons(6,f.data)==BUTTON_RC_MENU);
    remote_frame_names(&f,names,sizeof(names)); assert(!strcmp(names,"MENU"));
    send_frame(sizeof(release),release,false);
    assert(remote_capture_get(&iap_remote_capture,2,&f));
    assert(f.delta==12); remote_frame_names(&f,names,sizeof(names));
    assert(!strcmp(names,"(release)"));
    send_frame(sizeof(menu),menu,true);
    assert(queued==2 && iap_remote_capture.bad==1);
    assert(remote_capture_get(&iap_remote_capture,3,&f) && f.flags==REMOTE_BAD_CHECK);
    unsigned char unknown[]={0x77,0x91,0xaa};
    send_frame(3,unknown,false);
    assert(remote_capture_get(&iap_remote_capture,4,&f));
    remote_frame_names(&f,names,sizeof(names)); assert(!strcmp(names,"?"));
    send_frame(1,menu,false);
    assert(remote_capture_get(&iap_remote_capture,5,&f));
    assert(f.flags==REMOTE_BAD_LENGTH && f.received==1);
    send_frame(0,menu,false);
    assert(remote_capture_get(&iap_remote_capture,6,&f));
    assert(f.flags==REMOTE_BAD_LENGTH && f.received==0);
    iap_getc(0xff);iap_getc(0x55);iap_getc(6);iap_getc(2);
    current_tick+=4;iap_getc(0xff);iap_getc(0x55);iap_getc(2);iap_getc(0x77);iap_getc(0);
    iap_getc((unsigned char)-(2+0x77));
    assert(remote_capture_get(&iap_remote_capture,7,&f));
    assert(f.flags==REMOTE_BAD_TIMEOUT && f.received==1);
    static unsigned char large[65535];memset(large,0xa5,sizeof(large));
    send_frame(sizeof(large),large,true);
    assert(remote_capture_get(&iap_remote_capture,9,&f));
    assert(f.flags==(REMOTE_BAD_LENGTH|REMOTE_BAD_CHECK|REMOTE_TRUNC));
    assert(f.length==65535 && f.received==65535);
    assert(sizeof(f.data)==128 && !memcmp(f.data,large,128));
    unsigned long bad_before=iap_remote_capture.bad;
    send_frame(4096,large,false);
    assert(remote_capture_get(&iap_remote_capture,10,&f));
    assert(f.flags==REMOTE_TRUNC && f.length==4096 && f.received==4096);
    assert(iap_remote_capture.bad==bad_before);
    for (int i=0;i<40;i++) send_frame(6,menu,false);
    assert(!remote_capture_get(&iap_remote_capture,1,&f));
    unsigned long n=iap_remote_capture.frames;
    for(unsigned int i=0;i<REMOTE_HISTORY;i++) {
        assert(remote_capture_get(&iap_remote_capture,n-i,&f));
        assert(f.sequence==n-i && f.received==6);
    }
    /* Pending frames do not replace a completed slot before publication. */
    remote_capture_begin(&iap_remote_capture,6);
    remote_capture_byte(&iap_remote_capture,2);
    assert(remote_capture_get(&iap_remote_capture,n,&f) && f.received==6);
    menu[1]=2;send_frame(6,menu,false);
    assert(remote_capture_get(&iap_remote_capture,++n,&f));
    remote_frame_names(&f,names,sizeof(names)); assert(!strcmp(names,"?"));
    menu[1]=0;menu[3]=4;
    send_frame(6,menu,false);assert(remote_capture_get(&iap_remote_capture,++n,&f));
    remote_frame_names(&f,names,sizeof(names));assert(strstr(names,"?"));
    /* A valid frame rejected for RX buffer pressure is still captured. */
    iap_rxlen=0;send_frame(6,release,false);
    assert(remote_capture_get(&iap_remote_capture,++n,&f));
    assert(f.flags==REMOTE_BAD_LENGTH && f.received==6);
    iap_running=false;iap_rxlen=sizeof(rx);int before=queued;
    send_frame(6,release,false);
    assert(remote_capture_get(&iap_remote_capture,++n,&f));
    assert(!f.flags&&f.received==6&&queued==before);
    puts("serial framer + capture: checksum/length/timeout/unknown/release/128-byte TRUNC/declared length/ring wrap PASS");
}
'''
source=pre+body('apps-ipod/iap/remote_capture.c')+states+rxpre+function(core,'iap_getc')+test
(O/'capture.c').write_text(source)
chordtest='''
static int feed(struct agents_shortcut_state *s,int b,int held,bool talking,bool expected) {
    bool fire=false;int out=agents_shortcut_button(s,b,held,talking,&fire);
    assert(fire==expected);return out;
}
int main(void) {
    const int pair=BUTTON_SELECT|BUTTON_PLAY;
    struct agents_shortcut_state s={0};
    (void)feed(&s,BUTTON_SELECT,BUTTON_SELECT,false,false);
    assert(feed(&s,BUTTON_SELECT|BUTTON_REPEAT,BUTTON_SELECT,false,false)==
           (BUTTON_SELECT|BUTTON_REPEAT));
    s=(struct agents_shortcut_state){0};
    /* SELECT's press arms the unchanged keymap prerequisite, not an activation. */
    assert(feed(&s,BUTTON_SELECT,BUTTON_SELECT,false,false)==BUTTON_SELECT);
    assert(!feed(&s,pair,pair,false,true));
    assert(!feed(&s,pair|BUTTON_REPEAT,pair,false,false));
    assert(!feed(&s,pair|BUTTON_REL,0,false,false));
    assert(!s.latched);
    assert(feed(&s,BUTTON_PLAY,BUTTON_PLAY,false,false)==BUTTON_PLAY);
    assert(!feed(&s,pair,pair,false,true));
    assert(!feed(&s,BUTTON_SELECT|BUTTON_REL,BUTTON_PLAY,false,false));
    assert(!feed(&s,BUTTON_PLAY|BUTTON_REL,0,false,false));
    /* The very first SELECT repeat reaches the keymap immediately. */
    assert(feed(&s,BUTTON_SELECT,BUTTON_SELECT,false,false)==BUTTON_SELECT);
    assert(feed(&s,BUTTON_SELECT|BUTTON_REPEAT,BUTTON_SELECT,false,false)==
           (BUTTON_SELECT|BUTTON_REPEAT));
    assert(feed(&s,pair,pair,false,false)==pair);
    assert(feed(&s,pair|BUTTON_REPEAT,pair,false,false)==(pair|BUTTON_REPEAT));
    assert(feed(&s,pair|BUTTON_REL,0,false,false)==(pair|BUTTON_REL));
    assert(!s.latched);
    /* Active recording still consumes SELECT and preserves PLAY release. */
    assert(feed(&s,BUTTON_PLAY,BUTTON_PLAY,false,false)==BUTTON_PLAY);
    assert(!feed(&s,pair,pair,true,false));
    assert(feed(&s,pair|BUTTON_REPEAT,pair,true,false)==(BUTTON_PLAY|BUTTON_REPEAT));
    assert(!feed(&s,BUTTON_SELECT|BUTTON_REL,BUTTON_PLAY,true,false));
    assert(feed(&s,BUTTON_PLAY|BUTTON_REL,0,true,false)==(BUTTON_PLAY|BUTTON_REL));
    for(int f=0;f<3;f++) {
        int b=BUTTON_PLAY|(f==1?BUTTON_REPEAT:f==2?BUTTON_REL:0);
        assert(feed(&s,b,f==2?0:BUTTON_PLAY,false,false)==b);
    }
    /* A tap remains the native press followed by native release. */
    assert(feed(&s,BUTTON_SELECT,BUTTON_SELECT,false,false)==BUTTON_SELECT);
    assert(feed(&s,BUTTON_SELECT|BUTTON_REL,0,false,false)==(BUTTON_SELECT|BUTTON_REL));
    assert(feed(&s,(int)SYS_EVENT,0,false,false)==(int)SYS_EVENT);
    assert(feed(&s,BUTTON_MULTIMEDIA|1,0,false,false)==(BUTTON_MULTIMEDIA|1));
    assert(!feed(&s,pair,0,false,true));
    assert(!feed(&s,pair|BUTTON_REL,0,false,false));
    puts("native chord: SELECT first-repeat timing/tap/before-hold combo/after-hold passthrough/talk/PLAY alone PASS");
}
'''
(O/'chord.c').write_text(pre+body('apps-ipod/input/agents_shortcut.c')+chordtest)
for name in ('capture','chord'):
    if os.environ.get('REMOTE_TEST_ONLY') not in (None, name): continue
    cmd=['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie','-I'+str(O),'-I'+str(R),'-I'+str(R/'apps-ipod'),str(O/(name+'.c')),'-o',str(O/name)]
    subprocess.run(cmd,check=True);subprocess.run([str(O/name)],check=True)
if os.environ.get('REMOTE_TEST_ONLY'): raise SystemExit(0)
lingo=(R/'apps-ipod/iap/iap-lingo2.c').read_text()
handlerpre='''
#define CONFIG_TUNER 0
#define IAP_ACK_OK 0
#define IAP_ACK_BAD_PARAM 1
#define IAP_ACK_NO_AUTHEN 2
#define IAP_ACK_CMD_FAILED 3
#define AUDIO_STATUS_PLAY 1
#define TIME_AFTER(a,b) ((int32_t)((uint32_t)(a)-(uint32_t)(b))>0)
static bool authenticated=true,supported=true;
#define DEVICE_AUTHENTICATED authenticated
#define DEVICE_LINGO_SUPPORTED(x) supported
static unsigned long iap_remotebtn;
static int iap_repeatbtn, iap_timeoutbtn;
static bool iap_btnstop, iap_btnchapter, iap_btnshuffle, iap_btnrepeat;
static long current_tick=1000, iap_connected_tick;
static int playing, stopped, shuffled, repeated, chapter, ack;
static struct {bool playlist_shuffle;} global_settings;
bool remote_input_active;
static int audio_status(void) {return playing;}
static void audio_stop(void) {stopped++;}
static void iap_skip_chapter(int n) {chapter+=n;}
static void iap_shuffle_state(bool on) {(void)on;shuffled++;}
static void iap_repeat_next(void) {repeated++;}
static void cmd_ack(unsigned int cmd,int status) {(void)cmd;ack=status;}
#define cmd_ok(cmd) cmd_ack(cmd,IAP_ACK_OK)
#define CHECKLEN(n) do {if(len<(n)){cmd_ack(cmd,IAP_ACK_BAD_PARAM);return;}}while(0)
'''
handlertest='''
int main(void) {
    unsigned char b[]={2,0,0,0,0,0};
    const int bit[]={1,2,4,8,16,32,64};
    const unsigned long keys[]={BUTTON_RC_PLAY,BUTTON_RC_VOL_UP,BUTTON_RC_VOL_DOWN,
        BUTTON_RC_RIGHT,BUTTON_RC_LEFT,BUTTON_RC_NEXT_ALBUM,BUTTON_RC_PREV_ALBUM};
    for(int i=0;i<7;i++) {
        b[2]=bit[i];iap_handlepkt_mode2(6,b);assert(iap_remotebtn==keys[i]);
    }
    b[2]=2;b[4]=0x40;iap_handlepkt_mode2(5,b);
    assert(iap_remotebtn==(BUTTON_RC_MENU|BUTTON_RC_VOL_UP));
    iap_repeatbtn=0;iap_handlepkt_mode2(5,b);assert(!iap_repeatbtn);
    b[2]=0;iap_handlepkt_mode2(5,b);assert(iap_remotebtn==BUTTON_RC_MENU);
    b[4]=0;iap_handlepkt_mode2(6,b);assert(!iap_remotebtn&&!iap_timeoutbtn);
    b[4]=0xc0;iap_handlepkt_mode2(4,b);assert(!iap_remotebtn);
    iap_handlepkt_mode2(5,b);assert(iap_remotebtn==(BUTTON_RC_MENU|BUTTON_RC_SELECT));
    b[4]=0;b[3]=1;playing=0;iap_handlepkt_mode2(4,b);assert(iap_remotebtn==BUTTON_RC_PLAY);
    playing=1;iap_handlepkt_mode2(4,b);assert(!iap_remotebtn);
    b[3]=2;iap_handlepkt_mode2(4,b);assert(iap_remotebtn==BUTTON_RC_PLAY);
    current_tick=100;iap_handlepkt_mode2(4,b);assert(!iap_remotebtn);
    current_tick=1000;b[3]=0;b[4]=0x40;b[1]=2;
    iap_handlepkt_mode2(6,b);assert(!iap_remotebtn && ack==IAP_ACK_CMD_FAILED);
    authenticated=false;iap_handlepkt_mode2(6,b);assert(ack==IAP_ACK_NO_AUTHEN);
    b[1]=0;supported=false;iap_handlepkt_mode2(6,b);assert(iap_remotebtn==BUTTON_RC_MENU);
    /* Capture preserves input while suppressing playback and preference writes. */
    remote_input_active=true;b[2]=0x80;b[3]=0x88;b[4]=1;
    iap_handlepkt_mode2(6,b);assert(!stopped&&!shuffled&&!repeated&&!chapter);
    remote_input_active=false;b[3]=0;b[4]=0;
    iap_handlepkt_mode2(6,b);iap_handlepkt_mode2(6,b);assert(stopped==1);
    b[2]=0;iap_handlepkt_mode2(6,b);b[2]=0x80;iap_handlepkt_mode2(6,b);assert(stopped==2);
    /* Short malformed callers never read beyond their declared length. */
    iap_handlepkt_mode2(0,NULL);assert(ack==IAP_ACK_BAD_PARAM);
    unsigned char one=2;iap_handlepkt_mode2(1,&one);assert(ack==IAP_ACK_BAD_PARAM);
    puts("lingo2 production handler: Menu/all keys/optional bytes/combined/release/auth/latches/capture/short buffers PASS");
}
'''
(O/'lingo.c').write_text(pre+body('apps-ipod/iap/remote_capture.c')+handlerpre+function(lingo,'iap_handlepkt_mode2')+handlertest)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie','-I'+str(O),'-I'+str(R),'-I'+str(R/'apps-ipod'),str(O/'lingo.c'),'-o',str(O/'lingo')],check=True)
subprocess.run([str(O/'lingo')],check=True)
# Execute the real capture screen against deterministic LCD/action ports and
# ordinary temporary files. The serial producer is the same ring as above.
ui=(R/'apps-ipod/screens/system/remote_input.c').read_text()
actions=sorted(set(re.findall(r'\bACTION_[A-Z_]+\b',ui)))
uipre='\n'.join('#define %s %d'%(x,i+1) for i,x in enumerate(actions) if x!='ACTION_NONE')
uipre+='\n#define ACTION_NONE 0\n#define CONTEXT_STD 0\n'
uipre+='''
#include <unistd.h>
#include <fcntl.h>
#define MAX(a,b) ((a)>(b)?(a):(b))
#define FONT_SYSFIXED 0
#define FONT_UI 1
#define SYS_USB_CONNECTED ((int)SYS_EVENT|1)
static unsigned long iap_remotebtn;
static int calls, raw_button, highest, frames_drawn;
static bool inhibited;
static int disable_irq_save(void) {return 0;}
static void restore_irq(int n) {(void)n;}
static bool skin_flush_inhibited(void) {return inhibited;}
static void skin_inhibit_flush(bool n) {inhibited=n;}
static void lcd_set_viewport(void *p) {(void)p;}
static void lcd_setfont(int n) {(void)n;}
static void lcd_clear_display(void) {}
static void lcd_update(void) {frames_drawn++;}
static void lcd_puts(int x,int y,const char *s) {
    (void)x;if(y>highest)highest=y;
    if((y==2 || y==5) && (iap_remote_capture.pending.flags & REMOTE_TRUNC))
        assert(strstr(s,"TRUNC"));
    if(y==3 && (iap_remote_capture.pending.flags & REMOTE_BAD_MASK))
        assert(strstr(s,"RC_?")&&strstr(s,"ACTION_NONE"));
}
static void button_clear_queue(void) {}
static long default_event_handler(long n) {return n;}
static int get_action_statuscode(int *b) {*b=raw_button;return 0;}
static long get_action_tick(void) {return calls;}
static void inject(bool bad) {
    const unsigned char b[]={2,0,0,0,0x40,0};
    remote_capture_begin(&iap_remote_capture,6);
    for(int i=0;i<6;i++) remote_capture_byte(&iap_remote_capture,b[i]);
    remote_capture_end(&iap_remote_capture,bad?REMOTE_BAD_CHECK:0,calls*10);
}
static void inject_large(void) {
    remote_capture_begin(&iap_remote_capture,4096);
    for(int i=0;i<4096;i++) remote_capture_byte(&iap_remote_capture,i<2?2:0x55);
    remote_capture_end(&iap_remote_capture,0,calls*10);
}
static int get_action(int context,int timeout) {
    assert(context==CONTEXT_STD && timeout<=HZ/10 && timeout>0);
    assert(remote_input_active);calls++;
    switch(calls) {
    case 1:raw_button=BUTTON_SELECT|BUTTON_REL;return ACTION_STD_OK;
    case 2:inject(false);remote_volume_log(calls*10,0x27,0x2a,1,'P');raw_button=BUTTON_RC_MENU|BUTTON_REL;return ACTION_STD_MENU;
    case 3:inject(true);raw_button=0;return ACTION_NONE;
    case 4:inject_large();raw_button=0;return ACTION_NONE;
    case 5:raw_button=BUTTON_SELECT|BUTTON_REL;return ACTION_STD_OK;
    default:raw_button=BUTTON_MENU|BUTTON_REL;return ACTION_STD_MENU;
    }
}
'''
uipre+='\n#define ROCKBOX_DIR "'+str(O)+'"\n'
# remote_input_active is defined by the production screen below.
uipre=uipre.replace('static int get_action(int context,int timeout)', 'static int get_action(int context,int timeout)')
uitest='''
int main(void) {
    unlink(ROCKBOX_DIR "/remote-log.txt");
    for(int i=0;i<20;i++) inject(false);
    assert(!dbg_remote_input());
    assert(calls==6 && highest>=16 && frames_drawn==6);
    assert(!remote_input_active&&!inhibited);
    calls=0;assert(!dbg_remote_input());
    FILE *f=fopen(ROCKBOX_DIR "/remote-log.txt","r");assert(f);
    char line[1024];int n=0,bad=0,trunc=0,volume=0;
    while(fgets(line,sizeof(line),f)) {
        if(strstr(line,"VOL ")) {
            assert(strstr(line,"tick=20")&&strstr(line,"previous=27 target=2A dir=1 decision=P"));volume++;continue;
        }
        if(strstr(line,"TRUNC")) {
            assert(strstr(line,"len=4096 rx=4096 stored=128"));trunc++;
        } else {
            assert(strstr(line,"L2/00")&&strstr(line,"00 00 40 00")&&strstr(line,"MENU"));
        }
        if(strstr(line,"BAD:"))bad++;
        n++;
    }
    fclose(f);assert(n==6&&bad==2&&trunc==2&&volume==2);
    puts("capture screen: 16 history rows/10 Hz poll/remote isolation/native logging/append/BAD/TRUNC/exit PASS");
}
'''
uipre+='\nstatic void iap_agents_volume_enable(bool b){(void)b;}\n'
(O/'screen.c').write_text(pre+body('apps-ipod/iap/remote_capture.c')+uipre+body('apps-ipod/screens/system/remote_input.c')+uitest)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie','-I'+str(O),'-I'+str(R),'-I'+str(R/'apps-ipod'),str(O/'screen.c'),'-o',str(O/'screen')],check=True)
subprocess.run([str(O/'screen')],check=True)
agents=(R/'apps-ipod/screens/system/agents_screen.c').read_text()
a=agents.index('            if (action == ACTION_LISTTREE_PGUP ||')
b=agents.index('            const char *key = dir ?',a)
page=agents[a:b]
pagepre='''
#define MAX(a,b) ((a)>(b)?(a):(b))
#define MIN(a,b) ((a)<(b)?(a):(b))
#define ACTION_LISTTREE_PGUP 1
#define ACTION_LISTTREE_PGDOWN 2
#define ACTION_NONE 0
#define VIEW_USAGE 1
#define VIEW_DETAIL 2
#define VIEW_CONFIRM 3
#define SCREEN_MAIN 0
#define HR_USAGE_SVCS 4
#define USE_BLOCK 50
#define USE_LINE 21
struct vp {int height;};struct gl {struct vp *parent[1];};
static int view,usage_scroll,detail_scroll,detail_scroll_max,detail_page_lines,draws;
static int raw_page,volume_changes;
#define IPOD_ACCESSORY_PROTOCOL
#define BUTTON_MULTIMEDIA_VOLUME_UP 101
#define BUTTON_MULTIMEDIA_VOLUME_DOWN 102
static void get_action_statuscode(int *b){*b=raw_page;}
static void default_event_handler(int b){assert(b==101||b==102);volume_changes++;}
static void draw_usage(struct gl*l){(void)l;draws++;}
static void draw_detail(struct gl*l){(void)l;draws++;}
static void page_move(int action){struct vp vp={.height=100};struct gl list={.parent={&vp}};
'''
pagetest='''
}
int main(void){
 view=VIEW_USAGE;page_move(ACTION_LISTTREE_PGDOWN);assert(usage_scroll==100&&draws==1);
 page_move(ACTION_LISTTREE_PGDOWN);assert(usage_scroll==124);
 page_move(ACTION_LISTTREE_PGUP);assert(usage_scroll==24);
 page_move(ACTION_LISTTREE_PGUP);assert(usage_scroll==0);
 raw_page=BUTTON_RC_VOL_UP;int scroll=usage_scroll;page_move(ACTION_LISTTREE_PGUP);assert(volume_changes==1&&usage_scroll==scroll);raw_page=0;
 view=VIEW_DETAIL;detail_scroll_max=23;detail_page_lines=7;
 page_move(ACTION_LISTTREE_PGDOWN);assert(detail_scroll==7);
 page_move(ACTION_LISTTREE_PGDOWN);assert(detail_scroll==14);
 page_move(ACTION_LISTTREE_PGUP);assert(detail_scroll==7);
 view=VIEW_CONFIRM;int n=draws;page_move(ACTION_LISTTREE_PGDOWN);assert(draws==n);
 puts("Agents production page blocks: Usage/detail/confirm/page bounds PASS");
}
'''
(O/'timing.c').write_text(pre+pagepre+page+pagetest)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie','-I'+str(O),'-I'+str(R),'-I'+str(R/'apps-ipod'),str(O/'timing.c'),'-o',str(O/'timing')],check=True)
subprocess.run([str(O/'timing')],check=True)
guard=body('firmware/export/button_chord.h')
(O/'poweroff.c').write_text(pre+guard+'''
volatile bool button_agents_chord_latched;

int main(void) {
    assert(!button_chord_blocks_poweroff(0));
    assert(!button_chord_blocks_poweroff(BUTTON_PLAY));
    /* A physical chord alone is not enough: the UI must accept it. */
    assert(!button_chord_blocks_poweroff(BUTTON_PLAY|BUTTON_SELECT));
    button_chord_begin();
    assert(button_chord_blocks_poweroff(BUTTON_PLAY|BUTTON_SELECT));
    for(int i=0;i<100;i++) assert(button_chord_blocks_poweroff(BUTTON_PLAY|BUTTON_SELECT));
    assert(button_chord_blocks_poweroff(BUTTON_PLAY));
    assert(button_chord_blocks_poweroff(BUTTON_SELECT));
    assert(!button_chord_blocks_poweroff(0));
    assert(!button_chord_blocks_poweroff(BUTTON_PLAY));
    puts("native poweroff guard: chord and partial release suppressed/PLAY alone unchanged PASS");
}
''')
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(O),'-I'+str(R),'-I'+str(R/'apps-ipod'),str(O/'poweroff.c'),'-o',str(O/'poweroff')],check=True)
subprocess.run([str(O/'poweroff')],check=True)
# All existing remote maps are byte-for-byte unchanged; only the new Agents
# map and its dispatch case differ. The adapter is a no-op outside Agents.
baseline=subprocess.check_output(['git','show','db11c0fb1e:apps-ipod/input/keymap_ipod.c'],cwd=R,text=True)
candidate=(R/'apps-ipod/input/keymap_ipod.c').read_text()
for name in re.findall(r'static const struct button_mapping (remote_button_context_\w+)\[\]',baseline):
    if name=='remote_button_context_agents': continue
    pattern=r'static const struct button_mapping '+name+r'\[\].*?\n};'
    assert re.search(pattern,baseline,re.S).group()==re.search(pattern,candidate,re.S).group(),name
print('outside Agents: every pre-existing remote keymap array identical to db11c0fb1e PASS')
root=(R/'apps-ipod/root_menu.c').read_text()
navpre='''
#define GO_TO_PLUGIN 1
#define GO_TO_PLAYLIST_VIEWER 2
#define GO_TO_WPS 3
#define GO_TO_PREVIOUS_MUSIC 4
#define GO_TO_AGENTS 5
#define GO_TO_ROOT -1
#define GO_TO_PREVIOUS -2
#define AGENTS_SWITCH_WPS 2
static int agents_return, a_calls, w_calls;
static bool wps_return_agents,was_agents;
static bool poweroff=true;
static bool button_get_sw_poweroff_state(void) {return poweroff;}
static void button_set_sw_poweroff_state(bool on) {poweroff=on;}
static int agents_screen(void) {a_calls++;return a_calls==1?agents_return:0;}
static int wpsscrn(void *p) {(void)p;w_calls++;was_agents=wps_return_agents;wps_return_agents=false;return GO_TO_AGENTS;}
'''
navtest='''
int main(void) {
    assert(menu_result(GO_TO_AGENTS)==GO_TO_AGENTS);
    assert(menu_result(GO_TO_WPS)==GO_TO_WPS);
    agents_return=AGENTS_SWITCH_WPS;assert(agents_scrn(NULL)==GO_TO_WPS&&wps_return_agents);
    agents_return=0;a_calls=0;assert(agents_scrn(NULL)==GO_TO_PREVIOUS&&!wps_return_agents);
    a_calls=w_calls=0;agents_return=AGENTS_SWITCH_WPS;
    root_menu_agents_shortcut();assert(a_calls==2&&w_calls==1&&poweroff&&was_agents&&!wps_return_agents);
    a_calls=w_calls=0;agents_return=0;
    root_menu_wps_shortcut();assert(a_calls==1&&w_calls==1&&poweroff&&!was_agents&&!wps_return_agents);
    puts("navigation: System entry/Agents-to-WPS/modal alternation/remote WPS jump/normal cleanup PASS");
}
'''
(O/'navigation.c').write_text(pre+navpre+function(root,'menu_result')+function(root,'agents_scrn')+function(root,'root_menu_agents_shortcut')+function(root,'root_menu_wps_shortcut')+navtest)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(O),'-I'+str(R),'-I'+str(R/'apps-ipod'),str(O/'navigation.c'),'-o',str(O/'navigation')],check=True)
subprocess.run([str(O/'navigation')],check=True)
# Compile the actual polling hook too, to check adapter/chord ordering and
# raw-screen replay without replacing the production input decision.
action_source=(R/'apps-ipod/input/action.c').read_text()
hookpre='''
#define ACTION_NONE 0
#define ACTION_REDRAW 1
#define ACTION_AGENTS_SWITCH 400
#define CONTEXT_AGENTS 1
#define CONTEXT_WPS 2
#define ACTION_REMOTE_WPS 401
#define ACTIVITY_AGENTS 1
#define ACTIVITY_WPS 2
#define TIMEOUT_NOBLOCK 0
struct agents_shortcut_state shortcut;
static bool shortcut_talking;
static unsigned long shortcut_talking_at;
bool remote_input_active;
static struct agents_remote_state remote;
static int get_current_activity(void) {return 0;}
static int fake_page;
static int iap_agents_volume_take(void) {int b=fake_page;fake_page=0;return b;}
static int iap_agents_volume_timeout(int timeout) {return timeout;}
static long current_tick;
static int queued_button, held_button, polls;
static int button_get_w_tmo(int t) {(void)t;polls++;return queued_button;}
static int button_status(void) {return held_button;}
static bool has_flag(int a,int b) {return (a&b)==b;}
typedef struct {int button,action,context;bool wait_for_release;} action_last_t;
typedef struct {int button,timeout,context;} action_cur_t;
'''
hooktest='''
int main(void) {
    action_last_t last={.button=BUTTON_REL,.context=CONTEXT_AGENTS};
    action_cur_t cur={.context=CONTEXT_AGENTS,.timeout=5};
    queued_button=held_button=BUTTON_RC_LEFT;
    assert(!action_poll_button(&last,&cur));assert(cur.button==BUTTON_LEFT);
    queued_button=BUTTON_RC_LEFT|BUTTON_REL;held_button=0;current_tick=10;
    assert(!action_poll_button(&last,&cur));assert(cur.button==(BUTTON_LEFT|BUTTON_REL));
    queued_button=BUTTON_RC_PLAY;held_button=BUTTON_SELECT|BUTTON_RC_PLAY;
    assert(!action_poll_button(&last,&cur));assert(cur.button==BUTTON_RC_PLAY);
    queued_button=BUTTON_RC_PLAY|BUTTON_SELECT;
    assert(!action_poll_button(&last,&cur));
    assert(cur.button==BUTTON_SELECT);
    queued_button=held_button=BUTTON_SELECT|BUTTON_PLAY;
    assert(action_poll_button(&last,&cur));assert(cur.button==ACTION_AGENTS_SWITCH);
    queued_button=BUTTON_PLAY|BUTTON_SELECT|BUTTON_REL;held_button=0;
    assert(!action_poll_button(&last,&cur));
    assert(cur.button==(BUTTON_RC_PLAY|BUTTON_REL|BUTTON_REPEAT));
    queued_button=held_button=0;cur.context=CONTEXT_AGENTS;
    fake_page=BUTTON_RC_VOL_UP|BUTTON_REL|BUTTON_REPEAT;
    assert(!action_poll_button(&last,&cur));assert(cur.button& BUTTON_REL);
    assert(!fake_page);
    queued_button=held_button=BUTTON_SELECT;
    assert(agents_shortcut_get_button(5)==BUTTON_SELECT);
    queued_button=BUTTON_SELECT|BUTTON_REPEAT;
    assert(agents_shortcut_get_button(5)==(BUTTON_SELECT|BUTTON_REPEAT));
    queued_button=BUTTON_SELECT|BUTTON_PLAY;held_button=queued_button;
    assert(agents_shortcut_get_button(5)==queued_button);
    queued_button=BUTTON_SELECT|BUTTON_REL;held_button=0;
    assert(agents_shortcut_get_button(5)==(BUTTON_SELECT|BUTTON_REL));
    remote=(struct agents_remote_state){0};shortcut=(struct agents_shortcut_state){0};
    cur.context=CONTEXT_WPS;current_tick=100;queued_button=held_button=BUTTON_RC_PLAY;
    assert(action_poll_button(&last,&cur)&&!cur.button);
    current_tick=113;queued_button=BUTTON_RC_PLAY|BUTTON_REL;held_button=0;
    assert(action_poll_button(&last,&cur)&&!cur.button);
    current_tick=118;queued_button=held_button=BUTTON_RC_PLAY;
    assert(action_poll_button(&last,&cur)&&!cur.button);
    current_tick=131;queued_button=BUTTON_RC_PLAY|BUTTON_REL;held_button=0;
    assert(action_poll_button(&last,&cur)&&cur.button==ACTION_AGENTS_SWITCH);
    assert(last.button==BUTTON_REL&&last.action==ACTION_NONE&&!last.wait_for_release);
    puts("input polling hook: native-only chord/remote PLAY isolation/native SELECT timing PASS");
}
'''
(O/'hook.c').write_text(pre+'\n#include "apps-ipod/input/agents_remote.h"\n'+hookpre+body('apps-ipod/input/remote_menu.h')+body('apps-ipod/input/agents_shortcut.c')+body('apps-ipod/input/agents_remote.c')+function(action_source,'remote_poll')+function(action_source,'agents_shortcut_get_button')+function(action_source,'action_poll_button')+hooktest)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(O),'-I'+str(R),'-I'+str(R/'apps-ipod'),str(O/'hook.c'),'-o',str(O/'hook')],check=True)
subprocess.run([str(O/'hook')],check=True)
