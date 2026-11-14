/* Resident Cider client; owns no audio or app_buffer. GPL version 2+. */
#ifndef CIDER_H
#define CIDER_H
#include "config.h"
#include "screens/playback/cider_model.h"
#ifdef HAVE_USB_PODLINK
void cider_init(void);
bool cider_available(void);
bool cider_active(void);
void cider_snapshot(struct cider_state *state);
/* Small transport state for callers on tight stacks (UI, skin, status). */
struct cider_status { bool available, muted; int rate, volume, shuffle, repeat; };
void cider_status(struct cider_status *status);
void cider_control(const char *command, int value);
int cider_mode_get(int mode);
void cider_mode_set(int mode, int value);
void cider_prefetch_art(const char *key, const char *tier);
void cider_warm_art(const char *const *keys, unsigned int count, const char *tier);
void cider_request_art(const char *key, const char *tier);
void cider_cancel_art(void);
void cider_pin_art(const char *key);
void cider_unpin_art(void);
void cider_retain_art(const char *const *keys, unsigned int count,
                      const char *tier);
unsigned int cider_art_revision(void);
bool cider_has_art(const char *key, const char *tier);
void cider_request_xs(const char *const *keys, unsigned int count);
bool cider_copy_art(const char *key, const char *tier, void *pixels,
                     unsigned int size);
bool cider_play(const char *route);
/* Play a song of the list 'context' (a page route) at row 'index' */
bool cider_play_in(const char *route, const char *context, int index);
int cider_play_status(void);
void cider_local_play(void);
void cider_page_open(const char *route);
int cider_page_total(void);
bool cider_page_stale(void);
struct cider_row;
bool cider_page_row(int index, struct cider_row *row);
#else
static inline bool cider_active(void) { return false; }
static inline bool cider_available(void) { return false; }
static inline void cider_snapshot(struct cider_state *s)
{ *s = (struct cider_state){0}; }
struct cider_status { bool available, muted; int rate, volume, shuffle, repeat; };
static inline void cider_status(struct cider_status *s)
{ *s = (struct cider_status){0}; }
static inline void cider_local_play(void) { }
static inline void cider_control(const char *c, int v) { (void)c; (void)v; }
static inline int cider_mode_get(int m) { (void)m; return 0; }
static inline void cider_mode_set(int m, int v) { (void)m; (void)v; }
#endif
#endif
