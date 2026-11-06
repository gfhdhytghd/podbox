
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

#define MAX_MULTIPLE_AA 1
#define FOREACH_ALBUMART(i) for (int i=0;i<MAX_MULTIPLE_AA;i++)
enum {ERR_UNSUPPORTED_TYPE=-1,ERR_BUFFER_FULL=-2,ERR_BITMAP_TOO_LARGE=-3,TYPE_BITMAP=1};
enum {WPS_ART_AUTO,WPS_ART_ARTIST,WPS_ART_ALBUM};
struct {int wps_art_source,album_art;} global_settings={WPS_ART_ALBUM,AA_PREFER_EMBEDDED};
struct {struct dim dim; int used;} albumart_slots[1]={{{100,100},1}};
static char last_embedded_aa_album[MAX_PATH],last_embedded_aa_albumartist[MAX_PATH];
static int last_embedded_aa_discnum,last_embedded_aa_hid[1];
struct track_info {int aa_hid[1];};
struct bufopen_bitmap_data {struct dim *dim; struct mp3_albumart *embedded_albumart;};
static int opens;
static bool playlist_is_from_artist(void) {return false;}
static void clear_last_folder_album_art(void) {}
static void clear_last_embedded_album_art(void) {last_embedded_aa_album[0]=0;last_embedded_aa_hid[0]=0;}
static int load_cached_albumart(struct mp3entry*a,struct bufopen_bitmap_data*b,bool c,int d,bool e) {return -1;}
static bool find_albumart(struct mp3entry*a,char*b,int c,const struct dim*d) {return false;}
static int load_album_art_from_path(char*a,struct bufopen_bitmap_data*b,bool c,int d) {return -1;}
static int bufopen(const char*p,int o,int t,void *u) {opens++;return 100+opens;}
static void skin_albumart_art_opened(int h) {}

