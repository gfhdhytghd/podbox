/* Cider wire state and native artwork cache. GPL version 2 or later. */
#ifndef CIDER_MODEL_H
#define CIDER_MODEL_H
#include <stdbool.h>
#include <stdint.h>
#define CIDER_ART_MAX (12 + 240 * 240 * 2)
#define CIDER_RAM_BYTES (512 * 1024)
#define CIDER_SLOTS 24
#define CIDER_CREDIT (128 * 1024)
struct cider_state
{
    uint32_t generation, seq, host_ms, duration, position, received;
    uint32_t library_generation;
    bool available, have, muted, loading;
    int rate, volume, shuffle, repeat; /* repeat in Rockbox numbering */
    uint32_t bg;
    char previous_art[65], next_art[65];
    char id[80], art[65], title[124], artist[124], album[124], composer[124], reason[96];
};
#define CIDER_MODE_SHUFFLE 0   /* Cider shuffleMode: 0 off, 1 on */
#define CIDER_MODE_REPEAT 1    /* Rockbox repeat_mode: 0 off, 1 all, 2 one, 3 shuffle, 4 A-B */
#define CIDER_REPEAT_AB 4
#define CIDER_MODE_COUNT 2
#define CIDER_MODE_HOLD 2      /* seconds a requested value outlives its CN */
/* A shown-ahead mode value, until a CN agrees, the host refuses or time runs out. */
struct cider_modes
{
    bool pending[CIDER_MODE_COUNT];
    int want[CIDER_MODE_COUNT];
    uint32_t at[CIDER_MODE_COUNT], request[CIDER_MODE_COUNT];
};
struct cider_slot
{
    char key[65], tier[3];
    unsigned int offset, length, used;
    bool dirty, bg, pinned; /* bg: idle store fill, evicted first */
};
struct cider_cache
{
    unsigned char data[CIDER_RAM_BYTES];
    struct cider_slot slot[CIDER_SLOTS];
    unsigned int bytes, clock;
};
struct cider_transfer
{
    unsigned char data[CIDER_ART_MAX];
    uint32_t request, generation, received, total, crc;
    char key[65], tier[3];
    bool active, fill; /* fill: host-offered store fill, idle priority */
};
int cider_state_apply(struct cider_state *s, const void *data,
                      unsigned int length, uint32_t tick);
int cider_reply_status(const void *data, unsigned int length,
                       uint32_t generation, uint32_t request);
int cider_mode_step(int mode, int value, bool previous);
int cider_mode_show(struct cider_modes *m, int mode, int live,
                    uint32_t tick, int hz);
void cider_mode_ask(struct cider_modes *m, int mode, int value,
                    uint32_t tick, uint32_t request);
bool cider_mode_failed(struct cider_modes *m, uint32_t request);
uint32_t cider_position(const struct cider_state *s, uint32_t tick, int hz);
int cider_cache_find(struct cider_cache *c, const char *key, const char *tier);
bool cider_cache_put(struct cider_cache *c, const char *key, const char *tier,
                     const void *data, unsigned int length, bool dirty);
/* Idle fills sort first for eviction; dropped once written to the store. */
void cider_cache_demote(struct cider_cache *c, const char *key, const char *tier);
void cider_cache_drop_bg(struct cider_cache *c, const char *key, const char *tier);
/* A pinned slot is never evicted; one key at a time (NULL unpins all). */
bool cider_cache_pin(struct cider_cache *c, const char *key, const char *tier);
bool cider_art_valid(const void *data, unsigned int length, const char *tier);
int cider_chunk(struct cider_transfer *t, const void *data,
                 unsigned int length);
int cider_window(int center, int velocity, int out[9]);
int cider_slide_slot(const int loaded[9], int center, int wanted);
#endif
