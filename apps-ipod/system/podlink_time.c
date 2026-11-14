/* RTC holds local civil time; transport and UI never share an RTC write.
 * GPL version 2 or later. */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include "config.h"
#include "kernel.h"
#include "podlink.h"
#include "timefuncs.h"
#include "rtc.h"
#include "remote_log.h"
#include "settings/settings.h"
#include "podlink_time.h"

#define CLOCK_DRIFT_SECONDS 2
#define CLOCK_RECORD_MAX_AGE (5 * HZ)

static bool clock_date_valid(const struct tm *t)
{
    static const int days[] = {31,28,31,30,31,30,31,31,30,31,30,31};
    /* Signed 32-bit log/scrobble timestamps must remain representable. */
    if (t->tm_year < 100 || t->tm_year > 137 || t->tm_mon < 0 ||
        t->tm_mon > 11 || t->tm_hour < 0 || t->tm_hour > 23 ||
        t->tm_min < 0 || t->tm_min > 59 || t->tm_sec < 0 || t->tm_sec > 59)
        return false;
    int max = days[t->tm_mon] + (t->tm_mon == 1 && t->tm_year % 4 == 0);
    return t->tm_mday >= 1 && t->tm_mday <= max;
}

bool podlink_time_decode(const char *record, struct tm *local, int *offset)
{
    size_t n = strlen(record);
    if (n < 27 || n > 31 || memcmp(record, "TS\t1\t", 5) ||
        record[9] != '-' || record[12] != '-' || record[15] != 'T' ||
        record[18] != ':' || record[21] != ':' || record[24] != '\t' ||
        (record[25] != '+' && record[25] != '-')) return false;
    for (size_t i = 5; i < n; i++)
    {
        if (i == 9 || i == 12 || i == 15 || i == 18 || i == 21 ||
            i == 24 || i == 25) continue;
        if (record[i] < '0' || record[i] > '9') return false;
    }
    int end = 0;
    memset(local, 0, sizeof(*local));
    if (sscanf(record + 5, "%d-%d-%dT%d:%d:%d\t%d%n", &local->tm_year,
               &local->tm_mon, &local->tm_mday, &local->tm_hour,
               &local->tm_min, &local->tm_sec, offset, &end) != 7 ||
        end != (int)n - 5 || *offset < -50400 || *offset > 50400)
        return false;
    local->tm_year -= 1900;
    local->tm_mon--;
    if (!clock_date_valid(local)) return false;
    time_t civil = mktime(local);
    gmtime_r(&civil, local);
    local->tm_isdst = -1;
    return true;
}

void podlink_time_sync(void)
{
    char record[PODLINK_TIME_RECORD], note[REMOTE_LOG_DATA];
    unsigned long received;
    if (!podlink_take_time(record, &received)) return;
    unsigned long age = (unsigned long)current_tick - received;
    struct tm local;
    int offset = 0;
    long drift = 0;
    const char *decision;
    if (!global_settings.sync_time_from_computer) decision = "ignored-off";
    else if (!podlink_time_decode(record, &local, &offset)) decision = "ignored-invalid";
    else if (age > CLOCK_RECORD_MAX_AGE) decision = "ignored-stale";
    else
    {
        time_t target = mktime(&local) + age / HZ;
        gmtime_r(&target, &local);
        struct tm old = {0};
        bool old_valid = rtc_read_datetime(&old) == 0 && clock_date_valid(&old);
        time_t before = old_valid ? mktime(&old) : 0;
        drift = old_valid ? target - before : 0;
        if (!clock_date_valid(&local)) decision = "ignored-range";
        else if (old_valid && drift >= -CLOCK_DRIFT_SECONDS &&
                 drift <= CLOCK_DRIFT_SECONDS) decision = "ignored-drift";
        else if (set_time(&local) != 0) decision = "ignored-rtc-error";
        else
        {
            /* Preserve the logs' remaining expiry duration across RTC steps.
             * Historical records keep their original civil timestamps. */
            if (global_status.debug_log_since)
            {
                int64_t since = old_valid ?
                    (int64_t)global_status.debug_log_since + drift : target;
                global_status.debug_log_since = since < 0 ? 0 :
                    since > target ? target : (int)since;
            }
            decision = "applied";
        }
    }
    snprintf(note, sizeof(note), "%s drift=%ld offset=%d age_ticks=%lu record=%.63s",
             decision, drift, offset, age, record);
    remote_log_event(REMOTE_LOG_TIME, current_tick, 0, 0, note, strlen(note) + 1);
}
