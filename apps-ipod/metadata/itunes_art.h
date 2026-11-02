/***************************************************************************
 * GNU General Public License (version 2+)
 * Read-only iTunes artwork lookup by track path and database identity.
 ****************************************************************************/
#ifndef ITUNES_ART_H
#define ITUNES_ART_H
#include <stdbool.h>
struct albumart_source;
bool itunes_art_find(const char *track, struct albumart_source *source);
#endif
