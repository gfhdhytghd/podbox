#!/usr/bin/env python3
"""Host regressions for artwork; no player, mounted library or network writes."""
from pathlib import Path
import json, re, shlex, subprocess, shutil, struct, os, random, base64
from PIL import Image, ImageDraw
ROOT=Path(__file__).resolve().parents[1]
OUT=Path(__file__).resolve().parent
FIX=OUT/'fixtures'; FIX.mkdir(exist_ok=True)
BACKUP=Path('/home/wilf/data/PodJS/ipod/backups/pocketrock-2026-11-02/iPod_Control')

def function(path,name):
    text=(ROOT/path).read_text()
    match=re.search(r'(?m)^[\w *]+\b'+name+r'\s*\([^;{}]*\)\s*\{',text)
    if not match: raise ValueError(name)
    start=match.start();brace=match.end()-1;depth=0
    for token in re.finditer(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}]',text[brace:],re.S):
        if token.group()=='{': depth+=1
        if token.group()=='}':
            depth-=1
            if depth==0:
                return '\n#line %d "%s"\n%s\n'%(text[:start].count('\n')+1,path,text[start:brace+token.end()])
    raise ValueError(name)

def run(args, name, cwd=OUT):
    p=subprocess.run(args,cwd=cwd,capture_output=True,text=True)
    (OUT/(name+'.log')).write_text('$ '+shlex.join(map(str,args))+'\n'+p.stdout+p.stderr)
    print(name,p.returncode)
    if p.returncode: print((p.stdout+p.stderr)[-5000:])
    assert p.returncode==0, name
    assert 'runtime error:' not in p.stderr and 'ERROR: AddressSanitizer' not in p.stderr
    return p.stdout

# The actual decoder units are compiled by the decoder regression entry point.
flags=shlex.split((ROOT/'review-artifacts/decode-compile-flags.txt').read_text())
cc=flags[0];flags=flags[1:]
for source in ['apps-ipod/metadata/albumart.c','apps-ipod/metadata/itunes_art.c','apps-ipod/system/strutil.c','apps-ipod/database/path_key.c','firmware/common/strlcpy.c','firmware/common/strmemccpy.c']:
    run([cc,*flags,'-c',str(ROOT/source),'-o',str(OUT/(Path(source).stem+'.o'))], 'compile-'+Path(source).stem)
# Small native decoder fixtures; artwork adapter points only at the read-only backup.
im=Image.new('RGB',(80,48),'blue');im.save(FIX/'blue.png')
im=Image.new('RGB',(80,48),'red');im.save(FIX/'red.png')
(FIX/'cover.jpg').write_bytes(b'\xff\xd8\xff\xe0'+b'bad JPEG'*10)
shutil.copyfile(FIX/'blue.png',FIX/'folder.png')
for folder,filename in [('front-only','front.jpg'),('folder-bmp','folder.bmp'),('album-name','Album.png')]:
    (FIX/folder).mkdir(exist_ok=True)
    Image.new('RGB',(80,48),'blue').save(FIX/folder/filename)
# Select a real track with an ArtworkDB mapping using independent record parsing.
d=(BACKUP/'iTunes/iTunesDB').read_bytes();a=(BACKUP/'Artwork/ArtworkDB').read_bytes()
art_ids=set();dbids=set()
at=0
while (at:=a.find(b'mhii',at))>=0:
    art_ids.add(struct.unpack_from('<I',a,at+16)[0]);dbids.add(struct.unpack_from('<Q',a,at+20)[0]);at+=4
tracks=[];at=0
while (at:=d.find(b'mhit',at))>=0:
    h,total=struct.unpack_from('<II',d,at+4)
    if h<0x164 or at+total>len(d): at+=4;continue
    dbid=struct.unpack_from('<Q',d,at+0x70)[0];link=struct.unpack_from('<I',d,at+0x160)[0]
    end=at+total;q=at+h;path=None
    while q+24<=end and d[q:q+4]==b'mhod':
        mh,mt,kind=struct.unpack_from('<III',d,q+4)
        if kind==2:
            n=struct.unpack_from('<I',d,q+mh+4)[0]
            path=d[q+mh+16:q+mh+16+n].decode('utf-16le').rstrip('\0').replace(':','/')
        if mt<24: break
        q+=mt
    if path and ((link and link in art_ids) or (not link and dbid in dbids)): tracks.append(path)
    at=end
