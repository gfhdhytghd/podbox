/* Cider iPod-local art store: append-only pack files and an on-disk hash
 * table. Portable C (file access through struct cs_io) so the host tests
 * run the production code. GPL version 2 or later. */
#ifndef CIDER_STORE_H
#define CIDER_STORE_H
#include <stdbool.h>
#include <stdint.h>

#define CS_SECTOR 512u
#define CS_BUCKETS 4096u              /* 2 MiB table, one sector per bucket */
#define CS_SLOTS 24u                  /* 20-byte entries per bucket */
#define CS_PROBE 4u                   /* buckets searched before replacing */
#define CS_PACKS 40u
#ifndef CS_PACK_BYTES
#define CS_PACK_BYTES (32u << 20)     /* one pack file */
#endif
#define CS_BLOOM_BITS (1u << 18)      /* 32 KiB resident presence filter */
#define CS_MAX_ENTRIES 49152u         /* 50% of the table slots */
#define CS_REC_HEAD 64u
#define CS_REC_MAX (226u * CS_SECTOR) /* L cover record, sector aligned */
#define CS_SCAN_BUCKETS 128u          /* 64 KiB per table pass */
#define CS_DEFAULT_CAP (1024u << 20)

enum { CS_L, CS_M, CS_S, CS_XS, CS_TIERS };
enum { CS_OK = 0, CS_DUP = 1, CS_MISS = -1, CS_ERR = -2, CS_BUSY = -3,
       CS_BAD = -4, CS_NOTREADY = -5 };
#define CS_O_READ 1
#define CS_O_WRITE 2                  /* read/write, create, never truncate */

struct cs_io
{
    int (*open)(const char *path, int mode);
    int (*read)(int fd, void *buf, unsigned int n);
    int (*write)(int fd, const void *buf, unsigned int n);
    int (*seek)(int fd, uint32_t offset);
    int (*sync)(int fd);
    int (*close)(int fd);
    int (*remove)(const char *path);
    int (*fsize)(const char *path);   /* -1 when missing */
    void (*mkdir)(const char *path);
    void (*yield)(void);              /* may be NULL */
};

struct cider_store
{
    const struct cs_io *io;
    char dir[40], path[64];
    bool loaded;
    int ifd, pfd, pfd_index;          /* cached descriptors, -1 closed */
    uint32_t epoch, session, seq, count, digest, cap, total;
    struct cs_pack
    {
        uint32_t size, live, session0, seq0;
        uint16_t last;
        bool exists;
    } pack[CS_PACKS];
    int cur;                          /* append pack, -1 none */
    int evict;                        /* victim pack while scrubbing */
    uint32_t evict_at;
    int compact;
    uint32_t compact_off;
    bool rebuild, changed;
    uint8_t order[CS_PACKS];
    unsigned int order_n, order_at;
    uint32_t rebuild_off;
    uint8_t bucket[CS_SECTOR];
    uint8_t bloom[CS_BLOOM_BITS / 8];
};

/* Sizes follow the cover tiers: header 12 + dim*dim*2. */
unsigned int cs_payload_size(unsigned int tier);
unsigned int cs_record_size(unsigned int tier);
int cs_tier(const char *name);                      /* -1 if invalid */
bool cs_key_from_hex(const char *hex, uint8_t key[32]);
void cs_key_to_hex(const uint8_t key[32], char hex[65]);

void cs_init(struct cider_store *cs, const struct cs_io *io, const char *dir,
             uint32_t cap);
/* Opens/creates/validates the store. buf must hold CS_REC_MAX bytes. */
int cs_load(struct cider_store *cs, uint8_t *buf, unsigned int size,
            uint32_t seed);
bool cs_maybe(const struct cider_store *cs, const uint8_t key[32],
              unsigned int tier);                   /* RAM only */
int cs_get(struct cider_store *cs, const uint8_t key[32], unsigned int tier,
           uint8_t *buf, unsigned int size, const uint8_t **payload,
           unsigned int *length);
int cs_put(struct cider_store *cs, const uint8_t key[32], unsigned int tier,
           const void *payload, unsigned int length, uint8_t *buf,
           unsigned int size, uint32_t *crc);
/* One bounded maintenance step; returns true while more work remains. */
bool cs_maintain(struct cider_store *cs, uint8_t *buf, unsigned int size,
                 bool allow_compact);
bool cs_busy(const struct cider_store *cs);         /* evict/rebuild running */
/* Inventory: up to max entries from cursor (bucket<<5 | slot); returns the
 * next cursor (CS_BUCKETS<<5 when finished) or an error. Entry = key[0..8],
 * tier, crc. */
struct cs_item { uint8_t key[8]; uint8_t tier; uint32_t crc; };
int cs_list(struct cider_store *cs, unsigned int cursor, struct cs_item *out,
            unsigned int max, unsigned int *count, uint8_t *buf,
            unsigned int size);
uint32_t cs_item_hash(const struct cs_item *item);
void cs_release(struct cider_store *cs);            /* sync + close fds */
void cs_unload(struct cider_store *cs);             /* USB handover */
int cs_parse_offer(const char *s, unsigned int n, uint32_t gen, int *tier,
                   uint8_t keys[][32], unsigned int max);
int cs_parse_cursor(const char *s, unsigned int n, uint32_t gen,
                    uint32_t *cursor);
#endif
