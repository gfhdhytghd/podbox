/* Cider shuffle/repeat pages for Now Playing long-presses. GPL version 2+. */
#ifndef CIDER_CONTROLS_H
#define CIDER_CONTROLS_H
#include <stdbool.h>
/* Row title and value text of a Cider mode (CIDER_MODE_*), localized. */
const char *cider_mode_title(int mode);
const char *cider_mode_label(int mode, int value);
/* Long SELECT page: Shuffle and Repeat; true when USB took the screen. */
bool cider_context_show(void);
#endif
