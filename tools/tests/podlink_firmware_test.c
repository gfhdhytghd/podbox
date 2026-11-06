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
static int recv_size, recv_have, sent_size;
static int test_high_speed = 1;
int usb_drv_port_speed(void)
{
    return test_high_speed;
}
static unsigned char output[3 * PODLINK_FRAME_MAX];
static int output_size;
bool usb_podlink_open(void)
{
    if (lease || plugged)
        return false;
    lease = true;
    return true;
}
void usb_podlink_close(void)
{
    lease = false;
}
bool usb_podlink_active(void)
{
    return lease;
}
void usb_core_control_response(int response, void *data, int size)
{
    (void)response;
    (void)data;
    (void)size;
}
void usb_signal_class_notify(int driver, uint32_t data)
{
    (void)driver;
    (void)data;
    kicked = true;
}
int usb_drv_recv_nonblocking(int ep, void *data, int size)
{
    (void)ep;
    assert(!recv_ptr);
    recv_ptr = data;
    recv_size = size;
    recv_have = 0;
    return 0;
}
int usb_drv_send_nonblocking(int ep, const void *data, int size)
{
    (void)ep;
    assert(!sent_size);
    assert(size > 0 && size <= TRANSIT_BUFFER_SIZE);
    assert(((uintptr_t)data & 3) == 0);
    assert(output_size + size <= (int)sizeof(output));
    memcpy(output + output_size, data, size);
    output_size += size;
    sent_size = size;
    return 0;
}
static void controller(void)
{
    for (int i = 0; i < 32 && (kicked || sent_size); ++i)
    {
        if (kicked)
        {
            kicked = false;
            serial_notify(0);
        }
        if (sent_size)
        {
            int n = sent_size;
            sent_size = 0;
            usb_serial_transfer_complete(0, USB_DIR_IN, 0, n);
        }
    }
}
static void insert(void)
{
    plugged = true;
    recv_ptr = NULL;
    sent_size = 0;
    assert(usb_serial_init_connection() == 0);
}
static void remove_usb(void)
{
    plugged = false;
    recv_ptr = NULL;
    sent_size = 0;
    usb_serial_disconnect();
}
static void input_bytes(const unsigned char *b, unsigned int n)
{
    while (n)
    {
        controller();
        assert(recv_ptr);
        int packet = test_high_speed ? 512 : 64;
        int chunk = MIN(n, (unsigned)packet);
        chunk = MIN(chunk, recv_size - recv_have);
        memcpy((unsigned char *)recv_ptr + recv_have, b, chunk);
        recv_have += chunk;
        if (recv_have == recv_size || chunk < packet)
        {
            int length = recv_have;
            recv_ptr = NULL;
            usb_serial_transfer_complete(0, USB_DIR_OUT, 0, length);
        }
        b += chunk;
        n -= chunk;
    }
}
static void input_frame(unsigned int type, unsigned int seq, const void *p,
                        unsigned int n)
{
    unsigned char frame[PODLINK_FRAME_MAX];
    unsigned int length = podlink_encode(frame, type, seq, p, n);
    assert(length);
    for (unsigned int pos = 0; pos < length;)
    {
        unsigned int chunk = MIN(4096u, length - pos);
        input_bytes(frame + pos, chunk);
        pos += chunk;
        if (pos < length)
        {
            struct podlink_message m;
            assert(!podlink_poll(&m));
        }
    }
}
static int poll_message(struct podlink_message *m)
{
    controller();
    int n = podlink_poll(m);
    controller();
    return n;
}
static void clear_output(void)
{
    controller();
    output_size = 0;
}

static void handshake(unsigned int maximum, unsigned int seq)
{
    unsigned char offer[6] = {
        PODLINK_VERSION, 0, maximum & 255, (maximum >> 8) & 255,
        (maximum >> 16) & 255, (maximum >> 24) & 255};
    struct podlink_message m;
    clear_output();
    input_frame(PODLINK_OPEN, seq, offer, sizeof(offer));
    for (int i = 0; i < 20; ++i)
        assert(!poll_message(&m));
    unsigned int limit = MIN(maximum, PODLINK_MAX_MESSAGE);
    assert(podlink_state() == PODLINK_CONNECTED);
    assert(podlink_max_message() == limit);
    struct podlink_parser check = {0};
    int got = 0;
    for (int i = 0; i < output_size; ++i)
        if (podlink_decode(&check, output[i], &m) == 1)
        {
            assert(m.type == PODLINK_OPEN && m.seq == seq);
            assert(m.length == 6 && m.data[0] == PODLINK_VERSION);
            assert(m.data[2] == (limit & 255));
            assert(m.data[3] == (limit >> 8));
            ++got;
        }
    assert(got == 1);
}

