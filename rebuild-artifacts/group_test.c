
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
#include <stddef.h>

enum {tag_album,tag_albumartist,tag_playcount,tag_lastplayed,tag_year,tag_discnumber,tag_artist,NUMTAGS};
#define FLAG_DELETED 1
#define TAGCACHE_BUFSZ 512
struct index_entry {long tag_seek[NUMTAGS];unsigned flag;};
struct ramcache_header {int album_count,artist_count;ptrdiff_t album_first,artist_first;struct index_entry indices[8];};
static struct {struct ramcache_header *hdr;} tcramcache;
#define tcrc_albums ((struct tagcache_album*)((char*)tcramcache.hdr+tcramcache.hdr->album_first))
#define tcrc_artists ((struct tagcache_artist*)((char*)tcramcache.hdr+tcramcache.hdr->artist_first))
#define TC_ALIGN_PTR(p,type,g) ((*(g)=0),(p))
static void strmemccpy(char*d,const char*s,size_t n){strlcpy(d,s,n);}
static unsigned art_cache_dir_hash(const char*p){unsigned h=2166136261u;for(;*p;p++)h=(h^(unsigned char)*p)*16777619u;return h;}
struct tagcache_album {
    int32_t  album_seek;
    int32_t  artist_seek;        /* tag_albumartist */
    int32_t  first, last;
    int32_t  playcount;
    int32_t  lastplayed;
    uint32_t art_hash;           /* registry key of the representative track */
    uint32_t artist_art_hash;    /* and of the folder above that */
    uint32_t file_art_hash;      /* representative track, also used by books */
    int16_t  year;               /* the latest of its tracks' */
    uint16_t tracks;
    uint16_t spoken;             /* of them, in a spoken genre */
    uint16_t podcast;            /* of those, in a podcast genre */
};struct tagcache_artist {
    int32_t  seek;               /* tag_albumartist */
    int32_t  playcount;
    int32_t  lastplayed;
    uint32_t art_hash;           /* its first album's artist_art_hash */
    uint16_t albums;
    uint16_t spoken_albums;      /* albums all of whose tracks are spoken */
    uint16_t podcast_albums;     /* of them, all of whose are podcasts */
    uint16_t pad;
};
#line 912 "apps-ipod/database/tagcache.c"
static int album_entry_cmp(const void *a, const void *b)
{
    const struct index_entry *x = &tcramcache.hdr->indices[*(const int32_t *)a];
    const struct index_entry *y = &tcramcache.hdr->indices[*(const int32_t *)b];

    if (x->tag_seek[tag_album] != y->tag_seek[tag_album])
        return x->tag_seek[tag_album] < y->tag_seek[tag_album] ? -1 : 1;
    if (x->tag_seek[tag_albumartist] != y->tag_seek[tag_albumartist])
        return x->tag_seek[tag_albumartist] < y->tag_seek[tag_albumartist]
               ? -1 : 1;
    return *(const int32_t *)a - *(const int32_t *)b;
}

#line 925 "apps-ipod/database/tagcache.c"
static int seek_cmp(const void *a, const void *b)
{
    int32_t x = *(const int32_t *)a, y = *(const int32_t *)b;

    return x < y ? -1 : x > y;
}

#line 932 "apps-ipod/database/tagcache.c"
static int album_row(long album_seek, long artist_seek)
{
    int lo = 0, hi = tcramcache.hdr->album_count - 1;

    while (lo <= hi)
    {
        int mid = (lo + hi) / 2;
        const struct tagcache_album *r = &tcrc_albums[mid];

        if (r->album_seek == album_seek && r->artist_seek == artist_seek)
            return mid;
        if (r->album_seek < album_seek
            || (r->album_seek == album_seek && r->artist_seek < artist_seek))
            lo = mid + 1;
        else
            hi = mid - 1;
    }
    return -1;
}

#line 970 "apps-ipod/database/tagcache.c"
static int entry_album_row(int idx_id)
{
    const struct index_entry *e = &tcramcache.hdr->indices[idx_id];

    return album_row(e->tag_seek[tag_album], e->tag_seek[tag_albumartist]);
}