assert tracks,'backup has a track/art mapping'
(OUT/'itunes-track.json').write_text(json.dumps(tracks[:3],ensure_ascii=False,indent=2))
# Compile the adapter against a relocated constant without touching the backup.
run([cc,*flags,'-DITUNES_CONTROL="'+str(BACKUP)+'"','-c',str(ROOT/'apps-ipod/metadata/itunes_art.c'),'-o',str(OUT/'itunes_art.o')], 'compile-itunes-relocated')
prog=r'''
#include <assert.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include "config.h"
#include "system.h"
#include "file.h"
#include "metadata/albumart.h"
#include "metadata/itunes_art.h"
#include "draw/image_load.h"
void panicf(const char *fmt,...){va_list ap;va_start(ap,fmt);vfprintf(stderr,fmt,ap);va_end(ap);abort();}
void yield(void) {}
void debugf(const char *format,...) {(void)format;}
bool art_cache_lookup(const char*a,int b,char*c,int d,bool*e){(void)a;(void)b;(void)c;(void)d;(void)e;return false;}
int art_cache_size_index(const char *a){(void)a;return -1;}
static volatile unsigned request_generation;
static bool cancel(int done,int rows){(void)rows;if(done==3)request_generation++;return true;}
int main(int argc,char **argv){
 assert(argc==3); char key[MAX_PATH];
 assert(albumart_normalize("/Music//One/../Album/./TRACK.MP3/",key,sizeof key));
 assert(!strcmp(key,"/music/album/track.mp3"));
 assert(albumart_normalize("/<HDD0>/Music/Album/track.mp3",key,sizeof key));
 assert(!strcmp(key,"/music/album/track.mp3"));
 struct mp3entry id3={0}; snprintf(id3.path,sizeof id3.path,"%s/track.mp3",argv[1]);
 struct albumart_source src; unsigned cursor=0;unsigned seen=0;int ok=0;
 unsigned char *buf=malloc(1024*1024);assert(buf);
 while(albumart_next_source(&id3,AA_ROLE_ALBUM,&cursor,&src)){
   struct bitmap bm={.width=40,.height=40,.data=buf}; seen++;
   if(albumart_decode(&src,&bm,1024*1024,FORMAT_NATIVE|FORMAT_RESIZE|FORMAT_KEEP_ASPECT,NULL)>0){
      assert(strstr(src.path,"folder.png"));ok++;break;
   }
 }
 assert(seen>=2 && ok==1);puts("F05/F11: corrupt cover falls through to folder PNG");
 for(int i=0;i<3;i++){
   const char *dirs[]={"front-only","folder-bmp","album-name"};
   struct mp3entry other={0};other.album="Album";
   snprintf(other.path,sizeof other.path,"%s/%s/track.mp3",argv[1],dirs[i]);
   unsigned c=0;struct albumart_source found;
   assert(albumart_next_source(&other,AA_ROLE_ALBUM,&c,&found));
   struct bitmap check={.width=40,.height=40,.data=buf};
   assert(albumart_decode(&found,&check,1024*1024,FORMAT_NATIVE|FORMAT_RESIZE|FORMAT_KEEP_ASPECT,NULL)>0);
 }
 puts("F05/F11: album-name art, front.jpg and folder.bmp resolve and decode");
 uint64_t old=src.revision;
 FILE *f=fopen(src.path,"ab");assert(f);fputc(0,f);fclose(f);
 assert(albumart_source_revision(&src)&&src.revision!=old);puts("F04: external replacement changes revision");
 struct albumart_source embedded=src;embedded.kind=AA_SOURCE_EMBEDDED;
 embedded.pos=8;embedded.size=16;assert(albumart_source_revision(&embedded));
 old=embedded.revision;f=fopen(embedded.path,"r+b");assert(f);fseek(f,9,SEEK_SET);fputc(0x42,f);fclose(f);
 assert(albumart_source_revision(&embedded)&&embedded.revision!=old);
 puts("F04: changed embedded byte range invalidates its revision");
 /* Restore the image fixture before the independent decode checks. */
 f=fopen(embedded.path,"r+b");assert(f);fseek(f,9,SEEK_SET);fputc(0,f);fclose(f);
 struct bitmap bm={.width=40,.height=40,.data=buf};
 struct custom_format fmt=format_native;fmt.progress=cancel;fmt.cancel_generation=&request_generation;fmt.generation=request_generation;
 assert(albumart_decode(&src,&bm,1024*1024,FORMAT_NATIVE|FORMAT_RESIZE|FORMAT_KEEP_ASPECT,&fmt)<=0);
 for(int i=0;i<100;i++){
   bm.width=bm.height=40;
   assert(albumart_decode(&src,&bm,1024*1024,FORMAT_NATIVE|FORMAT_RESIZE|FORMAT_KEEP_ASPECT,NULL)>0);
 }
 puts("per-request cancellation; 100 subsequent independent decodes");
 struct albumart_source apple={0};
 assert(itunes_art_find(argv[2],&apple));assert(apple.kind==AA_SOURCE_ITUNES);
 bm.width=bm.height=56;
 assert(albumart_decode(&apple,&bm,1024*1024,FORMAT_NATIVE|FORMAT_RESIZE|FORMAT_KEEP_ASPECT,NULL)>0);
 printf("F15: %s at %ld %ux%u stride=%u\n",apple.path,(long)apple.pos,apple.width,apple.height,apple.stride);
 assert(!itunes_art_find("/iPod_Control/Music/F00/not-the-same-track.mp3",&apple));
 puts("iTunes nonexistent sibling: no folder guess");free(buf);return 0;
}
'''
(OUT/'resolver_test.c').write_text(prog)
objects=[OUT/(n+'.o') for n in ['albumart','itunes_art','strutil','path_key','strlcpy','strmemccpy']]
objects += [ROOT/'review-artifacts'/(n+'.o') for n in ['jpeg_load','png_load','gif_load','webp_load','resize','bmp','image_load','byte_stream','inflate','id3tags','ogg','vorbis','metadata_common','replaygain','host_io']]
run([cc,*flags,str(OUT/'resolver_test.c'),*map(str,objects),'-Wl,--gc-sections','-o',str(OUT/'resolver_test')],'link-resolver')
run([str(OUT/'resolver_test'),str(FIX),tracks[0]],'resolver')
# Probe the original playback function with source/decode stubs, as the review did.
common=r'''
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
'''
def build(name,code):
    (OUT/(name+'.c')).write_text(common+code)
    run(['cc','-std=gnu11','-g','-O1','-fsanitize=address,undefined','-fno-omit-frame-pointer',str(OUT/(name+'.c')),'-o',str(OUT/name)],'compile-'+name)
    return OUT/name
