"""Production function tests with queue, allocator and candidate adapters.

POSIX registry/reference I/O is real. Scheduling, storage and decoding outcomes
are deterministic adapters. This does not execute the firmware scheduler.
"""
import json
import shutil
import re
from support import ROOT, OUT, function, run, build

PLAY = 'apps-ipod/audio/playback.c'
CACHE = 'apps-ipod/metadata/art_cache.c'
source = (ROOT / CACHE).read_text()
modern = 'struct aa_request' in source
play = r'''

#define MAX_MULTIPLE_AA 1
#define FOREACH_ALBUMART(i) for (int i = 0; i < 1; i++)
enum
{
    ERR_UNSUPPORTED_TYPE = -1,
    ERR_BUFFER_FULL = -2,
    ERR_BITMAP_TOO_LARGE = -3,
    TYPE_BITMAP = 1
};
enum
{
    AA_OFF,
    AA_PREFER_EMBEDDED,
    AA_PREFER_IMAGE_FILE,
    AA_PREFER_CACHE
};
enum
{
    WPS_ART_AUTO,
    WPS_ART_ARTIST,
    WPS_ART_ALBUM
};
enum
{
    Q_AUDIO_REMAKE_AUDIO_BUFFER = 1,
    Q_AUDIO_ART_CHANGED
};
struct
{
    int wps_art_source, album_art;
} global_settings = {WPS_ART_ALBUM, AA_PREFER_CACHE};
struct dim
{
    int width, height;
};
struct
{
    struct dim dim;
    int used;
} albumart_slots[1] = {{{100, 100}, 1}};
struct mp3entry
{
    char path[MAX_PATH];
};
struct track_info
{
    int aa_hid[1];
    unsigned aa_owner[2], aa_generation;
};
struct bufopen_bitmap_data
{
    struct dim *dim;
};
static int opens, queued, preference_seen;
static bool ready;
static unsigned generation = 1;
static unsigned art_cache_generation(void)
{
    return generation;
}
static struct mp3entry current;
static struct track_info buffered[2];
static struct
{
    int first_hid;
} track_list = {1};
static unsigned track_list_count(void)
{
    return 2;
}
static void *track_list_get_info_from(int h, int i, struct track_info *t)
{
    (void)h;
    if (i < 0 || i > 1)
        return NULL;
    *t = buffered[i];
    return t;
}
static bool playlist_is_from_artist(void)
{
    return true;
}
static struct mp3entry *audio_current_track(void)
{
    return &current;
}
static unsigned art_cache_dir_hash(const char *s)
{
    unsigned h = 5381;
    while (*s)
        h = h * 33 + (unsigned char)*s++;
    return h;
}
static unsigned art_cache_preference_hash(const char *s, int p)
{
    return art_cache_dir_hash(s) + (p == AA_PREFER_CACHE ? 0 : p);
}
static void audio_queue_post(int id, intptr_t arg)
{
    (void)id;
    (void)arg;
    queued++;
}
static int art_cache_size_index(const char *a)
{
    (void)a;
    return 0;
}
static bool art_cache_lookup(const char *key, int size, char *path, int n,
                             bool *fallback)
{
    (void)size;
    snprintf(path, n, "%s.aat", key);
    *fallback = false;
    return ready;
}
static bool art_cache_lookup_preference(const char *k, int p, int z, char *d,
                                        int n, bool *f)
{
    preference_seen = p;
    return art_cache_lookup(k, z, d, n, f);
}
static int bufopen(const char *a, int b, int c, void *d)
{
    (void)a;
    (void)b;
    (void)c;
    (void)d;
    return ++opens + 100;
}
static void skin_albumart_art_opened(int a)
{
    (void)a;
}
'''
play += function(PLAY, 'audio_load_albumart') + function(PLAY, 'playback_art_changed')
if 'static bool audio_art_dependency_changed' in (ROOT / PLAY).read_text():
    play += function(PLAY, 'audio_art_dependency_changed')
else:
    # Compatibility adapter for the reviewed head: the callback itself gates
    # publication on the current track, before this assertion can be reached.
    play += 'static bool audio_art_dependency_changed(unsigned o){(void)o;return false;}\n'
