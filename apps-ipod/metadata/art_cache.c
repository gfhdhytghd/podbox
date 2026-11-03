/***************************************************************************
 * GNU General Public License (version 2+)
 *
 * Versioned artwork derivatives. The background worker owns decode scratch;
 * consumers read immutable files after verified atomic publication. Owner
 * references and source/revision identities have separate full-key records.
 * The byte quota evicts old derivative files; missing readers queue a retry.
 ****************************************************************************/

#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include "config.h"
#include "system/library_files.h"
#include "database/libfile.h"


#include "system.h"
#include "kernel.h"
#include "core_alloc.h"
#include "string-extra.h"
#include "file.h"
#include "dir.h"
#include "dircache.h"               /* whether a stamp check reads the disk */
#include "pathfuncs.h"
#include "rbpaths.h"
#include "metadata.h"
#include "albumart.h"
#include "art_cache.h"
#include "art_sizes.h"
#include "settings/settings.h"  /* global_settings.art_cache_* */
#include "system/debug_log.h"
#include "database/tagcache.h"
#include "database/db_spoken.h"   /* which albums are books */
#include "system/bg_task.h"         /* the caching pass runs as one */
#include "files/book_index.h"       /* the books the pass visits */
#include "files/zip_member.h"       /* a deflated cover, inflated */
#include "files/path_list.h"        /* the "found nothing" lists */
#include "system/strutil.h"         /* is_disc_folder() */
#include "lcd.h"
#include "draw/bmp.h"
#include "draw/img_filter.h"
#include "bitmaps/podboxnoart.h" /* shared "no art" placeholder for aa_ensure_fallback */
#include "draw/image_load.h"
#include "events.h"
#include "system/appevents.h"
#include "audio.h"
#include "audio/playback.h"
#include "storage.h"
#include "cpu.h"

/* Define LOGF_ENABLE to enable logf output in this file */
/*#define LOGF_ENABLE*/
#include "logf.h"

#define THUMBCACHE_DIR LIB_ART_DIR "/v10"
/* Folders a pass found no art for, one path per line, for the health screen
 * (screens/system/art_health.c). Written to a .new and renamed only when a
 * pass finishes, so an aborted pass leaves the previous -- complete -- list
 * standing rather than a partial one that reads as "the rest are fine". */
#define AA_NOART_ALBUMS  LIB_NO_ART_ALBUMS_FILE
#define AA_NOART_ARTISTS LIB_NO_ART_ARTISTS_FILE
/* Per-owner visit stamps and the library marks covered by a full pass.
 * A record with owner key 0 holds aa_marks_key for those marks. Source
 * revisions are checked from content; standalone offers publish references
 * and typed requests separately from this pass table. */
#define AA_STAMP_FILE   THUMBCACHE_DIR "/stamps.dat"
#define AA_STAMP_MAGIC  LIB_STAMPS_MAGIC

/* On-disk thumbnail format (struct art_cache_header + native pixels, in the
 * order the header names) is declared in art_cache.h so consumers can read it.
 * A magic/version lets a future format change be detected per file rather than
 * needing a global cache wipe. */

/* Bounded owner deduplication. Overflow entries are streamed without dedup. */
#define AA_SEEN_SLOTS 16384  /* power of two */

/* An owner's key and source stamp. A bit per table slot records whether
 * this pass has visited that owner. */
struct aa_stamp
{
    unsigned int key;    /* owner key; 0 marks an empty slot */
    unsigned int stamp;
};
#define AA_TABLE_BYTES (AA_SEEN_SLOTS * sizeof(struct aa_stamp) \
                        + AA_SEEN_SLOTS / 8)

/* Sentinel stamps for an unvisited owner, embedded art and a missing source.
 * Every visited owner is resolved again; these do not bypass source hashing. */
#define AA_STAMP_NONE     0u
#define AA_STAMP_EMBEDDED 1u
#define AA_STAMP_NO_EMBED 3u

/* tagcache_entry_key() of the last entry the marks cover, folded to 32 bits;
 * 0 for none. Read with the table. */
static unsigned int aa_marks_key;

static unsigned int aa_fold_key(uint64_t key)
{
    return (unsigned int)(key ^ key >> 32);
}

/* The table, while a pass holds it; NULL otherwise. */
static struct aa_stamp *aa_stamps;
static unsigned char *aa_visited;

static volatile bool cache_busy;

/* Counts every change to a thumbnail: see art_cache_generation(). */
static volatile unsigned int aa_generation;
static enum art_cache_state aa_state;
static char aa_active_identity[ART_CACHE_IDENTITY_MAX];
static unsigned aa_active_id;
struct aa_entry_state {
    unsigned key, generation;
    enum art_cache_state state;
};
static struct aa_entry_state aa_states[32];
static unsigned aa_state_next;

static void aa_record_state(unsigned key, enum art_cache_state state)
{
    unsigned at = aa_state_next;
    for (unsigned i = 0; i < ARRAYLEN(aa_states); i++)
        if (aa_states[i].key == key) { at = i; break; }
    aa_states[at] = (struct aa_entry_state){ key, aa_generation, state };
    if (at == aa_state_next) aa_state_next = (at + 1) % ARRAYLEN(aa_states);
}

enum art_cache_state art_cache_entry_status(unsigned key)
{
    for (unsigned i = 0; i < ARRAYLEN(aa_states); i++)
        if (aa_states[i].key == key && aa_states[i].generation == aa_generation)
            return aa_states[i].state;
    return ART_MISSING;
}

static bool aa_io_failed;
static bool aa_check_abort(void);
#define AA_QUOTA_BYTES (512ul * 1024 * 1024)


/* What the current (or last completed) pass has covered, and the folder it is
 * on. Every one of these is a count of a branch the pass already takes, so
 * keeping them costs nothing beyond the increment; aa_dir is the scratch
 * buffer the pass already keeps the current directory in. */
static struct art_cache_counts aa_counts;

/* Scratch id3 for source resolution; kept out of the
 * thread stack because struct mp3entry is large. */
static struct mp3entry aa_id3;

/* Scratch buffers used only by the background thread (aa_run_pass /
 * aa_generate_one). Kept off the thread stack -- together with the JPEG
 * decoder's own deep frames they otherwise overflow it. Single-threaded and
 * non-reentrant, so module-level statics are safe. */
static char aa_tcs_buf[TAGCACHE_BUFSZ];
static char aa_dir[MAX_PATH];
static char aa_artist_dir[MAX_PATH];
static char aa_check_path[MAX_PATH];
static char aa_out_path[MAX_PATH];
static char aa_chain_path[MAX_PATH];
static char aa_tmp_path[MAX_PATH];

/* Stamp-file records go through this a batch at a time. */
#define AA_STAMP_BATCH 64
static struct aa_stamp aa_stamp_io[AA_STAMP_BATCH];

/* Names collected by one round of aa_purge_thumbs(). A thumbnail is
 * "%08x.aat" and the shared placeholder "fallback.aat", so 32 bytes holds
 * any of them with room to spare; anything longer is left alone rather than
 * truncated, since a truncated name names a different file. */
#define AA_PURGE_BATCH 64
static char aa_purge_names[AA_PURGE_BATCH][32];

/* Queue event id: resolve a track, artist directory or indexed book. */
#define AA_EVENT_OFFER 1

/* Persisted beside an owner reference so repair retains its path and role.
 * policy == -1 follows the cache source setting; WPS overrides name a policy.
 */
struct aa_request
{
    uint32_t version;
    unsigned owner;
    int role;
    int policy;
    char path[MAX_PATH];
    uint32_t checksum;
};
static struct aa_request aa_offer;
static volatile bool aa_offer_posted;
static struct timeout aa_offer_tmo;
static unsigned aa_offer_retries;
static volatile unsigned aa_offer_generation;

bool art_cache_is_busy(void)
{
    return cache_busy;
}

void art_cache_get_counts(struct art_cache_counts *out)
{
    *out = aa_counts;
}

const char *art_cache_activity(void)
{
    return cache_busy ? aa_dir : "";
}

int art_cache_num_sizes(void)
{
    return ART_CACHE_NUM_SIZES;
}

int art_cache_size_dim(int size_index)
{
    if (size_index < 0 || size_index >= ART_CACHE_NUM_SIZES)
        return 0;
    return art_sizes[size_index].dim;
}

enum art_layout art_cache_size_layout(int size_index)
{
    if (size_index < 0 || size_index >= ART_CACHE_NUM_SIZES)
        return AA_ROWS;
    return art_sizes[size_index].layout;
}

const char *art_cache_size_name(int size_index)
{
    if (size_index < 0 || size_index >= ART_CACHE_NUM_SIZES)
        return NULL;
    return art_sizes[size_index].name;
}

int art_cache_size_index(const char *name)
{
    int i;
    if (!name)
        return -1;
    for (i = 0; i < ART_CACHE_NUM_SIZES; i++)
        if (!strcmp(art_sizes[i].name, name))
            return i;
    return -1;
}

