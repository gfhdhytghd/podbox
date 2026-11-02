"""Complete ID3/MP4/FLAC/Vorbis parsing and extracted bitmap ownership tests."""
import shlex
from pathlib import Path
import struct
import base64
from PIL import Image
from support import ROOT, OUT, run, build, function
FIX = OUT / 'fixtures'
flags = shlex.split((OUT / 'decode-compile-flags.txt').read_text())
cc, flags = flags[0], flags[1:] + ['-I' + str(ROOT)]
for color in ['red', 'blue']:
    Image.new('RGB', (80, 48), color).save(FIX / (color + '.png'))
objects = [OUT / (n + '.o') for n in ['jpeg_load', 'png_load', 'gif_load', 'webp_load',
           'resize', 'bmp', 'image_load', 'byte_stream', 'inflate', 'ogg',
           'metadata_common', 'replaygain', 'host_io']]
# ID3 parser regression: call the complete production frame loop, not a model.
red=(FIX/'red.png').read_bytes(); blue=(FIX/'blue.png').read_bytes()
def apic(pic,kind=3,encoding=1):
    description=b'\xff\xfeA\0\0\0' if encoding==1 else b'A\0'
    payload=bytes([encoding])+b'image/png\0'+bytes([kind])+description+pic
    return b'APIC'+struct.pack('>I',len(payload))+b'\0\0'+payload
frames=apic(red,4,0)+apic(blue,3,1)
n=len(frames);sync=bytes([(n>>21)&127,(n>>14)&127,(n>>7)&127,n&127])
tag=b'ID3\x03\0\0'+sync+frames
(FIX/'front.mp3').write_bytes(tag)
expected=tag.index(blue)
# Include MP4's static tag loop in this translation unit for a real covr test.
metadata_test=r'''

#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#include "config.h"
#include "system.h"
#include "file.h"
#include "metadata.h"
#include "draw/image_load.h"
#include "lib/rbcodec/metadata/mp4.c"
bool get_flac_metadata(int, struct mp3entry *);
void setid3v2title(int fd, struct mp3entry *id3);
void yield(void)
{
}
void debugf(const char *x, ...)
{
    (void)x;
}
void panicf(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    abort();
}
#include <assert.h>
int main(int argc, char **argv)
{
    assert(argc == 6);
    struct mp3entry id3 = {0};
    int fd = open(argv[1], O_RDONLY);
    assert(fd >= 0);
    id3.id3v2len = ffilesize(fd);
    setid3v2title(fd, &id3);
    assert(id3.has_embedded_albumart && id3.albumart_picture_type == 3);
    assert(id3.albumart.pos == atoi(argv[2]));
    unsigned char *pixels = malloc(1024 * 1024);
    assert(pixels);
    struct bitmap bm = {.width = 40, .height = 40, .data = pixels};
    struct image_src src = {fd, id3.albumart.pos, id3.albumart.size, 0};
    assert(image_load(&src, &bm, 1024 * 1024,
                      FORMAT_NATIVE | FORMAT_RESIZE | FORMAT_KEEP_ASPECT,
                      NULL) > 0);
    assert(RGB_UNPACK_BLUE(((fb_data *)pixels)[0]) > 200);
    close(fd);
    puts("full ID3 frame loop selects UTF16 front PNG at exact offset");
    memset(&id3, 0, sizeof id3);
    fd = open(argv[3], O_RDONLY);
    assert(fd >= 0);
    assert(read_mp4_tags(fd, &id3, ffilesize(fd)));
    assert(id3.has_embedded_albumart && id3.albumart.type == AA_TYPE_JPG);
    close(fd);
    puts("MP4 covr recognizes EXIF-first JPEG");
    for (int kind = 0; kind < 2; kind++)
    {
        memset(&id3, 0, sizeof id3);
        fd = open(argv[4 + kind], O_RDONLY);
        assert(fd >= 0);
        id3.codectype = kind ? AFMT_OGG_VORBIS : AFMT_FLAC;
        if (kind)
        {
            lseek(fd, 58, SEEK_SET);
            assert(read_vorbis_tags(fd, &id3, ffilesize(fd) - 58) > 0);
        }
        else
            assert(get_flac_metadata(fd, &id3));
        assert(id3.has_embedded_albumart && id3.albumart_picture_type == 3);
        bm.width = bm.height = 40;
        src.fd = fd;
        src.pos = id3.albumart.pos;
        src.len = id3.albumart.size;
        src.flags = id3.albumart.type & ~AA_CLEAR_FLAGS_MASK;
        assert(image_load(&src, &bm, 1024 * 1024,
                          FORMAT_NATIVE | FORMAT_RESIZE | FORMAT_KEEP_ASPECT,
                          NULL) > 0);
        assert(RGB_UNPACK_BLUE(((fb_data *)pixels)[0]) > 200);
        close(fd);
    }
    free(pixels);
    puts("FLAC PICTURE and Vorbis base64 choose front cover and decode PNG");
    return 0;
}
'''
# Read_mp4_tags accepts the ilst children (covr here), with its data subatom.
jpeg=(OUT/'fixtures/exif-first.jpg').read_bytes()
data=struct.pack('>I4sII',16+len(jpeg),b'data',13,0)+jpeg
(FIX/'covr.bin').write_bytes(struct.pack('>I4s',8+len(data),b'covr')+data)
def picture(pic,kind):
    return struct.pack('>II',kind,9)+b'image/png'+struct.pack('>I',0)+struct.pack('>IIIII',80,48,24,0,len(pic))+pic
