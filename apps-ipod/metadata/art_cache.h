/***************************************************************************
 * GNU General Public License (version 2+)
 *
 * Interface to art_cache.c.
 ****************************************************************************/

#ifndef _ART_CACHE_H_
#define _ART_CACHE_H_

#include <stdbool.h>
#include <stdint.h>
#include "system/bg_task.h"
#include "draw/img_filter.h"  /* struct img_filter, the caller-supplied chain */
#include "art_sizes.h"       /* enum art_layout, named by the file header */

struct bitmap;

/* On-disk thumbnail file format: this header, followed by width*height native
 * (fb_data) pixels in the order the header's 'layout' names. Exposed so
 * consumers (e.g. Cover Flow) can read a cached thumbnail directly. */
#define ART_CACHE_MAGIC          0x5441u  /* 'AT' */
/* Version 9 adds verified source identity, request metadata and a checksum.
 * It lives in art/v9; version 8 files are never interpreted as this format. */
#define ART_CACHE_FORMAT_VERSION 9
#define ART_CACHE_IDENTITY_MAX 512

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

/* The background worker resolves every track. A full-key registry maps a
 * normalized owner path to a source/revision and shared derivative pixels.
 * Album rows use their database representative track's owner key. */

/* Start the background cache thread. Call once at startup, after tagcache. */
void art_cache_init(void);
bool art_cache_read_header(int fd, struct art_cache_header *header);
enum art_cache_state { ART_MISSING, ART_LOADING, ART_READY, ART_FAILED };
enum art_cache_state art_cache_status(void);
enum art_cache_state art_cache_entry_status(unsigned key);

#ifdef DBTOOL
/* tools/artcache_pb: one caching pass with no thread, recording the
 * database's marks when it completes; 'rebuild' purges first. */
enum bg_result art_cache_tool_run(bool rebuild);
#endif

/* Load a cached .aat thumbnail from an open fd, area-average downscaled to
 * bm->width x bm->height (bm->data must hold that many fb_data). Returns the
 * pixel byte count, or <= 0 on failure. Rectangular thumbnails retain their
 * aspect ratio. Used by the buffering path so the WPS renders the 300px cache
 * instead of re-decoding source art.
 *
 * `filter` is the chain to run on the way out, or NULL for none. It is the
 * caller's rather than this function's, because each consumer treats cached art
 * differently and the buffering path must pass NULL: the bitmap it loads is what
 * the dynamic-colour extractor derives its palette from, and that palette has to
 * describe the album rather than anyone's treatment of it. */
int art_cache_load_aat(int fd, struct bitmap *bm, int max_size,
                       const struct img_filter *filter);


/* True while a generation pass is running. Drives the status-bar %lc token. */
bool art_cache_is_busy(void);

/* What the current -- or, once it has finished, the last -- pass covered.
 * "art" counts the folders that ended up with a thumbnail; the difference is
 * the folders that simply have no cover image to find. */
struct art_cache_counts
{
    int albums;
    int album_art;
    int artists;
    int artist_art;
};
void art_cache_get_counts(struct art_cache_counts *out);

/* The folder a running pass is on, or "" when idle. */
const char *art_cache_activity(void);

/* A number that changes whenever any thumbnail is written or deleted, for a
 * reader holding something made from one to tell whether it is still good. */
unsigned int art_cache_generation(void);

/* The caching pass, for the standard triggers.
 *
 * bg_task_rebuild() purges every cached thumbnail and regenerates from
 * scratch. bg_task_update() runs a pass without purging, so artwork added
 * since the last one is picked up -- needed because the thread otherwise idles
 * until the database's track count changes, and adding a folder.jpg does not
 * change it. That pass is cheap, since a folder whose image is unchanged is
 * skipped on its directory entry without reading the image. */
extern struct bg_task art_cache_task;

/* Values retained for reading configurations written before format 9. */
enum art_album_source
{
    ART_SOURCE_FILES,           /* image files only */
    ART_SOURCE_FILES_FIRST,     /* image files, then a track's embedded art */
    ART_SOURCE_EMBEDDED_FIRST,
    ART_SOURCE_EMBEDDED,        /* embedded art only */
};

/* The setting's callback: a change re-resolves every folder. */
void art_cache_album_source_callback(int source);

/* From settings_apply(): the same for a change a loaded .cfg made, which
 * runs no setting's callback. */
void art_cache_settings_applied(void);

/* Resolve the cache-file path for a given album folder and size index.
 * Returns true and fills 'out' if a thumbnail is available, false otherwise.
 * 'dir' is a track path, or the artist directory for the artist role.
 *
 * When the folder has no real art, the shared placeholder thumbnail is returned
 * instead (if generated), so callers get a valid thumbnail without managing a
 * "missing art" case. 'is_fallback' (may be NULL) is set true in that case, so a
 * caller that wants to prefer another source (e.g. embedded ID3 art) still can. */
bool art_cache_lookup(const char *dir, int size_index,
                           char *out, int out_len, bool *is_fallback);

/* Stable registry key, with complete normalized-key collision checking.
 * Returns zero for an invalid identity or a registry write failure. */
unsigned int art_cache_dir_hash(const char *dir);
bool art_cache_lookup_hash(unsigned int dir_hash, int size_index,
                           char *out, int out_len, bool *is_fallback);

/* Resolve an owner's published source reference to its derivative path.
 * A missing derivative queues a bounded retry on the worker. */
void art_cache_thumb_path(unsigned int dir_hash, int size_index,
                          char *out, int out_len);

/* The folders the last completed pass over every folder found no art for, as
 * a file of one path per line. Written only when such a pass finishes, so an
 * interrupted one, or one over added tracks alone, leaves the previous list
 * rather than a partial one. Read by the health screen
 * (screens/system/art_health.c); absent until a pass has completed. */
const char *art_cache_noart_list(bool artists);

/* Number of configured thumbnail sizes, and accessors for each. */
int         art_cache_num_sizes(void);
int         art_cache_size_dim(int size_index);
/* How a size is stored. A reader that copies pixels straight out wants
 * AA_ROWS; the column-major sizes exist for one consumer that reads them
 * that way and are unusable to anyone else. */
enum art_layout art_cache_size_layout(int size_index);
const char *art_cache_size_name(int size_index);
/* Index of the named size in the table, or -1 if not present. */
int         art_cache_size_index(const char *name);

#endif /* _ART_CACHE_H_ */