/* Modified FNV hash (same as PictureFlow's, good avalanche/distribution). */
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

static void aa_cache_path(char *out, int out_len, int size_index,
                          unsigned int arthash)
{
    snprintf(out, out_len, THUMBCACHE_DIR "/%s/%08x.aat",
             art_sizes[size_index].name, arthash);
}

/* The shared placeholder thumbnail for a size. Its name cannot collide with
 * an %08x hash filename, so it lives alongside the real thumbnails. */
static void aa_fallback_path(char *out, int out_len, int size_index)
{
    snprintf(out, out_len, THUMBCACHE_DIR "/%s/fallback.aat",
             art_sizes[size_index].name);
}

/* Streaming area-average downscale of an open .aat into `bm` (fd is consumed).
 *
 * The source is read once, top to bottom: each destination row averages the
 * contiguous band of source rows that map to it, so at most a few source rows
 * are resident at a time (aat_band[]). Downscale only -- the WPS never asks for
 * more than the 300px cache. Returns the pixel byte count (like the decoders,
 * so load_image can add sizeof(struct bitmap)), or <= 0 on failure. */
#define AAT_BAND_MAX 8    /* source rows resident per output row */
static fb_data aat_band[AAT_BAND_MAX * ART_CACHE_MAX_DIM];
/* Held while aat_band[] is in use: buffering, the art pass and the Report
 * all read thumbnails, and read() locks only the file. */
static struct mutex aat_mutex;

/* ---------------------------------------------------------------------- *
 * Reading a cached thumbnail                                              *
 * ---------------------------------------------------------------------- */

static uint32_t aa_checksum(uint32_t value, const void *data, size_t n)
{
    const unsigned char *p = data;
    while (n--) value = (value ^ *p++) * 16777619u;
    return value;
}

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

static struct mutex identity_mutex;
static bool identity_ready;

static void aa_identity_dirs(void)
{
    mkdir(LIB_ART_DIR);
    mkdir(THUMBCACHE_DIR);
    mkdir(THUMBCACHE_DIR "/keys");
    mkdir(THUMBCACHE_DIR "/refs");
    mkdir(THUMBCACHE_DIR "/requests");
}

/* Hashes index a full-key registry. A collision claims a different slot. */
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

static unsigned aa_referenced(unsigned owner)
{
    char path[MAX_PATH];
    uint32_t record[5];
    snprintf(path, sizeof(path), THUMBCACHE_DIR "/refs/%08x.ref", owner);
    int fd = open(path, O_RDONLY);
    bool ok =
        fd >= 0 && read(fd, record, sizeof(record)) == sizeof(record) &&
        record[0] == ART_CACHE_FORMAT_VERSION && record[1] == owner &&
        record[3] == (unsigned)global_settings.art_cache_album_source &&
        record[4] == aa_checksum(2166136261u, record, 4 * sizeof(*record));
    if (fd >= 0)
        close(fd);
    return ok ? record[2] : 0;
}

static bool aa_reference(unsigned owner, unsigned pixels)
{
    if (aa_referenced(owner) == pixels)
        return true;
    char path[MAX_PATH], tmp[MAX_PATH];
    uint32_t record[] = {ART_CACHE_FORMAT_VERSION, owner, pixels,
                         global_settings.art_cache_album_source, 0};
    record[4] = aa_checksum(2166136261u, record, 4 * sizeof(*record));
    snprintf(path, sizeof(path), THUMBCACHE_DIR "/refs/%08x.ref", owner);
    snprintf(tmp, sizeof(tmp), "%s.new", path);
    int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC, 0666);
    if (fd < 0)
    {
        aa_io_failed = true;
        aa_state = ART_FAILED;
        return false;
    }
    bool ok = write(fd, record, sizeof(record)) == sizeof(record);
    if (close(fd) < 0)
        ok = false;
    if (aa_check_abort())
        ok = false;
    if (ok)
        ok = rename(tmp, path) == 0;
    if (!ok)
    {
        remove(tmp);
        aa_io_failed = true;
        aa_state = ART_FAILED;
    }
    else
    {
        aa_generation++;
#ifndef DBTOOL
        playback_art_changed(owner);
#endif
    }
    return ok;
}

enum art_cache_state art_cache_status(void) { return aa_state; }

static bool aa_read_request(unsigned owner, struct aa_request *request)
{
    char path[MAX_PATH];
    snprintf(path, sizeof(path), THUMBCACHE_DIR "/requests/%08x.req", owner);
    int fd = open(path, O_RDONLY);
    bool ok =
        fd >= 0 && read(fd, request, sizeof(*request)) == sizeof(*request);
    if (fd >= 0)
        close(fd);
    return ok && request->version == ART_CACHE_FORMAT_VERSION &&
           request->owner == owner &&
           (request->role == AA_ROLE_ALBUM || request->role == AA_ROLE_ARTIST ||
            request->role == AA_ROLE_BOOK) &&
           request->policy >= -1 && request->policy <= ART_SOURCE_EMBEDDED &&
           request->path[0] &&
           memchr(request->path, 0, sizeof(request->path)) &&
           request->checksum ==
               aa_checksum(2166136261u, request,
                           offsetof(struct aa_request, checksum));
}

static bool aa_save_request(unsigned owner, const char *probe,
                            enum albumart_role role, int policy)
{
    struct aa_request request = {.version = ART_CACHE_FORMAT_VERSION,
                                 .owner = owner,
                                 .role = role,
                                 .policy = policy};
    struct aa_request stored;
    strlcpy(request.path, probe, sizeof(request.path));
    request.checksum = aa_checksum(2166136261u, &request,
                                   offsetof(struct aa_request, checksum));
    if (aa_read_request(owner, &stored) &&
        !memcmp(&stored, &request, sizeof(request)))
        return true;
    char path[MAX_PATH], tmp[MAX_PATH];
    snprintf(path, sizeof(path), THUMBCACHE_DIR "/requests/%08x.req", owner);
    snprintf(tmp, sizeof(tmp), "%s.new", path);
    int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC, 0666);
    bool ok = fd >= 0;
    if (ok)
    {
        ok = write(fd, &request, sizeof(request)) == sizeof(request);
        if (close(fd) < 0)
            ok = false;
    }
    if (ok)
        ok = rename(tmp, path) == 0;
    if (!ok)
    {
        remove(tmp);
        aa_io_failed = true;
        aa_state = ART_FAILED;
    }
    return ok;
}

static void aa_request_owner(unsigned owner);

void art_cache_thumb_path(unsigned int owner, int size_index,
                          char *out, int out_len)
{
    if (size_index < 0 || size_index >= ART_CACHE_NUM_SIZES)
    { strlcpy(out, THUMBCACHE_DIR "/missing.aat", out_len); return; }
    unsigned pixels = aa_referenced(owner);
    aa_cache_path(out, out_len, size_index, pixels);
    int fd = open(out, O_RDONLY);
    struct art_cache_header header;
    bool valid = fd >= 0 && art_cache_read_header(fd, &header) &&
                 header.source_id == pixels;
    if (fd >= 0) close(fd);
    if (!valid)
    {
        strlcpy(out, THUMBCACHE_DIR "/missing.aat", out_len);
        aa_request_owner(owner);
    }
}

bool art_cache_lookup(const char *dir, int size_index,
                           char *out, int out_len, bool *is_fallback)
{
    if (!dir)
    {
        if (is_fallback)
            *is_fallback = false;
        return false;
    }

    return art_cache_lookup_hash(art_cache_dir_hash(dir), size_index, out, out_len,
                                 is_fallback);
}

bool art_cache_lookup_hash(unsigned int dir_hash, int size_index,
                           char *out, int out_len, bool *is_fallback)
{
    if (is_fallback)
        *is_fallback = false;
    if (size_index < 0 || size_index >= ART_CACHE_NUM_SIZES)
        return false;

    aa_cache_path(out, out_len, size_index, aa_referenced(dir_hash));
    int fd = open(out, O_RDONLY);
    struct art_cache_header header;
    bool valid = fd >= 0 && art_cache_read_header(fd, &header) &&
                 header.source_id == aa_referenced(dir_hash);
    if (fd >= 0) close(fd);
    if (valid) return true;
    aa_request_owner(dir_hash);

    /* No real art for this folder -- hand back the placeholder so callers don't
     * each have to draw their own "missing art" state. Absent until the cache
     * has generated it (early boot), in which case this returns false and the
     * caller falls back to whatever it did before. */
    aa_fallback_path(out, out_len, size_index);
    if (file_exists(out))
    {
        if (is_fallback)
            *is_fallback = true;
        return true;
    }
    return false;
}

static void aa_ensure_dirs(void)
{
    int i;
    char p[MAX_PATH];
    aa_identity_dirs();
    for (i = 0; i < ART_CACHE_NUM_SIZES; i++)
    {
        snprintf(p, sizeof(p), THUMBCACHE_DIR "/%s", art_sizes[i].name);
        mkdir(p);
    }
}

