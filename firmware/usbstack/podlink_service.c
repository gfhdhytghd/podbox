/* Cable-owned PodLink and bounded foreground inbox. GPL version 2 or later. */
#include "config.h"
#include "podlink_session.h"
#include "usb.h"
#include "kernel.h"
#include "thread.h"
#include "remote_log.h"
#include <string.h>

#define INBOX 16
#define INBOX_BYTES (32 * 1024)
struct client_frame {
    struct podlink_message message;
    unsigned int offset;
};
static struct client_frame inbox[INBOX];
static unsigned char inbox_bytes[INBOX_BYTES], delivered[PODLINK_MAX_MESSAGE];
static unsigned int first, used, byte_first, byte_used;
static struct mutex service_mutex;
static unsigned char client, announced = 255;
static bool initialized, running, started;
static uint32_t generation, wire_revision;
static unsigned char welcome[128];
static unsigned int welcome_length;
static char log_line[REMOTE_LOG_LINE];
static unsigned int log_length, log_epoch;
static void (*background_message)(const struct podlink_message *);
static long service_stack[DEFAULT_STACK_SIZE * 2 / sizeof(long)];
_Static_assert(sizeof(inbox) + sizeof(inbox_bytes) + sizeof(delivered) +
               sizeof(service_stack) + sizeof(welcome) <
               64 * 1024, "resident PodLink RAM budget");

static void invalidate(void)
{
    first = used = byte_first = byte_used = 0;
    ++generation;
    welcome_length = 0;
    announced = 255;
    if (background_message && client != 1)
    {
        struct podlink_message lost = {.type = PODLINK_CLOSE};
        background_message(&lost);
    }
}

static void store_message(const struct podlink_message *m)
{
    struct client_frame *f = &inbox[(first + used++) % INBOX];
    f->message = *m;
    f->offset = (byte_first + byte_used) % INBOX_BYTES;
    unsigned int n = MIN(m->length, INBOX_BYTES - f->offset);
    if (m->length)
    {
        memcpy(inbox_bytes + f->offset, m->data, n);
        memcpy(inbox_bytes, m->data + n, m->length - n);
    }
    byte_used += m->length;
}

static void take_message(struct podlink_message *m)
{
    struct client_frame *f = &inbox[first];
    *m = f->message;
    unsigned int n = MIN(m->length, INBOX_BYTES - f->offset);
    memcpy(delivered, inbox_bytes + f->offset, n);
    memcpy(delivered + n, inbox_bytes, m->length - n);
    m->data = delivered;
    byte_first = (byte_first + m->length) % INBOX_BYTES;
    byte_used -= m->length;
    first = (first + 1) % INBOX;
    used--;
}

static void service_step(void)
{
    mutex_lock(&service_mutex);
    if (!running || !usb_podlink_active())
    {
        if (started)
        {
            podlink_session_abort();
            started = false;
            invalidate();
        }
        mutex_unlock(&service_mutex);
        return;
    }
    if (!started)
    {
        started = podlink_session_open();
        announced = 255;
    }
    for (int work = 0; started && work < 8; work++)
    {
        struct podlink_message m;
        int have = podlink_session_poll(&m);
        uint32_t revision = podlink_session_revision();
        if (revision != wire_revision)
        {
            wire_revision = revision;
            invalidate();
        }
        if (!have) break;
        if (m.type == PODLINK_PONG) continue;
        if (m.type == PODLINK_CLOSE)
            invalidate();
        if (m.type == PODLINK_TEXT && m.length >= 3 &&
            m.length <= sizeof(welcome) && !memcmp(m.data, "HR\t", 3))
        {
            memcpy(welcome, m.data, m.length);
            welcome_length = m.length;
        }
        if (client)
        {
            if (used == INBOX || m.length > INBOX_BYTES - byte_used)
            {
                /* Keep transport/voice keepalives alive through a blocked UI.
                 * Memory pressure drops the picture, not the cable session. */
                unsigned int saved_welcome = welcome_length;
                invalidate();
                welcome_length = saved_welcome;
                struct podlink_message lost = {.type = PODLINK_CLOSE};
                store_message(&lost);
                if (welcome_length)
                {
                    struct podlink_message greeting = {
                        .type = PODLINK_TEXT, .length = welcome_length,
                        .data = welcome};
                    store_message(&greeting);
                }
            }
            store_message(&m);
        }
        else if (background_message)
            background_message(&m);
    }
    if (started && podlink_session_state() == PODLINK_CONNECTED &&
        announced != client && podlink_session_max_message() >= 5)
    {
        unsigned char notice[] = {'P', 'L', 'F', 'G', client};
        if (podlink_session_send(PODLINK_PING, notice, sizeof(notice)) ==
            PODLINK_SENT)
            announced = client;
    }
    /* Out-of-band diagnostic traffic never attaches a foreground client.
     * At most one TEXT per worker step, after control/incoming traffic. */
    if (!remote_log_enabled() || log_epoch != remote_log_epoch())
    {
        log_length = 0;
        log_epoch = remote_log_epoch();
    }
    if (started && remote_log_enabled() &&
        podlink_session_state() == PODLINK_CONNECTED &&
        podlink_session_max_message() >= 10)
    {
        if (!log_length)
            log_length = remote_log_read(log_line, sizeof(log_line));
        if (log_length > podlink_session_max_message())
        {
            memcpy(log_line, "LOG LOST 1", 10);
            log_length = 10;
        }
        if (log_length && podlink_session_send(PODLINK_TEXT, log_line,
                                               log_length) == PODLINK_SENT)
            log_length = 0;
    }
    mutex_unlock(&service_mutex);
}

