/* FU15: PC FIFO while connected; FU13 Dock behavior when disconnected. */
static void request_at(unsigned long now,int id,int state)
{
    current_tick=now;
    struct usb_ctrlrequest req={0x40,0x5b,id,state,0};
    assert(button_usb_remote_request(&req));
}
static void heartbeat(unsigned long now) {request_at(now,5,1);}
static void pc(unsigned long now,int direction,bool frame)
{request_at(now,direction>0?3:4,frame?1:2);}
static bool dock(unsigned long now,int volume)
{
    current_tick=now;
    bool handled=iap_agents_volume(volume,false);
    if(!handled)global_status.volume=volume;
    return handled;
}
static int take_at(unsigned long now)
{current_tick=now;return iap_agents_volume_take();}
static void reset_sources(bool paging)
{
    current_tick=0;wakeups=0;activity=0;
    bridge_seen=false;test_link=false;
    global_status.volume=12;iap_agents_volume_enable(paging);iap_agents_volume_host_keys(false);activity=ACTIVITY_AGENTS;
    remote_log_enable(true,remote_log_format);
}
#include "apps-ipod/input/agents_remote.h"
#define TIMEOUT_NOBLOCK 0
static struct agents_remote_state remote;
bool agents_remote_pending(const struct agents_remote_state *s)
{(void)s;return false;}
static bool inject_pc;
static int native_key;
static int button_get_w_tmo(int timeout)
{
    if(native_key){int b=native_key;native_key=0;return b;}
    if(wakeups){wakeups--;return BUTTON_NONE;}
    if(inject_pc&&timeout){inject_pc=false;pc(current_tick+1,1,true);return BUTTON_NONE;}
    if(timeout>0)current_tick+=timeout;
    return BUTTON_NONE;
}
int main(void)
{
    /* A resident session alone is enough: no heartbeat, no PC report. */
    reset_sources(true);test_link=true;
    for(int i=0;i<100;i++)
    {
        assert(dock(100+i*10,15+(i%30)*3));
        assert(take_at(100+i*10)==BUTTON_NONE);
        assert(global_status.volume==12);
    }
    /* PC reports do not depend on heartbeat while resident host is up. */
    pc(1100,1,true);pc(1101,1,false);pc(1102,-1,true);
    assert(take_at(1102)==UP);assert(take_at(1102)==UP);
    assert(take_at(1102)==DOWN);assert(!take_at(1102));
    /* A fresh bridge also selects PC without a resident PodLink session. */
    reset_sources(true);heartbeat(100);assert(dock(101,15));
    assert(!take_at(101));pc(102,1,true);assert(take_at(102)==UP);
    /* Heartbeat lapses between presses, but PodLink stays up. */
    test_link=true;assert(dock(500,18));assert(!take_at(500));
    pc(501,-1,true);assert(take_at(501)==DOWN);
    /* Both signals absent: first Dock step and legacy repeats are immediate. */
    reset_sources(true);assert(dock(100,15));assert(take_at(100)==UP);
    for(int t=110;t<=150;t+=10){dock(t,18+(t-110)*3/10);assert(!take_at(t));}
    assert(dock(160,33));assert(take_at(160)==UP);
    assert(dock(170,36));assert(!take_at(170));
    assert(dock(190,39));assert(take_at(190)==UP);
    /* Unplugging a session without fresh heartbeat restores FU13 immediately. */
    reset_sources(true);test_link=true;dock(100,15);assert(!take_at(100));
    test_link=false;assert(dock(110,18));assert(take_at(110)==UP);
    assert(!take_at(110));
    /* A fresh heartbeat counts as connected until its defined expiry. */
    reset_sources(true);heartbeat(100);dock(399,15);assert(!take_at(399));
    assert(dock(400,18));assert(take_at(400)==UP);
    /* A pending old Dock event cannot leak across connection establishment. */
    reset_sources(true);dock(100,15);test_link=true;
    assert(!take_at(100));test_link=false;assert(!take_at(100));
    reset_sources(true);dock(100,15);test_link=true;pc(101,1,true);
    assert(take_at(101)==UP);test_link=false;assert(!take_at(102));
    /* Paging views keep FIFO; entering Usage discards old page reports. */
    reset_sources(true);test_link=true;
    pc(100,1,true);pc(101,-1,true);iap_agents_volume_enable(true);
    assert(take_at(101)==UP);assert(take_at(101)==DOWN);
    pc(102,1,true);iap_agents_volume_enable(false);
    assert(!take_at(103));iap_agents_volume_enable(true);assert(!take_at(103));
    /* S2: connected Usage has no iPod page/audio effect from either source. */
    reset_sources(false);test_link=true;
    assert(dock(100,15));assert(global_status.volume==12&&!take_at(100));
    pc(101,1,true);pc(102,-1,false);assert(!take_at(102));
    assert(global_status.volume==12);
    test_link=false;assert(!dock(103,18));assert(global_status.volume==18);
    assert(!take_at(103));
    /* FU16 detail delegates connected PC reports, keeps disconnected pages. */
    reset_sources(true);test_link=true;pc(100,1,true);
    iap_agents_volume_host_keys(true);assert(!take_at(100));
    pc(101,1,true);pc(102,-1,false);assert(!take_at(102));
    assert(dock(103,15));assert(!take_at(103)&&global_status.volume==12);
    test_link=false;assert(dock(104,18));assert(take_at(104)==UP);
    assert(global_status.volume==12);
    iap_agents_volume_host_keys(false);test_link=true;pc(105,-1,true);
    assert(take_at(105)==DOWN);
    /* Outside Agents no new PC volume behavior and original real Dock volume. */
    reset_sources(false);activity=0;test_link=true;heartbeat(100);pc(101,1,true);
    assert(!dock(102,15));assert(global_status.volume==15&&!take_at(102));
    assert(!button_usb_remote_read());
    /* Old Menu/Select EP0 sampling is untouched. */
    request_at(110,1,1);assert(button_usb_remote_read()==BUTTON_RC_MENU);
    request_at(111,1,0);current_tick=115;assert(!button_usb_remote_read());
    request_at(120,2,1);assert(button_usb_remote_read()==BUTTON_RC_SELECT);
    /* No connection means unsolicited PC steps are ignored. */
    reset_sources(true);pc(100,1,true);assert(!take_at(100));
    /* Exact endpoint validation remains unchanged. */
    for(int id=0;id<65536;id++)
    {
        struct usb_ctrlrequest req={0x40,0x5b,id,1,0};
        assert(button_usb_remote_request(&req)==(id>=1&&id<=5));
    }
    for(int id=1;id<=5;id++)for(int state=0;state<=4;state++)
    {
        struct usb_ctrlrequest req={0x40,0x5b,id,state,0};
        bool valid=id<=2?state<=1:id<=4?(state==1||state==2):state==1;
        assert(button_usb_remote_request(&req)==valid);
    }
    /* Live rows distinguish the new ignore policy, not an arbitration wait. */
    reset_sources(true);test_link=true;dock(100,15);pc(101,1,true);take_at(101);
    char line[REMOTE_LOG_LINE];bool ignored=false,volume_ignored=false,used=false;
    while(remote_log_read(line,sizeof(line)))
    {
        ignored|=strstr(line,"source=DOCK key=VOL+ decision=ignored-pc-connected")!=NULL;
        volume_ignored|=strstr(line,"LOG VOL")&&strstr(line,"decision=ignored-pc-connected");
        used|=strstr(line,"source=PC key=VOL+ decision=used")!=NULL;
    }
    assert(ignored&&volume_ignored&&used);
    /* The production poll wakes immediately; native input is never delayed. */
    reset_sources(true);test_link=true;current_tick=100;inject_pc=true;
    assert(remote_poll(-1,REMOTE_AGENTS)==UP);assert(current_tick==101);
    native_key=BUTTON_LEFT;
    assert(remote_poll(-1,REMOTE_AGENTS)==BUTTON_LEFT);assert(current_tick==101);
    /* No PC reports are merged, shaped or duplicated by the retained FIFO. */
    reset_sources(true);test_link=true;
    for(int i=0;i<AGENTS_VOLUME_STEPS+1;i++)pc(100,1,i==0);
    for(int i=0;i<AGENTS_VOLUME_STEPS;i++)assert(take_at(101)==UP);
    assert(!take_at(101));
    /* Fresh bridge and FIFO behavior survive tick wrap. */
    reset_sources(true);heartbeat(ULONG_MAX-5);pc(1,-1,true);
    dock(2,9);assert(take_at(2)==DOWN);assert(!take_at(2));
    puts("FU15: resident-only/fresh-bridge connection, Dock ignored/PC FIFO, stale heartbeat with live PodLink, disconnect FU13, Usage PC ignored/disconnected Dock real volume, outside/native/Menu/Select, endpoint validation, live decision strings, wakeup, queue/wrap PASS");
}
