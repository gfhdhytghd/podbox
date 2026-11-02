#include "config.h"
#include "kernel.h"
#include <stdio.h>
void yield(void);
#include "../../apps-ipod/screens/system/podlink_screen.c"
long current_tick;
void podlink_stats_reset(void)
{
}
int podlink_stats_json(char *dest, size_t size)
{
    (void)dest;
    (void)size;
    return 0;
}
unsigned long core_available(void)
{
    return 0;
}
int battery_level(void)
{
    return 50;
}
const char *rbversion = "review-harness";
static void command(const char *s)
{
    struct podlink_message m = {.type = PODLINK_TEXT,
                                .data = (const unsigned char *)s,
                                .length = strlen(s)};
    message(&m);
}

int main(void)
{
    const char *invalid[] = {
        "BENCH RX 0",        "BENCH RX x",
        "BENCH RX 16777217", "BENCH RX 999999999999999999999",
        "BENCH TX 12x",      "BENCH TX 0"};
    const char *start[] = {"BENCH RX 8192", "BENCH TX 8192"};
    for (unsigned int d = 0; d < 2; ++d)
    {
        command(start[d]);
        assert(goal == 8192);
        int active = operation;
        offset = 4096;
        errors = 3;
        for (unsigned int i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i)
        {
            command(invalid[i]);
            assert(reply_type == PODLINK_TEXT);
            assert(!memcmp(reply, "ERR benchmark size", reply_len));
            assert((int)operation == active && goal == 8192);
            assert(offset == 4096 && errors == 3 && goal - offset == 4096);
        }
        /* A valid command explicitly replaces either active transfer. */
        command("BENCH TX 4096");
        assert(operation == UPLOAD && goal == 4096 && offset == 0 &&
               errors == 0);
    }
    puts("PASS: rejected BENCH commands preserve upload/download state");
    return 0;
}
