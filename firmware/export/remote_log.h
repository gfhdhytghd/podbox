/* Bounded remote diagnostics, formatted only by PodLink. GPL version 2+. */
#ifndef REMOTE_LOG_H
#define REMOTE_LOG_H
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

#define REMOTE_LOG_RECORDS 32
#define REMOTE_LOG_DATA 192
#define REMOTE_LOG_LINE 768
enum remote_log_kind {
    REMOTE_LOG_RAW, REMOTE_LOG_VOL, REMOTE_LOG_HELD,
    REMOTE_LOG_TAP, REMOTE_LOG_DOUBLE, REMOTE_LOG_HOLD,
    REMOTE_LOG_EP0, REMOTE_LOG_PAGE, REMOTE_LOG_LOST, REMOTE_LOG_VOLUME_KEY,
    REMOTE_LOG_TRIPLE
};
struct remote_log_record {
    unsigned long tick, a, b;
    unsigned int kind;
    unsigned char data[REMOTE_LOG_DATA];
};
typedef unsigned int (*remote_log_formatter)(const struct remote_log_record *,
                                             char *, unsigned int);
#if defined(HAVE_USB_PODLINK) && !defined(BOOTLOADER) && \
    !defined(REMOTE_LOG_DISABLE)
void remote_log_enable(bool on, remote_log_formatter formatter);
bool remote_log_enabled(void);
unsigned int remote_log_epoch(void);
void remote_log_event(unsigned int kind, unsigned long tick,
                      unsigned long a, unsigned long b,
                      const void *data, unsigned int length);
unsigned int remote_log_read(char *out, unsigned int size);
#else
static inline void remote_log_enable(bool on, remote_log_formatter formatter)
{ (void)on; (void)formatter; }
static inline bool remote_log_enabled(void) { return false; }
static inline unsigned int remote_log_epoch(void) { return 0; }
static inline void remote_log_event(unsigned int kind, unsigned long tick,
                                    unsigned long a, unsigned long b,
                                    const void *data, unsigned int length)
{ (void)kind; (void)tick; (void)a; (void)b; (void)data; (void)length; }
static inline unsigned int remote_log_read(char *out, unsigned int size)
{ (void)out; (void)size; return 0; }
#endif
#endif