/* Prefer Cache uses the library policy; explicit WPS preferences have their
 * own references so a folder view and the WPS can choose different sources. */
unsigned art_cache_preference_hash(const char *path, int preference)
{
    if (preference == AA_PREFER_CACHE)
        return art_cache_dir_hash(path);
    char key[ART_CACHE_IDENTITY_MAX], normalized[MAX_PATH];
    if (!albumart_normalize(path, normalized, sizeof(normalized)))
        return 0;
    snprintf(key, sizeof(key), "/@wps/%d%s", preference, normalized);
    return art_cache_dir_hash(key);
}

bool art_cache_lookup_preference(const char *path, int preference, int size,
                                 char *out, int out_len, bool *fallback)
{
    if (preference == AA_OFF)
        return false;
    if (preference == AA_PREFER_CACHE)
        return art_cache_lookup(path, size, out, out_len, fallback);
    unsigned owner = art_cache_preference_hash(path, preference);
    int policy = preference == AA_PREFER_IMAGE_FILE ? ART_SOURCE_FILES_FIRST
                                                    : ART_SOURCE_EMBEDDED_FIRST;
    if (!owner || !aa_save_request(owner, path, AA_ROLE_ALBUM, policy))
        return false;
    return art_cache_lookup_hash(owner, size, out, out_len, fallback);
}

/* An empty stamp file records the format before a pass writes its entries. */
static void aa_stamps_clear(void)
{
    struct libfile_writer w;

    if (libfile_begin(&w, AA_STAMP_FILE, AA_STAMP_MAGIC,
                      ART_CACHE_FORMAT_VERSION, sizeof(struct aa_stamp), NULL))
        libfile_finish(&w, true);
}

static bool aa_check_abort(void);
static bool aa_progress(int done, int rows)
{
    (void)done; (void)rows;
    return !aa_check_abort();
}

/* Delete every cached thumbnail of every size (the directories themselves stay).
 * Stops for USB or a shutdown, with no stamp file, so the next pass purges the
 * rest; the format is recorded only once every size is empty. */
static void aa_purge_thumbs(void)
{
    int i;
    char dirpath[MAX_PATH];
    char filepath[MAX_PATH];

    /* The thumbnails are going, so nothing is cached for any entry count.
     * Said here rather than left to whoever asked, because the format-version
     * check below purges from inside a pass that may then be interrupted. */
    bg_task_forget(&art_cache_task);

    /* The miss lists describe thumbnails that are about to stop existing, so
     * they go too rather than being left to describe a cache that is gone. */
    remove(AA_NOART_ALBUMS);
    remove(AA_NOART_ARTISTS);
    remove(AA_STAMP_FILE);

    debug_log(DEBUG_LOG_ARTCACHE, "purge: start");

    for (i = 0; i < ART_CACHE_NUM_SIZES; i++)
    {
        int total = 0, n, removed;

        snprintf(dirpath, sizeof(dirpath), THUMBCACHE_DIR "/%s",
                 art_sizes[i].name);

        /* Trap: removing an entry while readdir() walks the same directory
         * mutates the structure being enumerated, which loses entries at best
         * and does not terminate at worst. Names are collected with the
         * directory open and deleted with it closed, a batch at a time, and
         * a round that frees nothing stops rather than rescanning forever. */
        do
        {
            DIR *d = opendir(dirpath);
            struct dirent *e;

            if (!d)
                break;

            n = 0;
            while (n < AA_PURGE_BATCH && (e = readdir(d)))
            {
                if (e->d_name[0] == '.')
                    continue;
                if (strlcpy(aa_purge_names[n], e->d_name,
                            sizeof(aa_purge_names[0]))
                        >= sizeof(aa_purge_names[0]))
                    continue;
                n++;
            }
            closedir(d);

            removed = 0;
            aa_generation++;
            for (int j = 0; j < n; j++)
            {
                snprintf(filepath, sizeof(filepath), "%s/%s", dirpath,
                         aa_purge_names[j]);
                if (remove(filepath) == 0)
                    removed++;
                yield();
                if (aa_check_abort())
                {
                    debug_log(DEBUG_LOG_ARTCACHE, "purge: interrupted");
                    return;
                }
            }
            total += removed;
        }
        while (n == AA_PURGE_BATCH && removed > 0);

        debug_log(DEBUG_LOG_ARTCACHE, "purge: %s %d files",
                  art_sizes[i].name, total);
    }

    aa_stamps_clear();
    debug_log(DEBUG_LOG_ARTCACHE, "purge: done");
}

/* Whether the stamp file says the thumbnails are in this format */
static bool aa_format_current(void)
{
    struct libfile_header h;

    return libfile_peek(AA_STAMP_FILE, AA_STAMP_MAGIC,
                        ART_CACHE_FORMAT_VERSION, &h);
}

/* The stamp header gates the cache format. Missing marks force a pass;
 * its initial purge also writes the current format's empty stamp file. */
static bool aa_check_format_version(void)
{
    if (aa_format_current())
        return true;

    logf("albumart cache: new format %d, purging", ART_CACHE_FORMAT_VERSION);
    aa_purge_thumbs();
    return aa_format_current();
}

/* bg_task.artifact_ok: whether the cache is still on disk. A cache deleted
 * over USB leaves the marks in RAM matching, so without this nothing reruns
 * until the library changes. The folders, not every thumbnail: a pass checks
 * those itself, folder by folder. */
static bool aa_artifact_ok(void)
{
    char p[MAX_PATH];
    int i;

    aa_offer_posted = false;
    if (!aa_format_current())
        return false;

    for (i = 0; i < ART_CACHE_NUM_SIZES; i++)
    {
        snprintf(p, sizeof(p), THUMBCACHE_DIR "/%s", art_sizes[i].name);
        if (!dir_exists(p))
            return false;
    }
    return true;
}

/* Extract the directory portion (without trailing slash) of a full path. */
static void aa_dirname(const char *path, char *dir, int dir_len)
{
    const char *sep = strrchr(path, '/');
    int len = sep ? (int)(sep - path) : 0;
    if (len >= dir_len)
        len = dir_len - 1;
    memcpy(dir, path, len);
    dir[len] = 0;
}

/* The table slot for owner key 'h', claimed with no stamp if it is new.
 * NULL when the table is full. h==0 is remapped so 0 can mark an empty slot. */
static struct aa_stamp *aa_slot(unsigned int h)
{
    unsigned int i, idx;
    if (h == 0)
        h = 1;
    idx = h & (AA_SEEN_SLOTS - 1);
    for (i = 0; i < AA_SEEN_SLOTS; i++)
    {
        struct aa_stamp *s = &aa_stamps[(idx + i) & (AA_SEEN_SLOTS - 1)];
        if (s->key == 0)
        {
            s->key = h;
            s->stamp = AA_STAMP_NONE;
            return s;
        }
        if (s->key == h)
            return s;
    }
    return NULL;
}

/* Whether this pass reached the folder with hash 'h' */
/* A folder this pass skipped for want of a slot. Its thumbnails are not
 * orphans, so the pass then removes none. */
static bool aa_table_full;

/* The slot for an owner's first visit. Full tables use a streaming slot;
 * only an owner already visited is skipped. */
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

/* Fill the table from the stamp file. A missing or unrecognised file leaves
 * every folder unstamped, so the pass adopts what is on disk. */
static void aa_stamps_load(void)
{
    struct libfile_header h;
    int fd, n, i;
    uint32_t left;

    fd = libfile_open(AA_STAMP_FILE, AA_STAMP_MAGIC, ART_CACHE_FORMAT_VERSION,
                      sizeof(struct aa_stamp), &h, NULL);
    aa_marks_key = 0;
    if (fd < 0)
        return;
    /* By the count: a torn append can leave a record past it */
    left = h.count;
    while (left > 0 && (n = read(fd, aa_stamp_io, sizeof(aa_stamp_io))) > 0)
    {
        n /= sizeof(aa_stamp_io[0]);
        if ((uint32_t)n > left)
            n = left;
        left -= n;
        for (i = 0; i < n; i++)
        {
            struct aa_stamp *s;

            if (aa_stamp_io[i].key == 0)
            {
                aa_marks_key = aa_stamp_io[i].stamp;
                continue;
            }
            s = aa_slot(aa_stamp_io[i].key);
            if (s)
                s->stamp = aa_stamp_io[i].stamp;
        }
    }
    close(fd);
}

/* Write the table back. A completed pass keeps only the folders it reached,
 * which drops those gone from the library; an interrupted one keeps every
 * stamp, since the folders it never reached still have their thumbnails.
 * The header keeps the last completed pass's marks, which are what the file
 * still covers: an interrupted pass that cleared them would have the next one
 * visit every folder. */