play += r'''

int main(int argc, char **argv)
{
    assert(argc == 2);
    strcpy(current.path, "/artist/album/a.mp3");
    struct mp3entry b = {0};
    strcpy(b.path, "/artist/album/b.mp3");
    struct track_info t = {.aa_hid = {-4}};
    if (!strcmp(argv[1], "R1"))
    {
        assert(audio_load_albumart(&t, &b, false));
        assert(t.aa_hid[0] == ERR_UNSUPPORTED_TYPE);
        buffered[1] = t;
        ready = true;
        generation++;
        playback_art_changed(art_cache_dir_hash(b.path));
        assert(queued == 1);
        assert(audio_art_dependency_changed(art_cache_dir_hash(b.path)));
        assert(!audio_art_dependency_changed(
            art_cache_dir_hash("/unrelated.mp3")));
        current = b;
        assert(audio_load_albumart(&t, &b, true));
        assert(opens == 1);
        global_settings.wps_art_source = WPS_ART_ARTIST;
        ready = false;
        t.aa_hid[0] = -4;
        assert(audio_load_albumart(&t, &b, true));
        buffered[1] = t;
        playback_art_changed(art_cache_dir_hash("/artist"));
        assert(queued == 2);
        assert(audio_art_dependency_changed(art_cache_dir_hash("/artist")));
        global_settings.wps_art_source = WPS_ART_AUTO;
        ready = true;
        t.aa_hid[0] = -4;
        assert(audio_load_albumart(&t, &b, true));
        buffered[1] = t;
        assert(audio_art_dependency_changed(art_cache_dir_hash("/artist")));
        assert(audio_art_dependency_changed(art_cache_dir_hash(b.path)));
        puts("R1: prefetched and artist dependencies; missing slot retries "
             "after generation");
    }
    else
    {
        ready = true;
        for (int p = AA_PREFER_EMBEDDED; p <= AA_PREFER_IMAGE_FILE; p++)
        {
            preference_seen = 0;
            global_settings.album_art = p;
            t.aa_hid[0] = -4;
            assert(audio_load_albumart(&t, &b, true));
            assert(preference_seen == p);
        }
        puts("R3-playback: explicit source preference reaches policy cache "
             "lookup");
    }
    return 0;
}
'''
play_exe = build('playback', play)

