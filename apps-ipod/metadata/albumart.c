/***************************************************************************
 * Original code from RockBox
 * was: apps/recorder/albumart.c
 * Copyright (C) 2007 Nicolas Pennequin
 * GNU General Public License (version 2+)
 * Ordered per-track artwork resolution and bounded source decoding.
 ****************************************************************************/
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include "string-extra.h"
#include "system.h"
#include "albumart.h"
#include "database/path_key.h"
#include "art_cache.h"
#include "itunes_art.h"
#include "file.h"
#include "dir.h"
#include "rbpaths.h"
#include "system/strutil.h"
#include "draw/image_load.h"
#include "draw/bmp.h"
#include "screens/playback/wps.h"
#include "settings/settings.h"

bool albumart_normalize(const char *path, char *out, size_t cap)
{
    size_t n = 0;
    path = path_key_strip(path);
    if (!path || !*path || cap < 2)
        return false;
    while (*path)
    {
        while (*path == '/')
            path++;
        if (!*path)
            break;
        const char *end = strchr(path, '/');
        size_t len = end ? (size_t)(end - path) : strlen(path);
        if (len == 1 && path[0] == '.')
        {
            path += len;
            continue;
        }
        if (len == 2 && path[0] == '.' && path[1] == '.')
        {
            while (n && out[--n] != '/')
                ;
            path += len;
            continue;
        }
        if (n + len + 1 >= cap)
            return false;
        out[n++] = '/';
        for (size_t i = 0; i < len; i++)
        {
            unsigned char c = path[i];
            out[n++] = c >= 'A' && c <= 'Z' ? c + 'a' - 'A' : c;
        }
        path += len;
    }
    if (!n)
        out[n++] = '/';
    out[n] = 0;
    return true;
}

static const char *const extensions[] = {"jpeg", "jpg", "png",
                                         "bmp",  "gif", "webp"};
static const char *const common_names[] = {"cover", "folder",   "front",
                                           "album", "albumart", "artwork"};
#define NAME_COUNT (2 + ARRAYLEN(common_names) * 2 + 2)
#define FILE_CANDIDATES (NAME_COUNT * ARRAYLEN(extensions))

static bool file_candidate(const struct mp3entry *id3, unsigned at, char *out,
                           size_t n)
{
    unsigned name = at / ARRAYLEN(extensions);
    const char *ext = extensions[at % ARRAYLEN(extensions)];
    char dir[MAX_PATH], stem[MAX_PATH];
    const char *slash = strrchr(id3->path, '/');
    if (!slash || (size_t)(slash - id3->path) >= sizeof(dir))
        return false;
    memcpy(dir, id3->path, slash - id3->path);
    dir[slash - id3->path] = 0;
    if (name == 0)
    {
        strip_extension(stem, sizeof(stem), id3->path);
        return snprintf(out, n, "%s.%s", stem, ext) < (int)n;
    }
    if (name == 1)
    {
        if (!id3->album || !*id3->album)
            return false;
        strlcpy(stem, id3->album, sizeof(stem));
        fix_path_part(stem, 0, strlen(stem));
        return snprintf(out, n, "%s/%s.%s", dir, stem, ext) < (int)n;
    }
    name -= 2;
    if (name < ARRAYLEN(common_names))
        return snprintf(out, n, "%s/%s.%s", dir, common_names[name], ext) <
               (int)n;
    name -= ARRAYLEN(common_names);
    if (name == 0)
    {
        const char *artist = id3->albumartist ? id3->albumartist : id3->artist;
        if (!artist || !id3->album)
            return false;
        int len = snprintf(out, n, ROCKBOX_DIR "/albumart/%s-%s.%s", artist,
                           id3->album, ext);
        fix_path_part(out, strlen(ROCKBOX_DIR "/albumart/"),
                      strlen(out) - strlen(ROCKBOX_DIR "/albumart/"));
        return len < (int)n;
    }
    char *parent = strrchr(dir, '/');
    if (!parent)
        return false;
    *parent = 0;
    name--;
    if (name < ARRAYLEN(common_names))
        return snprintf(out, n, "%s/%s.%s", dir, common_names[name], ext) <
               (int)n;
    if (!id3->album || !*id3->album)
        return false;
    strlcpy(stem, id3->album, sizeof(stem));
    fix_path_part(stem, 0, strlen(stem));
    return snprintf(out, n, "%s/%s.%s", dir, stem, ext) < (int)n;
}

