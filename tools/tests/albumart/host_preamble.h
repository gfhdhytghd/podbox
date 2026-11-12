
#define _GNU_SOURCE
#include <assert.h>
#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <dirent.h>
#include <errno.h>
#include <limits.h>
#define MAX_PATH 512
#define MIN(a, b) ((a) < (b) ? (a) : (b))
#define MAX(a, b) ((a) > (b) ? (a) : (b))
#define ARRAYLEN(a) (sizeof(a) / sizeof((a)[0]))
#define logf(...) ((void)0)
#define debug_log(...) ((void)0)
#define yield() ((void)0)
#define HZ 100
#define TIME_BEFORE(a, b) ((long)((a) - (b)) < 0)

#include <stddef.h>
#define strlcpy test_strlcpy
static size_t test_strlcpy(char *d, const char *s, size_t n)
{
    size_t l = strlen(s);
    if (n)
    {
        size_t k = MIN(l, n - 1);
        memcpy(d, s, k);
        d[k] = 0;
    }
    return l;
}

#define strmemccpy(d, s, n) strlcpy(d, s, n)