header = (ROOT / 'apps-ipod/metadata/art_cache.h').read_text()
cache = '#define CACHE_ROOT ' + json.dumps(str(OUT / 'cache')) + '\n'
cache += '#define CURRENT_HEAD 1\n' if modern else '#define REVIEWED_HEAD 1\n'
cache += r'''

#include <time.h>
#include <stdarg.h>
#define LIB_ART_DIR CACHE_ROOT
#define THUMBCACHE_DIR CACHE_ROOT "/v9"
#define ART_CACHE_MAGIC 0x5441u
#define ART_CACHE_FORMAT_VERSION 9
#define ART_CACHE_IDENTITY_MAX 512
#define AA_EVENT_OFFER 1
#define AA_STAMP_NO_EMBED 3
#define DBTOOL 1
enum
{
    AA_OFF,
    AA_PREFER_EMBEDDED,
    AA_PREFER_IMAGE_FILE,
    AA_PREFER_CACHE
};
enum
{
    ART_SOURCE_FILES,
    ART_SOURCE_FILES_FIRST,
    ART_SOURCE_EMBEDDED_FIRST,
    ART_SOURCE_EMBEDDED
};
enum albumart_role
{
    AA_ROLE_ALBUM,
    AA_ROLE_ARTIST,
    AA_ROLE_TRACK,
    AA_ROLE_BOOK
};
enum art_cache_state
{
    ART_MISSING,
    ART_LOADING,
    ART_READY,
    ART_FAILED
};
struct
{
    int art_cache_album_source, album_art;
} global_settings = {ART_SOURCE_EMBEDDED_FIRST, AA_PREFER_CACHE};
static enum art_cache_state aa_state;
static unsigned aa_generation, aa_active_id, aa_offer_generation;
static bool aa_io_failed;
static char aa_active_identity[ART_CACHE_IDENTITY_MAX];
struct mutex
{
    int unused;
};
static struct mutex identity_mutex;
static bool identity_ready;
static long current_tick;
static int posted, art_cache_task;
static bool aa_offer_posted;
static unsigned aa_offer_retries;
struct timeout
{
    int unused;
};
static struct timeout aa_offer_tmo;
static int (*poll_fn)(struct timeout *);
static void timeout_register(struct timeout *t, int (*fn)(struct timeout *),
                             int delay, intptr_t d)
{
    (void)t;
    (void)delay;
    (void)d;
    poll_fn = fn;
}
static void bg_task_post(int *a, int b)
{
    (void)a;
    (void)b;
    posted++;
}
static void mutex_init(struct mutex *m)
{
    (void)m;
}
static void mutex_lock(struct mutex *m)
{
    (void)m;
}
static void mutex_unlock(struct mutex *m)
{
    (void)m;
}
static bool aa_check_abort(void)
{
    return false;
}
static int fail_ref_open, open_calls;
static int checked_open(const char *p, int flags, ...)
{
    open_calls++;
    if (fail_ref_open && strstr(p, "/refs/") && strstr(p, ".ref.new"))
    {
        errno = ENOSPC;
        return -1;
    }
    if (flags & O_CREAT)
    {
        va_list ap;
        va_start(ap, flags);
        int mode = va_arg(ap, int);
        va_end(ap);
        return open(p, flags, mode);
    }
    return open(p, flags);
}
#define open checked_open
static int make_dir(const char *p)
{
    return mkdir(p, 0700);
}
#define mkdir make_dir
static void aa_record_state(unsigned o, enum art_cache_state s)
{
    (void)o;
    aa_state = s;
}
struct aa_request
{
    uint32_t version;
    unsigned owner;
    int role, policy;
    char path[MAX_PATH];
    uint32_t checksum;
#ifdef REVIEWED_HEAD
    off_t pos;
    unsigned size,flags;
#endif
};
static struct aa_request aa_offer;
struct aa_stamp
{
    unsigned key, stamp;
};
struct albumart_source
{
    int kind;
    char path[MAX_PATH];
    off_t pos;
    int size;
    unsigned flags;
    uint64_t revision;
};
struct aa_src
{
    struct albumart_source resolved;
    unsigned generation;
    const char *path;
    off_t emb_pos;
    unsigned long emb_size;
    int emb_flags;
};
struct mp3entry
{
    char path[MAX_PATH];
    struct
    {
        off_t pos;
        unsigned size, type;
    } albumart;
};
static struct mp3entry aa_id3, current;
static struct mp3entry *audio_current_track(void)
{
    return &current;
}
static const char *aa_miss, *aa_miss_src;
static int candidates, mode, last_role, asleep, memory_ok = 1, alloc_ok = 1,
                                                book_repairs;
static char last_probe[MAX_PATH], first_probe[MAX_PATH];
static void get_metadata(struct mp3entry *i, int fd, const char *p)
{
    (void)i;
    (void)fd;
    (void)p;
}
static void albumart_source_key(struct albumart_source *s, char *p, size_t n)
{
    snprintf(p, n, "%s@1", s->path);
}
static bool albumart_source_revision(struct albumart_source *s)
{
    (void)s;
    return true;
}
static bool aa_fill(const struct aa_src *s, unsigned p, void *w, size_t n,
                    bool *a)
{
    (void)p;
    (void)w;
    (void)n;
    (void)a;
    if (s->resolved.kind == 1 && mode)
    {
        if (mode == 1)
            aa_io_failed = true;
        return false;
    }
    return true;
}
static size_t aa_work_bytes(void)
{
    return 1024;
}
static size_t core_allocatable(void)
{
    return memory_ok ? 2048 : 0;
}
static int core_alloc(size_t n)
{
    (void)n;
    return alloc_ok;
}
static void *core_get_data_pinned(int h)
{
    (void)h;
    static char b[2048];
    return b;
}
static void core_unpin(int h)
{
    (void)h;
}
static void core_free(int h)
{
    (void)h;
}
static void aa_ensure_dirs(void);
static bool aa_disk_asleep(void)
{
    return asleep;
}
struct book_index_text
{
    int unused;
};
struct book_entry
{
    const char *path;
    long cover_at;
    unsigned cover_csize;
};
struct aa_books_ctx
{
    void *workbuf;
    size_t worksz;
    bool *aborted;
};
struct aa_book_repair
{
    const struct aa_request *request;
    struct aa_books_ctx context;
};
static bool aa_cache_book(const struct book_entry *e, struct aa_books_ctx *c,
                          unsigned owner, struct aa_stamp *s)
{
    (void)c;
    (void)owner;
    (void)s;
    assert(!strcmp(e->path, "/library/author/book.epub"));
    assert(e->cover_at == 123 && e->cover_csize == 456);
    book_repairs++;
    return true;
}
static void book_index_each(bool (*fn)(const struct book_entry *, void *),
                            void *d, struct book_index_text *t)
{
    (void)t;
    struct book_entry entries[] = {{"/other.epub", 1, 2},
                                   {"/library/author/book.epub", 123, 456}};
    for (unsigned i = 0; i < ARRAYLEN(entries); i++)
        if (!fn(&entries[i], d))
            break;
}
'''
cache = cache.replace('#define ART_CACHE_FORMAT_VERSION 9', re.search(r'#define ART_CACHE_FORMAT_VERSION \d+', header).group())
cache += re.search(r'struct art_cache_header\s*\{.*?\};', header, re.S).group()
# Small cache probes use the real normalization, registry and publication code.
for path, name in [('apps-ipod/database/path_key.c','path_key_strip'),
                   ('apps-ipod/metadata/albumart.c','albumart_normalize')]:
    cache += function(path, name)
