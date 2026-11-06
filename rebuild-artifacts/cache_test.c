
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
#define CACHE_ROOT "/home/wilf/data/PodJS/podbox.wt-int/rebuild-artifacts/cache-test"

#include <time.h>
#include <utime.h>
#include "../apps-ipod/metadata/art_sizes.h"
#define LIB_ART_DIR CACHE_ROOT
#define THUMBCACHE_DIR CACHE_ROOT "/v9"
#define ART_CACHE_MAGIC 0x5441u
#define ART_CACHE_FORMAT_VERSION 9
#define ART_CACHE_IDENTITY_MAX 512
#define AA_QUOTA_BYTES (1024ul*1024)
#define FB_DATA_SZ 2
#define ATTR_DIRECTORY 0x10
#define DBTOOL 1
#define AA_EVENT_OFFER 1
typedef uint16_t fb_data;
struct bitmap {int width,height;unsigned char *data;};
struct img_filter {int unused;};
enum art_cache_state {ART_MISSING,ART_LOADING,ART_READY,ART_FAILED};
static enum art_cache_state aa_state;
static void aa_record_state(unsigned key,enum art_cache_state state){(void)key;(void)state;}
static unsigned aa_generation,aa_active_id;
static bool aa_io_failed;
static char aa_active_identity[ART_CACHE_IDENTITY_MAX],aa_tmp_path[MAX_PATH];
struct mutex {int unused;};
static struct mutex identity_mutex,aat_mutex;
static bool identity_ready;
static long current_tick;
static int posted,art_cache_task;
static bool aa_offer_posted;
static struct {char path[MAX_PATH];} aa_offer;
static void bg_task_post(int*a,int b){(void)a;(void)b;posted++;}
static void mutex_init(struct mutex*m){(void)m;}
static void mutex_lock(struct mutex*m){(void)m;}
static void mutex_unlock(struct mutex*m){(void)m;}
static int fail_write,fail_close,fail_rename,stopped,stop_after_write;
static bool aa_check_abort(void){return stopped;}
static ssize_t checked_write(int fd,const void*p,size_t n){if(fail_write){errno=ENOSPC;return -1;}ssize_t rc=write(fd,p,n);if(stop_after_write)stopped=1;return rc;}
static int checked_close(int fd){int r=close(fd);return fail_close?-1:r;}
static int checked_rename(const char*a,const char*b){if(fail_rename)return -1;return rename(a,b);}
#define write checked_write
#define close checked_close
#define rename checked_rename
static off_t ffilesize(int fd){struct stat s;return fstat(fd,&s)?-1:s.st_size;}
static int make_dir(const char*p){return mkdir(p,0700);}
#define mkdir make_dir
static bool file_exists(const char*p){return access(p,F_OK)==0;}
struct dirinfo {off_t size;time_t mtime;unsigned attribute;};
static struct dirinfo dir_get_info(DIR*d,struct dirent*e){struct stat s;fstatat(dirfd(d),e->d_name,&s,0);return (struct dirinfo){s.st_size,s.st_mtime,S_ISDIR(s.st_mode)?ATTR_DIRECTORY:0};}
static void img_filter_apply_banded(fb_data*p,int w,int h,const struct img_filter*f){(void)p;(void)w;(void)h;(void)f;}
#define RGB_UNPACK_RED(p) (((p)>>11)<<3)
#define RGB_UNPACK_GREEN(p) ((((p)>>5)&63)<<2)
#define RGB_UNPACK_BLUE(p) (((p)&31)<<3)
#define LCD_RGBPACK(r,g,b) ((((r)>>3)<<11)|(((g)>>2)<<5)|((b)>>3))
struct dim {int width,height;};
static int recalc_dimension(struct dim*d,struct dim*s){int h=s->height*d->width/s->width;if(h<=d->height)d->height=MAX(1,h);else d->width=MAX(1,s->width*d->height/s->height);return 0;}
#define AAT_BAND_MAX 8
static fb_data aat_band[AAT_BAND_MAX*ART_CACHE_MAX_DIM];
struct art_cache_header
{
    uint16_t magic;
    uint16_t version;
    uint16_t width;
    uint16_t height;
    uint16_t layout;   /* enum art_layout, as stored */
    uint16_t pad;
    uint32_t source_id;
    uint32_t checksum;
    uint32_t fit;
    uint32_t pixel_format;
    char identity[ART_CACHE_IDENTITY_MAX];
};
#line 45 "apps-ipod/database/path_key.c"
const char *path_key_strip(const char *path)
{
    const char *p = path;

    if (path == NULL || p[0] != '/' || p[1] != '<')
        return path;

    for (p += 2; *p != '\0' && *p != '>' && *p != '/'; p++)
        ;

    return (p[0] == '>' && p[1] == '/') ? p + 1 : path;
}

