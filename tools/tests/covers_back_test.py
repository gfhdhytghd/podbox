#!/usr/bin/env python3
"""Offline regressions for the production screen dispatcher's WPS back path."""
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(os.environ.get('ART_TEST_ROOT', Path(__file__).resolve().parents[2]))
OUT = Path(tempfile.mkdtemp(prefix='covers-back-tests-'))
os.environ['ART_TEST_ROOT'] = str(ROOT)
os.environ['ART_TEST_OUT'] = str(OUT)
sys.path.insert(0, str(Path(__file__).with_name('albumart')))
from support import build, function, run  # noqa: E402

header = (ROOT / 'apps-ipod/root_menu.h').read_text()
enums = header[header.index('enum {'):]
enums = enums[:enums.index('\n};') + 3]
code = next(line for line in header.splitlines()
            if line.startswith('#define TAGNAVI_MAIN_MENU_SLOTS')) + '\n' + enums + r'''
enum current_activity {
    ACTIVITY_UNKNOWN, ACTIVITY_PLUGINBROWSER, ACTIVITY_SETTINGS,
    ACTIVITY_SYSTEMSCREEN
};
static struct { int last_screen; } global_status;
static int last_screen = GO_TO_ROOT, screen_result, calls;
static void status_save(bool force) { assert(!force); }
static void push_current_activity(enum current_activity a) { (void)a; }
static void pop_current_activity(void) {}
static void pop_current_activity_without_refresh(void) {}
static int screen_stub(void *param)
{ (void)param; calls++; return screen_result; }
static struct { int (*function)(void *); void *param; } items[256];
'''
code += function('apps-ipod/root_menu.c', 'load_screen')
code += r'''
static int enter(int screen, int result)
{
    screen_result = result;
    int count = calls;
    int next = load_screen(screen);
    assert(calls == count + 1);
    /* The resume file records the actual screen, never a back destination. */
    assert(global_status.last_screen == screen);
    return next;
}
int main(void)
{
    for (unsigned i = 0; i < sizeof(items) / sizeof(items[0]); i++)
        items[i].function = screen_stub;

    for (int round = 0; round < 3; round++)
    {
        last_screen = GO_TO_ROOT;
        assert(enter(GO_TO_ALBUM_COVERS, GO_TO_ALBUM_COVERS_TRACKS)
               == GO_TO_ALBUM_COVERS_TRACKS);
        assert(enter(GO_TO_ALBUM_COVERS_TRACKS, GO_TO_WPS) == GO_TO_WPS);
        assert(last_screen == GO_TO_ALBUM_COVERS);
        assert(enter(GO_TO_WPS, GO_TO_WPS_MENU) == GO_TO_PREVIOUS);
        assert(last_screen == GO_TO_ALBUM_COVERS);
        assert(enter(last_screen, GO_TO_PREVIOUS) == GO_TO_PREVIOUS);
        assert(last_screen == GO_TO_ROOT); /* MENU again leaves; no bounce. */

        /* Back from the track list without playback retains the old route. */
        enter(GO_TO_ALBUM_COVERS, GO_TO_ALBUM_COVERS_TRACKS);
        assert(enter(GO_TO_ALBUM_COVERS_TRACKS, GO_TO_PREVIOUS)
               == GO_TO_PREVIOUS);
        assert(last_screen == GO_TO_ALBUM_COVERS);
        enter(last_screen, GO_TO_PREVIOUS);
        assert(last_screen == GO_TO_ROOT);
    }

    /* Other track-list callers do not acquire a carousel return target. */
    int sources[] = {GO_TO_DBBROWSER, GO_TO_FILEBROWSER, GO_TO_ROOT,
                     GO_TO_ARTIST_PORTRAITS, GO_TO_WPS};
    for (unsigned i = 0; i < sizeof(sources) / sizeof(sources[0]); i++)
    {
        last_screen = sources[i];
        assert(enter(GO_TO_WPS, GO_TO_WPS_MENU) == GO_TO_ROOT);
    }
    last_screen = GO_TO_ARTIST_PORTRAITS;
    enter(GO_TO_ALBUM_COVERS_TRACKS, GO_TO_WPS);
    assert(last_screen == GO_TO_ALBUM_COVERS_TRACKS);
    assert(enter(GO_TO_WPS, GO_TO_WPS_MENU) == GO_TO_ROOT);

    /* Other WPS exits are not mistaken for MENU. */
    int exits[] = {GO_TO_ROOT, GO_TO_PREVIOUS_BROWSER, GO_TO_FILEBROWSER,
                   GO_TO_PLAYLIST_VIEWER, GO_TO_ALBUM_COVERS_TRACKS};
    for (unsigned i = 0; i < sizeof(exits) / sizeof(exits[0]); i++)
    {
        last_screen = GO_TO_ALBUM_COVERS;
        assert(enter(GO_TO_WPS, exits[i]) == exits[i]);
    }

    /* Direct Play Album and a WPS restored at boot. */
    last_screen = GO_TO_ROOT;
    enter(GO_TO_ALBUM_COVERS, GO_TO_WPS);
    assert(enter(GO_TO_WPS, GO_TO_WPS_MENU) == GO_TO_PREVIOUS);
    enter(last_screen, GO_TO_PREVIOUS);
    assert(last_screen == GO_TO_ROOT);
    last_screen = GO_TO_ROOT; /* process-local caller is reset at boot */
    assert(enter(GO_TO_WPS, GO_TO_WPS_MENU) == GO_TO_ROOT);
    puts("PASS: covers playback/back, track-list back, repeated exits, "
         "other callers, explicit WPS exits, direct album play and boot resume");
}
'''
print(run([build('covers-back', code)], 'covers-back'), end='')

# Keep the existing positioning contract separate from navigation history.
# Metadata lookup is adapted; the production entry/cancellation functions run.
code = r'''
static bool pf_resume_last_album, playing_audio;
static int pf_resume_album_index, selected;
static struct { int last_album; } pf_cfg;
struct mp3entry { int album; };
static struct mp3entry id3, playing;
static void set_current_slide(int album) { selected = album; }
static int id3_get_index(struct mp3entry *track) { return track->album; }
static int audio_status(void) { return playing_audio; }
static struct mp3entry *audio_current_track(void) { return &playing; }
static bool retrieve_id3(struct mp3entry *track, const char *file)
{ (void)file; track->album = 7; return true; }
'''
code += function('apps-ipod/screens/covers/album_covers.c', 'set_initial_slide')
code += function('apps-ipod/screens/covers/album_covers.c', 'album_covers_cancel_resume')
code += r'''
int main(void)
{
    pf_cfg.last_album = 1;
    pf_resume_album_index = 1;
    pf_resume_last_album = true;
    playing_audio = true;
    playing.album = 2;
    set_initial_slide(NULL);
    assert(selected == 1 && !pf_resume_last_album); /* track-list BACK */
    pf_resume_last_album = true;
    album_covers_cancel_resume(); /* track list started playback */
    set_initial_slide(NULL);
    assert(selected == 2); /* playback advanced into another album */
    playing.album = 3;
    set_initial_slide(NULL);
    assert(selected == 3);
    playing_audio = false;
    set_initial_slide(NULL);
    assert(selected == 1); /* no playback: last viewed album */
    set_initial_slide("/selected.mp3");
    assert(selected == 7);
    puts("PASS: track-list position, cancellation, changed playing album, "
         "idle fallback and explicit selection");
}
'''
print(run([build('covers-position', code)], 'covers-position'), end='')
print('Logs:', OUT)