for name in ['aa_hash', 'aa_checksum', 'aa_identity_dirs', 'art_cache_dir_hash',
             'aa_referenced', 'aa_reference']:
    cache += function(CACHE, name)
if modern:
    cache += function(CACHE, 'aa_read_request') + function(CACHE, 'aa_save_request')
else:
    cache += 'static bool aa_save_request(unsigned o,const char*p,int r,int policy){(void)o;(void)p;(void)r;(void)policy;return true;}\n'
cache += 'static void aa_ensure_dirs(void){aa_identity_dirs();}\n'
policy_arg = 'int policy,' if modern else ''
cache += '''static bool albumart_next_source(struct mp3entry*i,int role,%sunsigned*c,struct albumart_source*s){
%s
 last_role=role;strcpy(last_probe,i->path);if(!candidates)strcpy(first_probe,i->path);if(*c==2)return false;
 ++*c;++candidates;memset(s,0,sizeof(*s));s->kind=*c;
 snprintf(s->path,sizeof(s->path),"/source/%%u",*c);s->revision=1;return true;
}\n''' % (policy_arg, '(void)policy;' if modern else '')
cache += function(CACHE, 'aa_remove_thumbs') + function(CACHE, 'aa_cache_dir')
if modern:
    cache += function(CACHE, 'aa_offer_poll') + function(CACHE, 'aa_repair_book')
    cache += function(CACHE, 'art_cache_preference_hash')
