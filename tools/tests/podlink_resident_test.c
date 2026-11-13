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
    usb_serial_init();podlink_init();lease=true;service_step();
    insert();work();handshake(PODLINK_MAX_MESSAGE,1);work();
    uint32_t revision=podlink_session_revision();
    input_frame(PODLINK_TEXT,2,"HR\t1\thost",9);work();
    assert(podlink_open_agents(background));work();
    struct podlink_message greeting;
    assert(podlink_poll(&greeting)&&greeting.type==PODLINK_TEXT&&greeting.length==9);
    assert(podlink_state()==PODLINK_CONNECTED);podlink_close();work();
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
    lease=false;work();assert(losses>0&&!started);
    puts("resident real wire: 500 attach/switch/MENU exits; no OPEN/CLOSE/HELLO; cached first frame zero round trips; background list; client exclusion; payload lifetime; unplug PASS");
}
