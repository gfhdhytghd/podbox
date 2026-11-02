"""Complete resolver/iTunes/decoder modules with generated image and DB fixtures."""
import shlex
from pathlib import Path
import struct
from PIL import Image
from support import ROOT, OUT, run

FIX = OUT / 'resolver-fixtures'
CTRL = FIX / 'iPod_Control'
(CTRL / 'iTunes').mkdir(parents=True, exist_ok=True)
(CTRL / 'Artwork').mkdir(exist_ok=True)

def record(kind, header, body=b'', fields=()):
    data = bytearray(header)
    struct.pack_into('<4sII', data, 0, kind, header, header + len(body))
    for offset, fmt, value in fields:
        struct.pack_into(fmt, data, offset, value)
    return bytes(data) + body

path = ':iPod_Control:Music:F00:track.mp3'.encode('utf-16le')
text = record(b'mhod', 24, struct.pack('<IIII', 1, len(path), 0, 0) + path, [(12, '<I', 2)])
track = record(b'mhit', 0x164, text, [(0x70, '<Q', 123), (0x160, '<I', 7)])
db = record(b'mhbd', 12, record(b'mhsd', 16, record(b'mhlt', 12, track), [(12, '<I', 1)]))
(CTRL / 'iTunes/iTunesDB').write_bytes(db)

def thumb(name, dim, fmt):
    name = name.encode('utf-16le')
    leaf = record(b'mhod', 36, name, [(12, '<I', 3), (24, '<I', len(name)), (28, '<B', 2)])
    info = record(b'mhni', 40, leaf, [(16, '<I', fmt), (20, '<I', 0),
                                   (24, '<I', dim * dim * 2), (32, '<H', dim), (34, '<H', dim)])
    return record(b'mhod', 24, info, [(12, '<I', 2)])

small = thumb(':small.ithmb', 56, 1061)
large = thumb(':missing.ithmb', 320, 1060)
short = thumb(':short.ithmb', 128, 1055)
(CTRL / 'Artwork/small.ithmb').write_bytes(b'\x00\xf8' * 56 * 56)
(CTRL / 'Artwork/short.ithmb').write_bytes(b'\x00\x00' * 10)
art = record(b'mhfd', 12, record(b'mhsd', 16, record(b'mhli', 12,
             record(b'mhii', 40, small + large + short, [(16, '<I', 7), (20, '<Q', 123)])), [(12, '<I', 1)]))
(CTRL / 'Artwork/ArtworkDB').write_bytes(art)
author = FIX / 'library/author'
(author / 'book').mkdir(parents=True, exist_ok=True)
for target, color in [(author / 'folder.png', 'red'), (author.parent / 'folder.png', 'blue'),
                      (FIX / 'embedded.png', 'red'), (FIX / 'folder.png', 'blue')]:
    Image.new('RGB', (20, 20), color).save(target)

flags = shlex.split((OUT / 'decode-compile-flags.txt').read_text())
cc, flags = flags[0], flags[1:]
objects = []
for source in ['apps-ipod/metadata/albumart.c', 'apps-ipod/metadata/itunes_art.c',
               'apps-ipod/system/strutil.c', 'apps-ipod/database/path_key.c',
               'firmware/common/strlcpy.c', 'firmware/common/strmemccpy.c']:
    obj = OUT / (Path(source).stem + '-resolver.o')
    run([cc, *flags, '-DITUNES_CONTROL="' + str(CTRL) + '"', '-c', ROOT / source, '-o', obj],
        'compile-' + obj.stem)
    objects.append(obj)
objects += [OUT / (name + '.o') for name in ['jpeg_load','png_load','gif_load','webp_load','resize',
            'bmp','image_load','byte_stream','inflate','id3tags','ogg','vorbis','metadata_common','replaygain','host_io']]
