#ifndef CHARGING_TEST_KERNEL_H
#define CHARGING_TEST_KERNEL_H
#include "system.h"
#define HZ 100
#define TIME_BEFORE(a, b) ((long)((a) - (b)) < 0)
extern long current_tick;
struct event_queue;
struct timeout
{
    bool active;
};
static inline void timeout_register(struct timeout *t,
                                    int (*callback)(struct timeout *),
                                    int ticks, intptr_t data)
{
    (void)callback;
    (void)ticks;
    (void)data;
    t->active = true;
}
static inline void timeout_cancel(struct timeout *t)
{
    t->active = false;
}
#endif
