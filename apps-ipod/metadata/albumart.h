/***************************************************************************
 * Original code from RockBox
 * was: apps/recorder/albumart.h
 * Copyright (C) 2007 Nicolas Pennequin
 * GNU General Public License (version 2+)
 *
 * Interface to albumart.c.
 ****************************************************************************/

#ifndef _ALBUMART_H_
#define _ALBUMART_H_


#include <stdbool.h>
#include "metadata.h"
#include "skin/skin_engine.h"

/* Whether the image file at path exists and its decoder takes it, by its
 * headers; one that fails further in passes. */
bool art_file_usable(const char *path);


/* Where a track's cover comes from, by the Album Art setting's rules: the
 * preference (AA_PREFER_*) names the source tried first, and the others
 * follow in the order playback tries them -- art embedded in the file (a
 * JPEG, the one kind decoded from inside a file), an image file as
 * search_albumart_files() finds it, then the thumbnail cache. as_stored asks
 * for a JPEG exactly as the source holds it, or the cache's raw thumbnail,
 * for a caller that sends the bytes on rather than decoding them. False when
 * no source has art. */
enum { AA_SOURCE_NONE, AA_SOURCE_EMBEDDED, AA_SOURCE_FILE, AA_SOURCE_CACHE,
       AA_SOURCE_ITUNES };
struct albumart_source {
    int kind;               /* AA_SOURCE_* */
    char path[MAX_PATH];    /* the file to read */
    off_t pos;              /* where the image starts in it */
    int size;               /* its length, or -1 for the whole file */
    unsigned flags;
    unsigned width, height, stride; /* raw iTunes RGB565 geometry */
    uint64_t revision;
    char identity[MAX_PATH];
};
bool albumart_find_source(const struct mp3entry *id3, int preference,
                          bool as_stored, struct albumart_source *src);


enum albumart_role { AA_ROLE_ALBUM, AA_ROLE_ARTIST, AA_ROLE_TRACK, AA_ROLE_BOOK };
struct custom_format;
bool albumart_normalize(const char *path, char *out, size_t capacity);
bool albumart_next_source(const struct mp3entry *id3, enum albumart_role role,
                          unsigned *cursor, struct albumart_source *src);
bool albumart_source_revision(struct albumart_source *src);
int albumart_decode(const struct albumart_source *src, struct bitmap *bm,
                    int maxsize, int format, const struct custom_format *cf);
void albumart_source_key(const struct albumart_source *src, char *out, size_t n);

#endif /* _ALBUMART_H_ */
