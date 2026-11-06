
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
