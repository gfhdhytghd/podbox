
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

#define ROCKBOX_DIR "/.rockbox"
#define EXT
static const char * const extensions[]={"jpeg","jpg","bmp","png","gif","webp"};
static const unsigned char extension_lens[]={4,3,3,3,3,4};
static const char *files[10];
static bool art_file_usable(const char*p) {for(int i=0;files[i];i++)if(!strcasecmp(p,files[i]))return true;return false;}

#line 132 "apps-ipod/system/strutil.c"
char *strip_extension(char* buffer, int buffer_size, const char *filename)
{
    if (!buffer || !filename || buffer_size <= 0)
    {
        return NULL;
    }

    off_t dotpos = (strrchr(filename, '.') - filename) + 1;

    /* no match on filename beginning with '.' or beyond buffer_size */
    if(dotpos > 1 && dotpos < buffer_size)
        buffer_size = dotpos;
    strmemccpy(buffer, filename, buffer_size);

    return buffer;
}

#line 271 "apps-ipod/system/strutil.c"
void fix_path_part(char* path, int offset, int count)
{
    static const char invalid_chars[] = "*/:<>?\\|";
    int i;

    path += offset;

    for (i = 0; i <= count; i++, path++)
    {
        if (*path == 0)
            return;
        if (*path == '"')
            *path = '\'';
        else if (strchr(invalid_chars, *path))
            *path = '_';
    }
}

#line 336 "apps-ipod/system/strutil.c"
bool is_disc_folder(const char *start, const char *end)
{
    static const char * const words[] = { "cd", "disc", "disk" };
    const char *p = NULL;

    for (size_t i = 0; i < ARRAYLEN(words) && p == NULL; i++)
    {
        size_t len = strlen(words[i]);

        if ((size_t)(end - start) > len && !strncasecmp(start, words[i], len))
            p = start + len;
    }
    if (p == NULL)
        return false;

    while (p < end && (*p == ' ' || *p == '-' || *p == '_' || *p == '.'))
        p++;
    if (p == end)
        return false;
    while (p < end && isdigit((unsigned char)*p))
        p++;
    return p == end;
}

#line 47 "apps-ipod/metadata/albumart.c"
static char* strip_filename(char* buf, int buf_size, const char* fullpath)
{
    char* sep;
    int   len;

    if (!buf || buf_size <= 0 || !fullpath)
        return NULL;

    /* if 'fullpath' is only a filename return immediately */
    sep = strrchr(fullpath, '/');
    if (sep == NULL)
    {
        buf[0] = 0;
        return (char*)fullpath;
    }

    len = MIN(sep - fullpath + 1, buf_size - 1);
    strmemccpy(buf, fullpath, len + 1);
    return (sep + 1);
}

#line 133 "apps-ipod/metadata/albumart.c"
static bool try_exts(char *path, int len)
{
    int i;
    for (i = 0; i < (int)ARRAYLEN(extensions); i++)
    {
        if (extension_lens[i] + len > MAX_PATH)
            continue;
        strcpy(path + len, extensions[i]);
        if (art_file_usable(path))
            return true;
    }
    return false;
}

#line 148 "apps-ipod/metadata/albumart.c"
static bool try_folder(char *path, size_t size, const char *dir)
{
    snprintf(path, size, "%sfolder.jpg", dir);
    if (art_file_usable(path))
        return true;
    snprintf(path, size, "%sfolder.png", dir);
    if (art_file_usable(path))
        return true;
    snprintf(path, size, "%sfolder.gif", dir);
    if (art_file_usable(path))
        return true;
    snprintf(path, size, "%sfolder.webp", dir);
    return art_file_usable(path);
}

