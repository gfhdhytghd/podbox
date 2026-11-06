
#define _GNU_SOURCE
#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <ctype.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#define MAX_PATH 260
#define MIN(a,b) ((a)<(b)?(a):(b))
#define MAX(a,b) ((a)>(b)?(a):(b))
#define ARRAYLEN(a) (sizeof(a)/sizeof((a)[0]))
#define logf(...) ((void)0)
#define debug_log(...) ((void)0)
#define yield() ((void)0)
enum {AA_OFF, AA_PREFER_EMBEDDED, AA_PREFER_IMAGE_FILE, AA_PREFER_CACHE};
enum {AA_TYPE_UNKNOWN, AA_TYPE_JPG, AA_TYPE_PNG};
#define AA_FLAG_ID3_UNSYNC 0x100
struct mp3_albumart {off_t pos; unsigned long size; int type;};
struct mp3entry {char path[MAX_PATH]; char *album, *albumartist, *artist; int discnum; bool has_embedded_albumart; struct mp3_albumart albumart;};
struct dim {int width,height;};
static bool image_embedded_usable(int t) {return t==AA_TYPE_JPG || t==AA_TYPE_PNG;}

#line 29 "firmware/common/strmemccpy.c"
char * strmemccpy(char *dst, const char *src, size_t len)
{
    char * ret = (char *)memccpy(dst, src, '\0', len);
    if (ret == NULL && len > 0)
    {
        dst[len - 1] = '\0';
    }
    return ret;
}

#line 29 "firmware/common/strlcpy.c"
size_t strlcpy(char *dst, const char *src, size_t siz)
{
    /* Copy as many bytes as will fit */
    char *d = strmemccpy(dst, src, siz);
    if (d)
        return (d - dst - 1); /* count does not include NUL */

    /* Not enough room in dst, traverse rest of src */
    return(siz + strlen(src+siz)); /* count does not include NUL */
}

#include "../apps-ipod/metadata/art_sizes.h"
#define ART_CACHE_MAGIC 0x5441
#define ART_CACHE_FORMAT_VERSION 8
#define FB_DATA_SZ 2
typedef uint16_t fb_data;
struct bitmap {int width,height; void*data;};
struct art_cache_header {uint16_t magic,version,width,height,layout,pad;};
struct img_filter {int dummy;};
#define RGB_UNPACK_RED(p) (((p)>>11)<<3)
#define RGB_UNPACK_GREEN(p) ((((p)>>5)&63)<<2)
#define RGB_UNPACK_BLUE(p) (((p)&31)<<3)
#define LCD_RGBPACK(r,g,b) ((((r)>>3)<<11)|(((g)>>2)<<5)|((b)>>3))
#define AAT_BAND_MAX 8
static fb_data aat_band[AAT_BAND_MAX*ART_CACHE_MAX_DIM];
static int aat_mutex;
#define mutex_lock(x) ((void)0)
#define mutex_unlock(x) ((void)0)
#define img_filter_apply_banded(a,b,c,d) ((void)0)