#line 26 "apps-ipod/metadata/albumart.c"
bool albumart_normalize(const char *path, char *out, size_t cap)
{
    size_t n = 0;
    path = path_key_strip(path);
    if (!path || !*path || cap < 2) return false;
    while (*path)
    {
        while (*path == '/') path++;
        if (!*path) break;
        const char *end = strchr(path, '/');
        size_t len = end ? (size_t)(end - path) : strlen(path);
        if (len == 1 && path[0] == '.') { path += len; continue; }
        if (len == 2 && path[0] == '.' && path[1] == '.')
        {
            while (n && out[--n] != '/') ;
            path += len;
            continue;
        }
        if (n + len + 1 >= cap) return false;
        out[n++] = '/';
        for (size_t i = 0; i < len; i++)
        {
            unsigned char c = path[i];
            out[n++] = c >= 'A' && c <= 'Z' ? c + 'a' - 'A' : c;
        }
        path += len;
    }
    if (!n) out[n++] = '/';
    out[n] = 0;
    return true;
}

#line 260 "apps-ipod/metadata/art_cache.c"
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

#line 310 "apps-ipod/metadata/art_cache.c"
static uint32_t aa_checksum(uint32_t value, const void *data, size_t n)
{
    const unsigned char *p = data;
    while (n--) value = (value ^ *p++) * 16777619u;
    return value;
}

#line 317 "apps-ipod/metadata/art_cache.c"
bool art_cache_read_header(int fd, struct art_cache_header *h)
{
    if (lseek(fd, 0, SEEK_SET) != 0 ||
        read(fd, h, sizeof(*h)) != sizeof(*h) ||
        h->magic != ART_CACHE_MAGIC || h->version != ART_CACHE_FORMAT_VERSION ||
        !h->width || !h->height || h->width > ART_CACHE_MAX_DIM ||
        h->height > ART_CACHE_MAX_DIM || h->layout > AA_COLUMNS ||
        h->pixel_format != 565 || h->fit > AA_FIT_COVER ||
        !memchr(h->identity, 0, sizeof(h->identity))) return false;
    size_t bytes = (size_t)h->width * h->height * FB_DATA_SZ;
    if (ffilesize(fd) != (off_t)(sizeof(*h) + bytes)) return false;
    if (h->source_id)
    {
        char path[MAX_PATH], identity[ART_CACHE_IDENTITY_MAX];
        snprintf(path, sizeof(path), THUMBCACHE_DIR "/keys/%08x.key", (unsigned)h->source_id);
        int keyfd = open(path, O_RDONLY);
        if (keyfd < 0) return false;
        ssize_t n = read(keyfd, identity, sizeof(identity));
        close(keyfd);
        if (n != (ssize_t)strlen(h->identity) + 1 ||
            memcmp(identity, h->identity, n)) return false;
    }
    unsigned char buf[512];
    uint32_t expected = h->checksum;
    h->checksum = 0;
    uint32_t checksum = aa_checksum(2166136261u, h, sizeof(*h));
    h->checksum = expected;
    while (bytes)
    {
        size_t n = MIN(bytes, sizeof(buf));
        if (read(fd, buf, n) != (ssize_t)n) return false;
        checksum = aa_checksum(checksum, buf, n);
        bytes -= n;
    }
    return checksum == h->checksum &&
           lseek(fd, sizeof(*h), SEEK_SET) == sizeof(*h);
}

