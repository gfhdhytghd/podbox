#ifndef CHARGING_TEST_SYSTEM_H
#define CHARGING_TEST_SYSTEM_H
#define __SYSTEM_H__
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <assert.h>
#define MIN(a,b) ((a)<(b)?(a):(b))
#define ARRAYLEN(a) (sizeof(a)/sizeof((a)[0]))
#define PACKED __attribute__((packed))
#define INIT_ATTR
static inline int disable_irq_save(void) { return 0; }
static inline void restore_irq(int level) { (void)level; }
void trigger_cpu_boost(void);
void cancel_cpu_boost(void);
#endif