play=r'''
#define MAX_MULTIPLE_AA 1
#define FOREACH_ALBUMART(i) for(int i=0;i<1;i++)
enum {ERR_UNSUPPORTED_TYPE=-1,ERR_BUFFER_FULL=-2,ERR_BITMAP_TOO_LARGE=-3,TYPE_BITMAP=1};
enum {AA_OFF,AA_PREFER_EMBEDDED};
enum {WPS_ART_AUTO,WPS_ART_ARTIST,WPS_ART_ALBUM};
enum albumart_role {AA_ROLE_ALBUM,AA_ROLE_ARTIST};
struct {int wps_art_source,album_art;} global_settings={WPS_ART_ALBUM,AA_PREFER_EMBEDDED};
struct dim {int width,height;};
struct {struct dim dim;int used;} albumart_slots[1]={{{100,100},1}};
struct mp3entry {char path[MAX_PATH];char *album,*albumartist;};
struct track_info {int aa_hid[1];};
struct albumart_source {char path[MAX_PATH];};
struct bufopen_bitmap_data {struct dim *dim;struct albumart_source *source;const volatile unsigned *cancel_generation;unsigned generation;};
static int opens;
static bool playlist_is_from_artist(void){return false;}
static bool albumart_next_source(struct mp3entry*a,enum albumart_role role,unsigned*c,struct albumart_source*s){(void)role;if((*c)++)return false;strcpy(s->path,a->path);return true;}
static bool art_cache_source_lookup(struct albumart_source*s,int i,char*p,int n){(void)s;(void)i;(void)p;(void)n;return false;}
static int art_cache_size_index(const char*a){(void)a;return 0;}
static bool art_cache_lookup(const char*key,int size,char*path,int n,bool*fallback){(void)size;snprintf(path,n,"%s.aat",key);*fallback=false;return true;}
static int bufopen(const char*a,int b,int c,void*d){(void)a;(void)b;(void)c;(void)d;return ++opens+100;}
static void skin_albumart_art_opened(int a){(void)a;}
'''+function('apps-ipod/audio/playback.c','audio_load_albumart')+r'''
int main(void){
 struct mp3entry a={.album=NULL,.albumartist=NULL};strcpy(a.path,"/mixed/a.mp3");
 struct track_info t={{-4}};assert(audio_load_albumart(&t,&a,true));int first=t.aa_hid[0];
 strcpy(a.path,"/mixed/b.mp3");t.aa_hid[0]=-4;assert(audio_load_albumart(&t,&a,true));
 assert(first!=t.aa_hid[0] && opens==2);puts("F01/F02/F03: absent tags; distinct files do not reuse pixels");return 0;
}
'''
run([str(build('playback_test',play))],'playback')
print('Resolver and playback regressions passed')
# Stateful cache tests execute extracted production functions with POSIX I/O.
cache_root=OUT/'cache-test';shutil.rmtree(cache_root,ignore_errors=True);cache_root.mkdir()
header=(ROOT/'apps-ipod/metadata/art_cache.h').read_text()
header_struct=re.search(r'struct art_cache_header\s*\{.*?\};',header,re.S).group()
cache= r'''
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
'''+header_struct
for path,name in [('apps-ipod/database/path_key.c','path_key_strip'),('apps-ipod/metadata/albumart.c','albumart_normalize')]+[('apps-ipod/metadata/art_cache.c',n) for n in ['aa_hash','aa_checksum','art_cache_read_header','aa_identity_dirs','art_cache_dir_hash','aa_cache_path','aa_referenced','aa_reference','aa_make_room','aa_write_aat','art_cache_load_aat','aa_request_owner']]:
    cache+=function(path,name)