#line 448 "apps-ipod/metadata/art_cache.c"
static void aa_identity_dirs(void)
{
    mkdir(LIB_ART_DIR);
    mkdir(THUMBCACHE_DIR);
    mkdir(THUMBCACHE_DIR "/keys");
    mkdir(THUMBCACHE_DIR "/refs");
}

#line 457 "apps-ipod/metadata/art_cache.c"
unsigned int art_cache_dir_hash(const char *path)
{
    char key[ART_CACHE_IDENTITY_MAX], stored[ART_CACHE_IDENTITY_MAX];
    char filename[MAX_PATH];
    if (!albumart_normalize(path, key, sizeof(key))) return 0;
    if (!identity_ready)
    {
        mutex_init(&identity_mutex);
        identity_ready = true;
    }
    mutex_lock(&identity_mutex);
    aa_identity_dirs();
    unsigned h = aa_hash(key);
    for (unsigned probe = 0; probe < 1024; probe++, h++)
    {
        if (!h) h++;
        snprintf(filename, sizeof(filename), THUMBCACHE_DIR "/keys/%08x.key", h);
        int fd = open(filename, O_RDONLY);
        if (fd >= 0)
        {
            ssize_t n = read(fd, stored, sizeof(stored));
            close(fd);
            if (n == (ssize_t)strlen(key) + 1 && !memcmp(stored, key, n))
            { mutex_unlock(&identity_mutex); return h; }
            continue;
        }
        fd = open(filename, O_WRONLY | O_CREAT | O_EXCL, 0666);
        if (fd < 0) break;
        size_t n = strlen(key) + 1;
        bool ok = write(fd, key, n) == (ssize_t)n;
        if (close(fd) < 0) ok = false;
        if (!ok) { remove(filename); break; }
        mutex_unlock(&identity_mutex);
        return h;
    }
    aa_io_failed = true;
    aa_state = ART_FAILED;
    mutex_unlock(&identity_mutex);
    return 0;
}

#line 278 "apps-ipod/metadata/art_cache.c"
static void aa_cache_path(char *out, int out_len, int size_index,
                          unsigned int arthash)
{
    snprintf(out, out_len, THUMBCACHE_DIR "/%s/%08x.aat",
             art_sizes[size_index].name, arthash);
}

#line 498 "apps-ipod/metadata/art_cache.c"
static unsigned aa_referenced(unsigned owner)
{
    char path[MAX_PATH];
    uint32_t record[4];
    snprintf(path, sizeof(path), THUMBCACHE_DIR "/refs/%08x.ref", owner);
    int fd = open(path, O_RDONLY);
    bool ok = fd >= 0 && read(fd, record, sizeof(record)) == sizeof(record)
              && record[0] == ART_CACHE_FORMAT_VERSION && record[1] == owner &&
              record[3] == aa_checksum(2166136261u, record, 3 * sizeof(*record));
    if (fd >= 0) close(fd);
    return ok ? record[2] : 0;
}

#line 511 "apps-ipod/metadata/art_cache.c"
static bool aa_reference(unsigned owner, unsigned pixels)
{
    if (aa_referenced(owner) == pixels) return true;
    char path[MAX_PATH], tmp[MAX_PATH];
    uint32_t record[] = { ART_CACHE_FORMAT_VERSION, owner, pixels, 0 };
    record[3] = aa_checksum(2166136261u, record, 3 * sizeof(*record));
    snprintf(path, sizeof(path), THUMBCACHE_DIR "/refs/%08x.ref", owner);
    snprintf(tmp, sizeof(tmp), "%s.new", path);
    int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC, 0666);
    if (fd < 0) return false;
    bool ok = write(fd, record, sizeof(record)) == sizeof(record);
    if (close(fd) < 0) ok = false;
    if (aa_check_abort()) ok = false;
    if (ok) ok = rename(tmp, path) == 0;
    if (!ok) { remove(tmp); aa_io_failed = true; aa_state = ART_FAILED; }
    else
    {
        aa_generation++;
#ifndef DBTOOL
        playback_art_changed(owner);
#endif
    }
    return ok;
}

