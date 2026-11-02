/***************************************************************************
 * GNU General Public License (version 2+)
 * Read-only iTunesDB -> ArtworkDB -> ithmb adapter. Record layouts and RGB565
 * format identifiers follow libgpod's db-itunes-parser.h and itdb_device.c.
 * Every child is bounded by its parent; no file or directory is inferred from
 * a track's containing folder.
 ****************************************************************************/
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "file.h"
#include "string-extra.h"
#include "albumart.h"
#include "itunes_art.h"

#ifndef ITUNES_CONTROL
#define ITUNES_CONTROL "/iPod_Control"
#endif

static uint32_t le32(const unsigned char *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 |
           (uint32_t)p[3] << 24;
}

static uint64_t le64(const unsigned char *p)
{
    return le32(p) | (uint64_t)le32(p + 4) << 32;
}

static bool read_at(int fd, off_t at, void *buf, size_t n)
{
    return lseek(fd, at, SEEK_SET) == at && read(fd, buf, n) == (ssize_t)n;
}

struct record
{
    off_t at, body, end;
    unsigned char h[40];
};

static bool record_at(int fd, off_t at, off_t end, const char *kind,
                      struct record *r, bool list)
{
    if (at < 0 || end - at < 12 || !read_at(fd, at, r->h, 12) ||
        memcmp(r->h, kind, 4))
        return false;
    uint32_t header = le32(r->h + 4);
    uint32_t size = list ? (uint32_t)(end - at) : le32(r->h + 8);
    if (header < 12 || size < header || size > (uint64_t)(end - at))
        return false;
    r->at = at;
    r->body = at + header;
    r->end = at + size;
    size_t n = size < sizeof(r->h) ? size : sizeof(r->h);
    return read_at(fd, at, r->h, n);
}

/* UTF-16LE database strings are converted to UTF-8, including surrogate pairs.
 */
static bool string_at(int fd, off_t at, unsigned bytes, bool wide, char *out,
                      size_t cap)
{
    size_t used = 0;
    if (bytes > 4096 || (wide && (bytes & 1)))
        return false;
    while (bytes)
    {
        unsigned char b[4];
        unsigned unit = wide ? 2 : 1;
        if (!read_at(fd, at, b, unit))
            return false;
        at += unit;
        bytes -= unit;
        uint32_t c = wide ? b[0] | b[1] << 8 : b[0];
        if (!c)
            break;
        if (wide && c >= 0xd800 && c <= 0xdbff)
        {
            if (bytes < 2 || !read_at(fd, at, b, 2))
                return false;
            uint32_t low = b[0] | b[1] << 8;
            if (low < 0xdc00 || low > 0xdfff)
                return false;
            c = 0x10000 + ((c - 0xd800) << 10) + low - 0xdc00;
            bytes -= 2;
            at += 2;
        }
        if (!wide || c < 128)
        {
            if (used + 1 >= cap)
                return false;
            out[used++] = c == ':' ? '/' : c;
        }
        else
        {
            unsigned n = c < 0x800 ? 2 : c < 0x10000 ? 3 : 4;
            if (used + n >= cap)
                return false;
            for (unsigned j = n - 1; j; j--)
            {
                out[used + j] = 0x80 | (c & 63);
                c >>= 6;
            }
            out[used] = (n == 2 ? 0xc0 : n == 3 ? 0xe0 : 0xf0) | c;
            used += n;
        }
    }
    out[used] = 0;
    return true;
}

static bool track_ids(int fd, const char *path, uint64_t *dbid, uint32_t *link)
{
    struct record root, section, list, track, text;
    off_t end = ffilesize(fd);
    char wanted[MAX_PATH], candidate[MAX_PATH], normalized[MAX_PATH];
    if (!albumart_normalize(path, wanted, sizeof(wanted)) ||
        !record_at(fd, 0, end, "mhbd", &root, false))
        return false;
    for (off_t at = root.body; at < root.end; at = section.end)
    {
        if (!record_at(fd, at, root.end, "mhsd", &section, false))
            return false;
        if (le32(section.h + 12) != 1)
            continue;
        if (!record_at(fd, section.body, section.end, "mhlt", &list, true))
            return false;
        for (off_t p = list.body; p < list.end; p = track.end)
        {
            if (!record_at(fd, p, list.end, "mhit", &track, false))
                return false;
            for (off_t q = track.body; q < track.end; q = text.end)
            {
                if (!record_at(fd, q, track.end, "mhod", &text, false))
                    return false;
                if (le32(text.h + 12) != 2 || text.end - text.body < 16)
                    continue;
                unsigned char h[16];
                if (!read_at(fd, text.body, h, 16))
                    return false;
                unsigned bytes = le32(h + 4);
                if (bytes > (uint64_t)(text.end - text.body - 16) ||
                    !string_at(fd, text.body + 16, bytes, true, candidate,
                               sizeof(candidate)) ||
                    !albumart_normalize(candidate, normalized,
                                        sizeof(normalized)) ||
                    strcmp(normalized, wanted))
                    continue;
                unsigned char ids[8];
                if (track.body - track.at < 0x78 ||
                    !read_at(fd, track.at + 0x70, ids, sizeof(ids)))
                    return false;
                *dbid = le64(ids);
                *link = 0;
                if (track.body - track.at >= 0x164 &&
                    read_at(fd, track.at + 0x160, ids, 4))
                    *link = le32(ids);
                return true;
            }
        }
    }
    return false;
}

