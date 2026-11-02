/* Actual serial/session sources with RAM-backed controller and clock. */
#include "config.h"
#include "kernel.h"
#include <string.h>
#include <stdio.h>
#include "../../firmware/usbstack/usb_serial.c"
#include "../../firmware/usbstack/podlink.c"

long current_tick;
static bool lease, plugged, kicked;
static void *recv_ptr;
static int recv_size, sent_size;
static unsigned char output[32768];
static int output_size;
bool usb_podlink_open(void) { if (lease || plugged) return false; lease = true; return true; }
void usb_podlink_close(void) { lease = false; }
bool usb_podlink_active(void) { return lease; }
void usb_core_control_response(int response, void *data, int size) { (void)response;(void)data;(void)size; }
void usb_signal_class_notify(int driver, uint32_t data) { (void)driver;(void)data;kicked=true; }
int usb_drv_recv_nonblocking(int ep, void *data, int size)
{ (void)ep; assert(!recv_ptr); recv_ptr=data;recv_size=size;return 0; }
int usb_drv_send_nonblocking(int ep, const void *data, int size)
{
    (void)ep; assert(!sent_size); assert(output_size+size <= (int)sizeof(output));
    memcpy(output+output_size,data,size); output_size+=size;sent_size=size;return 0;
}
static void controller(void)
{
    for (int i=0; i<32 && (kicked || sent_size); ++i)
    {
        if(kicked) { kicked=false; serial_notify(0); }
        if(sent_size) { int n=sent_size;sent_size=0;usb_serial_transfer_complete(0,USB_DIR_IN,0,n); }
    }
}
static void insert(void)
{ plugged=true;recv_ptr=NULL;sent_size=0;assert(usb_serial_init_connection()==0); }
static void remove_usb(void)
{ plugged=false;recv_ptr=NULL;sent_size=0;usb_serial_disconnect(); }
static void input_bytes(const unsigned char *b,unsigned int n)
{
    while(n)
    {
        controller(); assert(recv_ptr);
        int chunk=MIN(n,(unsigned)recv_size);
        memcpy(recv_ptr,b,chunk);recv_ptr=NULL;
        usb_serial_transfer_complete(0,USB_DIR_OUT,0,chunk);
        b+=chunk;n-=chunk;
    }
}
static void input_frame(unsigned int type, unsigned int seq, const void *p,unsigned int n)
{
    unsigned char frame[PODLINK_FRAME_MAX];
    unsigned int length=podlink_encode(frame,type,seq,p,n);
    input_bytes(frame,length);
}
static int poll_message(struct podlink_message *m)
{ controller(); int n=podlink_poll(m);controller();return n; }
static void clear_output(void) { controller();output_size=0; }

int main(void)
{
    usb_serial_init();
    assert(!usb_serial_connected());
    assert(podlink_open());assert(!podlink_open());
    struct podlink_message m;
    insert();poll_message(&m);assert(podlink_state()==PODLINK_OPENING);
    input_frame(PODLINK_OPEN,11,hello,sizeof(hello));
    for(int i=0;i<20 && podlink_state()!=PODLINK_CONNECTED;i++) poll_message(&m);
    assert(podlink_state()==PODLINK_CONNECTED);
    clear_output();
    unsigned char payload[4096];for(int i=0;i<4096;i++)payload[i]=i%251;
    /* TX queue is bounded and DMA completes in fragments without losing bytes. */
    assert(podlink_send(PODLINK_BINARY,payload,4096)==1);
    assert(podlink_send(PODLINK_BINARY,payload,4096)==1);
    assert(podlink_send(PODLINK_BINARY,payload,4096)==0);
    for(int i=0;i<100;i++)poll_message(&m);
    struct podlink_parser check={0};int frames=0;
    for(int i=0;i<output_size;i++)if(podlink_decode(&check,output[i],&m)==1)
    { assert(m.type==PODLINK_BINARY);assert(m.length==4096);assert(!memcmp(m.data,payload,4096));++frames; }
    assert(frames==2);
    /* RX backpressure: eight packets fill the ring, ninth isn't armed. */
    clear_output();
    for(int i=0;i<8;i++)input_bytes(payload+i*512,512);
    assert(!recv_ptr);assert(rx_count==4096);
    unsigned char readback[4096];assert(usb_serial_read(readback,4096)==4096);
    assert(!memcmp(readback,payload,4096));controller();assert(recv_ptr);
    /* Corrupt frame followed by a valid frame resynchronizes. */
    unsigned char frame[PODLINK_FRAME_MAX];
    unsigned int n=podlink_encode(frame,PODLINK_TEXT,20,"broken",6);frame[n-1]^=1;
    input_bytes(frame,n);input_frame(PODLINK_TEXT,21,"hello",5);
    int got=0;for(int i=0;i<100 && !got;i++)got=poll_message(&m);
    assert(got && m.type==PODLINK_TEXT && m.length==5 && !memcmp(m.data,"hello",5));
    /* Ping replies preserve sequence. */
    clear_output();input_frame(PODLINK_PING,123,"p",1);
    for(int i=0;i<20;i++)poll_message(&m);
    check.used=0;got=0;
    for(int i=0;i<output_size;i++)if(podlink_decode(&check,output[i],&m)==1)
        if(m.type==PODLINK_PONG && m.seq==123)got=1;
    assert(got);
    /* Unplug emits one CLOSE; replug handshakes without restarting. */
    remove_usb();assert(poll_message(&m)==1 && m.type==PODLINK_CLOSE && m.data[0]==(1006&255));
    assert(!poll_message(&m));assert(lease);insert();poll_message(&m);
    input_frame(PODLINK_OPEN,22,hello,6);
    for(int i=0;i<20;i++)poll_message(&m);
    assert(podlink_state()==PODLINK_CONNECTED);
    /* No keepalive ACK: bounded timeout emits on_close. */
    current_tick=600;poll_message(&m);current_tick=2201;
    assert(poll_message(&m)==1 && m.type==PODLINK_CLOSE);
    assert(podlink_state()==PODLINK_OPENING);
    input_frame(PODLINK_OPEN,23,hello,6);for(int i=0;i<20;i++)poll_message(&m);
    /* Oversized header creates error/CLOSE without allocation. */
    n=podlink_encode(frame,PODLINK_TEXT,1,NULL,0);frame[12]=1;frame[13]=16;
    input_bytes(frame,16);got=0;for(int i=0;i<20 && !got;i++)got=poll_message(&m);
    assert(got && m.type==PODLINK_CLOSE && m.data[0]==(1009&255));
    /* User close times out cleanly, releases USB mode, never sleeps here. */
    podlink_close();current_tick+=101;poll_message(&m);
    assert(podlink_state()==PODLINK_CLOSED && !lease);
    char diagnostics[4096];
    int diag_size = podlink_stats_json(diagnostics, sizeof(diagnostics));
    assert(diag_size > 0 && diag_size < (int)sizeof(diagnostics));
    assert(strstr(diagnostics, "\"build\":\"profile-1\"") != NULL);
    puts(diagnostics);
    puts("PASS: real C wire/session/serial: bounded queues, RX backpressure, 4 KiB integrity, CRC recovery, ping, timeout, close, replug");
    return 0;
}
