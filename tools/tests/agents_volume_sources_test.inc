/* Offline integration with real EP0 producer and volume consumer. */
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
    if(!handled) global_status.volume=volume;
    return handled;
}
static int take_at(unsigned long now)
{current_tick=now;return iap_agents_volume_take();}
static void reset_sources(bool paging)
{
    current_tick=0;wakeups=0;activity=ACTIVITY_AGENTS;bridge_seen=false;
    global_status.volume=12;iap_agents_volume_enable(paging);
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
static void concurrent_pc(void){pc(101,1,true);}
int main(void)
{
    reset_sources(true);
    assert(!button_usb_remote_bridge_fresh(0));
    /* The old Dock source is immediate when heartbeat is absent/stale. */
    assert(dock(10,15));assert(take_at(10)==UP);assert(!take_at(10));
    assert(dock(20,18));assert(!take_at(20));
    heartbeat(30);assert(button_usb_remote_bridge_fresh(329));
    assert(!button_usb_remote_bridge_fresh(330));
    assert(dock(400,21));assert(take_at(400)==UP);
    /* Both paths, PC first: the Dock's initial and accelerated repeats lose. */
    reset_sources(true);heartbeat(100);pc(101,1,true);assert(dock(104,15));
    assert(take_at(104)==UP);assert(!take_at(104));
    assert(dock(114,18));assert(dock(124,24));assert(!take_at(124));
    pc(125,1,false);pc(126,1,false);
    assert(take_at(126)==UP);assert(take_at(126)==UP);assert(!take_at(126));
    reset_sources(true);heartbeat(100);pc(101,1,true);pc(140,1,false);
    assert(take_at(140)==UP);assert(take_at(140)==UP);
    assert(dock(144,15));assert(!take_at(152));
    reset_sources(true);heartbeat(100);pc(101,1,true);dock(104,15);take_at(104);
    dock(114,18);dock(124,24);
    /* A later Dock-only press is not globally disabled by the heartbeat. */
    assert(dock(160,27));assert(!take_at(167));assert(take_at(168)==UP);
    assert(!take_at(168));
    /* Both paths, Dock first: first candidate waits <= named 80 ms window. */
    reset_sources(true);heartbeat(100);assert(dock(100,15));
    assert(!take_at(107));assert(iap_agents_volume_timeout(100)==1);
    assert(iap_agents_volume_timeout(0)==0);pc(107,1,true);
    assert(take_at(107)==UP);assert(!take_at(108));
    /* Boundary: an arriving PC report wins if not yet dispatched at expiry. */
    reset_sources(true);heartbeat(100);assert(dock(100,15));pc(108,1,true);
    assert(take_at(108)==UP);assert(!take_at(109));
    /* Dock-only fallback keeps the existing 600/250 ms repeat schedule. */
    reset_sources(true);heartbeat(100);assert(dock(100,15));
    assert(!take_at(107));assert(take_at(108)==UP);
    for(int t=110;t<=150;t+=10){assert(dock(t,18+(t-110)*3/10));assert(!take_at(t));}
    assert(dock(160,33));assert(take_at(160)==UP);
    assert(dock(170,36));assert(!take_at(170));
    assert(dock(190,39));assert(take_at(190)==UP);
    /* Opposite unmatched presses may overlap their arbitration windows. */
    reset_sources(true);heartbeat(100);assert(dock(100,15));
    assert(dock(107,12));assert(take_at(108)==UP);
    assert(!take_at(114));assert(take_at(115)==DOWN);assert(!take_at(115));
    reset_sources(true);heartbeat(100);assert(dock(100,15));
    assert(dock(107,12));pc(107,1,true);
    assert(take_at(108)==UP);assert(!take_at(108));
    assert(take_at(115)==DOWN);assert(!take_at(115));
    reset_sources(true);heartbeat(100);assert(dock(100,15));
    heartbeat(500);pc(500,1,true);
    assert(take_at(500)==UP);assert(take_at(500)==UP);assert(!take_at(500));
    /* PC steps are FIFO, including rapid frames and direction reversal. */
    reset_sources(true);heartbeat(100);
    pc(101,1,true);pc(102,1,true);pc(103,-1,true);pc(104,-1,false);
    assert(dock(105,9));
    assert(take_at(105)==UP);assert(take_at(105)==UP);
    assert(take_at(105)==DOWN);assert(take_at(105)==DOWN);assert(!take_at(105));
    reset_sources(true);heartbeat(100);pc(101,1,true);pc(102,1,true);
    iap_agents_volume_enable(false);assert(take_at(103)==UP);
    assert(take_at(103)==UP);assert(!take_at(103));
    /* Usage emits the same remote action; the existing Usage arm changes
     * real audio volume instead of scrolling. Dock does not change it twice. */
    reset_sources(false);heartbeat(100);pc(101,1,true);
    assert(dock(104,15));assert(global_status.volume==12);
    assert(take_at(104)==UP);assert(!take_at(104));
    global_status.volume++;assert(global_status.volume==13);
    /* Outside Agents: no PC step, and Dock keeps its real absolute setter. */
    reset_sources(false);activity=0;heartbeat(100);pc(101,1,true);
    assert(!dock(104,15));assert(global_status.volume==15&&!take_at(104));
    assert(!button_usb_remote_read());
    /* Menu/Select remain their original sampled EP0 buttons. */
    request_at(110,1,1);assert(button_usb_remote_read()==BUTTON_RC_MENU);
    request_at(111,1,0);current_tick=115;assert(!button_usb_remote_read());
    request_at(120,2,1);assert(button_usb_remote_read()==BUTTON_RC_SELECT);
    /* Exact new ids and state validation; all unknown ids remain STALL. */
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
    /* Tick wrap uses the same unsigned time policy as button sampling. */
    reset_sources(true);heartbeat(ULONG_MAX-5);
    assert(button_usb_remote_bridge_fresh(3));
    pc(ULONG_MAX-3,1,true);assert(dock(1,15));
    assert(take_at(1)==UP);assert(!take_at(1));
    /* Log source/decisions are visible in the same resident stream. */
    reset_sources(true);heartbeat(100);pc(101,1,true);dock(104,15);
    take_at(104);dock(150,18);take_at(158);
    char line[REMOTE_LOG_LINE];bool used_pc=false,duplicate=false,fallback=false;
    bool paired=false,unmatched=false;
    while(remote_log_read(line,sizeof(line)))
    {
        used_pc|=strstr(line,"source=PC key=VOL+ decision=used")!=NULL;
        duplicate|=strstr(line,"source=DOCK key=VOL+ decision=dropped-dup")!=NULL;
        fallback|=strstr(line,"source=DOCK key=VOL+ decision=fallback")!=NULL;
        paired|=strstr(line,"press=1 pc_tick=101 dock_tick=104")!=NULL;
        unmatched|=strstr(line,"press=2 pc_tick=- dock_tick=150")!=NULL;
    }
    assert(used_pc&&duplicate&&fallback&&paired&&unmatched);
    reset_sources(true);heartbeat(100);dock(100,15);pc(107,1,true);
    paired=false;
    while(remote_log_read(line,sizeof(line)))
        paired|=strstr(line,"press=1 pc_tick=107 dock_tick=100")!=NULL;
    assert(paired);
    reset_sources(true);heartbeat(0);pc(0,1,true);dock(0,15);
    paired=false;
    while(remote_log_read(line,sizeof(line)))
        paired|=strstr(line,"press=1 pc_tick=0 dock_tick=0")!=NULL;
    assert(paired);
    /* An older candidate must keep its own stamp during a direction change. */
    reset_sources(true);heartbeat(100);dock(100,15);dock(107,12);pc(107,1,true);
    paired=false;
    while(remote_log_read(line,sizeof(line)))
        paired|=strstr(line,"press=1 pc_tick=107 dock_tick=100")!=NULL;
    assert(paired);
    /* Heartbeat may first become fresh in the middle of an existing hold. */
    reset_sources(true);dock(100,15);assert(take_at(100)==UP);
    while(remote_log_read(line,sizeof(line))) {}
    heartbeat(110);
    for(int t=110;t<=150;t+=10)dock(t,18+(t-110)*3/10);
    dock(160,33);assert(take_at(160)==UP);
    paired=false;
    while(remote_log_read(line,sizeof(line)))
        paired|=strstr(line,"decision=fallback")&&
                strstr(line,"press=1 pc_tick=- dock_tick=100");
    assert(paired);
    /* Actual remote_poll wakes for PC and caps a blocking fallback wait. */
    reset_sources(true);heartbeat(100);dock(100,15);wakeups=0;
    assert(remote_poll(-1,REMOTE_AGENTS)==UP);assert(current_tick==108);
    reset_sources(true);heartbeat(100);current_tick=100;inject_pc=true;
    assert(remote_poll(-1,REMOTE_AGENTS)==UP);assert(current_tick==101);
    reset_sources(true);heartbeat(100);dock(100,15);current_tick=100;
    native_key=BUTTON_LEFT;
    assert(remote_poll(-1,REMOTE_AGENTS)==BUTTON_LEFT);assert(current_tick==100);
    reset_sources(true);heartbeat(100);current_tick=100;
    before_irq_mask=concurrent_pc;assert(iap_agents_volume(15,false));
    assert(take_at(101)==UP);assert(!take_at(109));
    /* Queue bounds: no collapse of multiple reports, no allocation/blocking. */
    reset_sources(true);heartbeat(100);
    for(int i=0;i<AGENTS_VOLUME_STEPS+1;i++)pc(101,1,i==0);
    for(int i=0;i<AGENTS_VOLUME_STEPS;i++)assert(take_at(102)==UP);
    assert(!take_at(102));
    puts("PC/Dock real integration: PC/Dock-first, late unmatched Dock, Dock-only fallback, rapid reports/reversal, exact repeats, stale/absent heartbeat, Usage/outside, Menu/Select, exhaustive EP0 STALL, tick wrap, source logs, queue bound PASS");
}