#line 291 "apps-ipod/metadata/art_cache.c"
int art_cache_load_aat(int fd, struct bitmap *bm, int max_size,
                       const struct img_filter *filter)
{
    struct art_cache_header hdr;
    int dw = bm->width, dh = bm->height;
    int sw, sh, dy, dx;
    int rc = dw * dh * FB_DATA_SZ;

    lseek(fd, 0, SEEK_SET);
    if (read(fd, &hdr, sizeof(hdr)) != (ssize_t)sizeof(hdr) ||
        hdr.magic != ART_CACHE_MAGIC || hdr.version != ART_CACHE_FORMAT_VERSION)
        return -1;

    /* This reads the file a band of rows at a time, so it can only make sense
     * of a row-major one. Refused rather than transposed: no caller wants a
     * column-major source, and silently reading one the wrong way round would
     * produce a plausible-looking but mirrored thumbnail. */
    if (hdr.layout != AA_ROWS)
        return -1;

    sw = hdr.width;
    sh = hdr.height;
    /* Both dimensions, not just the one that sizes aat_band[]. sh drives
     * the row arithmetic below, and a header claiming a huge height
     * overflows dy * sh, which can leave sy1 behind sy0 -- `rows` is then
     * negative, the clamp above it only tests the high side, and read()
     * takes it as a size_t. 300 is the largest the cache ever writes.
     * A source too elongated to crop is stored non-square, and is refused:
     * scaling it to the size asked for would stretch it. */
    if (sw <= 0 || sh <= 0 || sw > ART_CACHE_MAX_DIM ||
        sh > ART_CACHE_MAX_DIM || sw != sh ||
        dw <= 0 || dh <= 0 || dw > sw || dh > sh)   /* downscale only */
        return -1;
    if ((size_t)dw * dh * FB_DATA_SZ > (size_t)max_size)
        return -1;

    mutex_lock(&aat_mutex);
    for (dy = 0; dy < dh; dy++)
    {
        int sy0 = dy * sh / dh;
        int sy1 = (dy + 1) * sh / dh;
        int rows = sy1 - sy0;
        fb_data *out = (fb_data *)bm->data + (size_t)dy * dw;

        if (rows > AAT_BAND_MAX)
            rows = AAT_BAND_MAX;    /* extreme ratios: sample the top of the band */
        if (read(fd, aat_band, (size_t)rows * sw * FB_DATA_SZ) !=
            (ssize_t)((size_t)rows * sw * FB_DATA_SZ))
        {
            rc = -1;
            break;
        }
        /* skip the remainder of a clamped band so the file stays aligned */
        if (sy1 - sy0 > rows)
            lseek(fd, (off_t)(sy1 - sy0 - rows) * sw * FB_DATA_SZ, SEEK_CUR);

        for (dx = 0; dx < dw; dx++)
        {
            int sx0 = dx * sw / dw;
            int sx1 = (dx + 1) * sw / dw;
            unsigned r = 0, g = 0, b = 0, n = 0;
            int sy, sx;

            for (sy = 0; sy < rows; sy++)
                for (sx = sx0; sx < sx1; sx++)
                {
                    fb_data p = aat_band[sy * sw + sx];
                    r += RGB_UNPACK_RED(p);
                    g += RGB_UNPACK_GREEN(p);
                    b += RGB_UNPACK_BLUE(p);
                    n++;
                }
            out[dx] = n ? LCD_RGBPACK(r / n, g / n, b / n) : 0;
        }
    }
    mutex_unlock(&aat_mutex);

    if (rc > 0 && filter)
        img_filter_apply_banded((fb_data *)bm->data, dw, dh, filter);

    return rc;
}

#line 241 "apps-ipod/metadata/art_cache.c"
static unsigned int aa_hash(const char *str)
{
    const unsigned int p = 16777619;
    unsigned int hash = 0x811C9DC5;

    if (!str)
        return 0;

    while (*str)
        hash = (hash ^ (unsigned char)*str++) * p;
    hash += hash << 13;
    hash ^= hash >> 7;
    hash += hash << 3;
    hash ^= hash >> 17;
    hash += hash << 5;
    return hash;
}

struct aa_src {const char*path;off_t emb_pos;unsigned long emb_size;int emb_flags;};
struct aa_stamp {unsigned int key,stamp;};
#define AA_STAMP_NONE 0u
static const char *aa_miss,*aa_miss_src;
static char aa_check_path[MAX_PATH],aa_out_path[MAX_PATH];
static int generated,removed;
static bool file_exists(const char*p) {return true;}
static bool aa_thumbs_exist(unsigned int h) {return true;}
static void aa_remove_thumbs(unsigned int h) {removed++;}
static void aa_cache_path(char*a,int b,int c,unsigned int d) {strcpy(a,"test.aat");}
static void aa_sizes_largest_first(int*o) {for(int i=0;i<ART_CACHE_NUM_SIZES;i++)o[i]=i;}
static bool aa_check_abort(void) {return false;}
static bool tagcache_is_busy(void) {return false;}
static void aa_generate_size(const struct aa_src*a,int b,unsigned int c,const char*d,void*e,size_t f) {generated++;}