static void aa_stamps_save(bool completed)
{
    const struct bg_marks *done = &art_cache_task.done_marks;
    struct libfile_marks lm;
    struct libfile_writer w;
    bool ok;
    int i, n = 0;

    lm.entries = done->entries;
    lm.commitid = done->commitid;
    lm.deleted = done->deleted;
    if (!libfile_begin(&w, AA_STAMP_FILE, AA_STAMP_MAGIC,
                       ART_CACHE_FORMAT_VERSION, sizeof(struct aa_stamp),
                       &lm))
        return;
    ok = true;
    if (aa_marks_key)
    {
        aa_stamp_io[n].key = 0;
        aa_stamp_io[n++].stamp = aa_marks_key;
    }

    for (i = 0; ok && i < AA_SEEN_SLOTS; i++)
    {
        const struct aa_stamp *s = &aa_stamps[i];

        if (s->key == 0 || s->stamp == AA_STAMP_NONE)
            continue;
        if (completed && !(aa_visited[i / 8] & (1u << (i % 8))))
            continue;
        aa_stamp_io[n++] = *s;
        if (n == AA_STAMP_BATCH)
        {
            ok = libfile_write(&w, aa_stamp_io, sizeof(aa_stamp_io), n);
            n = 0;
        }
    }
    if (ok && n)
        ok = libfile_write(&w, aa_stamp_io, n * sizeof(aa_stamp_io[0]), n);
    libfile_finish(&w, ok);
}

/* Whether every size of one folder's thumbnails is on disk. */
static bool aa_thumbs_exist(unsigned int dh)
{
    int s;

    for (s = 0; s < ART_CACHE_NUM_SIZES; s++)
    {
        aa_cache_path(aa_check_path, sizeof(aa_check_path), s, dh);
        int fd = open(aa_check_path, O_RDONLY);
        struct art_cache_header h;
        bool ok = fd >= 0 && art_cache_read_header(fd, &h) && h.source_id == dh;
        if (fd >= 0) close(fd);
        if (!ok) return false;
    }
    return true;
}

unsigned int art_cache_generation(void)
{
    return aa_generation;
}

/* Delete every size of one folder's thumbnails. */
static void aa_remove_thumbs(unsigned int owner)
{
    char path[MAX_PATH];
    snprintf(path, sizeof(path), THUMBCACHE_DIR "/refs/%08x.ref", owner);
    if (remove(path) == 0)
    {
        aa_generation++;
#ifndef DBTOOL
        playback_art_changed(owner);
#endif
    }
}

/* Where an album-art image comes from: a file on disk (folder art), or an
 * image embedded in an audio file (emb_pos >= 0, reusing metadata already
 * parsed by playback -- see aa_track_change_cb()). */
struct aa_src
{
    struct albumart_source resolved;
    unsigned generation;
    const char   *path;      /* file to read: the folder image, or the track */
    off_t         emb_pos;   /* embedded image offset, or -1 for a whole file */
    unsigned long emb_size;  /* embedded image length (embedded only) */
    int           emb_flags; /* embedded mp3_albumart type and flags */
};

/* Rendering a book's cover: fitted at its own shape, never cropped */
static bool aa_book;

/* Read the source image into `bm` with `fmt`, requesting a `w` x `h` target (the
 * readers treat that as the resize target, or overwrite it with the source's own
 * dimensions when `fmt` carries no FORMAT_RESIZE). Returns the reader's result. */
static int aa_read_source(const struct aa_src *src, struct bitmap *bm, int fmt,
                          int w, int h, void *workbuf, size_t workbuf_sz)
{
    memset(bm, 0, sizeof(*bm));
    bm->data = workbuf;
    bm->width = w;
    bm->height = h;
    bm->format = FORMAT_NATIVE;

    struct custom_format decode_format = format_native;
    decode_format.progress = aa_progress;
    decode_format.cancel_generation = &aa_offer_generation;
    decode_format.generation = src->generation;
    if (src->resolved.kind)
        return albumart_decode(&src->resolved, bm, workbuf_sz, fmt,
                               &decode_format);
    struct image_src is = {
        .fd = open(src->path, O_RDONLY),
        .pos = src->emb_pos >= 0 ? src->emb_pos : 0,
        .len = src->emb_pos >= 0 ? (off_t)src->emb_size : -1,
        .flags = src->emb_pos >= 0 ? src->emb_flags & ~AA_CLEAR_FLAGS_MASK : 0,
    };
    int rc;

    if (is.fd < 0)
        return -1;
    rc = image_load(&is, bm, (int)workbuf_sz, fmt, &decode_format);
    close(is.fd);
    return rc;
}

/* Target size for an AA_FIT_COVER decode: scale so the SHORTER side lands on
 * exactly `dim`, leaving the longer side >= dim to be cropped away. False if the
 * staged image would not fit the work buffer (caller falls back to CONTAIN) or
 * its dimensions are nonsense. */
static bool aa_cover_dim(int sw, int sh, int dim, int *tw, int *th)
{
    if (sw <= 0 || sh <= 0)
        return false;

    if (sw <= sh)
    {
        *tw = dim;
        *th = (sh * dim + sw / 2) / sw;     /* rounded; >= dim */
    }
    else
    {
        *th = dim;
        *tw = (sw * dim + sh / 2) / sh;
    }

    /* rounding must never leave the short side under dim -- the crop below
     * assumes both sides are at least dim */
    if (*tw < dim)
        *tw = dim;
    if (*th < dim)
        *th = dim;

    return (long)*tw * *th <= ART_CACHE_COVER_MAX_PX &&
           *tw <= ART_CACHE_COVER_MAX_W;
}

/* Centre-crop a tw x th image down to dim x dim, in place. Each output row sits
 * at or before its source row (dim <= tw), so copying front-to-back is safe. */
static void aa_crop_center(void *buf, int tw, int th, int dim)
{
    fb_data *px = buf;
    int x0 = (tw - dim) / 2;
    int y0 = (th - dim) / 2;
    int y;

    for (y = 0; y < dim; y++)
        memmove(px + (size_t)y * dim,
                px + (size_t)(y + y0) * tw + x0,
                (size_t)dim * FB_DATA_SZ);
}

/* Bound disk use by bytes. Eviction never removes the source being published. */
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

/* Write a native (row-major fb_data) bitmap to a .aat file: the shared header
 * followed by the pixels. Removes the file on a short write. */
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

/* Decode/scale the source art into a thumbnail and write it.
 *
 * CONTAIN: fitted (aspect preserved) inside dim x dim, so the cached image is
 * only square when the source is.
 * COVER:   decoded to the cover size (shorter side == dim) and centre-cropped to
 * exactly dim x dim. A source wider than ART_CACHE_COVER_MAX_ASPECT falls
 * back to CONTAIN rather than overrun the work buffer.
 *
 * Returns true on success. */
static bool aa_generate_one(const struct aa_src *src, int size_index,
                            const char *out_path, void *workbuf,
                            size_t workbuf_sz)
{
    int dim = art_sizes[size_index].dim;
    bool cover = art_sizes[size_index].fit == AA_FIT_COVER && !aa_book;
    struct bitmap bm;
    int fmt = FORMAT_NATIVE | FORMAT_RESIZE | FORMAT_DITHER;
    int tw = dim, th = dim;
    int ret;

    if (cover)
    {
        /* Header-only probe: with no FORMAT_RESIZE the readers report the
         * source's own dimensions, and FORMAT_RETURN_SIZE stops them decoding
         * any pixels. */
        ret = aa_read_source(src, &bm, FORMAT_NATIVE | FORMAT_RETURN_SIZE,
                             dim, dim, workbuf, workbuf_sz);
        if (ret <= 0 || !aa_cover_dim(bm.width, bm.height, dim, &tw, &th))
        {
            cover = false;      /* unreadable header, or too elongated to crop */
            tw = th = dim;
        }
    }

    /* KEEP_ASPECT is what makes a decode CONTAIN: it shrinks the requested box
     * to the source's aspect. COVER instead asks for the exact cover size it
     * computed above, so the decode must NOT keep aspect. */
    if (!cover)
        fmt |= FORMAT_KEEP_ASPECT;

    ret = aa_read_source(src, &bm, fmt, tw, th, workbuf, workbuf_sz);

    if (ret <= 0 || bm.width <= 0 || bm.height <= 0)
        return false;

    /* the decode should have landed on exactly tw x th, but never crop from an
     * image smaller than the crop window -- write what we got instead */
    if (cover && bm.width >= dim && bm.height >= dim)
    {
        aa_crop_center(bm.data, bm.width, bm.height, dim);
        bm.width = dim;
        bm.height = dim;
    }

    if (!cover && !aa_book && (bm.width != dim || bm.height != dim))
    {
        fb_data *px = (fb_data *)bm.data;
        int x0 = (dim - bm.width) / 2, y0 = (dim - bm.height) / 2;
        if (bm.width > dim || bm.height > dim ||
            (size_t)dim * dim * FB_DATA_SZ > workbuf_sz) return false;
        for (int y = bm.height - 1; y >= 0; y--)
            memmove(px + (y + y0) * dim + x0, px + y * bm.width,
                    bm.width * FB_DATA_SZ);
        for (int y = 0; y < dim; y++)
            for (int x = 0; x < dim; x++)
                if (y < y0 || y >= y0 + bm.height || x < x0 || x >= x0 + bm.width)
                    px[y * dim + x] = 0;
        bm.width = bm.height = dim;
    }
    return aa_write_aat(out_path, &bm, size_index);
}

