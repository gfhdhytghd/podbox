/* PodLink message sessions. SPDX-License-Identifier: GPL-2.0-or-later */
#include "config.h"
#include "podlink.h"
#include "usb.h"
#include "usb_serial.h"
#include "kernel.h"
#include <string.h>
#include <stdio.h>
#include "system.h"
#include "podlink_stats.h"

struct podlink_stats podlink_stats;
static uint32_t last_poll_us;
struct podlink_trace_entry
{
    uint32_t us, event, data;
};
static struct podlink_trace_entry trace[16];
static unsigned int trace_next;

void podlink_trace(unsigned int event, unsigned int data)
{
    int level = disable_irq_save();
    unsigned int i = trace_next % 16;
    trace[i].us = USEC_TIMER;
    trace[i].event = event;
    trace[i].data = data;
    ++trace_next;
    restore_irq(level);
}

void podlink_stats_reset(void)
{
    int level = disable_irq_save();
    memset(&podlink_stats, 0, sizeof(podlink_stats));
    last_poll_us = USEC_TIMER;
    restore_irq(level);
}

int podlink_stats_json(char *dest, size_t size)
{
    struct podlink_stats s;
    struct podlink_trace_entry saved[16];
    unsigned int saved_next;
    int level = disable_irq_save();
    s = podlink_stats;
    memcpy(saved, trace, sizeof(saved));
    saved_next = trace_next;
    restore_irq(level);
#ifdef HAVE_ADJUSTABLE_CPU_FREQ
    unsigned long clock_hz = cpu_frequency;
#else
    unsigned long clock_hz = 0;
#endif
    int n = snprintf(
        dest, size,
        "{\"build\":\"" PODLINK_BUILD "\""
        ",\"cpu_hz\":%lu"
        ",\"ui_turn_us_max\":%lu"
        ",\"polls\":%lu"
        ",\"poll_us\":%lu"
        ",\"poll_us_max\":%lu"
        ",\"poll_gap_us_max\":%lu"
        ",\"gaps_1ms\":%lu"
        ",\"gaps_5ms\":%lu"
        ",\"read_calls\":%lu"
        ",\"read_bytes\":%lu"
        ",\"empty_reads\":%lu"
        ",\"write_calls\":%lu"
        ",\"write_bytes\":%lu"
        ",\"rx_xfers\":%lu"
        ",\"rx_bytes\":%lu"
        ",\"rx_max\":%lu"
        ",\"tx_xfers\":%lu"
        ",\"tx_bytes\":%lu"
        ",\"tx_max\":%lu"
        ",\"rx_full\":%lu"
        ",\"write_busy\":%lu"
        ",\"queue_busy\":%lu"
        ",\"encodes\":%lu"
        ",\"encode_us\":%lu"
        ",\"decode_frames\":%lu"
        ",\"decode_crc_us\":%lu"
        ",\"lease\":%d"
        ",\"trace\":[",
        clock_hz, (unsigned long)s.ui_turn_us_max, (unsigned long)s.polls,
        (unsigned long)s.poll_us, (unsigned long)s.poll_us_max,
        (unsigned long)s.poll_gap_us_max, (unsigned long)s.gaps_1ms,
        (unsigned long)s.gaps_5ms, (unsigned long)s.read_calls,
        (unsigned long)s.read_bytes, (unsigned long)s.empty_reads,
        (unsigned long)s.write_calls, (unsigned long)s.write_bytes,
        (unsigned long)s.rx_xfers, (unsigned long)s.rx_bytes,
        (unsigned long)s.rx_max, (unsigned long)s.tx_xfers,
        (unsigned long)s.tx_bytes, (unsigned long)s.tx_max,
        (unsigned long)s.rx_full, (unsigned long)s.write_busy,
        (unsigned long)s.queue_busy, (unsigned long)s.encodes,
        (unsigned long)s.encode_us, (unsigned long)s.decode_frames,
        (unsigned long)s.decode_crc_us, usb_podlink_active());
    unsigned int count = MIN(saved_next, 16);
    for (unsigned int j = 0; j < count && n > 0 && (size_t)n < size; ++j)
    {
        unsigned int i = (saved_next - count + j) % 16;
        n += snprintf(dest + n, size - n, "%s[%lu,%lu,%lu]", j ? "," : "",
                      (unsigned long)saved[i].us, (unsigned long)saved[i].event,
                      (unsigned long)saved[i].data);
    }
    if (n > 0 && (size_t)n < size)
        n += snprintf(dest + n, size - n, "]}");
    return n;
}

#define QUEUE_SIZE                                                             \
    3 /* two application messages, one reserved control frame                  \
       */
