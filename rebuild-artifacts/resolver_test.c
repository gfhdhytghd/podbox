
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