/* art_sizes[] indices, largest dim first. Fast-build derives each thumbnail
 * from the next larger one, so generation has to run in this order; the table
 * itself stays free to list its sizes in whatever order reads best. */
static void aa_sizes_largest_first(int *order)
{
    int i, j;

    for (i = 0; i < ART_CACHE_NUM_SIZES; i++)
        order[i] = i;

    for (i = 1; i < ART_CACHE_NUM_SIZES; i++)
    {
        int v = order[i];
        for (j = i; j > 0 && art_sizes[order[j - 1]].dim < art_sizes[v].dim; j--)
            order[j] = order[j - 1];
        order[j] = v;
    }
}

/* Fast build: make this size by downscaling an already-cached larger thumbnail
 * rather than decoding the source again. Reading a 300px .aat and area-averaging
 * it costs a fraction of a full JPEG decode on a 30MHz ARM7, at the price of
 * resampling an image that was already resampled once.
 *
 * Chains from the SMALLEST cached size still larger than this one, so the least
 * is thrown away. Only sound when that image is square: a COVER thumbnail always
 * is, but an over-elongated source falls back to CONTAIN, and stretching a
 * letterboxed image to a square would distort it.
 *
 * Returns false for "no usable larger thumbnail" -- the caller then decodes from
 * the source as normal. */
static bool aa_generate_chained(int size_index, unsigned int dh,
                                const char *out_path,
                                void *workbuf, size_t workbuf_sz)
{
    int dim = art_sizes[size_index].dim;
    int order[ART_CACHE_NUM_SIZES];
    int i;

    if (aa_book)
        return false;

    aa_sizes_largest_first(order);

    for (i = ART_CACHE_NUM_SIZES - 1; i >= 0; i--)
    {
        int s = order[i];
        struct art_cache_header hdr;
        struct bitmap bm;
        int fd, ret;

        if (art_sizes[s].dim <= dim)
            continue;
        /* Chaining reads with art_cache_load_aat(), which is row-major only, so
         * a size stored by columns cannot be a source. Nothing is lost: the
         * loop simply carries on to the next size up. */
        if (art_sizes[s].layout != AA_ROWS)
            continue;

        aa_cache_path(aa_chain_path, sizeof(aa_chain_path), s, dh);
        fd = open(aa_chain_path, O_RDONLY);
        if (fd < 0)
            continue;

        /* Peek the header for squareness; art_cache_load_aat() seeks back to 0
         * and does its own magic/version validation. */
        if (read(fd, &hdr, sizeof(hdr)) != (ssize_t)sizeof(hdr) ||
            hdr.width != hdr.height)
        {
            close(fd);
            continue;
        }

        memset(&bm, 0, sizeof(bm));
        bm.data = workbuf;
        bm.width = dim;
        bm.height = dim;
        bm.format = FORMAT_NATIVE;

        /* Unfiltered: this is the generator chaining one cached size into a
         * smaller one, and what it writes back to disk has to stay the
         * theme's raw material. Filtering happens on the way out, per read. */
        ret = art_cache_load_aat(fd, &bm, (int)workbuf_sz, NULL);
        close(fd);

        if (ret > 0)
            return aa_write_aat(out_path, &bm, size_index);
    }

    return false;
}

/* Render one missing thumbnail by the configured strategy. */
static void aa_generate_size(const struct aa_src *src, int size_index,
                             unsigned int dh, const char *out_path,
                             void *workbuf, size_t workbuf_sz)
{
    if (global_settings.art_cache_fast_build &&
        aa_generate_chained(size_index, dh, out_path, workbuf, workbuf_sz))
        return;

    aa_generate_one(src, size_index, out_path, workbuf, workbuf_sz);
}

/* Area-average downscale of a native (fb_data) image. Used only for the
 * compiled-in placeholder, so it needs no upscale or aspect handling: the source
 * is square and always larger than the thumbnail sizes. */
static void aa_scale_native(const fb_data *src, int sw, int sh,
                            fb_data *dst, int dw, int dh)
{
    for (int dy = 0; dy < dh; dy++)
    {
        int sy0 = dy * sh / dh, sy1 = (dy + 1) * sh / dh;
        if (sy1 <= sy0)
            sy1 = sy0 + 1;
        for (int dx = 0; dx < dw; dx++)
        {
            int sx0 = dx * sw / dw, sx1 = (dx + 1) * sw / dw;
            unsigned r = 0, g = 0, b = 0, n = 0;
            if (sx1 <= sx0)
                sx1 = sx0 + 1;
            for (int sy = sy0; sy < sy1; sy++)
                for (int sx = sx0; sx < sx1; sx++)
                {
                    fb_data p = src[sy * sw + sx];
                    r += RGB_UNPACK_RED(p);
                    g += RGB_UNPACK_GREEN(p);
                    b += RGB_UNPACK_BLUE(p);
                    n++;
                }
            dst[dy * dw + dx] = LCD_RGBPACK(r / n, g / n, b / n);
        }
    }
}

/* Downscale one compiled-in (square) placeholder bitmap into every size's
 * placeholder .aat, once. `pathfn` picks the per-size destination file. Cheap in
 * steady state (a file_exists per size); regenerated after a format bump because
 * the purge removes it. */
static void aa_render_placeholder(const fb_data *src, int sw, int sh,
                                  void (*pathfn)(char *, int, int), void *workbuf)
{
    int s;
    char path[MAX_PATH];

    for (s = 0; s < ART_CACHE_NUM_SIZES; s++)
    {
        int dim = art_sizes[s].dim;
        struct bitmap bm;

        pathfn(path, sizeof(path), s);
        if (file_exists(path))
            continue;

        aa_scale_native(src, sw, sh, (fb_data *)workbuf, dim, dim);
        bm.data = workbuf;
        bm.width = dim;
        bm.height = dim;
        bm.format = FORMAT_NATIVE;
        aa_write_aat(path, &bm, s);
        yield();
    }
}

/* Render podboxnoart into the placeholder .aat of every size, one file that
 * album and artist rows share. A square source, so COVER is a plain
 * downscale. */
static void aa_ensure_fallback(void *workbuf, size_t workbuf_sz)
{
    (void)workbuf_sz;
    aa_render_placeholder((const fb_data *)podboxnoart,
                          BMPWIDTH_podboxnoart, BMPHEIGHT_podboxnoart,
                          aa_fallback_path, workbuf);
}

/* True if the pass should stop right now: a USB connection or shutdown is
 * pending, or a task that outranks this one is waiting to start. Standing
 * down costs nothing: the pass resumes from where the thumbnails on disk
 * leave it. */
static bool aa_check_abort(void)
{
    return bg_task_should_stop(&art_cache_task);
}

/* Whether this pass visits only the tracks added since the last completed
 * one. The library has only grown since then, so a folder that pass covered
 * has lost nothing; one that gained a track is visited with it. */
static bool aa_added_only;

/* Diagnostic for the last source that could not produce a derivative. */
static const char *aa_miss;
static const char *aa_miss_src;