#line 1357 "apps-ipod/metadata/art_cache.c"
static bool aa_fill(const struct aa_src *src, unsigned int stamp,
                    unsigned int dh, struct aa_stamp *slot,
                    void *workbuf, size_t worksz, bool *aborted)
{
    bool all_exist = aa_thumbs_exist(dh);
    int order[ART_CACHE_NUM_SIZES];
    int i, s;

    /* Stamped before generating, so an interrupted pass leaves a part set
     * that the next one finishes rather than throws away again. Every size
     * goes on a change, because fast build derives the small ones from the
     * large and must not find an old one to derive from. */
    if (slot->stamp == AA_STAMP_NONE)
        slot->stamp = stamp;
    else if (slot->stamp != stamp)
    {
        debug_log(DEBUG_LOG_ARTCACHE, "art changed: %s", src->path);
        aa_remove_thumbs(dh);
        slot->stamp = stamp;
        all_exist = false;
    }
    if (all_exist)
        return true;

    /* Largest first, so a fast build has the big thumbnail on disk to derive
     * the smaller ones from. */
    aa_sizes_largest_first(order);

    for (i = 0; i < ART_CACHE_NUM_SIZES; i++)
    {
        s = order[i];
        aa_cache_path(aa_check_path, sizeof(aa_check_path), s, dh);
        if (file_exists(aa_check_path))
            continue;
        /* Re-check right before a (potentially slow) decode so a USB
         * connection is acknowledged with minimal delay. */
        if (aa_check_abort() || tagcache_is_busy())
        {
            *aborted = true;
            return false;
        }
        aa_cache_path(aa_out_path, sizeof(aa_out_path), s, dh);
        aa_generate_size(src, s, dh, aa_out_path, workbuf, worksz);
        yield();
    }
    /* An image that would not decode leaves the folder bare, and is counted
     * and listed as one with no art. */
    if (aa_thumbs_exist(dh))
        return true;
    aa_miss = "would not decode";
    aa_miss_src = src->path;
    return false;
}

int main(void){
    const char*paths[]={"/Music/Album056789","/music/Album056789","/Music/Album056789/"};
    for(int i=0;i<3;i++)printf("hash(%s)=%08x\n",paths[i],aa_hash(paths[i]));
    printf("collision /Music/Album-549131099299=%08x /Music/Album-874266555771=%08x\n",aa_hash("/Music/Album-549131099299"),aa_hash("/Music/Album-874266555771"));
    struct aa_src src={.path="changed-track.mp3",.emb_pos=100,.emb_size=1000};
    struct aa_stamp slot={99,1};bool aborted=false;
    bool ok=aa_fill(&src,1,99,&slot,NULL,0,&aborted);
    printf("changed embedded same stamp: ok=%d generated=%d removed=%d\n",ok,generated,removed);
    slot.stamp=123;ok=aa_fill(&src,123,99,&slot,NULL,0,&aborted);
    printf("corrupt but existing files same stamp: ok=%d generated=%d removed=%d\n",ok,generated,removed);
    for(int rectangular=0;rectangular<2;rectangular++){
        struct art_cache_header h={ART_CACHE_MAGIC,8,300,rectangular?100:300,AA_ROWS,0};
        int fd=open("sample.aat",O_CREAT|O_TRUNC|O_RDWR,0600);write(fd,&h,sizeof h);
        uint16_t row[300]={0};for(int i=0;i<h.height;i++)write(fd,row,sizeof row);
        uint16_t output[56*56];struct bitmap b={56,56,output};
        printf("aat %ux%u ->56x56 rc=%d\n",h.width,h.height,art_cache_load_aat(fd,&b,sizeof output,NULL));close(fd);
    }
    return 0;
}
