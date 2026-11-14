# Cider Remote v1: resident data channel and cache

This is an internal protocol and implementation reference. Step 1 provides
Cider Now Playing. Library browsing and the Cider Cover Flow source adapter
belong to steps 2 and 3; native Cover Flow stays native in this package.

## Ownership and connection

HerdRemote owns `hrcider.py` and `hrcider_cache.py` in its existing resident
PodLink session. Only the computer calls Cider or downloads artwork. The player
holds cached views and native bitmaps. No Cider plugin or new service is used.

The Cider worker starts after the player advertises `ci`. Legacy firmware does
not start it. Older daemons ignore `ci`; native screens remain available.
Cider records have their own session generation and do not require an Agents
foreground `hr` handshake. Disconnect/reconnect invalidates pending requests,
credits and controls, while validated memory/disk artwork survives.

The player sends `ci<TAB>1<TAB>window`. The host selects a fresh nonzero 32-bit
session generation and sends `CA`. The legacy inbox profile allows at most
24 KiB outstanding. The resident profile advertises 128 KiB. **The larger
profile bypasses the 16-frame/32-KiB foreground inbox entirely:**
`podlink_service.c` calls the resident receiver before storing foreground
messages. Known Cider state records and binary artwork go to that receiver.
Unknown/legacy records retain their original route.

The resident receiver and its outgoing mailbox share the service owner; they
never call PodLink recursively. The UI only copies a state snapshot or a
completed bitmap. Disk reads/writes run on the low-priority Cider cache worker.

## Records and priorities

One record is one PodLink TEXT payload. Fields use tabs, UTF-8 text fields have
control characters removed and byte limits. All numeric ids are unsigned.
`gen` is the current session generation; a mismatched generation is ignored.
Request ids are unique within that generation; zero is reserved for cancel-all.

| Player -> host | Meaning |
| --- | --- |
| `ci 1 window` | Start/restart this capability; discard old requests/credits |
| `cg gen bytes` | Add byte credits, capped at the negotiated window |
| `cr gen req art-id tier offset priority distance` | One whole cover, starting at offset, no existence probe |
| `cb gen req art-id,art-id,...` | Pull up to five XS covers in one range message |
| `cx gen req` | Cancel queued/fetching/streaming work for one id; id 0 cancels all artwork |
| `cc gen req command value` | Explicit control; never retry a timed-out control |
| `cp gen req kind offset limit library-gen` | Cached library page, limit 1..50 |
| `ct gen` | Request a rate-limited full metadata rescan |

Queue classes, in order: control reply (0), now-playing/availability/library
revision (1), visible list page (2), visible art (3), idle prefetch (4). Within
artwork, distance from center wins; equal-priority chunks retain their original
cover order. Consecutive chunks complete a cover without per-chunk requests.
Controls/state may preempt between chunks. Queues are bounded, canceled jobs
are removed, and a result from an obsolete fetch token is discarded.

Small state records are coalesced to at most 4 Hz. Bulk frames consume their
**entire PodLink payload length**, including the artwork header, from credits.
No large data is pushed without a pull request. Credits return only after the
resident receiver has consumed the bytes. A canceled same-generation frame is
consumed and refunded; an obsolete-generation frame cannot mutate the picture.
The 128-KiB grant fits an entire L cover or three M covers without a grant round
trip. Credits do not allocate a second 128-KiB queue: chunks copy directly into
the bounded assembly buffer.

Host -> player:

```
CA gen available reason
CN gen seq host-ms duration-ms position-ms rate volume shuffle repeat track-id art-id bg title artist album
CG gen library-generation
CK gen req ok message
CL gen req library-generation offset total possibly-stale count
id title subtitle art-id duration-ms track-count
...
```

`rate` is 0 or 1000; `volume` is 0..100; shuffle is 0/1 and repeat is 0/1/2
(none/one/all). `host-ms` is monotonic milliseconds modulo 2^32. The player
anchors at reception and extrapolates position locally; no per-second position
traffic is needed. Polling compares position against the last published
anchor, sends real changes/seeks, and sends a 15-second liveness anchor.
Unavailability disables controls and routes Now Playing back to native WPS.
Now-playing is **never** loaded from the persistent cache.

List ids are preserved, with compact title/subtitle fields. A frame returns as
many rows as fit, at most 50; the next offset is `offset + count`. Each page
carries its library generation. A future list view discards pages older than
`CG`, reissues at the new generation, and prefetches the next page near its end.
Step 1 implements the host cache/page sender and resident credit route; page
view storage/rendering is a step 2 responsibility.

## Artwork identity, geometry and framing

`art-id = SHA-256(exact artwork URL template)`, lowercase hex. Cache keys also
include the tier and the native format contract. URL placeholders request
**240x240**. Existing numeric dimension variants are rewritten to 240x240;
URLs without a capped variant are refused. Downloads are bounded to 2 MiB and
images exceeding 240 pixels on either side are refused before persistence.
The host's "original" is this capped download, never a 4000-pixel original.

