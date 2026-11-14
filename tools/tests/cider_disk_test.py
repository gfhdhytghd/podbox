"""Wiring of the iPod-local store in the production Cider worker: single owner,
spinning-disk-only writes, USB park, no filesystem calls on UI/service paths."""
from pathlib import Path
import re,sys
R=Path(__file__).resolve().parents[2]
s=(R/'apps-ipod/screens/playback/cider.c').read_text()
def fn(name):
 m=re.search(r'(?m)^(?:static )?[\w *]+\b'+name+r'\([^;{}]*\)\s*\{',s);assert m,name
 e=m.end();d=1
 while d:d+=(s[e]=='{')-(s[e]=='}');e+=1
 return s[m.start():e]
worker=fn('disk_worker')
# Writes need an already spinning disk and an idle UI; the load is user-driven only.
batch=fn('store_write_batch')
assert worker.index('active && !busy && store.loaded')<worker.index('store_write_batch()')
assert 'cs_put(' in batch and 'batch < 6' in batch and 'queue_empty(&disk_queue)' in batch
assert re.search(r'!store\.loaded && \(active \|\| wanted\)',worker)
# USB: flush and forget the index before acknowledging the handover.
assert worker.index('cs_unload(&store)')<worker.index('usb_acknowledge(SYS_USB_CONNECTED_ACK')
assert 'SYS_USB_DISCONNECTED' in worker
# Maintenance (evict/compact/rebuild) is gated by the same spinning-disk check.
assert worker.index('active && !busy && store.loaded')<worker.index('cs_maintain(')
# Only the worker touches the store files; callers see the RAM filter.
ui=''.join(fn(n) for n in ['cider_request_art','cider_pin_art','cider_unpin_art','cider_warm_art','cider_request_xs','background_start','begin_transfer','next','receive','store_text','store_record'])
for banned in ['cs_get(','cs_put(','cs_load(','cs_maintain(','cs_list(','open(','read(','write(','fsync(','remove(','rename(']:
 assert banned not in ui,banned
assert 'cs_maybe' in fn('store_local')
# Limits: 1 GiB default cap, 32 MiB packs, old per-cover cache ignored.
assert 'CIDER_STORE_CAP CS_DEFAULT_CAP' in s
h=(R/'apps-ipod/screens/playback/cider_store.h').read_text()
assert 'CS_DEFAULT_CAP (1024u << 20)' in h and 'CS_PACK_BYTES (32u << 20)' in h
print('Cider store wiring: worker-only file access, spinning-disk-only writes/maintenance, USB park before ACK, 1 GiB cap PASS')