#ifndef PODLINK_SERVICE_TEST
static void service_thread(void)
{
    while (true)
    {
        service_step();
        sleep(MAX(1, HZ / 50));
    }
}
#endif

void podlink_init(void)
{
    if (initialized) return;
    mutex_init(&service_mutex);
    initialized = running = true;
#ifndef PODLINK_SERVICE_TEST
    if (!create_thread(service_thread, service_stack, sizeof(service_stack), 0,
                       "podlink" IF_PRIO(, PRIORITY_BACKGROUND) IF_COP(, CPU)))
        running = false;
#else
    (void)service_stack;
#endif
}

static bool attach(unsigned char kind,
                    void (*background)(const struct podlink_message *))
{
    if (!initialized) return false;
    mutex_lock(&service_mutex);
    bool ok = running && !client && usb_podlink_open();
    if (ok)
    {
        client = kind;
        first = used = byte_first = byte_used = 0;
        if (background) background_message = background;
        if (kind == 1 && welcome_length)
        {
            struct podlink_message greeting = {
                .type = PODLINK_TEXT, .length = welcome_length,
                .data = welcome};
            store_message(&greeting);
        }
    }
    mutex_unlock(&service_mutex);
    return ok;
}

bool podlink_open(void)
{
    return attach(2, NULL);
}

bool podlink_open_agents(void (*background)(const struct podlink_message *))
{
    return attach(1, background);
}

void podlink_close(void)
{
    if (!initialized) return;
    mutex_lock(&service_mutex);
    /* Deliver queued list deltas before enabling the background consumer. */
    if (client == 1 && background_message)
        while (used)
        {
            struct podlink_message m;
            take_message(&m);
            background_message(&m);
        }
    client = 0;
    first = used = byte_first = byte_used = 0;
    mutex_unlock(&service_mutex);
}

enum podlink_connection podlink_state(void)
{
    if (!initialized) return PODLINK_CLOSED;
    mutex_lock(&service_mutex);
    enum podlink_connection result = !client ? PODLINK_CLOSED :
        started ? podlink_session_state() : PODLINK_WAIT_USB;
    mutex_unlock(&service_mutex);
    return result;
}

uint32_t podlink_generation(void)
{
    if (!initialized) return 0;
    mutex_lock(&service_mutex);
    uint32_t result = generation;
    mutex_unlock(&service_mutex);
    return result;
}

unsigned int podlink_max_message(void)
{
    if (!initialized) return PODLINK_MAX_MESSAGE;
    mutex_lock(&service_mutex);
    unsigned int result = podlink_session_max_message();
    mutex_unlock(&service_mutex);
    return result;
}

int podlink_send(unsigned int type, const void *data, unsigned int length)
{
    if (!initialized) return PODLINK_BUSY;
    mutex_lock(&service_mutex);
    int result = client && started ?
        podlink_session_send(type, data, length) : PODLINK_BUSY;
    mutex_unlock(&service_mutex);
    return result;
}

int podlink_poll(struct podlink_message *message)
{
    if (!initialized || !message) return 0;
    mutex_lock(&service_mutex);
    int have = client && used;
    if (have)
    {
        take_message(message);
    }
    mutex_unlock(&service_mutex);
    return have;
}

void podlink_wait(int ticks)
{
    /* The transport worker owns serial_wake. UI waits remain bounded. */
    sleep(MIN(ticks, MAX(1, HZ / 50)));
}

void podlink_shutdown(void)
{
    if (!initialized) return;
    mutex_lock(&service_mutex);
    if (started)
    {
        podlink_session_close();
        podlink_session_abort();
    }
    running = started = false;
    invalidate();
    mutex_unlock(&service_mutex);
}
