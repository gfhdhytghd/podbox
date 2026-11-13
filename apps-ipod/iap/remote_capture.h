/* Serial iAP capture. Copyright (C) 2026. GPL version 2+. */
#ifndef REMOTE_CAPTURE_H
#define REMOTE_CAPTURE_H
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

/* Keep the wire lengths, but store only a bounded diagnostic prefix. */
#define REMOTE_HISTORY 16
#define REMOTE_FRAME_MAX 128
#define REMOTE_BAD_LENGTH 1
#define REMOTE_BAD_CHECK 2
#define REMOTE_BAD_TIMEOUT 4
#define REMOTE_BAD_MASK (REMOTE_BAD_LENGTH | REMOTE_BAD_CHECK | REMOTE_BAD_TIMEOUT)
#define REMOTE_TRUNC 8
struct remote_frame {
    unsigned long sequence, tick, delta;
    unsigned int length, received, flags;
    unsigned char data[REMOTE_FRAME_MAX];
};
struct remote_capture {
    unsigned long frames, bad, previous_tick;
    struct remote_frame pending;
    struct remote_frame history[REMOTE_HISTORY];
};
static inline unsigned int remote_frame_stored(const struct remote_frame *f)
{
    return f->received < REMOTE_FRAME_MAX ? f->received : REMOTE_FRAME_MAX;
}
extern struct remote_capture iap_remote_capture;
extern bool remote_input_active;
/* Producer: serial ISR only. Reader: mask serial interrupts while copying. */
void remote_capture_begin(struct remote_capture *r, unsigned int length);
void remote_capture_byte(struct remote_capture *r, unsigned char byte);
void remote_capture_end(struct remote_capture *r, unsigned int flags,
                        unsigned long tick);
bool remote_capture_get(const struct remote_capture *r, unsigned long sequence,
                        struct remote_frame *out);
unsigned long remote_context_buttons(unsigned int len, const unsigned char *buf);
void remote_button_names(unsigned long buttons, char *out, size_t size);
void remote_frame_names(const struct remote_frame *f, char *out, size_t size);
bool dbg_remote_input(void);
struct remote_log_record;
unsigned int remote_log_format(const struct remote_log_record *record,
                                char *out, unsigned int size);
#define REMOTE_VOLUME_HISTORY 32
struct remote_volume_trace {
    unsigned long sequence, tick;
    unsigned char echo, target;
    signed char direction;
    char decision;
};
void remote_volume_log(unsigned long tick, unsigned char echo,
                       unsigned char target, int direction, char decision);
unsigned long remote_volume_count(void);
bool remote_volume_get(unsigned long sequence, struct remote_volume_trace *out);
#endif