| Tier | Dimensions | Pixels | Native payload including header |
| --- | --- | --- | --- |
| L | 240x240 | 115,200 bytes | 115,212 bytes |
| M | 128x128 | 32,768 bytes | 32,780 bytes |
| S | 56x56 | 6,272 bytes | 6,284 bytes |
| XS | 32x32 | 2,048 bytes | 2,060 bytes |

All classes derive from the capped source using Pillow, with a small ordered
dither before RGB565 quantization. M replaces the preliminary 140-pixel tier.
M is **column-major**, matching the native carousel. Other tiers are row-major.
No JPEG decoding, resize, transpose or dither runs on the player.

The payload is exactly `struct art_cache_header` (six little-endian u16 fields)
then native little-endian RGB565 pixels: magic 0x5441, version 8, width, height,
layout (M=1, others=0), zero pad. A native format change requires the matching
host renderer/cache schema to change too. Native local WPS caches are 300px
(`art_sizes.h`); that is unchanged. This Cider screen draws a 240x240 art box
at x=40, with its bottom overlaid by track information. Its L is always 240,
including when native WPS happens to use a 300px cache.

A normal artwork PodLink BINARY payload begins with little-endian `<4s6I>`:

```
"CAR1", gen, req, byte-offset, whole-payload-size, whole-CRC32, chunk-CRC32
<bytes>
```

The whole payload includes the native header. Maximum **frame payload** is
12,288 bytes, leaving at most 12,260 data bytes. Both CRCs are checked; offsets,
request id, generation, geometry/layout/version and total bounds must match.
An interrupted request can resume at its retained byte offset. Reconnect
drops an incomplete assembly; completed cache entries remain.

An XS range uses `"CXS1", gen, req, count` (`<4sIII>`), followed by each entry's
32-byte art-id, u32 size, u32 CRC32, and native XS payload. Five entries occupy
10,516 bytes. `bg` is an immediate solid placeholder; XS upgrades it during
fast scrolling. The resident receiver validates entries before caching them.

## Cache budgets, durability and synchronization

Host root: `$XDG_CACHE_HOME/podbox-cider/`, default `~/.cache/podbox-cider/`,
mode 0700. SQLite holds albums, songs/tracks, playlists and artists: stable ids,
names, subtitle, counts, durations, dateAdded, artwork templates and generation.
The database schema is versioned; an incompatible schema rebuilds the cache.
WAL/transactions protect metadata. Artwork uses fsynced temp files plus atomic
rename, with indexed LRU eviction. Orphan/temp recovery runs on cache open.
The default art cap is **500 MiB**, including capped originals and rendered
L/M/S/XS files. API tokens are never persisted or sent to the CDN.

Cache reads precede network fills. Cached pages and bitmaps remain usable when
Cider is slow or restarting; pages carry a possibly-stale flag. Control still
requires live Cider. Initial metadata indexing fetches 50 items per request,
at most one request per second. Incremental probes run every 180 seconds,
compare counts and first-page attribute/dateAdded fingerprints, and rescan a
changed kind. A complete background scan also runs every 15 minutes; `ct`
requests it on demand. Only a completed scan removes missing ids. Errors back
off to 300 seconds; an unsupported kind cannot starve the others. Link reads
and sync/art workers share guarded SQLite access, with no HTTP under that lock.

Player RAM: 512-KiB compact LRU arena with 24 descriptors; 115,212-byte assembly;
115,212-byte disk I/O buffer; 115,200-byte screen bitmap; bounded commands and
worker stack. A compile-time assertion keeps this below **1 MiB**. None uses
`app_buffer`. The arena can hold one L plus nine M and nine XS covers together.
Only complete validated bitmaps enter it. Generation changes retain those
entries while invalidating assembly and requests.

Optional persistent player art is capped at **32 MiB**. L/S/XS use
`/.rockbox/cider-cache/`; ready M `.aat` files use
`/.rockbox/library/cache/art/cider-v1/<art-id>-M.aat`. The separate `cider-v1`
namespace cannot collide with local 32-bit album hashes. The local cache pass
purges only its named size folders, not this namespace. A visible miss does
one open/read on its worker, with no prior existence probe. Writes use temp +
fsync + rename, in batches of at most four. They yield, defer during database
work, park for USB storage handover, and never wake a sleeping disk solely for
background writes/eviction. Eviction scans run only after writes, not every
idle wake. Idle fill uses low-priority explicit M pulls and these same gates;
step 1 exposes that transport class, not a whole-library device prefetch loop.

## Fast Cover Flow contract for step 3

Reuse the existing renderer and slide slots. Native references:

- `metadata/art_sizes.h`: 128px `AA_COLUMNS` Cover Flow size, 300px local WPS.
- `metadata/art_cache.c`: pre-rendered `.aat` files; bounded/yielding generation;
  `aa_disk_asleep()` holds work for a sleeping disk.
- `screens/covers/carousel.c`: `read_aat()` performs one aligned whole-file
  read; `free_slide_prio()` evicts only a farther slide; `load_new_slide()`
  grows a contiguous run from center on the shorter side; its worker drains
  pending wakeups and rereads center for each load.

`cider_window()` and the host Window helper implement center-first, contiguous
nine-slide plans, with direction breaking equal-distance ties. At velocity
>=3 albums/second use XS/bg; at slower/stopped positions request M, and request
L only for Now Playing. Re-read center before selecting each missing cover.
Keep an in-flight cover if it remains in the window; otherwise cancel it.
The portable `cider_slide_slot()` admission helper refuses a farther replacement
and selects the farthest eligible slot; it does not clear the window on moves.
Never flush/re-request an unchanged cached window. Evict/cancel farthest-first
only to admit a nearer cover. Batch XS for the whole visible range and weight
the next prefetch range by direction/speed.

A source adapter is still necessary: the native `load_and_prepare_surface()`
resolves albums through the local database's numeric indices/hashes, whereas
Cider has Apple string ids and an independently paged generation. Step 3 adds
an album-provider/path adapter to that function and hands the ready M file to
its existing `read_aat()` path. It must not introduce a parallel renderer or a
nested app_buffer owner. This package does not replace the native carousel.

## Cider API and acceptance

The installed Cider 4.0.0 app.asar is the source for these routes. Full source
hash and bounded excerpts are in the FU22 evidence (`cider-api-proof.json`).

| Method / path | Body / result |
| --- | --- |
| GET `/api/v1/playback/now-playing` | `info` attributes and playParams |
| GET `/api/v1/playback/is-playing` | `is_playing` |
| GET `/api/v2/playback/time` | `data.currentTime`, seconds (legacy `currentPlaybackTime` also accepted) |
| GET `/api/v1/playback/volume` | `volume`, 0..1 |
| GET `/api/v1/playback/repeat-mode`, `shuffle-mode` | `value` |
| POST `/api/v1/playback/playpause` | `{}` |
| POST `/api/v1/playback/next`, `previous` | `{}` |
| POST `/api/v1/playback/seek` | `{"position": seconds}` |
| POST `/api/v1/playback/volume` | `{"volume": 0..1}` |
| POST `/api/v1/playback/play-item` | `{"type": kind, "id": stable-id}` |
| POST `/api/v1/amapi/run-v3` | `{"path":"/v1/me/library/...?..."}` |

The optional token header is **`apptoken`**, supplied via `CIDER_API_TOKEN`.
`CIDER_API_URL` defaults to `http://127.0.0.1:10767`; only loopback RPC URLs are
accepted, and RPC ignores HTTP proxy environment settings. Socket.IO broadcasts
`API:Playback` with `{type,data}` messages (item/state/time changes); the time
event is approximately once per second. Step 1 uses the allowed <=1-second
REST polling fallback, with change detection, avoiding another socket dependency.
Pillow is already present in the build/test venv; no package installation is
needed for this work. A deployed daemon needs Pillow in its own environment.

Native WPS destinations of Agents' double-Play/SELECT+PLAY shortcuts resolve
to Cider when available, native WPS otherwise. Existing Agents destinations
from other screens remain Agents; Cider double/chord returns to Agents. Triple
still opens the native Cover Flow. Cider Play tap toggles, Left/Right skip,
held Left/Right preview seek (5 seconds per repeat, one seek on release),
volume changes by one point per report, and MENU returns. Cider uses the WPS
keymap but disables real iPod volume mutation; existing PC-only connected /
Dock-only disconnected source filtering remains in force. Losing the host
falls back to native WPS, where normal Dock behavior resumes.

Control endpoint tests use an ephemeral loopback mock Cider server. The
permission update also permits direct Cider changes, with per-file backups
before mutation. Real read-only state/API and capped-CDN checks can supplement
the mocks; playback is not changed by those checks. The FU22 benchmark uses
real framing/CRC, a mock callback link and the 20-ms host pump, plus a host
filesystem one-open/one-read `.aat` reference. It does **not** measure USB,
iPod disk DMA, physical IR latency, audio or actual Cider rendering. Device
acceptance and native disk timings require separately authorized installation.


## Diagnostic capture

Enable **System > Debug > Remote log stream** after reboot. HerdRemote then
writes device `LOG CIDER` records to `~/.local/state/podbox/remote-live.log`:
request id/offset/tick, granted/outstanding credits, chunk bytes/result and
completion tick/size. The host journal also records fetch/cache-ready latency.
The existing bounded diagnostic ring reports loss; logging never holds up art
or foreground keepalives. No token, decoded audio or artwork pixels enter logs.