cache+=r'''
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
'''
# Find a collision among normalized candidate paths using the production hash algorithm.
def old_hash(s):
    h=0x811c9dc5
    for x in s.encode(): h=((h^x)*16777619)&0xffffffff
    h=(h+(h<<13))&0xffffffff;h^=h>>7;h=(h+(h<<3))&0xffffffff;h^=h>>17;return (h+(h<<5))&0xffffffff
seen={};collision=None
rng=random.Random(42)
for i in range(300000):
    s='/music/collision-%d'%rng.randrange(10**14);h=old_hash(s)
    if h in seen: collision=(seen[h],s);break
    seen[h]=s
assert collision
(OUT/'collision.json').write_text(json.dumps(collision))
cache='#define CACHE_ROOT '+json.dumps(str(cache_root))+'\n'+cache
run([str(build('cache_test',cache)),*collision],'cache')
print('Cache regressions passed')
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
#include "../lib/rbcodec/metadata/mp4.c"
bool get_flac_metadata(int,struct mp3entry*);
void setid3v2title(int fd,struct mp3entry *id3);
void yield(void){}
void debugf(const char *x,...){(void)x;}
void panicf(const char *fmt,...){va_list ap;va_start(ap,fmt);vfprintf(stderr,fmt,ap);va_end(ap);abort();}
#include <assert.h>
int main(int argc,char**argv){
 assert(argc==6);struct mp3entry id3={0};int fd=open(argv[1],O_RDONLY);assert(fd>=0);
 id3.id3v2len=ffilesize(fd);setid3v2title(fd,&id3);
 assert(id3.has_embedded_albumart && id3.albumart_picture_type==3);
 assert(id3.albumart.pos==atoi(argv[2]));
 unsigned char *pixels=malloc(1024*1024);assert(pixels);
 struct bitmap bm={.width=40,.height=40,.data=pixels};
 struct image_src src={fd,id3.albumart.pos,id3.albumart.size,0};
 assert(image_load(&src,&bm,1024*1024,FORMAT_NATIVE|FORMAT_RESIZE|FORMAT_KEEP_ASPECT,NULL)>0);
 assert(RGB_UNPACK_BLUE(((fb_data*)pixels)[0])>200);
 close(fd);puts("F08/F16: full ID3 frame loop selects UTF16 front PNG at exact offset");
 memset(&id3,0,sizeof id3);fd=open(argv[3],O_RDONLY);assert(fd>=0);
 assert(read_mp4_tags(fd,&id3,ffilesize(fd)));
 assert(id3.has_embedded_albumart && id3.albumart.type==AA_TYPE_JPG);
 close(fd);puts("F10: MP4 covr recognizes EXIF-first JPEG");
 for(int kind=0;kind<2;kind++){
   memset(&id3,0,sizeof id3);fd=open(argv[4+kind],O_RDONLY);assert(fd>=0);
   id3.codectype=kind?AFMT_OGG_VORBIS:AFMT_FLAC;
   if(kind){lseek(fd,58,SEEK_SET);assert(read_vorbis_tags(fd,&id3,ffilesize(fd)-58)>0);}
   else assert(get_flac_metadata(fd,&id3));
   assert(id3.has_embedded_albumart && id3.albumart_picture_type==3);
   bm.width=bm.height=40;src.fd=fd;src.pos=id3.albumart.pos;src.len=id3.albumart.size;
   src.flags=id3.albumart.type & ~AA_CLEAR_FLAGS_MASK;
   assert(image_load(&src,&bm,1024*1024,FORMAT_NATIVE|FORMAT_RESIZE|FORMAT_KEEP_ASPECT,NULL)>0);
   assert(RGB_UNPACK_BLUE(((fb_data*)pixels)[0])>200);close(fd);
 }
 free(pixels);puts("F08: FLAC PICTURE and Vorbis base64 choose front cover and decode PNG");return 0;
}
'''
# Read_mp4_tags accepts the ilst children (covr here), with its data subatom.
jpeg=(ROOT/'review-artifacts/fixtures/exif-first.jpg').read_bytes()
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
for source in ['lib/rbcodec/metadata/id3tags.c','lib/rbcodec/metadata/flac.c','lib/rbcodec/metadata/vorbis.c','firmware/common/unicode.c','firmware/common/strcasestr.c','firmware/common/pathfuncs.c','lib/rbcodec/metadata/mp3data.c','lib/fixedpoint/fixedpoint.c']:
    run([cc,*flags,*(['-D__PCTOOL__'] if source.endswith('unicode.c') else []),'-c',str(ROOT/source),'-o',str(OUT/(Path(source).stem+'.o'))],'compile-meta-'+Path(source).stem)
metaobjs=[p for p in objects if p.name not in ['albumart.o','itunes_art.o','id3tags.o','vorbis.o']]
metaobjs += [OUT/(n+'.o') for n in ['id3tags','flac','vorbis','unicode','strcasestr','pathfuncs','mp3data','fixedpoint']]
run([cc,*flags,str(OUT/'metadata_test.c'),*map(str,metaobjs),'-Wl,--gc-sections','-o',str(OUT/'metadata_test')],'link-metadata')
run([str(OUT/'metadata_test'),str(FIX/'front.mp3'),str(expected),str(FIX/'covr.bin'),str(FIX/'pictures.flac'),str(FIX/'pictures.ogg')],'metadata')
print('Metadata regressions passed')
refs=r'''
struct mutex {int n;};static struct mutex llist_mutex;
static void mutex_lock(struct mutex*m){(void)m;}static void mutex_unlock(struct mutex*m){(void)m;}
enum {TYPE_BITMAP=1};
struct memory_handle {int id,type,fd;unsigned references;int pinned;};
static struct memory_handle current;static bool alive;static int freed;
static struct memory_handle *find_handle(int id){return alive&&current.id==id?&current:NULL;}
static void close_fd(int*fd){*fd=-1;}
static void unlink_handle(struct memory_handle*h){assert(h->references==1);alive=false;freed++;}
'''
for name in ['bufretain','buf_pin_handle','close_handle']:
    refs+=function('apps-ipod/audio/buffering.c',name)
refs+=r'''
int main(void){
 for(int i=0;i<1000;i++){
   current=(struct memory_handle){i+1,TYPE_BITMAP,-1,1,0};alive=true;
   assert(bufretain(i+1));assert(buf_pin_handle(i+1,true));
   assert(close_handle(i+1));assert(alive&&current.references==1);
   assert(buf_pin_handle(i+1,false));assert(close_handle(i+1));assert(!alive);
   assert(!bufretain(i+1));
 }
 assert(freed==1000);puts("1000 track releases preserve retained bitmap readers until their release");return 0;
}
'''
run([str(build('references_test',refs))],'references')
# Assert the integration routes retain the database's group semantics and use
# generation at every memory-cache boundary. Runtime UI scheduling is Phase 4.
checks={
 'database-group': 'a->tag_seek[tag_albumartist] != b->tag_seek[tag_albumartist]' in (ROOT/'apps-ipod/database/tagcache.c').read_text(),
 'database-representative': 'r->art_hash = r->file_art_hash;' in (ROOT/'apps-ipod/database/tagcache.c').read_text(),
 'background-per-track': 'dh = art_cache_dir_hash(tcs.result);' in (ROOT/'apps-ipod/metadata/art_cache.c').read_text(),
 'WPS': 'playback_art_changed(owner)' in (ROOT/'apps-ipod/metadata/art_cache.c').read_text(),
 'browser': 'seen_generation' in (ROOT/'apps-ipod/screens/browse/browser.c').read_text(),
 'carousel': 'art_seen_generation' in (ROOT/'apps-ipod/screens/covers/carousel.c').read_text(),
 'palette': 'seen_generation' in (ROOT/'apps-ipod/skin/skin_albumart_color.c').read_text(),
 'accessory': 'k->generation == art_cache_generation()' in (ROOT/'apps-ipod/iap/iap-artwork.c').read_text(),
 'immutable-filter': 'img_filter_apply((fb_data *)bmp->data' not in (ROOT/'apps-ipod/skin/skin_albumart_color.c').read_text(),
 'no-global-hook': all('image_row_hook' not in p.read_text() for p in (ROOT/'apps-ipod').rglob('*.c')),
}
(OUT/'integration-wiring.json').write_text(json.dumps(checks,indent=2)+'\n')
assert all(checks.values()),checks
print('Integration source checks passed; these do not simulate the UI scheduler')
tag_header=(ROOT/'apps-ipod/database/tagcache.h').read_text()
album_struct=re.search(r'struct tagcache_album\s*\{.*?\};',tag_header,re.S).group()
artist_struct=re.search(r'struct tagcache_artist\s*\{.*?\};',tag_header,re.S).group()
group=r'''
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
'''+album_struct+artist_struct
for name in ['album_entry_cmp','seek_cmp','album_row','entry_album_row','albums_group','albums_note_path']:
    group+=function('apps-ipod/database/tagcache.c',name)
group+=r'''
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
'''
# ptrdiff_t is supplied explicitly because this harness uses only the table code.
group='#include <stddef.h>\n'+group
run([str(build('group_test',group))],'group')
capacity=r'''
#define AA_STAMP_NONE 0
struct aa_stamp {unsigned key,stamp;};
static struct aa_stamp slots[2],*aa_stamps=slots;
static unsigned char bits[1],*aa_visited=bits;
static bool aa_table_full;
static struct aa_stamp *aa_slot(unsigned h){(void)h;return NULL;}
'''+function('apps-ipod/metadata/art_cache.c','aa_visit')+r'''
int main(void){for(unsigned i=1;i<=20000;i++){struct aa_stamp*s=aa_visit(i);assert(s&&s->key==i);}assert(aa_table_full);puts("F14: exhausted dedup table streams 20000 remaining owners without skipping");return 0;}
'''
run([str(build('capacity_test',capacity))],'capacity')