bool art_file_usable(const char *path)
{
    int fd = open(path, O_RDONLY);
    unsigned char b[12];
    bool ok = fd >= 0 && read(fd, b, sizeof(b)) == sizeof(b) &&
              ((b[0] == 0xff && b[1] == 0xd8 && b[2] == 0xff) ||
               !memcmp(b, "\x89PNG\r\n\x1a\n", 8) || !memcmp(b, "GIF8", 4) ||
               !memcmp(b, "BM", 2) ||
               (!memcmp(b, "RIFF", 4) && !memcmp(b + 8, "WEBP", 4)));
    if (fd >= 0)
        close(fd);
    return ok;
}

bool albumart_source_revision(struct albumart_source *src)
{
    unsigned char buf[512];
    if (!albumart_normalize(src->path, src->identity, sizeof(src->identity)))
        return false;
    int fd = open(src->path, O_RDONLY);
    if (fd < 0)
        return false;
    off_t size = ffilesize(fd);
    off_t left = src->size < 0 ? size : src->size;
    if (src->pos < 0 || src->pos > size || left > size - src->pos ||
        left <= 0 || lseek(fd, src->pos, SEEK_SET) != src->pos)
    {
        close(fd);
        return false;
    }
    uint64_t hash = 14695981039346656037ull ^ (uint64_t)size;
    while (left > 0)
    {
        size_t n = left < (off_t)sizeof(buf) ? (size_t)left : sizeof(buf);
        if (read(fd, buf, n) != (ssize_t)n)
        {
            close(fd);
            return false;
        }
        for (size_t i = 0; i < n; i++)
            hash = (hash ^ buf[i]) * 1099511628211ull;
        left -= n;
        yield();
    }
    close(fd);
    src->revision = hash;
    return true;
}

void albumart_source_key(const struct albumart_source *src, char *out, size_t n)
{
    snprintf(out, n, "%s@%llu:%u:%u:%u:%u:%u:%016llx", src->identity,
             (unsigned long long)src->pos, (unsigned)src->size, src->flags,
             src->width, src->height, src->stride,
             (unsigned long long)src->revision);
}

bool albumart_next_source(const struct mp3entry *id3, enum albumart_role role,
                          int policy, unsigned *cursor,
                          struct albumart_source *src)
{
    if (!id3 || !id3->path[0])
        return false;
    while (*cursor <= FILE_CANDIDATES + 1)
    {
        unsigned at = (*cursor)++;
        if (role != AA_ROLE_ARTIST)
        {
            bool files_first =
                policy == ART_SOURCE_FILES || policy == ART_SOURCE_FILES_FIRST;
            if (files_first)
                at = at < FILE_CANDIDATES    ? at + 1
                     : at == FILE_CANDIDATES ? 0
                                             : at;
            if (policy == ART_SOURCE_FILES && at == 0)
                continue;
            if (policy == ART_SOURCE_EMBEDDED && at > 0 &&
                at <= FILE_CANDIDATES)
                continue;
        }
        memset(src, 0, sizeof(*src));
        if (at == 0)
        {
            if (role == AA_ROLE_ARTIST || !id3->has_embedded_albumart ||
                !image_embedded_usable(id3->albumart.type))
                continue;
            src->kind = AA_SOURCE_EMBEDDED;
            strlcpy(src->path, id3->path, sizeof(src->path));
            src->pos = id3->albumart.pos;
            src->size = id3->albumart.size;
            src->flags = id3->albumart.type & ~AA_CLEAR_FLAGS_MASK;
        }
        else if (at <= FILE_CANDIDATES)
        {
            if (role == AA_ROLE_ARTIST)
            {
                unsigned index = at - 1;
                if (index >= ARRAYLEN(common_names) * ARRAYLEN(extensions))
                    continue;
                char dir[MAX_PATH];
                strlcpy(dir, id3->path, sizeof(dir));
                snprintf(src->path, sizeof(src->path), "%s/%s.%s", dir,
                         common_names[index / ARRAYLEN(extensions)],
                         extensions[index % ARRAYLEN(extensions)]);
            }
            else if (!file_candidate(id3, at - 1, src->path, sizeof(src->path)))
                continue;
            if (!art_file_usable(src->path))
                continue;
            src->kind = AA_SOURCE_FILE;
            src->size = -1;
        }
        else
        {
            if (role == AA_ROLE_ARTIST || role == AA_ROLE_BOOK ||
                policy == ART_SOURCE_FILES || !itunes_art_find(id3->path, src))
                continue;
        }
        if (albumart_source_revision(src))
            return true;
    }
    return false;
}