/* Validate or generate the complete derivative set for one source revision. */
static bool aa_fill(const struct aa_src *src, unsigned int dh,
                    void *workbuf, size_t worksz, bool *aborted)
{
    bool all_exist = aa_thumbs_exist(dh);
    int order[ART_CACHE_NUM_SIZES];
    int i, s;

    if (all_exist)
        return true;

    /* Largest first, so a fast build has the big thumbnail on disk to derive
     * the smaller ones from. */
    aa_sizes_largest_first(order);

    for (i = 0; i < ART_CACHE_NUM_SIZES; i++)
    {
        s = order[i];
        aa_cache_path(aa_check_path, sizeof(aa_check_path), s, dh);
        int existing = open(aa_check_path, O_RDONLY);
        /* This decoder pass runs only on bgtask. */
        static struct art_cache_header h;
        bool valid = existing >= 0 && art_cache_read_header(existing, &h);
        if (existing >= 0) close(existing);
        if (valid) continue;
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

/* Resolve candidates in order; publish the owner only after every size is ready. */
static bool aa_cache_dir(const char *probe_path, unsigned int owner,
                         struct aa_stamp *slot, bool artist, int policy,
                         void *workbuf, size_t worksz, bool *aborted)
{
    /* Only bgtask enters this non-reentrant resolver. */
    static struct aa_src src;
    src = (struct aa_src){.generation = aa_offer_generation};
    if (!aa_save_request(owner, probe_path,
                         artist ? AA_ROLE_ARTIST : AA_ROLE_ALBUM, policy))
        return false;
    if (policy < 0)
        policy = global_settings.art_cache_album_source;
    memset(&aa_id3, 0, sizeof(aa_id3));
    if (!artist)
        get_metadata(&aa_id3, -1, probe_path);
    strlcpy(aa_id3.path, probe_path, sizeof(aa_id3.path));
    unsigned cursor = 0;
    unsigned starting_generation = aa_generation;
    bool had_candidate = false;
    aa_record_state(owner, ART_LOADING);
    while (albumart_next_source(&aa_id3,
                                artist ? AA_ROLE_ARTIST : AA_ROLE_ALBUM, policy,
                                &cursor, &src.resolved))
    {
        had_candidate = true;
        if (aa_check_abort() || src.generation != aa_offer_generation)
        {
            *aborted = true;
            aa_record_state(owner, ART_MISSING);
            return false;
        }
        src.path = src.resolved.path;
        src.emb_pos = src.resolved.pos;
        src.emb_size = src.resolved.size;
        src.emb_flags = src.resolved.flags;
        albumart_source_key(&src.resolved, aa_active_identity,
                            sizeof(aa_active_identity));
        /* Registry keys use the same normalized bytes as persisted headers. */
        static char normalized[ART_CACHE_IDENTITY_MAX];
        albumart_normalize(aa_active_identity, normalized, sizeof(normalized));
        strcpy(aa_active_identity, normalized);
        unsigned pixels = art_cache_dir_hash(aa_active_identity);
        if (!pixels)
            return false;
        aa_active_id = pixels;
        unsigned stamp =
            (unsigned)(src.resolved.revision ^ (src.resolved.revision >> 32));
        if (aa_fill(&src, pixels, workbuf, worksz, aborted))
        {
            static struct albumart_source checked;
            checked = src.resolved;
            if (!albumart_source_revision(&checked) ||
                checked.revision != src.resolved.revision)
            {
                *aborted = true;
                return false;
            }
            if (aa_check_abort() || src.generation != aa_offer_generation)
            {
                *aborted = true;
                aa_record_state(owner, ART_MISSING);
                return false;
            }
            slot->stamp = stamp;
            bool same_reference = aa_referenced(owner) == pixels;
            bool ready = aa_reference(owner, pixels);
#ifndef DBTOOL
            if (ready && same_reference && aa_generation != starting_generation)
                playback_art_changed(owner);
#else
            (void)same_reference;
            (void)starting_generation;
#endif
            aa_record_state(owner, ready ? ART_READY : ART_FAILED);
            return ready;
        }
        if (src.generation != aa_offer_generation)
            *aborted = true;
        if (*aborted || aa_io_failed)
        {
            aa_record_state(owner, aa_io_failed ? ART_FAILED : ART_MISSING);
            return false;
        }
    }
    aa_record_state(owner, had_candidate ? ART_FAILED : ART_MISSING);
    aa_remove_thumbs(owner);
    slot->stamp = AA_STAMP_NO_EMBED;
    aa_miss = "no decodable source";
    aa_miss_src = NULL;
    return false;
}

static void aa_log_miss(const char *kind, const char *dir)
{
    if (aa_miss_src)
        debug_log(DEBUG_LOG_ARTCACHE, "no art: %s %s: %s %s", kind, dir,
                  aa_miss_src, aa_miss);
    else
        debug_log(DEBUG_LOG_ARTCACHE, "no art: %s %s: %s", kind, dir,
                  aa_miss);
}

/* The two miss lists a pass is writing. One open file each for the whole pass
 * rather than an open/append/close per folder, which on a library with a lot
 * of coverless folders would be thousands of them. */
static struct path_list_writer noart_albums;
static struct path_list_writer noart_artists;

/* Both or neither: one pass fills both lists, so publishing only the one that
 * opened would leave the two describing different passes. A pass whose lists
 * could not be opened still runs -- the thumbnails are the point, these are
 * diagnostics -- it simply publishes nothing, leaving the previous pair. */
static void noart_open(void)
{
    if (path_list_write_open(&noart_albums, AA_NOART_ALBUMS)
        && path_list_write_open(&noart_artists, AA_NOART_ARTISTS))
        return;

    path_list_write_close(&noart_albums, false);
    path_list_write_close(&noart_artists, false);
}

/* Publish on a complete pass, discard on an aborted one. */
static void noart_close(bool completed)
{
    path_list_write_close(&noart_albums, completed);
    path_list_write_close(&noart_artists, completed);
}

const char *art_cache_noart_list(bool artists)
{
    return artists ? AA_NOART_ARTISTS : AA_NOART_ALBUMS;
}

/* The scratch one decode needs. An AA_FIT_COVER decode stages a non-square
 * image before cropping it square, so the buffer is sized for the widest one
 * aa_cover_dim() will pass: the largest size at COVER_MAX_ASPECT. That fixes
 * both of the terms BM_SCALED_SIZE adds up -- the pixels, and the scaler's
 * line buffers, which depend on the width alone -- and those are the two
 * bounds aa_cover_dim() tests a stage against. See art_sizes.h. */
static size_t aa_work_bytes(void)
{
    return BM_SCALED_SIZE(ART_CACHE_MAX_DIM * ART_CACHE_COVER_MAX_ASPECT,
                          ART_CACHE_MAX_DIM, FORMAT_NATIVE, 0)
           + IMAGE_DECODE_OVERHEAD;
}

/* ---- books ---- */

#define AA_COVER_TMP LIB_CACHE_DIR "/cover.tmp"

struct aa_books_ctx
{
    void *workbuf;
    size_t worksz;
    bool *aborted;
};

/* The folders an author's photograph may be in, as an artist's is: the
 * book's own, for books kept as author, file, and the one above it, for
 * author, book, file. Each once, whatever number of books share it. */
static void aa_book_folders(const char *path, struct aa_books_ctx *c)
{
    char dir[MAX_PATH];

    strmemccpy(dir, path, sizeof(dir));
    for (int up = 0; up < 2 && !*c->aborted; up++)
    {
        char *slash = strrchr(dir, '/');
        unsigned int dh;
        struct aa_stamp *slot;

        if (!slash || slash == dir)
            return;                     /* at the top: no author's folder */
        *slash = '\0';
        dh = art_cache_dir_hash(dir);
        if (!(slot = aa_visit(dh)))
            continue;
        aa_cache_dir(dir, dh, slot, true, -1, c->workbuf, c->worksz,
                     c->aborted);
    }
}

/* Decode an indexed book using its ZIP member metadata. */
static bool aa_cache_book(const struct book_entry *e, struct aa_books_ctx *c,
                          unsigned owner, struct aa_stamp *slot)
{
    if (!aa_save_request(owner, e->path, AA_ROLE_BOOK, -1))
        return false;
    memset(&aa_id3, 0, sizeof(aa_id3));
    strlcpy(aa_id3.path, e->path, sizeof(aa_id3.path));
    unsigned cursor = 0;
    bool embedded = e->cover_at >= 0, ready = false;
    aa_record_state(owner, ART_LOADING);
    for (;;)
    {
        struct aa_src source = {.generation = aa_offer_generation};
        bool temporary = false;
        if (embedded)
        {
            embedded = false;
            source.resolved.kind = AA_SOURCE_EMBEDDED;
            source.resolved.pos = e->cover_at;
            source.resolved.size = e->cover_csize;
            strlcpy(source.resolved.path, e->path,
                    sizeof(source.resolved.path));
            if (e->cover_method != 0)
            {
                if (!zip_member_to_file(e->path, e->cover_at, e->cover_csize,
                                        e->cover_usize, e->cover_method,
                                        AA_COVER_TMP, c->workbuf, c->worksz))
                    continue;
                temporary = true;
                source.resolved.kind = AA_SOURCE_FILE;
                source.resolved.pos = 0;
                source.resolved.size = -1;
                strlcpy(source.resolved.path, AA_COVER_TMP,
                        sizeof(source.resolved.path));
            }
            if (!albumart_source_revision(&source.resolved))
            {
                if (temporary)
                    remove(AA_COVER_TMP);
                continue;
            }
            albumart_normalize(e->path, source.resolved.identity,
                               sizeof(source.resolved.identity));
            source.resolved.revision ^= (uint64_t)e->cover_at << 32;
            source.resolved.revision ^= e->cover_csize;
        }
        else if (!albumart_next_source(&aa_id3, AA_ROLE_BOOK,
                                       ART_SOURCE_EMBEDDED_FIRST, &cursor,
                                       &source.resolved))
            break;
        char key[ART_CACHE_IDENTITY_MAX];
        albumart_source_key(&source.resolved, key, sizeof(key));
        albumart_normalize(key, aa_active_identity, sizeof(aa_active_identity));
        aa_active_id = art_cache_dir_hash(aa_active_identity);
        source.path = source.resolved.path;
        aa_book = true;
        ready = aa_active_id && aa_fill(&source, aa_active_id, c->workbuf,
                                        c->worksz, c->aborted);
        aa_book = false;
        if (temporary)
            remove(AA_COVER_TMP);
        if (ready)
        {
            slot->stamp = (unsigned)source.resolved.revision;
            ready = aa_reference(owner, aa_active_id);
            break;
        }
        if (*c->aborted || aa_io_failed)
            break;
    }
    if (!ready && !*c->aborted && !aa_io_failed)
        aa_remove_thumbs(owner);
    aa_record_state(owner, ready          ? ART_READY
                           : aa_io_failed ? ART_FAILED
                                          : ART_MISSING);
    return !*c->aborted;
}

/* One visit per indexed book during a full pass. */
static bool aa_book_visit(const struct book_entry *e, void *data)
{
    struct aa_books_ctx *c = data;
    unsigned owner = art_cache_dir_hash(e->path);
    struct aa_stamp *slot;
    if (aa_check_abort() || tagcache_is_busy())
    {
        *c->aborted = true;
        return false;
    }
    aa_book_folders(e->path, c);
    if (*c->aborted || !(slot = aa_visit(owner)))
        return !*c->aborted;
    return aa_cache_book(e, c, owner, slot);
}

struct aa_book_repair
{
    const struct aa_request *request;
    struct aa_books_ctx context;
};

static bool aa_repair_book(const struct book_entry *e, void *data)
{
    struct aa_book_repair *repair = data;
    char key[MAX_PATH], wanted[MAX_PATH];
    if (!albumart_normalize(e->path, key, sizeof(key)) ||
        !albumart_normalize(repair->request->path, wanted, sizeof(wanted)) ||
        strcmp(key, wanted))
        return true;
    struct aa_stamp slot = {0};
    aa_cache_book(e, &repair->context, repair->request->owner, &slot);
    return false;
}

static enum bg_result aa_run_pass(void)
{
    struct tagcache_search tcs;
    size_t worksz;
    int wh, sh;
    void *workbuf;
    bool aborted = false;
    bool failed = false;
    bool completed;
    int since_yield = 0;

    aa_io_failed = false;
    aa_state = ART_LOADING;
    worksz = aa_work_bytes();
    /* No marks to have covered: a trigger, a first pass or a format bump */

    wh = core_alloc(worksz);
    if (wh <= 0)
        return BG_INTERRUPTED; /* not enough free memory right now */

    sh = core_alloc(AA_TABLE_BYTES);
    if (sh <= 0)
    {
        core_free(wh);
        return BG_INTERRUPTED;
    }

    workbuf = core_get_data_pinned(wh);
    aa_stamps = core_get_data_pinned(sh);
    aa_visited = (unsigned char *)(aa_stamps + AA_SEEN_SLOTS);
    memset(aa_stamps, 0, AA_TABLE_BYTES);

    aa_ensure_dirs();
    /* A purge cut short leaves no stamp file, which saving one would hide */
    if (!aa_check_format_version())
    {
        aa_stamps = NULL;
        aa_visited = NULL;
        core_unpin(wh);
        core_unpin(sh);
        core_free(sh);
        core_free(wh);
        return BG_INTERRUPTED;
    }
    aa_table_full = false;
    aa_active_id = 0;
    strcpy(aa_active_identity, "placeholder");
    aa_ensure_fallback(workbuf, worksz);
    aa_stamps_load();
    /* A Rebuild renumbers the entries, which the file at the old last entry
     * shows */
    {
        const struct bg_marks *done = &art_cache_task.done_marks;
        struct tagcache_marks now;

        tagcache_get_marks(&now);
        aa_added_only = done->entries >= 0 && done->deleted >= 0
                     && now.deleted_ct == done->deleted
                     && now.commitid >= done->commitid
                     && tagcache_get_stat()->total_entries > done->entries
                     && aa_marks_key != 0
                     && aa_fold_key(tagcache_entry_key(done->entries - 1))
                        == aa_marks_key;
    }

    if (!tagcache_search(&tcs, tag_filename))
    {
        aborted = true;
        goto out;
    }
    /* New tracks are appended to the index. A filename search walks the
     * index only with the database in RAM; from the disk it takes everything,
     * and the pass is a whole one. */
    if (aa_added_only && tcs.ramsearch)
        tagcache_search_set_range(&tcs, art_cache_task.done_marks.entries,
                                  tagcache_get_stat()->total_entries - 1);
    else
        aa_added_only = false;
    debug_log(DEBUG_LOG_ARTCACHE, aa_added_only ? "visiting added tracks"
                                                : "visiting every folder");

    /* Counting starts over: these describe this pass, not every pass since
     * boot. Zeroed here rather than at the top so an early return -- no
     * memory, no search -- leaves the previous pass's figures readable. */
    memset(&aa_counts, 0, sizeof(aa_counts));
    noart_open();

    /* A pass is running -> lights the status-bar %lc ("Caching") token. */
    cache_busy = true;
    /* Speed up the (CPU-bound) image decoding, like Cover Flow's own
     * generator does. Passes are rare (only after the database settles or
     * changes), so the extra clock is a brief one-off. */
    cpu_boost(true);

    while (tagcache_get_next(&tcs, aa_tcs_buf, sizeof(aa_tcs_buf)))
    {
        unsigned int dh, ah;
        struct aa_stamp *slot;

        /* Abort promptly on USB/shutdown (the thread loop then acknowledges)
         * or yield the disk to an incoming database commit, so a long pass
         * never blocks either. */
        if (aa_check_abort() || tagcache_is_busy())
        {
            aborted = true;
            break;
        }

        /* Each track owns its reference, even when tags or directories match.
         * Album counts include only the database's representative tracks. */
        aa_dirname(tcs.result, aa_dir, sizeof(aa_dir));
        dh = art_cache_dir_hash(tcs.result);
        if ((slot = aa_visit(dh)))
        {
            struct tagcache_album album;
            bool representative = tagcache_album_get(tagcache_album_of(tcs.idx_id),
                                                       &album) && album.first == tcs.idx_id;
            if (representative) aa_counts.albums++;
            if (aa_cache_dir(tcs.result, dh, slot, false, -1, workbuf, worksz,
                             &aborted))
            {
                if (representative) aa_counts.album_art++;
            }
            else if (!aborted)
            {
                path_list_write_record(&noart_albums, aa_dir);
                aa_log_miss("album", aa_dir);
            }
        }
        if (aborted)
            break;

        /* Artist art belongs to the directory above the album, for the
         * <artist>/<album>/<track> layout, deduplicated across tracks. */
        aa_dirname(aa_dir, aa_artist_dir, sizeof(aa_artist_dir));
        if (aa_artist_dir[0] && strcmp(aa_artist_dir, aa_dir) != 0)
        {
            ah = art_cache_dir_hash(aa_artist_dir);
            if ((slot = aa_visit(ah)))
            {
                aa_counts.artists++;
                if (aa_cache_dir(aa_artist_dir, ah, slot, true, -1, workbuf,
                                 worksz, &aborted))
                    aa_counts.artist_art++;
                else if (!aborted)
                {
                    path_list_write_record(&noart_artists, aa_artist_dir);
                    aa_log_miss("artist", aa_artist_dir);
                }
            }
        }
        if (aborted)
            break;

        if (++since_yield >= 16)
        {
            since_yield = 0;
            yield();
        }
    }
    /* The walk ends the same way at the last entry and at an unreadable one,
     * and only the search knows which it was. A database a rebuild took away
     * mid-walk is an interruption: the rebuilt one can carry the same marks. */
    if (tcs.failed && (!tagcache_is_usable() || tagcache_is_busy()))
        aborted = true;
    failed = !aborted && tcs.failed;
    tagcache_search_finish(&tcs);

    /* Every book in the index, which the orphan sweep below then spares */
    if (!aborted)
    {
        static struct book_index_text text;
        struct aa_books_ctx books = { workbuf, worksz, &aborted };

        book_index_each(aa_book_visit, &books, &text);
    }
    cpu_boost(false); /* balances the boost above (skipped on the goto-out path) */

out:
    failed |= aa_io_failed;
    completed = !aborted && !failed;
    /* What the walk did not visit is not missing when it saw only the
     * added tracks */
    noart_close(completed && !aa_added_only);

    aa_stamps_save(completed && !aa_added_only);
    aa_stamps = NULL;
    aa_visited = NULL;
    core_unpin(wh);
    core_unpin(sh);
    core_free(sh);
    core_free(wh);
    cache_busy = false;
    if (failed)
        return BG_FAILED;
    return aborted ? BG_INTERRUPTED : BG_DONE;
}



/* Whether the disk is asleep. Hosted storage reports itself never active, and
 * has no spin-up to save, so it is never asleep: waiting on it would hold the
 * offer forever. */
static bool aa_disk_asleep(void)
{
#ifdef HAVE_HOSTFS
    return false;
#else
    return !storage_disk_is_active();
#endif
}

/* Poll storage state without spinning it up for artwork. */
static int aa_offer_poll(struct timeout *tmo)
{
    (void)tmo;
    if (!aa_offer.path[0])
        return 0;
    if (aa_disk_asleep())
        return HZ;
    if (!aa_offer_posted)
    {
        aa_offer_posted = true;
        bg_task_post(&art_cache_task, AA_EVENT_OFFER);
    }
    return 0;
}

/* Resolve a typed owner request. A newer offer supersedes an older one.
 * Scratch shortages get at most three retries, without shrinking audio. */
static void aa_handle_offer(void)
{
    aa_offer_posted = false;
    if (!aa_offer.path[0])
        return;
    if (aa_disk_asleep())
    {
        timeout_register(&aa_offer_tmo, aa_offer_poll, HZ, 0);
        return;
    }
    aa_io_failed = false;
    struct aa_request request = aa_offer;
    if (!request.owner)
    {
        int preference =
            request.policy == ART_SOURCE_FILES_FIRST      ? AA_PREFER_IMAGE_FILE
            : request.policy == ART_SOURCE_EMBEDDED_FIRST ? AA_PREFER_EMBEDDED
                                                          : AA_PREFER_CACHE;
        request.owner = art_cache_preference_hash(request.path, preference);
        if (!request.owner)
            return;
    }
    size_t size = aa_work_bytes();
    int handle = core_allocatable() >= size ? core_alloc(size) : 0;
    if (handle <= 0)
    {
        if (aa_offer_retries++ < 3)
            timeout_register(&aa_offer_tmo, aa_offer_poll, 5 * HZ, 0);
        return;
    }
    void *work = core_get_data_pinned(handle);
    bool aborted = false;
    struct aa_stamp slot = {0};
    aa_ensure_dirs();
    if (request.role == AA_ROLE_BOOK)
    {
        static struct book_index_text text;
        struct aa_book_repair repair = {&request, {work, size, &aborted}};
        book_index_each(aa_repair_book, &repair, &text);
    }
    else
        aa_cache_dir(request.path, request.owner, &slot,
                     request.role == AA_ROLE_ARTIST, request.policy, work, size,
                     &aborted);
    core_unpin(handle);
    core_free(handle);
}

static void aa_request_owner(unsigned owner)
{
    static struct
    {
        unsigned key, generation;
        long until;
    } misses[32];
    static unsigned next;
    if (!owner)
        return;
    for (unsigned i = 0; i < ARRAYLEN(misses); i++)
        if (misses[i].key == owner && misses[i].generation == aa_generation &&
            TIME_BEFORE(current_tick, misses[i].until))
            return;
    misses[next].key = owner;
    misses[next].generation = aa_generation;
    misses[next].until = current_tick + 5 * HZ;
    next = (next + 1) % ARRAYLEN(misses);
    struct aa_request request;
    if (!aa_read_request(owner, &request))
        return;
    aa_record_state(owner, ART_LOADING);
    aa_offer = request;
    aa_offer_retries = 0;
    if (!aa_offer_posted)
    {
        aa_offer_posted = true;
        bg_task_post(&art_cache_task, AA_EVENT_OFFER);
    }
}

/* A current track requests the cache policy or its explicit WPS policy. */
static void aa_track_change_cb(unsigned short id, void *event_data)
{
    (void)id;
    (void)event_data;
    struct mp3entry *id3 = audio_current_track();
    if (!id3 || !id3->path[0])
        return;
    if (global_settings.album_art == AA_OFF)
        return;
    aa_offer_generation++;
    memset(&aa_offer, 0, sizeof(aa_offer));
    strlcpy(aa_offer.path, id3->path, sizeof(aa_offer.path));
    aa_offer.role = AA_ROLE_ALBUM;
    aa_offer.policy = global_settings.album_art == AA_PREFER_IMAGE_FILE
                          ? ART_SOURCE_FILES_FIRST
                      : global_settings.album_art == AA_PREFER_EMBEDDED
                          ? ART_SOURCE_EMBEDDED_FIRST
                          : -1;
    aa_offer_retries = 0;
    if (!aa_offer_posted)
    {
        aa_offer_posted = true;
        bg_task_post(&art_cache_task, AA_EVENT_OFFER);
    }
}

/* A pass trusts each folder's stamp for what its tags hold, so a change of
 * source needs the pass that reads them again. aa_applied_source is the
 * source the last change was acted on for, or -1 before the first. */
static int aa_applied_source = -1;

void art_cache_album_source_callback(int source)
{
    aa_applied_source = source;
    aa_offer_generation++;
    bg_task_update(&art_cache_task);
}

void art_cache_settings_applied(void)
{
    int source = global_settings.art_cache_album_source;

    if (aa_applied_source >= 0 && aa_applied_source != source)
        art_cache_album_source_callback(source);
    aa_applied_source = source;
}

/* bg_task.run: one pass, with the tracing the pass itself does not do. */
static enum bg_result aa_task_run(void)
{
    enum bg_result result;

    debug_log(DEBUG_LOG_ARTCACHE, "starting pass");
    aa_offer_posted = false;
    result = aa_run_pass();
    debug_log(DEBUG_LOG_ARTCACHE,
              result == BG_DONE   ? "pass complete, idle" :
              result == BG_FAILED ? "pass failed: database unreadable" :
                                    "pass interrupted");
    return result;
}

/* bg_task.handle_event: everything on the queue that is ours rather than the
 * tick's -- which is just the offer posted by the track-change hook. */
static void aa_task_event(const struct queue_event *ev)
{
    if (ev->id == AA_EVENT_OFFER)
        aa_handle_offer();
}

/* bg_task.read_marks and write_marks: the stamp file's header. The marks are
 * read back at init, never started at -1: -1 matches no settled count, so
 * every boot would run a full pass with cache_busy set, holding the
 * "Building" indicator up with nothing to do. */
static void aa_read_marks(struct bg_marks *m)
{
    struct libfile_header h;

    if (libfile_peek(AA_STAMP_FILE, AA_STAMP_MAGIC, ART_CACHE_FORMAT_VERSION,
                     &h))
    {
        m->entries = h.marks.entries;
        m->commitid = h.marks.commitid;
        m->deleted = h.marks.deleted;
    }
    else
        m->entries = m->commitid = m->deleted = -1;
}

static void aa_write_marks(const struct bg_marks *m)
{
    struct libfile_marks lm;

    libfile_no_marks(&lm);
    if (m)
    {
        lm.entries = m->entries;
        lm.commitid = m->commitid;
        lm.deleted = m->deleted;
    }
    if (libfile_set_marks(AA_STAMP_FILE, AA_STAMP_MAGIC,
                          ART_CACHE_FORMAT_VERSION, &lm) && m)
    {
        struct aa_stamp rec =
            { 0, aa_fold_key(tagcache_entry_key(m->entries - 1)) };

        libfile_append(AA_STAMP_FILE, AA_STAMP_MAGIC, ART_CACHE_FORMAT_VERSION,
                       sizeof(rec), &rec, 1);
    }
}

#ifdef DBTOOL
/* tools/artcache_pb: one pass, run as the background task runs it, against
 * the database tagcache_tool_open() loaded. The marks it records on success
 * are the ones the player computes for the same database, so the player
 * finds the cache covered and runs no pass of its own. 'rebuild' throws
 * every thumbnail away first, as Rebuild Art Cache does. */
enum bg_result art_cache_tool_run(bool rebuild)
{
    struct tagcache_marks tm;
    struct bg_marks covered;
    enum bg_result result;

    if (rebuild)
    {
        aa_purge_thumbs();
        aa_write_marks(NULL);
    }
    aa_read_marks(&art_cache_task.done_marks);

    tagcache_get_marks(&tm);
    covered.entries = tagcache_get_stat()->total_entries;
    covered.commitid = tm.commitid;
    covered.deleted = tm.deleted_ct;

    result = aa_task_run();
    if (result == BG_DONE)
    {
        art_cache_task.done_marks = covered;
        aa_write_marks(&covered);
    }
    return result;
}
#endif /* DBTOOL */

struct bg_task art_cache_task =
{
    .name         = "art cache",
    .read_marks   = aa_read_marks,
    .write_marks  = aa_write_marks,
    .rank         = BG_RANK_ART,
    .run          = aa_task_run,
    .purge        = aa_purge_thumbs,
    .artifact_ok  = aa_artifact_ok,
    .handle_event = aa_task_event,
};

void art_cache_init(void)
{
    cache_busy = false;
    mutex_init(&aat_mutex);

    /* A header per boot, so each run's lines start from a readable point --
     * and so the file exists from boot however the setting was set. Settings
     * are loaded before init_tagcache() reaches here, so the gate reads the
     * real value. */
    if (debug_log_enabled(DEBUG_LOG_ARTCACHE))
        debug_log_header(DEBUG_LOG_ARTCACHE, "started");

    /* A pass holds the decode scratch and the folder table at once, so the
     * peak declared to bg_task is both. A format bump needs nothing here: the
     * stamp file carries the format as its version, so aa_read_marks() finds
     * no marks, the task is stale, and the pass purges. */
    art_cache_task.work_bytes = aa_work_bytes() + AA_TABLE_BYTES;

    bg_task_init(&art_cache_task);

    /* Track changes resolve the selected source policy on the worker. */
    add_event(PLAYBACK_EVENT_TRACK_CHANGE, aa_track_change_cb);
}

