
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

#line 2036 "apps-ipod/audio/playback.c"
static int audio_load_albumart(struct track_info *infop,
                                struct mp3entry *track_id3, bool is_current_track)
{
    FOREACH_ALBUMART(i)
    {
        struct bufopen_bitmap_data user_data;
        int *aa_hid = &infop->aa_hid[i];
        int hid = ERR_UNSUPPORTED_TYPE;
        if (*aa_hid >= 0 || *aa_hid == ERR_UNSUPPORTED_TYPE ||
            !albumart_slots[i].used)
            continue;
        memset(&user_data, 0, sizeof(user_data));
        user_data.dim = &albumart_slots[i].dim;
        if (global_settings.album_art != AA_OFF)
        {
            bool auto_art = global_settings.wps_art_source == WPS_ART_AUTO &&
                            playlist_is_from_artist();
            bool artist = global_settings.wps_art_source == WPS_ART_ARTIST || auto_art;
            char owner[MAX_PATH], path[MAX_PATH];
            strlcpy(owner, track_id3->path, sizeof(owner));
            if (artist)
            {
                char *slash = strrchr(owner, '/');
                if (slash) *slash = 0;
                slash = strrchr(owner, '/');
                if (slash) *slash = 0;
            }
            bool fallback = false;
            int size = art_cache_size_index("wps");
            if (size >= 0 && art_cache_lookup(owner, size, path, sizeof(path),
                                              &fallback) && !fallback)
                hid = bufopen(path, 0, TYPE_BITMAP, &user_data);
            if (auto_art && hid < 0 && hid != ERR_BUFFER_FULL && size >= 0 &&
                art_cache_lookup(track_id3->path, size, path, sizeof(path),
                                 &fallback) && !fallback)
                hid = bufopen(path, 0, TYPE_BITMAP, &user_data);
        }
        if (hid >= 0) skin_albumart_art_opened(hid);

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

    (void)is_current_track;
    return true;
}

int main(void){
 struct mp3entry a={.album=NULL,.albumartist=NULL};strcpy(a.path,"/mixed/a.mp3");
 struct track_info t={{-4}};assert(audio_load_albumart(&t,&a,true));int first=t.aa_hid[0];
 strcpy(a.path,"/mixed/b.mp3");t.aa_hid[0]=-4;assert(audio_load_albumart(&t,&a,true));
 assert(first!=t.aa_hid[0] && opens==2);puts("F01/F02/F03: absent tags; distinct files do not reuse pixels");return 0;
}
