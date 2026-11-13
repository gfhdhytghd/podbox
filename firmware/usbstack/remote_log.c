/* IRQ-safe producers; one resident PodLink consumer. GPL version 2+. */
#include "config.h"
#include "remote_log.h"
#include "system.h"
#include <limits.h>
#include <string.h>

static struct remote_log_record records[REMOTE_LOG_RECORDS], scratch;
static unsigned int first, used, lost, epoch;
static bool enabled, lost_turn;
static remote_log_formatter format;
_Static_assert(sizeof(records) + sizeof(scratch) + REMOTE_LOG_LINE < 8192,
               "remote log RAM budget");

void remote_log_enable(bool on, remote_log_formatter formatter)
{
    int level = disable_irq_save();
    enabled = on && formatter;
    format = formatter;
    first = used = lost = 0;
    lost_turn = true;
    epoch++;
    restore_irq(level);
}

bool remote_log_enabled(void) { return enabled; }
unsigned int remote_log_epoch(void) { return epoch; }

void remote_log_event(unsigned int kind, unsigned long tick,
                      unsigned long a, unsigned long b,
                      const void *data, unsigned int length)
{
    int level = disable_irq_save();
    if (enabled)
    {
        if (used == REMOTE_LOG_RECORDS || length > REMOTE_LOG_DATA)
        {
            if (lost != UINT_MAX) lost++;
        }
        else
        {
            struct remote_log_record *r =
                &records[(first + used++) % REMOTE_LOG_RECORDS];
            r->kind = kind;
            r->tick = tick;
            r->a = a;
            r->b = b;
            if (length) memcpy(r->data, data, length);
        }
    }
    restore_irq(level);
}

unsigned int remote_log_read(char *out, unsigned int size)
{
    int level = disable_irq_save();
    remote_log_formatter formatter = enabled ? format : NULL;
    bool have = false;
    if (formatter && lost && (lost_turn || !used))
    {
        scratch.kind = REMOTE_LOG_LOST;
        scratch.a = lost;
        lost = 0;
        lost_turn = false;
        have = true;
    }
    else if (formatter && used)
    {
        scratch = records[first];
        first = (first + 1) % REMOTE_LOG_RECORDS;
        used--;
        lost_turn = true;
        have = true;
    }
    restore_irq(level);
    /* No formatting, I/O, allocation or waiting in an input producer. */
    return have ? formatter(&scratch, out, size) : 0;
}