struct tx_frame
{
    unsigned int length, sent;
    unsigned char data[PODLINK_FRAME_MAX];
};
static struct tx_frame tx[QUEUE_SIZE];
static unsigned int head, count;
static struct podlink_parser parser;
static unsigned char input[4096];
static unsigned int input_pos, input_length;
static unsigned char event_data[PODLINK_MAX_MESSAGE];
static enum podlink_connection state;
static uint32_t next_seq, ping_seq, peer_max;
static uint32_t transport_generation;
static long last_rx, ping_at, close_at;
static bool leased, had_usb, ping_pending, release_requested;
static const unsigned char hello[] = {
    PODLINK_VERSION, 0,
    PODLINK_MAX_MESSAGE & 255, (PODLINK_MAX_MESSAGE >> 8) & 255,
    (PODLINK_MAX_MESSAGE >> 16) & 255, (PODLINK_MAX_MESSAGE >> 24) & 255};

static int enqueue(unsigned int type, uint32_t seq, const void *data,
                   unsigned int length, bool control)
{
    if (count >= (control ? QUEUE_SIZE : QUEUE_SIZE - 1))
    {
        ++podlink_stats.queue_busy;
        return PODLINK_BUSY;
    }
    struct tx_frame *f = &tx[(head + count) % QUEUE_SIZE];
    uint32_t started = USEC_TIMER;
    f->length = podlink_encode(f->data, type, seq, data, length);
    ++podlink_stats.encodes;
    podlink_stats.encode_us += (uint32_t)USEC_TIMER - started;
    if (!f->length)
        return PODLINK_INVALID;
    f->sent = 0;
    ++count;
    return PODLINK_SENT;
}
static void pump(void)
{
    if (!count)
        return;
    struct tx_frame *f = &tx[head];
    f->sent += usb_serial_write(f->data + f->sent, f->length - f->sent);
    if (f->sent == f->length)
    {
        head = (head + 1) % QUEUE_SIZE;
        --count;
    }
}
static void reset_session(void)
{
    head = count = 0;
    memset(&parser, 0, sizeof(parser));
    input_pos = input_length = 0;
    ping_pending = false;
    next_seq = 1;
    peer_max = PODLINK_MAX_MESSAGE;
}
static void finish(void)
{
    usb_podlink_close();
    leased = false;
    state = PODLINK_CLOSED;
    reset_session();
}
static int close_event(struct podlink_message *m, unsigned int code)
{
    event_data[0] = code;
    event_data[1] = code >> 8;
    m->type = PODLINK_CLOSE;
    m->flags = 0;
    m->seq = 0;
    m->length = 2;
    m->data = event_data;
    return 1;
}
static void send_close(unsigned int code)
{
    unsigned char data[2] = {code, code >> 8};
    enqueue(PODLINK_CLOSE, next_seq++, data, sizeof(data), true);
}

bool podlink_open(void)
{
    if (leased || !usb_podlink_open())
        return false;
    leased = true;
    had_usb = release_requested = false;
    podlink_trace(1, 0);
    reset_session();
    state = PODLINK_WAIT_USB;
    return true;
}
enum podlink_connection podlink_state(void)
{
    return state;
}
unsigned int podlink_max_message(void)
{
    return peer_max;
}
int podlink_send(unsigned int type, const void *data, unsigned int length)
{
    if (type < PODLINK_TEXT || type > PODLINK_PONG || length > peer_max ||
        (length && !data))
        return PODLINK_INVALID;
    if (state != PODLINK_CONNECTED)
        return PODLINK_BUSY;
    int rc = enqueue(type, next_seq, data, length, false);
    if (rc == PODLINK_SENT)
        ++next_seq;
    return rc;
}
void podlink_close(void)
{
    if (!leased || release_requested)
        return;
    release_requested = true;
    podlink_trace(2, state);
    if (!had_usb)
    {
        finish();
        return;
    }
    send_close(1000);
    state = PODLINK_CLOSING;
    close_at = current_tick;
}