#line 981 "apps-ipod/database/tagcache.c"
static bool albums_group(int32_t *scratch, int entries, char **pp,
                         ssize_t *bytesleft)
{
    struct ramcache_header *hdr = tcramcache.hdr;
    struct tagcache_album *rows;
    struct tagcache_artist *arts;
    ssize_t gap;
    char *p;
    int n = 0, nrows = 0, nart = 0;

    hdr->album_count = 0;
    hdr->artist_count = 0;

    for (int i = 0; i < entries; i++)
    {
        if (!(hdr->indices[i].flag & FLAG_DELETED))
            scratch[n++] = i;
    }
    qsort(scratch, n, sizeof(*scratch), album_entry_cmp);
    for (int i = 0; i < n; i++)
    {
        const struct index_entry *a = i ? &hdr->indices[scratch[i - 1]]
                                        : NULL;
        const struct index_entry *b = &hdr->indices[scratch[i]];

        if (!a || a->tag_seek[tag_album] != b->tag_seek[tag_album]
            || a->tag_seek[tag_albumartist] != b->tag_seek[tag_albumartist])
            nrows++;
    }

    p = TC_ALIGN_PTR(*pp, struct tagcache_album, &gap);
    if (*bytesleft < gap + (ssize_t)(nrows * sizeof(*rows)))
        return false;
    rows = (struct tagcache_album *)p;
    memset(rows, 0, nrows * sizeof(*rows));

    for (int i = 0, r = -1; i < n; i++)
    {
        const struct index_entry *e = &hdr->indices[scratch[i]];
        long plays = e->tag_seek[tag_playcount];

        if (r < 0 || rows[r].album_seek != e->tag_seek[tag_album]
            || rows[r].artist_seek != e->tag_seek[tag_albumartist])
        {
            r++;
            rows[r].album_seek = e->tag_seek[tag_album];
            rows[r].artist_seek = e->tag_seek[tag_albumartist];
            rows[r].first = scratch[i];
        }
        rows[r].last = scratch[i];
        rows[r].tracks++;
        if (e->tag_seek[tag_year] > rows[r].year)
            rows[r].year = e->tag_seek[tag_year];
        if (plays > 0)
        {
            rows[r].playcount += plays;
            if (e->tag_seek[tag_lastplayed] > rows[r].lastplayed)
                rows[r].lastplayed = e->tag_seek[tag_lastplayed];
        }
    }

    /* The artists: the distinct album artists of the albums */
    for (int r = 0; r < nrows; r++)
        scratch[r] = rows[r].artist_seek;
    qsort(scratch, nrows, sizeof(*scratch), seek_cmp);
    for (int r = 0; r < nrows; r++)
    {
        if (r == 0 || scratch[r] != scratch[nart - 1])
            scratch[nart++] = scratch[r];
    }

    p += nrows * sizeof(*rows);
    p = TC_ALIGN_PTR(p, struct tagcache_artist, &gap);
    if (*bytesleft < (ssize_t)((char *)p - *pp)
                     + (ssize_t)(nart * sizeof(*arts)))
        return false;
    arts = (struct tagcache_artist *)p;
    memset(arts, 0, nart * sizeof(*arts));
    for (int a = 0; a < nart; a++)
    {
        arts[a].seek = scratch[a];
    }
    p += nart * sizeof(*arts);

    hdr->album_first = (char *)rows - (char *)hdr;
    hdr->album_count = nrows;
    hdr->artist_first = (char *)arts - (char *)hdr;
    hdr->artist_count = nart;
    *bytesleft -= p - *pp;
    *pp = p;
    return true;
}

#line 1075 "apps-ipod/database/tagcache.c"
static void albums_note_path(int idx_id, const char *filename)
{
    static char dir[TAGCACHE_BUFSZ];
    struct tagcache_album *r;
    char *sep;
    int n;

    if (tcramcache.hdr->album_count == 0
        || (n = entry_album_row(idx_id)) < 0)
        return;
    r = &tcrc_albums[n];
    if (r->first != idx_id)
        return;

    r->file_art_hash = art_cache_dir_hash(filename);
    strmemccpy(dir, filename, sizeof(dir));
    sep = strrchr(dir, '/');
    if (!sep || sep == dir)
        return;
    *sep = '\0';                     /* track file -> album folder */
    r->art_hash = r->file_art_hash;
    sep = strrchr(dir, '/');
    if (!sep || sep == dir)
        return;
    *sep = '\0';                     /* album -> artist folder */
    r->artist_art_hash = art_cache_dir_hash(dir);
}

int main(void){
 unsigned long pool[2048]={0};tcramcache.hdr=(struct ramcache_header*)pool;
 long tags[5][NUMTAGS]={{10,100,0,0,2020,1,501},{10,100,0,0,2020,2,502},{11,100,0,0,2021,1,501},{10,200,0,0,2020,1,501},{0,0,0,0,0,0,0}};
 for(int i=0;i<5;i++)memcpy(tcramcache.hdr->indices[i].tag_seek,tags[i],sizeof tags[i]);
 char *p=(char*)(tcramcache.hdr+1);ssize_t left=sizeof(pool)-sizeof(*tcramcache.hdr);int32_t scratch[8];
 assert(albums_group(scratch,5,&p,&left));assert(tcramcache.hdr->album_count==4);
 assert(entry_album_row(0)==entry_album_row(1));assert(entry_album_row(0)!=entry_album_row(2));assert(entry_album_row(0)!=entry_album_row(3));
 albums_note_path(0,"/music/mixed/a.mp3");albums_note_path(1,"/music/mixed/disc2.mp3");albums_note_path(2,"/music/mixed/b.mp3");
 assert(tcrc_albums[entry_album_row(0)].art_hash==art_cache_dir_hash("/music/mixed/a.mp3"));
 assert(tcrc_albums[entry_album_row(0)].art_hash!=tcrc_albums[entry_album_row(2)].art_hash);
 puts("F02: multi-disc compilation grouped by Album+Album Artist; different albums in one directory keep distinct representative artwork; absent tags accepted");return 0;
}