#line 186 "apps-ipod/metadata/albumart.c"
bool search_albumart_files(const struct mp3entry *id3, const char *size_string,
                           char *buf, int buflen)
{
    char path[MAX_PATH + 11]; /* need room for filename and null termination */
    char dir[MAX_PATH + 1];
    bool found = false;
    int track_first = 1;
    int pass;
    const char *trackname;
    const char *artist;
    int dirlen;
    int albumlen;
    int pathlen;

    if (!id3 || !buf)
        return false;

    trackname = id3->path;

    if (strcmp(trackname, "No file!") == 0)
        return false;

    if (*size_string == ':')
    {
        size_string++;
        track_first = 0;
    }

    strip_filename(dir, sizeof(dir), trackname);
    dirlen = strlen(dir);
    albumlen = id3->album ? strlen(id3->album) : 0;

    for(pass = 0; pass < 2 - track_first; pass++)
    {
        if (track_first || pass)
        {
            /* the first file we look for is one specific to the
               current track */
            strip_extension(path, sizeof(path) - strlen(size_string) - 4,
                            trackname);
            strcat(path, size_string);
            strcat(path, "." EXT);
            pathlen = strlen(path);
            found = try_exts(path, pathlen);
        }
        if (pass)
            break;
        if (!found && albumlen > 0)
        {
            /* if it doesn't exist,
            * we look for a file specific to the track's album name */
            pathlen = snprintf(path, sizeof(path),
                            "%s%s%s." EXT, dir, id3->album, size_string);
            fix_path_part(path, dirlen, albumlen);
            found = try_exts(path, pathlen);
        }

        if (!found)
        {
            /* if it still doesn't exist, we look for a generic file */
            pathlen = snprintf(path, sizeof(path),
                            "%scover%s." EXT, dir, size_string);
            found = try_exts(path, pathlen);
        }

        if (!found && !*size_string)
            found = try_folder(path, sizeof(path), dir);

        artist = id3->albumartist != NULL ? id3->albumartist : id3->artist;

        if (!found && artist && id3->album)
        {
            /* look in the albumart subdir of .rockbox */
            pathlen = snprintf(path, sizeof(path),
                            ROCKBOX_DIR "/albumart/%s-%s%s." EXT,
                            artist,
                            id3->album,
                            size_string);
            fix_path_part(path, strlen(ROCKBOX_DIR "/albumart/"), MAX_PATH);
            found = try_exts(path, pathlen);
        }

        if (!found)
        {
            /* if it still doesn't exist,
            * we continue to search in the parent directory */
            strcpy(path, dir);
            path[dirlen - 1] = 0;
            strip_filename(dir, sizeof(dir), path);
            dirlen = strlen(dir);
        }

        /* only try parent if there is one */
        if (dirlen > 0)
        {
            if (!found && albumlen > 0)
            {
                /* we look in the parent directory
                * for a file specific to the track's album name */
                pathlen = snprintf(path, sizeof(path),
                                "%s%s%s." EXT, dir, id3->album, size_string);
                fix_path_part(path, dirlen, albumlen);
                found = try_exts(path, pathlen);
            }

            if (!found)
            {
                /* if it still doesn't exist, we look in the parent directory
                * for a generic file */
                pathlen = snprintf(path, sizeof(path),
                                "%scover%s." EXT, dir, size_string);
                found = try_exts(path, pathlen);
            }

            if (!found && !*size_string)
                found = try_folder(path, sizeof(path), dir);
        }
        if (found)
            break;
    }

    if (!found)
        return false;

    strmemccpy(buf, path, buflen);
    logf("Album art found: %s", path);
    return true;
}

#line 318 "apps-ipod/metadata/albumart.c"
bool find_albumart(const struct mp3entry *id3, char *buf, int buflen,
                    const struct dim *dim)
{
    if (!id3 || !buf)
        return false;

    char size_string[15];/* .-32768x-32768\0 */
    logf("Looking for album art for %s", id3->path);

    /* Write the size string, e.g. ".100x100". */
    snprintf(size_string, sizeof(size_string), ".%dx%d",
              dim->width, dim->height);

    /* First we look for a bitmap of the right size */
    if (search_albumart_files(id3, size_string, buf, buflen))
        return true;

    /* Then we look for generic bitmaps */
    *size_string = 0;
    return search_albumart_files(id3, size_string, buf, buflen);
}

#line 668 "apps-ipod/metadata/art_cache.c"
static bool aa_art_is_folders(const char *probe, const char *art, bool artist)
{
    const char *end = strrchr(probe, '/');
    const char *start;
    size_t dirlen;

    if (!end)
        return true;
    dirlen = end - probe + 1;
    if (!strncmp(probe, art, dirlen) && !strchr(art + dirlen, '/'))
        return true;
    if (artist)
        return strrchr(art, '/') != art;

    for (start = end; start > probe && start[-1] != '/'; start--)
        ;
    return is_disc_folder(start, end);
}

static struct mp3entry aa_id3;
static char aa_artpath[MAX_PATH];

#line 1327 "apps-ipod/metadata/art_cache.c"
static bool aa_find_file(const char *probe_path, bool artist)
{
    memset(&aa_id3, 0, sizeof(aa_id3));
    strlcpy(aa_id3.path, probe_path, sizeof(aa_id3.path));
    return search_albumart_files(&aa_id3, "", aa_artpath, sizeof(aa_artpath))
           && aa_art_is_folders(probe_path, aa_artpath, artist);
}

static void check(const char *label,const char *file,const char *path){
    struct mp3entry a={.album="Album",.albumartist="Artist"};strcpy(a.path,path);
    files[0]=file;files[1]=NULL;char out[MAX_PATH]="";
    bool direct=search_albumart_files(&a,"",out,sizeof out),cached=aa_find_file(path,false);
    printf("%s: direct=%d cache=%d direct_path=%s\n",label,direct,cached,out);
}
int main(void){
    check("album-name", "/Music/Artist/Album/Album.jpg","/Music/Artist/Album/01.mp3");
    check("central-art", "/.rockbox/albumart/Artist-Album.jpg","/Music/Artist/Album/01.mp3");
    check("parent-art", "/Music/Artist/cover.jpg","/Music/Artist/Album/01.mp3");
    check("disc-parent", "/Music/Artist/Album/cover.jpg","/Music/Artist/Album/CD1/01.mp3");
    check("front", "/Music/Artist/Album/front.jpg","/Music/Artist/Album/01.mp3");
    check("folder-bmp", "/Music/Artist/Album/folder.bmp","/Music/Artist/Album/01.mp3");
    check("fat-uppercase", "/Music/Artist/Album/COVER.JPG","/Music/Artist/Album/01.mp3");
    return 0;
}
