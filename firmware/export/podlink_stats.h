/* RAM-only transport instrumentation. SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef PODLINK_STATS_H
#define PODLINK_STATS_H
#include <stddef.h>
#include <stdint.h>
struct podlink_stats {
    uint32_t ui_turn_us_max;
    uint32_t polls, poll_us, poll_us_max, poll_gap_us_max, gaps_1ms, gaps_5ms;
    uint32_t read_calls, read_bytes, empty_reads, write_calls, write_bytes;
    uint32_t rx_xfers, rx_bytes, rx_max, tx_xfers, tx_bytes, tx_max;
    uint32_t rx_full, write_busy, queue_busy, encodes, encode_us;
    uint32_t decode_frames, decode_crc_us;
};
extern struct podlink_stats podlink_stats;
void podlink_trace(unsigned int event, unsigned int data);
void podlink_stats_reset(void);
int podlink_stats_json(char *dest, size_t size);
#endif
