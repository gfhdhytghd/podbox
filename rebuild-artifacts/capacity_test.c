
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
#define MIN(a,b) ((a)<(b)?(a):(b))
#define MAX(a,b) ((a)>(b)?(a):(b))
#define ARRAYLEN(a) (sizeof(a)/sizeof((a)[0]))
#define logf(...) ((void)0)
#define debug_log(...) ((void)0)
#define yield() ((void)0)
#define HZ 100
#define TIME_BEFORE(a,b) ((long)((a)-(b))<0)

#define AA_STAMP_NONE 0
struct aa_stamp {unsigned key,stamp;};
static struct aa_stamp slots[2],*aa_stamps=slots;
static unsigned char bits[1],*aa_visited=bits;
static bool aa_table_full;
static struct aa_stamp *aa_slot(unsigned h){(void)h;return NULL;}

#line 821 "apps-ipod/metadata/art_cache.c"
static struct aa_stamp *aa_visit(unsigned int h)
{
    struct aa_stamp *s = aa_slot(h);
    unsigned int i;

    if (!s)
    {
        static struct aa_stamp overflow;
        if (!aa_table_full)
            debug_log(DEBUG_LOG_ARTCACHE, "seen table full: streaming remaining entries");
        aa_table_full = true;
        overflow.key = h;
        overflow.stamp = AA_STAMP_NONE;
        return &overflow;
    }
    i = s - aa_stamps;
    if (aa_visited[i / 8] & (1u << (i % 8)))
        return NULL;
    aa_visited[i / 8] |= 1u << (i % 8);
    return s;
}

int main(void){for(unsigned i=1;i<=20000;i++){struct aa_stamp*s=aa_visit(i);assert(s&&s->key==i);}assert(aa_table_full);puts("F14: exhausted dedup table streams 20000 remaining owners without skipping");return 0;}
