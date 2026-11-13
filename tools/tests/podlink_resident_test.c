/* Real session/serial plus resident owner, deterministic worker, no USB device. */
#define PODLINK_SERVICE_TEST
#define DEFAULT_STACK_SIZE 4096
#ifndef MAX
#define MAX(a,b) ((a) > (b) ? (a) : (b))
#endif
#define main transport_harness_main
#include "podlink_firmware_test.c"
#undef main
#undef podlink_open
#undef podlink_state
#undef podlink_max_message
#undef podlink_send
#undef podlink_close
#undef podlink_poll
#undef podlink_wait
static void sleep(int ticks) { current_tick += ticks; }
#include "../../firmware/usbstack/podlink_service.c"
#include "herdremote_model.h"

static struct hr_model cached;
static int updates, losses;
static unsigned int log_format(const struct remote_log_record *r, char *out,
                                unsigned int size)
{
    return snprintf(out, size, "LOG TEST %lu", r->a);
}
static void background(const struct podlink_message *m)
{
    if (m->type == PODLINK_CLOSE) losses++;
    else if (m->type == PODLINK_TEXT)
    {
        hr_model_apply(&cached, (const char *)m->data, m->length);
        updates++;
    }
}
static void work(void)
{
    static struct podlink_parser peer;
    static int cursor;
    if (output_size < cursor) {cursor=0;memset(&peer,0,sizeof(peer));}
    for (int i = 0; i < 20; i++) {
        service_step();controller();current_tick++;
        struct podlink_message m;
        while(cursor<output_size)if(podlink_decode(&peer,output[cursor++],&m)==1 && m.type==PODLINK_PING)
            input_frame(PODLINK_PONG,m.seq,m.data,m.length);
    }
}
int main(void)
{
    assert(!podlink_host_connected());
    usb_serial_init();podlink_init();lease=true;service_step();
    assert(!podlink_host_connected());
    insert();work();assert(!podlink_host_connected());
    handshake(PODLINK_MAX_MESSAGE,1);work();assert(podlink_host_connected());
    /* The input callback must not try either mutex, even if the worker or
     * USB thread was interrupted while holding it. */
    mutex_lock(&service_mutex);mutex_lock(&serial_mtx);
    assert(podlink_host_connected());
    mutex_unlock(&serial_mtx);mutex_unlock(&service_mutex);
    uint32_t revision=podlink_session_revision();
    input_frame(PODLINK_TEXT,2,"HR\t1\thost",9);work();
    assert(podlink_open_agents(background));work();
    struct podlink_message greeting;
    assert(podlink_poll(&greeting)&&greeting.type==PODLINK_TEXT&&greeting.length==9);
    assert(podlink_state()==PODLINK_CONNECTED);podlink_close();work();
    assert(podlink_state()==PODLINK_CLOSED&&podlink_host_connected());
    const char *list="W\t1\nw1\t-\t1\t0\t0\t0\tCached";
    input_frame(PODLINK_TEXT,50,list,strlen(list));work();
    assert(updates>=1&&cached.nws==1);
    for(int i=0;i<500;i++) {
        clear_output();assert(podlink_open_agents(background));
        assert(!strcmp(cached.ws[0].label,"Cached")); /* first frame, zero I/O */
        assert(podlink_state()==PODLINK_CONNECTED);
        assert(output_size==0);
        work();podlink_close();work();
        assert(lease&&podlink_session_revision()==revision);
        struct podlink_parser parser={0};struct podlink_message m;
        for(int j=0;j<output_size;j++)if(podlink_decode(&parser,output[j],&m)==1)
            assert(m.type!=PODLINK_HELLO&&m.type!=PODLINK_OPEN&&m.type!=PODLINK_CLOSE);
    }
    assert(podlink_open());assert(!podlink_open_agents(background));work();
    assert(podlink_session_revision()==revision);podlink_close();work();
    /* Same wire owner in menus/WPS (0), Agents (1), and another client (2).
     * Enable/toggle does not attach, reconnect or alter the foreground. */
    for (int foreground=0; foreground<=2; foreground++)
    {
        if (foreground==1) assert(podlink_open_agents(background));
        if (foreground==2) assert(podlink_open());
        work();clear_output();
        remote_log_enable(true,log_format);
        remote_log_event(REMOTE_LOG_TAP,1,42,0,NULL,0);
        work();
        struct podlink_parser parser={0};struct podlink_message frame;
        int logs=0;
        for(int j=0;j<output_size;j++)
            if(podlink_decode(&parser,output[j],&frame)==1)
            {
                if(frame.type==PODLINK_TEXT && frame.length==11 &&
                   !memcmp(frame.data,"LOG TEST 42",11)) logs++;
                assert(frame.type!=PODLINK_HELLO && frame.type!=PODLINK_OPEN &&
                       frame.type!=PODLINK_CLOSE);
            }
        assert(logs==1 && client==foreground);
        assert(podlink_session_revision()==revision);
        remote_log_enable(false,log_format);
        clear_output();remote_log_event(REMOTE_LOG_TAP,1,43,0,NULL,0);
        work();
        memset(&parser,0,sizeof(parser));
        for(int j=0;j<output_size;j++)
            if(podlink_decode(&parser,output[j],&frame)==1)
                assert(frame.type!=PODLINK_TEXT);
        podlink_close();work();
    }
    /* Fill the real TX queue and freeze its serial sink for one step. */
    while(podlink_session_send(PODLINK_TEXT,"busy",4)==PODLINK_SENT) {}
    int saved_tx_count=tx_count;tx_count=BUFFER_SIZE;
    remote_log_enable(true,log_format);
    remote_log_event(REMOTE_LOG_TAP,1,44,0,NULL,0);
    service_step();assert(log_length==11);
    assert(!memcmp(log_line,"LOG TEST 44",11));
    remote_log_enable(false,log_format);
    service_step();assert(!log_length);
    tx_count=saved_tx_count;work();
    /* A second busy line survives until the transport accepts it. */
    while(podlink_session_send(PODLINK_TEXT,"busy",4)==PODLINK_SENT) {}
    saved_tx_count=tx_count;tx_count=BUFFER_SIZE;
    remote_log_enable(true,log_format);
    remote_log_event(REMOTE_LOG_TAP,1,45,0,NULL,0);
    service_step();assert(log_length==11);
    tx_count=saved_tx_count;clear_output();work();
    assert(!log_length);
    struct podlink_parser log_parser={0};struct podlink_message log_frame;
    bool retried=false;
    for(int j=0;j<output_size;j++)
        if(podlink_decode(&log_parser,output[j],&log_frame)==1 &&
           log_frame.type==PODLINK_TEXT && log_frame.length==11 &&
           !memcmp(log_frame.data,"LOG TEST 45",11)) retried=true;
    assert(retried);remote_log_enable(false,log_format);work();
    /* A small peer gets an explicit loss notice, never an INVALID loop. */
    uint32_t saved_peer_max=peer_max;peer_max=10;
    remote_log_enable(true,log_format);clear_output();
    remote_log_event(REMOTE_LOG_TAP,1,12345678,0,NULL,0);work();
    memset(&log_parser,0,sizeof(log_parser));bool small_notice=false;
    for(int j=0;j<output_size;j++)
        if(podlink_decode(&log_parser,output[j],&log_frame)==1 &&
           log_frame.type==PODLINK_TEXT && log_frame.length==10 &&
           !memcmp(log_frame.data,"LOG LOST 1",10)) small_notice=true;
    assert(small_notice && !log_length);
    peer_max=9;remote_log_event(REMOTE_LOG_TAP,1,46,0,NULL,0);work();
    assert(remote_log_read(log_line,sizeof(log_line))==11);
    assert(!memcmp(log_line,"LOG TEST 46",11));
    peer_max=saved_peer_max;remote_log_enable(false,log_format);work();
    /* Payload lifetime survives later worker polls until the next client poll. */
    assert(podlink_open_agents(background));
    struct podlink_message replay;while(podlink_poll(&replay)){}
    input_frame(PODLINK_TEXT,60,"one",3);work();struct podlink_message m;
    assert(podlink_poll(&m));assert(!memcmp(m.data,"one",3));
    input_frame(PODLINK_TEXT,61,"two",3);work();assert(!memcmp(m.data,"one",3));
    assert(podlink_poll(&m)&&!memcmp(m.data,"two",3));podlink_close();work();
    assert(podlink_open_agents(background));while(podlink_poll(&m)){}
    uint32_t before_pressure=podlink_generation();
    for(int i=0;i<80;i++){input_frame(PODLINK_TEXT,100+i,"delta",5);work();}
    assert(podlink_generation()!=before_pressure);
    assert(podlink_session_state()==PODLINK_CONNECTED&&podlink_session_revision()==revision);
    bool greeting_after_pressure=false;
    while(podlink_poll(&m))if(m.type==PODLINK_TEXT&&m.length>=3&&!memcmp(m.data,"HR\t",3))greeting_after_pressure=true;
    assert(greeting_after_pressure);podlink_close();work();
    /* Disconnect/reset invalidates the snapshot before a worker poll. */
    assert(podlink_host_connected());remove_usb();
    assert(!podlink_host_connected());
    insert();assert(!podlink_host_connected());
    work();assert(!podlink_host_connected());
    lease=false;assert(!podlink_host_connected());
    work();assert(losses>0&&!started);
    puts("resident real wire: 500 attach/switch/MENU exits; no OPEN/CLOSE/HELLO; cached first frame zero round trips; background list; client exclusion; payload lifetime; unplug PASS");
    puts("remote log real wire: prefixed TEXT in foreground 0/1/2; default/off no TEXT; no reconnect/client ownership change PASS");
    puts("resident host snapshot: configured current generation + OPEN; background independent; IRQ-safe/no mutex; immediate USB disconnect/reset invalidation PASS");
}