stream=bytearray(34);stream[10:18]=((44100<<44)|44100).to_bytes(8,'big')
blocks=[bytes(stream),picture(red,4),picture(blue,3)]
flac=b'fLaC'
for i,b in enumerate(blocks):
    flac+=bytes([(0 if i==0 else 6)|(128 if i==2 else 0)])+len(b).to_bytes(3,'big')+b
(FIX/'pictures.flac').write_bytes(flac)
def page(payload,seq):
    lace=bytes([255])*(len(payload)//255)+bytes([len(payload)%255])
    return b'OggS'+bytes([0,2 if seq==0 else 0])+b'\0'*8+struct.pack('<III',1,seq,0)+bytes([len(lace)])+lace+payload
ident=b'\x01vorbis'+b'\0'*23
comments=[b'METADATA_BLOCK_PICTURE='+base64.b64encode(picture(pic,kind)) for pic,kind in [(red,4),(blue,3)]]
body=b'\x03vorbis'+struct.pack('<I',0)+struct.pack('<I',len(comments))+b''.join(struct.pack('<I',len(c))+c for c in comments)+b'\x01'
(FIX/'pictures.ogg').write_bytes(page(ident,0)+page(body,1))
(OUT/'metadata_test.c').write_text(metadata_test)
# Full Unicode helpers are needed by ID3 text tags even for an image-only file.
for source in ['apps-ipod/system/strutil.c','lib/rbcodec/metadata/id3tags.c','lib/rbcodec/metadata/flac.c','lib/rbcodec/metadata/vorbis.c','firmware/common/unicode.c','firmware/common/strcasestr.c','firmware/common/pathfuncs.c','lib/rbcodec/metadata/mp3data.c','lib/fixedpoint/fixedpoint.c']:
    run([cc,*flags,*(['-D__PCTOOL__'] if source.endswith('unicode.c') else []),'-c',str(ROOT/source),'-o',str(OUT/(Path(source).stem+'.o'))],'compile-meta-'+Path(source).stem)
metaobjs=[p for p in objects if p.name not in ['albumart.o','itunes_art.o','id3tags.o','vorbis.o']]
metaobjs += [OUT/(n+'.o') for n in ['strutil','id3tags','flac','vorbis','unicode','strcasestr','pathfuncs','mp3data','fixedpoint']]
run([cc,*flags,str(OUT/'metadata_test.c'),*map(str,metaobjs),'-Wl,--gc-sections','-o',str(OUT/'metadata_test')],'link-metadata')
run([str(OUT/'metadata_test'),str(FIX/'front.mp3'),str(expected),str(FIX/'covr.bin'),str(FIX/'pictures.flac'),str(FIX/'pictures.ogg')],'metadata')
print('Metadata regressions passed')
refs=r'''

struct mutex
{
    int n;
};
static struct mutex llist_mutex;
static void mutex_lock(struct mutex *m)
{
    (void)m;
}
static void mutex_unlock(struct mutex *m)
{
    (void)m;
}
enum
{
    TYPE_BITMAP = 1
};
struct memory_handle
{
    int id, type, fd;
    unsigned references;
    int pinned;
};
static struct memory_handle current;
static bool alive;
static int freed;
static struct memory_handle *find_handle(int id)
{
    return alive && current.id == id ? &current : NULL;
}
static void close_fd(int *fd)
{
    *fd = -1;
}
static void unlink_handle(struct memory_handle *h)
{
    assert(h->references == 1);
    alive = false;
    freed++;
}
'''
for name in ['bufretain','buf_pin_handle','close_handle']:
    refs+=function('apps-ipod/audio/buffering.c',name)
refs+=r'''

int main(void)
{
    for (int i = 0; i < 1000; i++)
    {
        current = (struct memory_handle){i + 1, TYPE_BITMAP, -1, 1, 0};
        alive = true;
        assert(bufretain(i + 1));
        assert(buf_pin_handle(i + 1, true));
        assert(close_handle(i + 1));
        assert(alive && current.references == 1);
        assert(buf_pin_handle(i + 1, false));
        assert(close_handle(i + 1));
        assert(!alive);
        assert(!bufretain(i + 1));
    }
    assert(freed == 1000);
    puts("1000 track releases preserve retained bitmap readers until their "
         "release");
    return 0;
}
'''
run([str(build('references_test',refs))],'references')
