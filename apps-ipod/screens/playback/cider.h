/* Resident Cider client; owns no audio or app_buffer. GPL version 2+. */
#ifndef CIDER_H
#define CIDER_H
#include "screens/playback/cider_model.h"
void cider_init(void);
bool cider_available(void);
bool cider_active(void);
void cider_set_active(bool active);
void cider_snapshot(struct cider_state *state);
void cider_control(const char *command, int value);
void cider_request_art(const char *key, const char *tier);
void cider_cancel_art(void);
void cider_request_xs(const char *const *keys, unsigned int count);
bool cider_copy_art(const char *key, const char *tier, void *pixels,
                     unsigned int size);
int cider_screen(void);
#endif