#line 1067 "apps-ipod/metadata/art_cache.c"
static bool aa_make_room(size_t incoming)
{
    for (;;)
    {
        uint64_t used = 0;
        time_t oldest = 0;
        bool victim = false;
        char oldest_path[MAX_PATH] = "";
        for (int s = 0; s < ART_CACHE_NUM_SIZES; s++)
        {
            char path[MAX_PATH];
            snprintf(path, sizeof(path), THUMBCACHE_DIR "/%s", art_sizes[s].name);
            DIR *dir = opendir(path);
            if (!dir) continue;
            struct dirent *entry;
            while ((entry = readdir(dir)))
            {
                if (entry->d_name[0] == '.') continue;
                struct dirinfo info = dir_get_info(dir, entry);
                if (info.attribute & ATTR_DIRECTORY) continue;
                used += info.size;
                unsigned id = strtoul(entry->d_name, NULL, 16);
                if (id == aa_active_id || !strcmp(entry->d_name, "fallback.aat"))
                    continue;
                if (!victim || info.mtime < oldest)
                {
                    oldest = info.mtime;
                    victim = true;
                    snprintf(oldest_path, sizeof(oldest_path), "%s/%s", path,
                             entry->d_name);
                }
            }
            closedir(dir);
        }
        if (used + incoming <= AA_QUOTA_BYTES) return true;
        if (!victim || aa_check_abort() || remove(oldest_path) < 0) return false;
        aa_generation++;
    }
}

#line 1109 "apps-ipod/metadata/art_cache.c"
static bool aa_write_aat(const char *out_path, struct bitmap *bm, int size_index)
{
    struct art_cache_header hdr = {0};
    size_t bytes;
    bool ok;
    enum art_layout layout = AA_ROWS;
    int fd;

    /* Transposed here, once, for a size that wants columns -- and only when the
     * thumbnail is square, because that is an in-place swap where a rectangle
     * would need a second full-size buffer. A COVER thumbnail is square unless
     * its source was too elongated to crop, so the occasional rectangle is
     * stored by rows and the header says which it is. */
    if (art_sizes[size_index].layout == AA_COLUMNS
        && bm->width == bm->height)
    {
        fb_data *px = (fb_data *)bm->data;
        int n = bm->width, r, c;

        for (r = 0; r < n; r++)
            for (c = r + 1; c < n; c++)
            {
                fb_data t = px[r * n + c];
                px[r * n + c] = px[c * n + r];
                px[c * n + r] = t;
            }

        layout = AA_COLUMNS;
    }

    /* Written whole under another name and renamed over the thumbnail, so a
     * reader never sees one part-written. ".new" is not ".aat", so the orphan
     * sweep passes it by. */
    if (strlcpy(aa_tmp_path, out_path, sizeof(aa_tmp_path))
            >= sizeof(aa_tmp_path))
        return false;
    strcpy(aa_tmp_path + strlen(aa_tmp_path) - 4, ".new");
    if (!aa_make_room(sizeof(hdr) + (size_t)bm->width * bm->height * FB_DATA_SZ))
    { aa_io_failed = true; aa_state = ART_FAILED; return false; }
    fd = open(aa_tmp_path, O_WRONLY | O_CREAT | O_TRUNC, 0666);
    if (fd < 0) { aa_io_failed = true; aa_state = ART_FAILED; return false; }

    hdr.magic = ART_CACHE_MAGIC;
    hdr.version = ART_CACHE_FORMAT_VERSION;
    hdr.width = bm->width;
    hdr.height = bm->height;
    hdr.layout = layout;
    hdr.pad = 0;
    hdr.source_id = aa_active_id;
    hdr.fit = art_sizes[size_index].fit;
    hdr.pixel_format = 565;
    strlcpy(hdr.identity, aa_active_identity, sizeof(hdr.identity));
    bytes = (size_t)bm->width * bm->height * FB_DATA_SZ;

    hdr.checksum = aa_checksum(aa_checksum(2166136261u, &hdr, sizeof(hdr)),
                               bm->data, bytes);
    ok = (write(fd, &hdr, sizeof(hdr)) == (ssize_t)sizeof(hdr)) &&
         (write(fd, bm->data, bytes) == (ssize_t)bytes);
    if (close(fd) < 0) ok = false;
    if (ok)
    {
        fd = open(aa_tmp_path, O_RDONLY);
        struct art_cache_header check;
        ok = fd >= 0 && art_cache_read_header(fd, &check);
        if (fd >= 0) close(fd);
    }
    if (aa_check_abort()) ok = false;
    if (ok) ok = rename(aa_tmp_path, out_path) == 0;
    if (!ok && !aa_check_abort()) { aa_io_failed = true; aa_state = ART_FAILED; }
    else if (ok) { aa_generation++; aa_state = ART_READY; }
    if (!ok)
        remove(aa_tmp_path);
    return ok;
}