#line 2177 "apps-ipod/audio/playback.c"
static int audio_load_albumart(struct track_info *infop,
                                struct mp3entry *track_id3, bool is_current_track)
{
    FOREACH_ALBUMART(i)
    {
        struct bufopen_bitmap_data user_data;
        int *aa_hid = &infop->aa_hid[i];
        int hid = ERR_UNSUPPORTED_TYPE;
        bool checked_image_file = false;

        /* albumart_slots may change during a yield of bufopen,
         * but that's no problem */
        if (*aa_hid >= 0 || *aa_hid == ERR_UNSUPPORTED_TYPE ||
            !albumart_slots[i].used)
            continue;

        memset(&user_data, 0, sizeof(user_data));
        user_data.dim = &albumart_slots[i].dim;

        /* Artist art has exactly one source: the artist folder's cached
         * thumbnail. Every other source below is album art by definition --
         * embedded tags and folder images both belong to the record, not the
         * performer -- so under the explicit Artist setting an artist with no
         * portrait ends with no artwork rather than quietly falling through to
         * the album cover. Missing portraits are meant to be visible; that is
         * what the art cache health screen reports on.
         *
         * "Auto" is the exception, and asks for artist art only for a playlist
         * built from an artist browse. It is a convenience rather than a
         * statement about the library, so it does fall back. */
        bool auto_art = global_settings.wps_art_source == WPS_ART_AUTO &&
                        playlist_is_from_artist();
        bool artist_art = global_settings.wps_art_source == WPS_ART_ARTIST ||
                          auto_art;

        char path[MAX_PATH];
        if (artist_art && global_settings.album_art != AA_OFF)
        {
            hid = load_cached_albumart(track_id3, &user_data,
                                       is_current_track, i, true);

            if (auto_art && hid < 0 && hid != ERR_BUFFER_FULL)
                artist_art = false;   /* no portrait: take the album cover */
        }

        if (!artist_art && global_settings.album_art == AA_PREFER_CACHE)
        {
            /* cache first: the other sources below chain on hid < 0, so a hit
             * here skips them */
            hid = load_cached_albumart(track_id3, &user_data,
                                       is_current_track, i, false);
        }
        else if(!artist_art &&
                global_settings.album_art == AA_PREFER_IMAGE_FILE)
        {
            if (find_albumart(track_id3, path, sizeof(path),
                          &albumart_slots[i].dim))
            {
                hid = load_album_art_from_path(path, &user_data, is_current_track, i);
            }
            checked_image_file = true;
        }

        /* We can only decode JPEG and PNG for embedded AA */
        if (!artist_art && global_settings.album_art != AA_OFF &&
            hid < 0 && hid != ERR_BUFFER_FULL &&
            track_id3->has_embedded_albumart && image_embedded_usable(track_id3->albumart.type))
        {
            /* Tracks on the same album (same album+albumartist+discnum tags)
             * typically carry byte-identical embedded art -- reuse the
             * already-decoded handle instead of re-extracting/re-decoding
             * on every track change (see statics/comment near
             * last_embedded_aa_album above). */
            bool same_album = track_id3->album && track_id3->album[0] &&
                               track_id3->albumartist && track_id3->albumartist[0] &&
                               strcmp(last_embedded_aa_album, track_id3->album) == 0 &&
                               strcmp(last_embedded_aa_albumartist, track_id3->albumartist) == 0 &&
                               last_embedded_aa_discnum == track_id3->discnum;

            if (same_album && last_embedded_aa_hid[i] != 0)
            {
                hid = last_embedded_aa_hid[i];
            }
            else
            {
                if (is_current_track)
                    clear_last_folder_album_art();

                bool is_cacheable = i == 0 &&
                                    (is_current_track || last_embedded_aa_album[0] == 0);
                if (!same_album && is_cacheable)
                {
                    clear_last_embedded_album_art();
                    strlcpy(last_embedded_aa_album, track_id3->album,
                            sizeof(last_embedded_aa_album));
                    strlcpy(last_embedded_aa_albumartist, track_id3->albumartist,
                            sizeof(last_embedded_aa_albumartist));
                    last_embedded_aa_discnum = track_id3->discnum;
                }

                user_data.embedded_albumart = &track_id3->albumart;
                hid = bufopen(track_id3->path, 0, TYPE_BITMAP, &user_data);
                skin_albumart_art_opened(hid);
                if (hid != ERR_BUFFER_FULL && (same_album || is_cacheable))
                    last_embedded_aa_hid[i] = hid;
            }
        }

        if (!artist_art && global_settings.album_art != AA_OFF &&
            !checked_image_file &&
            hid < 0 && hid != ERR_BUFFER_FULL)
        {
            /* No embedded AA or it couldn't be loaded - try other sources */
            if (find_albumart(track_id3, path, sizeof(path),
                              &albumart_slots[i].dim))
            {
                hid = load_album_art_from_path(path, &user_data, is_current_track, i);
            }
        }

        /* Last resort for the non-cache-first settings: the pre-scaled cache
         * (cache-first already tried it above). */
        if (!artist_art && global_settings.album_art != AA_OFF &&
            global_settings.album_art != AA_PREFER_CACHE &&
            hid < 0 && hid != ERR_BUFFER_FULL)
        {
            hid = load_cached_albumart(track_id3, &user_data,
                                       is_current_track, i, false);
        }

        if (hid == ERR_BUFFER_FULL)
        {
            logf("buffer is full for now (%s)", __func__);
            return ERR_BUFFER_FULL;
        }
        else if (hid == ERR_BITMAP_TOO_LARGE){
            logf("image is too large to fit in buffer (%s)", __func__);
            return ERR_BITMAP_TOO_LARGE;
        }
        else
        {
            /* If error other than a full buffer, then mark it "unsupported"
               to avoid reloading attempt */
            if (hid < 0)
            {
                logf("Album art loading failed");
                hid = ERR_UNSUPPORTED_TYPE;
            }
            else
            {
                logf("Loaded album art:%dx%d", user_data.dim->width,
                     user_data.dim->height);
            }

            *aa_hid = hid;
        }
    }

    return true;
}

int main(int argc,char**argv) {
    struct mp3entry a={.album="Album",.albumartist="Artist",.discnum=1,.has_embedded_albumart=true,.albumart={.type=AA_TYPE_JPG,.pos=100,.size=999}};
    strcpy(a.path,"/Music/A/one.mp3");
    if(argc>1 && !strcmp(argv[1],"null-artist")) a.albumartist=NULL;
    if(argc>1 && !strcmp(argv[1],"null-album")) a.album=NULL;
    struct track_info t={{-4}};
    audio_load_albumart(&t,&a,true);
    int first=t.aa_hid[0];
    strcpy(a.path,"/Music/B/two.mp3"); a.albumart.pos=500; a.albumart.size=1234;
    t.aa_hid[0]=-4; audio_load_albumart(&t,&a,true);
    printf("first=%d second=%d decode_opens=%d; distinct source art reused=%d\n",first,t.aa_hid[0],opens,first==t.aa_hid[0]);
    return 0;
}
