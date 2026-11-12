#!/usr/bin/env python3
"""Host regression of production idle-palette handoff; no device access."""
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(os.environ.get('ART_TEST_ROOT', Path(__file__).resolve().parents[2]))
OUT = Path(tempfile.mkdtemp(prefix='storage-idle-tests-'))
os.environ['ART_TEST_ROOT'] = str(ROOT)
os.environ['ART_TEST_OUT'] = str(OUT)
sys.path.insert(0, str(Path(__file__).with_name('albumart')))
from support import build, function, run  # noqa: E402

code = r'''
#define PREFETCH_SLOTS 4
#define PALETTE_EVENT_IDLE 3
struct queue_event { long id; intptr_t data; };
struct bg_task { const char *name; void (*handle_event)(const struct queue_event *); };
struct mp3entry { char path[260]; };
static struct { bool dynamic_colors; } global_settings;
static bool active = true, usb, queued;
static int posts, peeks, reads, dim = 128;
static bool usb_host_wants_storage(void) { return usb; }
static bool storage_disk_is_active(void) { return active; }
static int palette_dim(void) { return dim; }
static bool audio_peek_buffered_track(struct mp3entry *id3, int offset)
{ peeks++; assert(offset > 0); strcpy(id3->path, "/fixture/track.mp3"); return offset < 3; }
static unsigned int id3_artist_key(struct mp3entry *id3) { (void)id3; return 7; }
static void prefetch_path(const char *path, unsigned int artist)
{ assert(artist == 7 && active && !usb && !strcmp(path, "/fixture/track.mp3")); reads++; }
static bool bg_task_queued(const struct bg_task *task, long id)
{ (void)task; assert(id == PALETTE_EVENT_IDLE); return queued; }
static void bg_task_post(struct bg_task *task, long id)
{ assert(task->handle_event && id == PALETTE_EVENT_IDLE); posts++; queued = true; }
'''
code += function('apps-ipod/skin/skin_albumart_color.c', 'prefetch_idle_work')
text = (ROOT / 'apps-ipod/skin/skin_albumart_color.c').read_text()
start = text.index('static struct bg_task palette_idle_task')
code += text[start:text.index('\n};', start) + 3]
code += function('apps-ipod/skin/skin_albumart_color.c', 'prefetch_idle_cb')
code += r'''
int main(void)
{
    prefetch_idle_cb(0, NULL);
    assert(!posts && !peeks && !reads);
    global_settings.dynamic_colors = true;
    prefetch_idle_cb(0, NULL);
    prefetch_idle_cb(0, NULL);
    assert(posts == 1 && queued && !peeks && !reads);
    /* Worker delayed beyond sleep/USB must do no metadata or disk work. */
    active = false;
    prefetch_idle_work(NULL);
    assert(!peeks && !reads);
    active = true; usb = true;
    prefetch_idle_work(NULL);
    assert(!peeks && !reads);
    usb = false; dim = 0;
    prefetch_idle_work(NULL);
    assert(!peeks && !reads);
    dim = 128;
    prefetch_idle_work(NULL);
    assert(peeks == 3 && reads == 2);
    global_settings.dynamic_colors = false;
    prefetch_idle_work(NULL);
    assert(peeks == 3 && reads == 2);
    queued = false; global_settings.dynamic_colors = true;
    prefetch_idle_cb(0, NULL);
    assert(posts == 2);
    puts("PASS: deferred idle dispatch, coalescing, disabled colors, USB, sleeping disk, missing size, buffered tracks");
}
'''
print(run([build('storage-idle', code)], 'storage-idle'), end='')
print('Logs:', OUT)

# The book callback must not enter audio_current_track's playlist/UI fallback
# on the ATA stack. Preserve the save, bounded wait and unconditional release.
code = r'''
struct queue_event { long id; intptr_t data; };
#define IDLE_SAVE_EVENT 1
#define IDLE_SAVE_WAIT 25
static bool idle_armed = true, on_storage;
static int idle_saved, idle_save_task, posts, waits, saves, releases;
static void bg_task_post(int *task, long id)
{ assert(task == &idle_save_task && id == IDLE_SAVE_EVENT); posts++; }
static void semaphore_wait(int *sem, int timeout)
{ assert(sem == &idle_saved && timeout == IDLE_SAVE_WAIT); waits++; }
static void semaphore_release(int *sem) { assert(sem == &idle_saved); releases++; }
static void book_resume_save(void) { assert(!on_storage); saves++; }
'''
code += function('apps-ipod/metadata/book_resume.c', 'storage_idle')
code += function('apps-ipod/metadata/book_resume.c', 'idle_save_event')
code += r'''
int main(void)
{
    on_storage = true;
    storage_idle();
    assert(!idle_armed && posts == 1 && waits == 1 && !saves);
    on_storage = false;
    idle_save_event(NULL);
    assert(saves == 1 && releases == 1);
    idle_save_event(NULL);
    assert(saves == 2 && releases == 2);
    puts("PASS: storage book callback only posts/waits; worker preserves save and always releases");
}
'''
print(run([build('storage-book-idle', code)], 'storage-book-idle'), end='')