static bool thumbnail(int fd, const struct record *image,
                      struct albumart_source *src)
{
    struct record data, thumb, name;
    bool found = false;
    for (off_t at = image->body; at < image->end; at = data.end)
    {
        if (!record_at(fd, at, image->end, "mhod", &data, false))
            return false;
        if ((data.h[12] | data.h[13] << 8) != 2 ||
            !record_at(fd, data.body, data.end, "mhni", &thumb, false))
            continue;
        unsigned format = le32(thumb.h + 16), dim;
        switch (format)
        {
        case 1061:
            dim = 56;
            break;
        case 1055:
            dim = 128;
            break;
        case 1060:
            dim = 320;
            break;
        default:
            continue;
        }
        unsigned height = thumb.h[32] | thumb.h[33] << 8;
        unsigned width = thumb.h[34] | thumb.h[35] << 8;
        unsigned top = thumb.h[28] | thumb.h[29] << 8;
        unsigned left = thumb.h[30] | thumb.h[31] << 8;
        unsigned bytes = le32(thumb.h + 24);
        if (!width || !height || width + left > dim || height + top > dim ||
            bytes < dim * dim * 2 || (found && src->width >= width))
            continue;
        if (!record_at(fd, thumb.body, thumb.end, "mhod", &name, false) ||
            (name.h[12] | name.h[13] << 8) != 3 || name.end - name.at < 36)
            continue;
        unsigned n = le32(name.h + 24);
        char filename[MAX_PATH];
        if (n > (uint64_t)(name.end - name.at - 36) ||
            !string_at(fd, name.at + 36, n, name.h[28] == 2, filename,
                       sizeof(filename)))
            continue;
        const char *base = filename[0] == '/' ? filename + 1 : filename;
        if (!*base || strchr(base, '/') || strstr(base, ".."))
            continue;
        char candidate[MAX_PATH];
        if (snprintf(candidate, sizeof(candidate), ITUNES_CONTROL "/Artwork/%s",
                     base) >= (int)sizeof(candidate))
            continue;
        int pixels = open(candidate, O_RDONLY);
        off_t pos = le32(thumb.h + 20);
        bool valid = pixels >= 0 && pos <= ffilesize(pixels) &&
                     bytes <= (uint64_t)(ffilesize(pixels) - pos);
        if (pixels >= 0)
            close(pixels);
        if (!valid)
            continue;
        strlcpy(src->path, candidate, sizeof(src->path));
        src->kind = AA_SOURCE_ITUNES;
        src->pos = pos + (top * dim + left) * 2;
        src->size = ((height - 1) * dim + width) * 2;
        src->width = width;
        src->height = height;
        src->stride = dim;
        src->flags = 0;
        found = true;
    }
    return found;
}

bool itunes_art_find(const char *path, struct albumart_source *src)
{
    uint64_t dbid = 0;
    uint32_t link = 0;
    int fd = open(ITUNES_CONTROL "/iTunes/iTunesDB", O_RDONLY);
    if (fd < 0)
        return false;
    bool found = track_ids(fd, path, &dbid, &link);
    close(fd);
    if (!found)
        return false;
    fd = open(ITUNES_CONTROL "/Artwork/ArtworkDB", O_RDONLY);
    if (fd < 0)
        return false;
    struct record root, section, list, image;
    found = false;
    if (!record_at(fd, 0, ffilesize(fd), "mhfd", &root, false))
        goto out;
    for (off_t at = root.body; at < root.end; at = section.end)
    {
        if (!record_at(fd, at, root.end, "mhsd", &section, false))
            break;
        if (le32(section.h + 12) != 1)
            continue;
        if (!record_at(fd, section.body, section.end, "mhli", &list, true))
            break;
        for (off_t p = list.body; p < list.end; p = image.end)
        {
            if (!record_at(fd, p, list.end, "mhii", &image, false))
                goto out;
            if ((link && le32(image.h + 16) == link) ||
                (!link && le64(image.h + 20) == dbid))
            {
                found = thumbnail(fd, &image, src);
                goto out;
            }
        }
    }
out:
    close(fd);
    return found;
}