modern = 'int policy' in (ROOT / 'apps-ipod/metadata/albumart.h').read_text()
prog = r'''

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#include "config.h"
#include "system.h"
#include "file.h"
#include "metadata/albumart.h"
#include "metadata/itunes_art.h"
#include "metadata/art_cache.h"
#include "draw/image_load.h"
#include "settings/settings.h"
struct user_settings global_settings;
void panicf(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    abort();
}
void yield(void)
{
}
void debugf(const char *fmt, ...)
{
    (void)fmt;
}
bool art_cache_lookup(const char *a, int b, char *c, int d, bool *e)
{
    (void)a;
    (void)b;
    (void)c;
    (void)d;
    (void)e;
    return false;
}
bool art_cache_lookup_preference(const char *a, int p, int b, char *c, int d,
                                 bool *e)
{
    (void)p;
    return art_cache_lookup(a, b, c, d, e);
}
int art_cache_size_index(const char *a)
{
    (void)a;
    return -1;
}
'''
prog += '#define NEXT(i,r,p,c,s) albumart_next_source(i,r,' + ('p,' if modern else '') + 'c,s)\n'
prog += r'''

int main(int argc, char **argv)
{
    assert(argc == 3);
    struct albumart_source s = {0};
    struct mp3entry id3 = {0};
    if (!strcmp(argv[2], "R7"))
    {
        assert(itunes_art_find("/iPod_Control/Music/F00/track.mp3", &s));
        assert(s.width == 56 && s.height == 56 && s.stride == 56 &&
               s.pos == 0 && s.size == 56 * 56 * 2);
        assert(strstr(s.path, "/small.ithmb"));
        unsigned char *buf = malloc(1024 * 1024);
        struct bitmap bm = {.width = 56, .height = 56, .data = buf};
        assert(
            albumart_decode(&s, &bm, 1024 * 1024,
                            FORMAT_NATIVE | FORMAT_RESIZE | FORMAT_KEEP_ASPECT,
                            NULL) > 0);
        assert(RGB_UNPACK_RED(((fb_data *)buf)[0]) > 240);
        free(buf);
        assert(!itunes_art_find("/iPod_Control/Music/F00/other.mp3", &s));
        puts("R7: rejected missing and short candidates preserve accepted "
             "pixels and geometry");
    }
    else if (!strcmp(argv[2], "R6"))
    {
        snprintf(id3.path, sizeof(id3.path), "%s/library/author", argv[1]);
        unsigned c = 0;
        assert(NEXT(&id3, AA_ROLE_ARTIST, ART_SOURCE_FILES, &c, &s));
        char wanted[MAX_PATH];
        snprintf(wanted, sizeof(wanted), "%s/library/author/folder.png",
                 argv[1]);
        assert(!strcmp(s.path, wanted));
        puts("R6-resolver: artist directory resolves its own portrait");
    }
    else
    {
        snprintf(id3.path, sizeof(id3.path), "%s/embedded.png", argv[1]);
        int fd = open(id3.path, O_RDONLY);
        assert(fd >= 0);
        id3.has_embedded_albumart = true;
        id3.albumart.type = AA_TYPE_PNG;
        id3.albumart.pos = 0;
        id3.albumart.size = ffilesize(fd);
        close(fd);
        assert(albumart_find_source(&id3, AA_PREFER_IMAGE_FILE, false, &s));
        assert(s.kind == AA_SOURCE_FILE);
        assert(albumart_find_source(&id3, AA_PREFER_EMBEDDED, false, &s));
        assert(s.kind == AA_SOURCE_EMBEDDED);
        for (int policy = ART_SOURCE_FILES; policy <= ART_SOURCE_EMBEDDED;
             policy++)
        {
            unsigned c = 0;
            assert(NEXT(&id3, AA_ROLE_ALBUM, policy, &c, &s));
            assert(s.kind == (policy <= ART_SOURCE_FILES_FIRST
                                  ? AA_SOURCE_FILE
                                  : AA_SOURCE_EMBEDDED));
            do
            {
                if (policy == ART_SOURCE_FILES)
                    assert(s.kind == AA_SOURCE_FILE);
                if (policy == ART_SOURCE_EMBEDDED)
                    assert(s.kind != AA_SOURCE_FILE);
            } while (NEXT(&id3, AA_ROLE_ALBUM, policy, &c, &s));
        }
        puts("R3-resolver: four cache policies and two explicit playback "
             "preferences");
    }
    return 0;
}
'''
(OUT / 'resolver.c').write_text(prog)
run([cc, *flags, OUT / 'resolver.c', *objects, '-Wl,--gc-sections', '-o', OUT / 'resolver'], 'link-resolver')
failed = []
for case in ['R3', 'R6', 'R7']:
    try:
        print(run([OUT / 'resolver', FIX, case], case + '-resolver'), end='', flush=True)
    except RuntimeError as error:
        print(error, flush=True)
        failed.append(case)
if failed:
    raise SystemExit('FAIL: ' + ', '.join(failed))
