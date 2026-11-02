/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "podlink.h"
#include <string.h>

static uint32_t get32(const unsigned char *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 |
           (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}
static void put32(unsigned char *p, uint32_t n)
{
    for (int i = 0; i < 4; ++i) p[i] = n >> (8 * i);
}
uint32_t podlink_crc32(const unsigned char *data, unsigned int length)
{
    uint32_t crc = ~0u;
    while (length--)
    {
        crc ^= *data++;
        for (int i = 0; i < 8; ++i)
            crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1)));
    }
    return ~crc;
}
unsigned int podlink_encode(unsigned char *dest, unsigned int type, uint32_t seq,
                            const void *data, unsigned int length)
{
    if (type < PODLINK_OPEN || type > PODLINK_HELLO ||
        length > PODLINK_MAX_MESSAGE || (length && !data)) return 0;
    memcpy(dest, "PLNK", 4);
    dest[4] = type;
    dest[5] = dest[6] = dest[7] = 0;
    put32(dest + 8, seq);
    put32(dest + 12, length);
    if (length) memcpy(dest + PODLINK_HEADER, data, length);
    put32(dest + PODLINK_HEADER + length,
          podlink_crc32(dest + 4, PODLINK_HEADER - 4 + length));
    return PODLINK_HEADER + length + 4;
}
static void discard(struct podlink_parser *p)
{
    --p->used;
    memmove(p->bytes, p->bytes + 1, p->used);
}
int podlink_decode(struct podlink_parser *p, unsigned char byte,
                   struct podlink_message *m)
{
    /* Completed payload remains readable until this next call. */
    if (p->used == PODLINK_FRAME_MAX) p->used = 0;
    p->bytes[p->used++] = byte;
    while (p->used >= 4 && memcmp(p->bytes, "PLNK", 4)) discard(p);
    if (p->used < PODLINK_HEADER) return 0;
    unsigned char *b = p->bytes;
    if (b[4] < PODLINK_OPEN || b[4] > PODLINK_HELLO || b[5] || b[6] || b[7])
    { discard(p); return -1; }
    uint32_t length = get32(b + 12);
    if (length > PODLINK_MAX_MESSAGE) { discard(p); return -2; }
    if (p->used < PODLINK_HEADER + length + 4) return 0;
    if (get32(b + PODLINK_HEADER + length) !=
        podlink_crc32(b + 4, PODLINK_HEADER - 4 + length))
    { discard(p); return -1; }
    m->type = b[4]; m->flags = b[5]; m->seq = get32(b + 8);
    m->length = length; m->data = b + PODLINK_HEADER;
    p->used = 0;
    return 1;
}