#line 355 "apps-ipod/metadata/art_cache.c"
int art_cache_load_aat(int fd, struct bitmap *bm, int max_size,
                       const struct img_filter *filter)
{
    struct art_cache_header hdr;
    int dw = bm->width, dh = bm->height;
    int sw, sh, dy, dx;
    int rc = dw * dh * FB_DATA_SZ;

    if (!art_cache_read_header(fd, &hdr))
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
        sh > ART_CACHE_MAX_DIM ||
        dw <= 0 || dh <= 0)   /* downscale only */
        return -1;
    struct dim source = { sw, sh }, target = { dw, dh };
    recalc_dimension(&target, &source);
    dw = bm->width = MAX(1, target.width);
    dh = bm->height = MAX(1, target.height);
    rc = dw * dh * FB_DATA_SZ;
    if ((size_t)dw * dh * FB_DATA_SZ > (size_t)max_size)
        return -1;

    mutex_lock(&aat_mutex);
    for (dy = 0; dy < dh; dy++)
    {
        int sy0 = dy * sh / dh;
        int sy1 = (dy + 1) * sh / dh;
        if (sy1 <= sy0) sy1 = sy0 + 1;
        int rows = sy1 - sy0;
        if (lseek(fd, sizeof(hdr) + (off_t)sy0 * sw * FB_DATA_SZ,
                  SEEK_SET) < 0) { rc = -1; break; }
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
            if (sx1 <= sx0) sx1 = sx0 + 1;
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

#line 1977 "apps-ipod/metadata/art_cache.c"
static void aa_request_owner(unsigned owner)
{
    static struct { unsigned key, generation; long until; } misses[32];
    static unsigned next;
    if (!owner) return;
    for (unsigned i = 0; i < ARRAYLEN(misses); i++)
        if (misses[i].key == owner && misses[i].generation == aa_generation &&
            TIME_BEFORE(current_tick, misses[i].until)) return;
    misses[next].key = owner;
    misses[next].generation = aa_generation;
    misses[next].until = current_tick + 5 * HZ;
    next = (next + 1) % ARRAYLEN(misses);
    char path[MAX_PATH], key[ART_CACHE_IDENTITY_MAX];
    snprintf(path, sizeof(path), THUMBCACHE_DIR "/keys/%08x.key", owner);
    int fd = open(path, O_RDONLY);
    if (fd < 0) return;
    ssize_t n = read(fd, key, sizeof(key));
    close(fd);
    if (n < 2 || key[n - 1] || strchr(key, '@') || strlen(key) >= MAX_PATH)
        return;
    aa_record_state(owner, ART_LOADING);
    strlcpy(aa_offer.path, key, sizeof(aa_offer.path));
    if (!aa_offer_posted)
    {
        aa_offer_posted = true;
        bg_task_post(&art_cache_task, AA_EVENT_OFFER);
    }
}

static void dirs(void){aa_identity_dirs();for(int s=0;s<ART_CACHE_NUM_SIZES;s++){char p[MAX_PATH];snprintf(p,sizeof p,THUMBCACHE_DIR "/%s",art_sizes[s].name);mkdir(p);}}
int main(int argc,char**argv){
 assert(argc==3);dirs();
 unsigned a=art_cache_dir_hash(argv[1]),b=art_cache_dir_hash(argv[2]);
 assert(a && b && a!=b);assert(art_cache_dir_hash("/Music/./Case/../ALBUM/")==art_cache_dir_hash("/music/album"));
 puts("F13: full-key collision and normalized-path identity");
 strcpy(aa_active_identity,"/test/source@revision");aa_active_id=art_cache_dir_hash(aa_active_identity);
 fb_data *pixels=calloc(300*300,sizeof(fb_data));assert(pixels);
 for(int i=0;i<300*100;i++)pixels[i]=0xf800;
 struct bitmap bm={300,100,(unsigned char*)pixels};char path[MAX_PATH];aa_cache_path(path,sizeof path,2,aa_active_id);
 assert(aa_write_aat(path,&bm,2));assert(aa_generation==1);
 int fd=open(path,O_RDONLY);struct art_cache_header h;assert(art_cache_read_header(fd,&h));
 uint16_t output[56*56];struct bitmap target={56,56,(unsigned char*)output};
 assert(art_cache_load_aat(fd,&target,sizeof output,NULL)>0);assert(target.width==56&&target.height==18);close(fd);
 puts("F07: 300x100 cache contains into 56x18 without stretching");
 fd=open(path,O_WRONLY);lseek(fd,sizeof h+10,SEEK_SET);uint8_t corrupt=7;assert(write(fd,&corrupt,1)==1);close(fd);
 fd=open(path,O_RDONLY);assert(!art_cache_read_header(fd,&h));close(fd);
 assert(aa_write_aat(path,&bm,2));fd=open(path,O_RDONLY);assert(art_cache_read_header(fd,&h));close(fd);
 fd=open(path,O_WRONLY);ftruncate(fd,30);close(fd);fd=open(path,O_RDONLY);assert(!art_cache_read_header(fd,&h));close(fd);
 assert(aa_write_aat(path,&bm,2));puts("F06: checksum corruption and truncation detected and rebuilt");
 unsigned owner=art_cache_dir_hash("/music/track.mp3");assert(aa_reference(owner,aa_active_id));assert(aa_referenced(owner)==aa_active_id);
 unsigned gen=aa_generation;assert(aa_reference(owner,aa_active_id));assert(gen==aa_generation);
 unsigned other=art_cache_dir_hash("/other/source");assert(aa_reference(owner,other));assert(aa_generation>gen);
 puts("F09: publication generation changes only for changed references");
 char failed[MAX_PATH];snprintf(failed,sizeof failed,THUMBCACHE_DIR "/wps/failed.aat");
 stop_after_write=1;assert(!aa_write_aat(failed,&bm,2));stop_after_write=stopped=0;assert(!file_exists(failed));
 stopped=1;assert(!aa_write_aat(failed,&bm,2));stopped=0;assert(!file_exists(failed));
 fail_write=1;assert(!aa_write_aat(failed,&bm,2));fail_write=0;assert(!file_exists(failed));assert(aa_io_failed&&aa_state==ART_FAILED);
 fail_close=1;assert(!aa_write_aat(failed,&bm,2));fail_close=0;assert(!file_exists(failed));
 fail_rename=1;assert(!aa_write_aat(failed,&bm,2));fail_rename=0;assert(!file_exists(failed));
 puts("F06/F14: disk-full, close and rename errors never publish READY");
 char big[MAX_PATH];snprintf(big,sizeof big,THUMBCACHE_DIR "/list/abcdef12.aat");fd=open(big,O_CREAT|O_RDWR,0600);assert(fd>=0);assert(!ftruncate(fd,AA_QUOTA_BYTES));close(fd);
 assert(aa_make_room(100));assert(!file_exists(big));puts("F14: byte quota evicts an older derivative");
 aa_offer_posted=false;posted=0;aa_request_owner(owner);assert(posted==1);aa_offer_posted=false;aa_request_owner(owner);assert(posted==1);
 current_tick+=6*HZ;aa_request_owner(owner);assert(posted==2);aa_generation++;aa_offer_posted=false;aa_request_owner(owner);assert(posted==3);
 puts("negative request lifetime expires; new generation invalidates misses");
 free(pixels);return 0;
}