int albumart_decode(const struct albumart_source *src, struct bitmap *bm,
                    int maxsize, int format, const struct custom_format *cf)
{
    int fd = open(src->path, O_RDONLY);
    if (fd < 0)
        return -1;
    int rc;
    if (src->kind == AA_SOURCE_ITUNES)
    {
        struct dim source = {src->width, src->height};
        if (!(format & FORMAT_RESIZE))
        {
            bm->width = source.width;
            bm->height = source.height;
        }
        else if (format & FORMAT_KEEP_ASPECT)
        {
            struct dim target = {bm->width, bm->height};
            recalc_dimension(&target, &source);
            bm->width = target.width;
            bm->height = target.height;
        }
        size_t bytes = (size_t)bm->width * bm->height * FB_DATA_SZ;
        rc = -1;
        if (bm->width > 0 && bm->height > 0 && bytes <= (size_t)maxsize)
        {
            rc = bytes;
            if (!(format & FORMAT_RETURN_SIZE))
            {
                unsigned char row[640];
                if (src->width > 320)
                    rc = -1;
                for (int y = 0; rc > 0 && y < bm->height; y++)
                {
                    off_t at =
                        src->pos +
                        (off_t)(y * src->height / bm->height) * src->stride * 2;
                    if (!image_progress(cf, y, bm->height) ||
                        lseek(fd, at, SEEK_SET) != at ||
                        read(fd, row, src->width * 2) !=
                            (ssize_t)(src->width * 2))
                    {
                        rc = -1;
                        break;
                    }
                    fb_data *dst = (fb_data *)bm->data + (size_t)y * bm->width;
                    for (int x = 0; x < bm->width; x++)
                    {
                        int sx = x * src->width / bm->width;
                        unsigned px = row[sx * 2] | row[sx * 2 + 1] << 8;
                        dst[x] = LCD_RGBPACK(((px >> 11) & 31) * 255 / 31,
                                             ((px >> 5) & 63) * 255 / 63,
                                             (px & 31) * 255 / 31);
                    }
                    yield();
                }
            }
        }
    }
    else
    {
        struct image_src image = {fd, src->pos, src->size, src->flags};
        rc = image_load(&image, bm, maxsize, format, cf);
    }
    close(fd);
    return rc;
}

bool albumart_find_source(const struct mp3entry *id3, int preference,
                          bool as_stored, struct albumart_source *src)
{
    if (!id3 || preference <= AA_OFF)
        return false;
    if (!as_stored)
    {
        unsigned cursor = 0;
        int policy = preference == AA_PREFER_IMAGE_FILE ? ART_SOURCE_FILES_FIRST
                     : preference == AA_PREFER_EMBEDDED
                         ? ART_SOURCE_EMBEDDED_FIRST
                         : global_settings.art_cache_album_source;
        return albumart_next_source(id3, AA_ROLE_ALBUM, policy, &cursor, src);
    }
    /* Exports use the worker's chosen and decoded source, including PNG and
     * iTunes-only artwork. A sniffed but corrupt JPEG cannot bypass it. */
    bool fallback;
    int size = art_cache_size_index("wps");
    if (size >= 0 &&
        art_cache_lookup_preference(id3->path, preference, size, src->path,
                                    sizeof(src->path), &fallback) &&
        !fallback)
    {
        src->kind = AA_SOURCE_CACHE;
        src->pos = 0;
        src->size = -1;
        return true;
    }
    src->kind = AA_SOURCE_NONE;
    return false;
}
