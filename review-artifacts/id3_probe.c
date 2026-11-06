
#define _GNU_SOURCE
#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <ctype.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#define MAX_PATH 260
#define MIN(a,b) ((a)<(b)?(a):(b))
#define MAX(a,b) ((a)>(b)?(a):(b))
#define ARRAYLEN(a) (sizeof(a)/sizeof((a)[0]))
#define logf(...) ((void)0)
#define debug_log(...) ((void)0)
#define yield() ((void)0)
enum {AA_OFF, AA_PREFER_EMBEDDED, AA_PREFER_IMAGE_FILE, AA_PREFER_CACHE};
enum {AA_TYPE_UNKNOWN, AA_TYPE_JPG, AA_TYPE_PNG};
#define AA_FLAG_ID3_UNSYNC 0x100
struct mp3_albumart {off_t pos; unsigned long size; int type;};
struct mp3entry {char path[MAX_PATH]; char *album, *albumartist, *artist; int discnum; bool has_embedded_albumart; struct mp3_albumart albumart;};
struct dim {int width,height;};
static bool image_embedded_usable(int t) {return t==AA_TYPE_JPG || t==AA_TYPE_PNG;}

#line 29 "firmware/common/strmemccpy.c"
char * strmemccpy(char *dst, const char *src, size_t len)
{
    char * ret = (char *)memccpy(dst, src, '\0', len);
    if (ret == NULL && len > 0)
    {
        dst[len - 1] = '\0';
    }
    return ret;
}

#line 29 "firmware/common/strlcpy.c"
size_t strlcpy(char *dst, const char *src, size_t siz)
{
    /* Copy as many bytes as will fit */
    char *d = strmemccpy(dst, src, siz);
    if (d)
        return (d - dst - 1); /* count does not include NUL */

    /* Not enough room in dst, traverse rest of src */
    return(siz + strlen(src+siz)); /* count does not include NUL */
}

static int string_option(const char*s,const char*const*o,bool c){for(int i=0;o[i];i++)if(!strcmp(s,o[i]))return i;return -1;}

#line 300 "lib/rbcodec/metadata/id3tags.c"
static int parsealbumart( struct mp3entry* entry, char* tag, int bufferpos )
{
    /* don't parse albumart if already one found. This callback function is
     * called unconditionally. */
    if(entry->has_embedded_albumart)
        return bufferpos;

    bool unsync = entry->albumart.type == AA_FLAG_ID3_UNSYNC;

    entry->albumart.type = AA_TYPE_UNKNOWN;

    char *start = tag;
    /* skip text encoding */
    tag += 1;
    static const char *img_options[] = {"jpeg", "jpg", "png", NULL};

    if (memcmp(tag, "image/", 6) == 0)
    {
        /* ID3 v2.3+ */
        tag += 6;
        int tg_op = string_option(tag, img_options, false);

        if (tg_op == 0) /*jpeg*/
        {
            entry->albumart.type = AA_TYPE_JPG;
            tag += 5;
        }
        else if (tg_op == 1) /*jpg*/
        {
            /* image/jpg is technically invalid, but it does occur in
             * the wild */
            entry->albumart.type = AA_TYPE_JPG;
            tag += 4;
        }
        else if (tg_op == 2) /*png*/
        {
            entry->albumart.type = AA_TYPE_PNG;
            tag += 4;
        }
    }
    else
    {
        /* ID3 v2.2 */
        if (memcmp(tag, "JPG", 3) == 0)
            entry->albumart.type = AA_TYPE_JPG;
        else if (memcmp(tag, "PNG", 3) == 0)
            entry->albumart.type = AA_TYPE_PNG;
        tag += 3;
    }

    if (entry->albumart.type != AA_TYPE_UNKNOWN)
    {
        /* skip picture type */
        tag += 1;
        /* skip description */
        tag = strchr(tag, '\0') + 1;
        /* fixup offset&size for image data */
        entry->albumart.pos  += tag - start;
        entry->albumart.size -= tag - start;
        if (unsync)
        {
            entry->albumart.type |= AA_FLAG_ID3_UNSYNC;
        }
        /* check for malformed tag with no picture data */
        entry->has_embedded_albumart = (entry->albumart.size != 0);
    }
    /* return bufferpos as we didn't store anything in id3v2buf */
    return bufferpos;
}

int main(void){
    char icon[]="\0image/jpeg\0\x04\0\xff\xd8\xff\xe0";
    char front[]="\0image/jpeg\0\x03\0\xff\xd8\xff\xe0";
    char utf16[]={1,'i','m','a','g','e','/','j','p','e','g',0,3,(char)0xff,(char)0xfe,'A',0,0,0,(char)0xff,(char)0xd8,(char)0xff,(char)0xe0};
    struct mp3entry a={.albumart={.pos=100,.size=sizeof icon,.type=0}};
    parsealbumart(&a,icon,0);off_t first=a.albumart.pos;
    parsealbumart(&a,front,0);
    printf("back before front: first=%ld final=%ld unchanged=%d\n",(long)first,(long)a.albumart.pos,first==a.albumart.pos);
    memset(&a,0,sizeof a);a.albumart.size=sizeof utf16;
    parsealbumart(&a,utf16,0);
    printf("UTF16 description: selected_offset=%ld JPEG_offset=19 selected_byte=%02x\n",(long)a.albumart.pos,(unsigned char)utf16[a.albumart.pos]);
    char png[]={1,'i','m','a','g','e','/','p','n','g',0,3,(char)0xff,(char)0xfe,'A',0,0,0,(char)0x89,'P','N','G',13,10,26,10};
    memset(&a,0,sizeof a);a.albumart.size=sizeof png;
    parsealbumart(&a,png,0);
    printf("UTF16 PNG: selected_offset=%ld PNG_offset=18 selected_byte=%02x\n",(long)a.albumart.pos,(unsigned char)png[a.albumart.pos]);
    return 0;
}