static int podlink_poll_impl(struct podlink_message *m)
{
    if (!m || !leased)
        return 0;
    uint32_t generation;
    bool connected = usb_serial_get_state(&generation);
    if (!connected || (had_usb && generation != transport_generation))
    {
        bool notify = had_usb;
        if (notify)
            podlink_trace(11, state);
        had_usb = false;
        reset_session();
        state = PODLINK_WAIT_USB;
        if (release_requested)
            finish();
        return notify ? close_event(m, 1006) : 0;
    }
    if (!had_usb)
    {
        had_usb = true;
        transport_generation = generation;
        podlink_trace(12, 0);
        reset_session();
        state = PODLINK_OPENING;
        last_rx = current_tick;
        enqueue(PODLINK_HELLO, next_seq++, hello, sizeof(hello), true);
    }
    pump();
    if (release_requested && TIME_AFTER(current_tick, close_at + HZ))
    {
        finish();
        return close_event(m, 1000);
    }
    if (state == PODLINK_CONNECTED)
    {
        if (ping_pending && TIME_AFTER(current_tick, ping_at + 15 * HZ))
        {
            send_close(1001);
            state = PODLINK_OPENING;
            ping_pending = false;
            return close_event(m, 1001);
        }
        if (!ping_pending && TIME_AFTER(current_tick, last_rx + 5 * HZ))
        {
            ping_seq = next_seq++;
            if (enqueue(PODLINK_PING, ping_seq, NULL, 0, true) == PODLINK_SENT)
            {
                ping_pending = true;
                ping_at = current_tick;
            }
        }
    }
    /* Do not consume a frame unless there is space to answer control traffic.
     */
    if (count)
        return 0;
    for (int work = 0; work < 64; ++work)
    {
        if (input_pos == input_length)
        {
            input_length = usb_serial_read(input, sizeof(input));
            input_pos = 0;
        }
        struct podlink_message incoming;
        unsigned int consumed;
        uint32_t started = USEC_TIMER;
        int rc = podlink_decode_bytes(&parser, input + input_pos,
                                      input_length - input_pos, &consumed,
                                      &incoming);
        uint32_t elapsed = (uint32_t)USEC_TIMER - started;
        if (rc == 1)
        {
            podlink_stats.decode_crc_us += elapsed;
            ++podlink_stats.decode_frames;
        }
        input_pos += consumed;
        if (rc == -2)
        {
            send_close(1009);
            state = PODLINK_OPENING;
            return close_event(m, 1009);
        }
        if (rc <= 0)
        {
            if (!rc && !input_length)
                break;
            continue; /* bad CRC/magic: scan for the next frame */
        }
        last_rx = current_tick;
        if (state == PODLINK_CONNECTED && incoming.type != PODLINK_OPEN &&
            incoming.type != PODLINK_HELLO && incoming.length > peer_max)
        {
            send_close(1009);
            state = PODLINK_OPENING;
            return close_event(m, 1009);
        }
        if (incoming.type == PODLINK_OPEN)
        {
            const unsigned char *d = incoming.data;
            if (release_requested)
                continue;
            if (incoming.length != sizeof(hello) || d[0] != PODLINK_VERSION ||
                d[1])
            {
                send_close(1002);
                state = PODLINK_OPENING;
                return close_event(m, 1002);
            }
            uint32_t limit = (uint32_t)d[2] | (uint32_t)d[3] << 8 |
                             (uint32_t)d[4] << 16 | (uint32_t)d[5] << 24;
            if (!limit)
            {
                send_close(1002);
                continue;
            }
            peer_max = MIN(limit, PODLINK_MAX_MESSAGE);
            unsigned char answer[6] = {
                PODLINK_VERSION, 0, peer_max & 255, (peer_max >> 8) & 255,
                (peer_max >> 16) & 255, (peer_max >> 24) & 255};
            enqueue(PODLINK_OPEN, incoming.seq, answer, sizeof(answer), true);
            state = PODLINK_CONNECTED;
            ping_pending = false;
        }
        else if (incoming.type == PODLINK_CLOSE)
        {
            podlink_trace(9, incoming.length >= 2
                                 ? incoming.data[0] | incoming.data[1] << 8
                                 : 0);
            if (incoming.length < 2)
            {
                send_close(1002);
                return close_event(m, 1002);
            }
            memcpy(event_data, incoming.data, incoming.length);
            *m = incoming;
            m->data = event_data;
            if (release_requested)
                finish();
            else
            {
                enqueue(PODLINK_CLOSE, incoming.seq, event_data,
                        incoming.length, true);
                state = PODLINK_OPENING;
                ping_pending = false;
            }
            return 1;
        }
        else if (state == PODLINK_CONNECTED)
        {
            if (incoming.type == PODLINK_PING)
                enqueue(PODLINK_PONG, incoming.seq, incoming.data,
                        incoming.length, true);
            else if (incoming.type == PODLINK_PONG && incoming.seq == ping_seq)
                ping_pending = false;
            if (incoming.type == PODLINK_TEXT ||
                incoming.type == PODLINK_BINARY ||
                incoming.type == PODLINK_PONG)
            {
                memcpy(event_data, incoming.data, incoming.length);
                *m = incoming;
                m->data = event_data;
                return 1;
            }
        }
        /* Once a response filled the control slot, stop before another frame.
         */
        if (count)
            break;
    }
    pump();
    return 0;
}

int podlink_poll(struct podlink_message *m)
{
    uint32_t started = USEC_TIMER;
    ++podlink_stats.polls;
    if (last_poll_us)
    {
        uint32_t gap = started - last_poll_us;
        if (gap > podlink_stats.poll_gap_us_max)
            podlink_stats.poll_gap_us_max = gap;
        if (gap >= 1000)
            ++podlink_stats.gaps_1ms;
        if (gap >= 5000)
            ++podlink_stats.gaps_5ms;
    }
    last_poll_us = started;
    int rc = podlink_poll_impl(m);
    uint32_t elapsed = (uint32_t)USEC_TIMER - started;
    podlink_stats.poll_us += elapsed;
    if (elapsed > podlink_stats.poll_us_max)
        podlink_stats.poll_us_max = elapsed;
    return rc;
}

void podlink_wait(int ticks)
{
    if (input_pos == input_length)
        usb_serial_wait(ticks);
}
