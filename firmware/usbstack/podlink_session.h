/* Resident owner's transport ports. GPL version 2 or later. */
#ifndef PODLINK_SESSION_H
#define PODLINK_SESSION_H
#include "podlink.h"
bool podlink_session_open(void);
int podlink_session_send(unsigned int type, const void *data, unsigned int length);
int podlink_session_poll(struct podlink_message *message);
unsigned int podlink_session_max_message(void);
enum podlink_connection podlink_session_state(void);
void podlink_session_close(void);
void podlink_session_wait(int ticks);
void podlink_session_abort(void);
uint32_t podlink_session_revision(void);
#endif
