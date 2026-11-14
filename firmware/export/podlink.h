/***************************************************************************
 *             __________               __   ___.
 *   Open      \______   \ ____   ____ |  | _\_ |__   _______  ___
 *   Source     |       _//  _ \_/ ___\|  |/ /| __ \ /  _ \  \/  /
 *   Jukebox    |    |   (  <_> )  \___|    < | \_\ (  <_> > <  <
 *   Firmware   |____|_  /\____/ \___  >__|_ \|___  /\____/__/\_ \
 *                     \/            \/     \/    \/            \/
 *
 * PodLink USB messaging
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 *
 * This software is distributed on an "AS IS" basis, WITHOUT WARRANTY OF ANY
 * KIND, either express or implied.
 *
 ****************************************************************************/
#ifndef PODLINK_H
#define PODLINK_H
#include <stdbool.h>
#include <stdint.h>

#define PODLINK_VERSION 1
#define PODLINK_MAX_MESSAGE 16384
#define PODLINK_HEADER 16
#define PODLINK_FRAME_MAX (PODLINK_HEADER + PODLINK_MAX_MESSAGE + 4)
enum podlink_type
{
    PODLINK_OPEN = 1,
    PODLINK_TEXT,
    PODLINK_BINARY,
    PODLINK_PING,
    PODLINK_PONG,
    PODLINK_CLOSE,
    PODLINK_HELLO
};
enum podlink_connection
{
    PODLINK_CLOSED,
    PODLINK_WAIT_USB,
    PODLINK_OPENING,
    PODLINK_CONNECTED,
    PODLINK_CLOSING
};
enum podlink_result
{
    PODLINK_BUSY = 0,
    PODLINK_SENT = 1,
    PODLINK_INVALID = -1
};
struct podlink_message
{
    uint8_t type, flags;
    uint32_t seq, length;
    /* Valid until the next poll. CLOSE begins with a little-endian u16 code. */
    const unsigned char *data;
};

/* One foreground client attaches to a cable-owned resident transport.
 * close() detaches only. Payload remains valid until the client's next poll.
 * Background callback runs on the resident thread and must not call this API. */
/* Resident worker retains only the newest time record; UI consumes it.
 * No RTC access on the transport thread. Disconnect invalidates the mailbox. */
#define PODLINK_TIME_RECORD 64
bool podlink_take_time(char *record, unsigned long *received_tick);
void podlink_init(void);
void podlink_shutdown(void);
bool podlink_open_agents(void (*background)(const struct podlink_message *));
uint32_t podlink_generation(void);
bool podlink_open(void);
int podlink_send(unsigned int type, const void *data, unsigned int length);
int podlink_poll(struct podlink_message *message);
/* Maximum outgoing payload negotiated with the peer. */
unsigned int podlink_max_message(void);
enum podlink_connection podlink_state(void);
/* Resident host session, independent of the foreground client. No mutexes;
 * safe from IRQ-masked input callbacks. Also checks current configured USB. */
bool podlink_host_connected(void);
void podlink_close(void);
/* Optional screen idle wait; wakes on transport progress, bounded by ticks. */
void podlink_wait(int ticks);

/* A resident data consumer bypasses the foreground inbox. Callbacks run
 * under the service mutex and must not call the PodLink API. */
struct podlink_resident
{
    bool (*receive)(const struct podlink_message *message);
    unsigned int (*next)(char *record, unsigned int size);
    void (*reset)(void);
};
void podlink_set_resident(const struct podlink_resident *consumer);

/* Portable wire codec, also built by the host tests. */
struct podlink_parser
{
    unsigned char bytes[PODLINK_FRAME_MAX];
    unsigned int used, consumed;
};
uint32_t podlink_crc32(const unsigned char *data, unsigned int length);
unsigned int podlink_encode(unsigned char *dest, unsigned int type,
                            uint32_t seq, const void *data,
                            unsigned int length);
/* Feed a chunk, stopping at a message/error. Always report consumed bytes.
 * A result can consume zero bytes when draining buffered recovery data.
 * Call with length zero to drain without input. Payload lasts until next call.
 */
int podlink_decode_bytes(struct podlink_parser *parser,
                         const unsigned char *data, unsigned int length,
                         unsigned int *consumed,
                         struct podlink_message *message);
/* Feed one byte. 1=message, 0=more, -1=bad CRC/header, -2=oversize. */
int podlink_decode(struct podlink_parser *parser, unsigned char byte,
                   struct podlink_message *message);
#endif