cache += 'static struct aa_stamp *aa_visit(unsigned o){static struct aa_stamp s;(void)o;return &s;}\nstatic char aa_probe[MAX_PATH];\n'
cache += function(CACHE, 'aa_book_folders')
cache += function(CACHE, 'aa_handle_offer') + function(CACHE, 'aa_request_owner')
cache += function(CACHE, 'aa_track_change_cb')
cache += r'''

int main(int argc, char **argv)
{
    assert(argc == 2);
    mkdir(CACHE_ROOT);
    aa_identity_dirs();
    unsigned owner = art_cache_dir_hash("/library/author");
    if (!strcmp(argv[1], "R2"))
    {
        assert(aa_save_request(owner, "/library/author", AA_ROLE_ARTIST, -1));
        aa_request_owner(owner);
        assert(posted == 1);
        aa_handle_offer();
        assert(last_role == AA_ROLE_ARTIST &&
               !strcmp(last_probe, "/library/author"));
        unsigned book = art_cache_dir_hash("/library/author/book.epub");
        assert(aa_save_request(book, "/library/author/book.epub", AA_ROLE_BOOK,
                               -1));
        aa_request_owner(book);
        aa_handle_offer();
        assert(book_repairs == 1);
        puts("R2: persisted artist and book requests dispatch to their own "
             "adapters");
    }
    else if (!strcmp(argv[1], "R6"))
    {
        bool aborted = false;
        struct aa_books_ctx c = {NULL, 0, &aborted};
        aa_book_folders("/library/author/book.epub", &c);
        assert(!strcmp(first_probe, "/library/author"));
        candidates = 0;
        aa_book_folders("/library/author/book/book.epub", &c);
        assert(!strcmp(first_probe, "/library/author/book"));
        assert(!strcmp(last_probe, "/library/author"));
        puts("R6-folders: flat and nested books pass artist directories");
    }
    else if (!strcmp(argv[1], "R4"))
    {
        aa_io_failed = false;
        fail_ref_open = 1;
        assert(!aa_reference(owner, 123));
        assert(aa_io_failed && aa_state == ART_FAILED);
        assert(!aa_referenced(owner));
        puts("R4: reference open ENOSPC marks request failed");
    }
    else if (!strcmp(argv[1], "R5"))
    {
        asleep = 1;
        strcpy(current.path, "/music/asleep.mp3");
        int before = open_calls;
        aa_track_change_cb(0, NULL);
        assert(open_calls == before && posted == 1);
        posted = 0;
        aa_handle_offer();
        assert(candidates == 0 && poll_fn);
        assert(poll_fn(&aa_offer_tmo) == HZ);
        assert(posted == 0);
        asleep = 0;
        assert(poll_fn(&aa_offer_tmo) == 0 && posted == 1);
        aa_handle_offer();
        assert(candidates > 0);
        candidates = 0;
        memory_ok = 0;
        poll_fn = NULL;
        aa_offer_retries = 0;
        aa_handle_offer();
        assert(poll_fn && candidates == 0);
        memory_ok = 1;
        poll_fn(&aa_offer_tmo);
        aa_handle_offer();
        assert(candidates > 0);
        memory_ok = 0;
        aa_offer_retries = 3;
        poll_fn = NULL;
        aa_handle_offer();
        assert(!poll_fn);
        memory_ok = 1;
        alloc_ok = 0;
        aa_offer_retries = 0;
        aa_handle_offer();
        assert(poll_fn);
        alloc_ok = 1;
        poll_fn(&aa_offer_tmo);
        aa_handle_offer();
        puts("R5: sleep retains request; wake and bounded scratch retries "
             "resume it");
    }
    else if (!strcmp(argv[1], "R8"))
    {
        strcpy(aa_offer.path, "/music/io.mp3");
        aa_offer.owner = art_cache_dir_hash(aa_offer.path);
        aa_offer.policy = -1;
        mode = 1;
        aa_handle_offer();
        assert(aa_io_failed);
        mode = 2;
        candidates = 0;
        aa_handle_offer();
        assert(candidates == 2 && aa_referenced(aa_offer.owner) != 0);
        puts("R8: standalone request resets I/O error before candidate "
             "fallback");
    }
    else
    {
        assert(aa_reference(owner, 123));
        assert(aa_referenced(owner) == 123);
        global_settings.art_cache_album_source = ART_SOURCE_FILES;
        assert(aa_referenced(owner) == 0);
#ifdef CURRENT_HEAD
        unsigned embedded=art_cache_preference_hash("/<HDD0>/Music/./Track.mp3",AA_PREFER_EMBEDDED);
        assert(embedded==art_cache_preference_hash("/music/track.mp3",AA_PREFER_EMBEDDED));
        assert(embedded!=art_cache_preference_hash("/music/track.mp3",AA_PREFER_IMAGE_FILE));
        assert(embedded!=art_cache_preference_hash("/music/track.mp3",AA_PREFER_CACHE));
#endif
        puts("R3-reference: source setting invalidates references from another "
             "policy");
    }
    return 0;
}
'''
cache_exe = build('cache-flows', cache)
failed = []
for exe, case in [(play_exe, 'R1'), (play_exe, 'R3-playback'),
                  *[(cache_exe, c) for c in ['R2', 'R3-reference', 'R4', 'R5', 'R6', 'R8']]]:
    try:
        if exe == cache_exe:
            shutil.rmtree(OUT / 'cache', ignore_errors=True)
        print(run([exe, case], case), end='', flush=True)
    except RuntimeError as error:
        print(error, flush=True)
        failed.append(case)
if failed:
    raise SystemExit('FAIL: ' + ', '.join(failed))
