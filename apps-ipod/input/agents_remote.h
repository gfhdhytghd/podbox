/* Dock remote input for Agents. Copyright (C) 2026. GPL version 2+. */
#ifndef AGENTS_REMOTE_H
#define AGENTS_REMOTE_H
#include <stdbool.h>
struct agents_remote_state {
    bool menu_down;
    bool menu_long;
    unsigned long menu_at;
};
int agents_remote_button(struct agents_remote_state *s, bool active,
                         int button, int held, unsigned long now);
#endif