static void roundtrip(unsigned int length)
{
    unsigned char payload[PODLINK_MAX_MESSAGE];
    for (unsigned int i = 0; i < length; ++i)
        payload[i] = i % 251;
    clear_output();
    input_frame(PODLINK_BINARY, 500, payload, length);
    struct podlink_message m;
    int got = 0;
    for (int i = 0; i < 100 && !got; ++i)
        got = poll_message(&m);
    assert(got && m.type == PODLINK_BINARY && m.length == length);
    assert(!memcmp(m.data, payload, length));
    assert(podlink_send(m.type, m.data, m.length) == PODLINK_SENT);
    for (int i = 0; i < 100; ++i)
        assert(!poll_message(&m));
    struct podlink_parser check = {0};
    got = 0;
    for (int i = 0; i < output_size; ++i)
        if (podlink_decode(&check, output[i], &m) == 1)
        {
            assert(m.type == PODLINK_BINARY && m.length == length);
            assert(!memcmp(m.data, payload, length));
            ++got;
        }
    assert(got == 1);
}

int main(void)
{
    usb_serial_init();
    assert(!usb_serial_connected());
    assert(podlink_open());
    assert(!podlink_open());
    struct podlink_message m;
    insert();
    poll_message(&m);
    assert(podlink_state() == PODLINK_OPENING);
    assert(hello[2] == (PODLINK_MAX_MESSAGE & 255));
    assert(hello[3] == (PODLINK_MAX_MESSAGE >> 8));
    input_frame(PODLINK_OPEN, 11, hello, sizeof(hello));
    for (int i = 0; i < 20 && podlink_state() != PODLINK_CONNECTED; i++)
        poll_message(&m);
    assert(podlink_state() == PODLINK_CONNECTED);
    clear_output();
    unsigned char payload[PODLINK_MAX_MESSAGE];
    for (int i = 0; i < PODLINK_MAX_MESSAGE; i++)
        payload[i] = i % 251;
    /* TX queue is bounded and DMA completes in fragments without losing bytes.
     */
    assert(podlink_send(PODLINK_BINARY, payload, sizeof(payload)) == 1);
    assert(podlink_send(PODLINK_BINARY, payload, sizeof(payload)) == 1);
    assert(podlink_send(PODLINK_BINARY, payload, sizeof(payload)) == 0);
    assert(podlink_send(PODLINK_BINARY, payload, sizeof(payload) + 1) == -1);
    for (int i = 0; i < 100; i++)
        poll_message(&m);
    struct podlink_parser check = {0};
    int frames = 0;
    for (int i = 0; i < output_size; i++)
        if (podlink_decode(&check, output[i], &m) == 1)
        {
            assert(m.type == PODLINK_BINARY);
            assert(m.length == sizeof(payload));
            assert(!memcmp(m.data, payload, sizeof(payload)));
            ++frames;
        }
    assert(frames == 2);
    /* Fill the bounded RX ring with whole multi-packet DMA requests. */
    clear_output();
    for (int i = 0; i < BUFFER_SIZE / (int)sizeof(payload); i++)
        input_bytes(payload, sizeof(payload));
    assert(!recv_ptr);
    assert(rx_count == BUFFER_SIZE);
    unsigned char readback[BUFFER_SIZE];
    assert(usb_serial_read(readback, BUFFER_SIZE) == BUFFER_SIZE);
    for (int i = 0; i < BUFFER_SIZE / (int)sizeof(payload); i++)
        assert(
            !memcmp(readback + i * sizeof(payload), payload, sizeof(payload)));
    controller();
    assert(recv_ptr);
    /* Corrupt frame followed by a valid frame resynchronizes. */
    unsigned char frame[PODLINK_FRAME_MAX];
    unsigned int n = podlink_encode(frame, PODLINK_TEXT, 20, "broken", 6);
    frame[n - 1] ^= 1;
    input_bytes(frame, n);
    input_frame(PODLINK_TEXT, 21, "hello", 5);
    int got = 0;
    for (int i = 0; i < 100 && !got; i++)
        got = poll_message(&m);
    assert(got && m.type == PODLINK_TEXT && m.length == 5 &&
           !memcmp(m.data, "hello", 5));
    /* A complete frame aligned to one USB packet must not wait for a ZLP. */
    input_frame(PODLINK_BINARY, 122, payload, 492);
    got = 0;
    for (int i = 0; i < 20 && !got; i++)
        got = poll_message(&m);
    assert(got && m.length == 492 && !memcmp(m.data, payload, 492));
    /* Max frame crosses the RX/TX rings. Packet/ring-aligned frames finish
     * without a host ZLP; adjacent lengths finish on a short packet. */
    roundtrip(PODLINK_MAX_MESSAGE);
    roundtrip(PODLINK_MAX_MESSAGE - 20);
    roundtrip(PODLINK_MAX_MESSAGE - 21);
    /* Old hosts advertise 4096, newer peers may advertise more than us. */
    handshake(4096, 124);
    roundtrip(4096);
#if PODLINK_MAX_MESSAGE > 4096
    assert(podlink_send(PODLINK_BINARY, payload, 4097) == PODLINK_INVALID);
    input_frame(PODLINK_BINARY, 125, payload, 4097);
    assert(poll_message(&m) == 1 && m.type == PODLINK_CLOSE);
    assert(m.data[0] == (1009 & 255));
    poll_message(&m);
#endif
    handshake(16384, 125);
    roundtrip(PODLINK_MAX_MESSAGE);
    handshake(65536, 126);
    roundtrip(PODLINK_MAX_MESSAGE);
    /* Ping replies preserve sequence. */
    clear_output();
    input_frame(PODLINK_PING, 123, "p", 1);
    for (int i = 0; i < 20; i++)
        poll_message(&m);
    memset(&check, 0, sizeof(check));
    got = 0;
    for (int i = 0; i < output_size; i++)
        if (podlink_decode(&check, output[i], &m) == 1)
            if (m.type == PODLINK_PONG && m.seq == 123)
                got = 1;
    assert(got);
    /* Unplug emits one CLOSE; replug handshakes without restarting. */
    remove_usb();
    assert(poll_message(&m) == 1 && m.type == PODLINK_CLOSE &&
           m.data[0] == (1006 & 255));
    assert(!poll_message(&m));
    assert(lease);
    insert();
    poll_message(&m);
    input_frame(PODLINK_OPEN, 22, hello, 6);
    for (int i = 0; i < 20; i++)
        poll_message(&m);
    assert(podlink_state() == PODLINK_CONNECTED);
    /* No keepalive ACK: bounded timeout emits on_close. */
    current_tick = 600;
    poll_message(&m);
    current_tick = 2201;
    assert(poll_message(&m) == 1 && m.type == PODLINK_CLOSE);
    assert(podlink_state() == PODLINK_OPENING);
    input_frame(PODLINK_OPEN, 23, hello, 6);
    for (int i = 0; i < 20; i++)
        poll_message(&m);
    /* Oversized header creates error/CLOSE without allocation. */
    n = podlink_encode(frame, PODLINK_TEXT, 1, NULL, 0);
    frame[12] = (PODLINK_MAX_MESSAGE + 1) & 255;
    frame[13] = (PODLINK_MAX_MESSAGE + 1) >> 8;
    input_bytes(frame, 16);
    got = 0;
    for (int i = 0; i < 20 && !got; i++)
        got = poll_message(&m);
    assert(got && m.type == PODLINK_CLOSE && m.data[0] == (1009 & 255));
    /* User close times out cleanly, releases USB mode, never sleeps here. */
    podlink_close();
    current_tick += 101;
    poll_message(&m);
    assert(podlink_state() == PODLINK_CLOSED && !lease);
    /* Full-speed streams also complete a packet-aligned final write. */
    remove_usb();
    test_high_speed = 0;
    insert();
    assert(recv_size == 64);
    input_bytes(payload, 64);
    unsigned char full_speed[64];
    assert(usb_serial_read(full_speed, 64) == 64);
    assert(!memcmp(full_speed, payload, 64));
    assert(serial_wake.count <= 1);
    remove_usb();
    assert(podlink_open());
    insert();
    poll_message(&m);
    handshake(PODLINK_MAX_MESSAGE, 127);
    roundtrip(PODLINK_MAX_MESSAGE);
    roundtrip(PODLINK_MAX_MESSAGE - 20);
    roundtrip(PODLINK_MAX_MESSAGE - 21);
    podlink_close();
    current_tick += 101;
    poll_message(&m);
    remove_usb();
    char diagnostics[4096];
    int diag_size = podlink_stats_json(diagnostics, sizeof(diagnostics));
    assert(diag_size > 0 && diag_size < (int)sizeof(diagnostics));
    assert(strstr(diagnostics, "\"build\":\"fast-1\"") != NULL);
    puts(diagnostics);
    printf("PASS: real C wire/session/serial: bounded queues, RX backpressure, "
           "%u KiB integrity at HS/FS, negotiated limits, CRC recovery, ping, "
           "timeout, close, replug\n", PODLINK_MAX_MESSAGE / 1024);
    return 0;
}
