/* Cider's bounded native-list provider. Copyright (C) 2026. GPL v2+. */
#ifndef CIDER_LIBRARY_H
#define CIDER_LIBRARY_H
#include <stdbool.h>
#include <stdint.h>
#define CIDER_PAGE_ROWS 50
struct cider_row
{
    char id[80], title[124], subtitle[124], art[65], route[320];
    uint32_t duration, bg;
    int count, year;
    unsigned char kind;
};
enum
{
    CIDER_ROW_MENU,
    CIDER_ROW_ALBUM,
    CIDER_ROW_ARTIST,
    CIDER_ROW_PLAYLIST,
    CIDER_ROW_SONG
};
void cider_library_open(const char *route);
int cider_library_total(void);
bool cider_library_stale(void);
bool cider_library_row(int index, struct cider_row *row);
bool cider_library_receive(const void *data, unsigned int size,
                           uint32_t generation, uint32_t library);
unsigned int cider_library_next(char *out, unsigned int size,
                                uint32_t generation);
void cider_library_reset(void);
void cider_library_invalidate(uint32_t generation);
int cider_browse(void);
int cider_browse_covers(bool home);
bool cider_browse_remote_return(void);
#define CIDER_COVERS_REMOTE_RETURN (-1001)
int cider_covers(void);
void cider_browse_album(const struct cider_row *row);
#endif
