#!/usr/bin/env python3
"""Measure targeted stack-margin fixtures in the linked ipod6g ARM ELF.

No boot, device or MMIO. Real prefetch/registry/validation, source resolution
caller frames, BMP decoder, font-cache loader and idle/log dispatch execute.
Deterministic filesystem/scheduler/metadata adapters are reported. Measurements
are fixture high-water values; caller reserves and filesystem tails are separate.
Requires pyelftools and Unicorn. Run serially with the other ARM layout probes.
"""
import argparse
import json
from pathlib import Path
import shlex
import struct
import subprocess
import tempfile

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm_const import (UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC,
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
    UC_ARM_REG_CPSR)

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--build', type=Path, required=True)
p.add_argument('--elf', type=Path, required=True)
p.add_argument('--case', choices=['audio-prefetch', 'background-bmp',
                                 'audio-font', 'storage-log'], required=True)
p.add_argument('--budget', type=int, required=True)
p.add_argument('--caller-reserve', type=int, default=0)
a = p.parse_args()
build = a.build.resolve()
fields = ['offsetof(struct user_settings, dynamic_colors)',
          'offsetof(struct user_settings, wps_art_source)',
          'offsetof(struct user_settings, debug_log_tagcache)',
          'offsetof(struct mp3entry, path)', 'offsetof(struct track_event, id3)',
          'PLAYBACK_EVENT_TRACK_BUFFER', 'DISK_EVENT_SPINUP', 'SYS_TIMEOUT',
          'sizeof(struct albumart_source)', 'AA_SOURCE_FILE',
          *['offsetof(struct albumart_source, %s)' % n for n in
            ['kind', 'path', 'pos', 'size', 'revision']],
          'sizeof(struct mutex)', 'offsetof(struct font, fd)', 'offsetof(struct font, cache._capacity)']
with tempfile.TemporaryDirectory(prefix='arm-margin-layout-') as tmp:
    tmp = Path(tmp)
    plan = subprocess.check_output(['make', '-C', str(build), '-Bn',
        str(build / 'apps-ipod/skin/skin_render.o'), 'V=1'], text=True)
    cmd = shlex.split(next(l for l in plan.splitlines()
                          if ' -c ' in l and 'skin_render.c' in l))
    if cmd[0].endswith('ccache'):
        cmd.pop(0)
    cmd = cmd[:cmd.index('-c')]
    source, obj = tmp / 'layout.c', tmp / 'layout.o'
    source.write_text('#include <stddef.h>\n#include "settings/settings.h"\n'
        '#include "metadata.h"\n#include "metadata/albumart.h"\n'
        '#include "system/appevents.h"\n#include "ata_idle_notify.h"\n'
        '#include "font.h"\nconst unsigned device_layout[]={'
        + ','.join(fields) + '};\n')
    subprocess.run(cmd + ['-c', str(source), '-o', str(obj)], check=True)
    with obj.open('rb') as f:
        e = ELFFile(f)
        s = e.get_section_by_name('.symtab').get_symbol_by_name('device_layout')[0]
        data = e.get_section(s['st_shndx']).data()[s['st_value']:s['st_value'] + 4 * len(fields)]
        layout = dict(zip(fields, struct.unpack('<%dI' % len(fields), data)))

u = Uc(UC_ARCH_ARM, UC_MODE_ARM)
u.reg_write(UC_ARM_REG_CPSR, 0xdf)
u.mem_map(0, 0x10000)
u.mem_map(0x08000000, 0x4000000)
u.mem_map(0x10000000, 0x800000)
symbols, local = {}, {}
with a.elf.open('rb') as f:
    e = ELFFile(f)
    unit = ''
    for seg in e.iter_segments():
        if seg['p_type'] == 'PT_LOAD':
            u.mem_write(seg['p_vaddr'], seg.data())
    for s in e.get_section_by_name('.symtab').iter_symbols():
        if s['st_info']['type'] == 'STT_FILE':
            unit = s.name
        if s.name:
            symbols[s.name] = s['st_value']
            local[unit, s.name] = s['st_value']

stop, stacktop, scratch, work = 0x10050000, 0x10110000, 0x10200000, 0x10300000
minimum = stacktop
calls, visits, fds, positions = {}, {}, {}, {}
nextfd, waits, sources = 10, 0, 0
registers = [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3]

def word(at, value):
    u.mem_write(at, struct.pack('<I', value & 0xffffffff))

def cstr(at):
    return bytes(u.mem_read(at, 1024)).split(b'\0', 1)[0].decode()

def ret(value=0):
    u.reg_write(UC_ARM_REG_R0, value & 0xffffffff)
    u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

def fn(name):
    matches = [v for n, v in symbols.items() if n == name or n.startswith(name + '.')]
    assert len(matches) == 1, (name, matches)
    return matches[0]

def checksum(data):
    h = 2166136261
    for b in data:
        h = ((h ^ b) * 16777619) & 0xffffffff
    return h

header = bytearray(struct.pack('<6H4I', 0x5441, 10, 1, 1, 0, 0, 1, 0, 0, 565)
                   + b'fixture\0'.ljust(512, b'\0'))
struct.pack_into('<I', header, 16, checksum(header + b'\0\xf8'))
# Valid 24-bit 2x2 BMP, padded to four-byte rows.
bmp = struct.pack('<2sIHHI', b'BM', 70, 0, 0, 54)
bmp += struct.pack('<IiiHHIIiiII', 40, 2, 2, 1, 24, 0, 16, 0, 0, 0, 0)
bmp += bytes([0, 0, 255, 0, 255, 0, 0, 0] * 2)

adapters = ['mutex_init', 'mutex_lock', 'mutex_unlock', 'yield', 'sleep',
    'rbfs_open', 'rbfs_close', 'rbfs_read', 'rbfs_write', 'rbfs_lseek',
    'rbfs_ffilesize', 'rbfs_mkdir', 'rbfs_remove', 'rbfs_rename', 'panicf']
if a.case == 'audio-prefetch':
    adapters += ['audio_is_unrecorded', 'storage_disk_is_active',
                 'ata_disk_is_active', 'palette_dim', 'playlist_peek',
                 'playlist_is_from_artist']
elif a.case == 'background-bmp':
    adapters += ['aa_save_request', 'get_metadata', 'albumart_next_source',
        'albumart_source_revision', 'bg_task_should_stop', 'tagcache_is_busy',
        'art_cache_dir_hash', 'aa_thumbs_exist', 'aa_write_aat',
        'aa_remove_thumbs', 'aa_progress', 'playback_art_changed', 'cpu_boost']
elif a.case == 'audio-font':
    adapters += ['font_get_bits']
else:
    adapters += ['queue_wait_w_tmo', 'ata_event', 'db_dir_stamp',
                 'fdprintf', 'get_time', 'battery_level', 'current_entry',
                 'power_input_status', 'battery_voltage', 'charging_state',
                 'tagcache_get_stat', 'dircache_is_ready', 'library_files_need_upgrade']
byaddr = {symbols[n]: n for n in adapters if n in symbols}
# Some helpers are compiler clones; adapt that exact entry, not its caller.
for n in ['aa_thumbs_exist', 'aa_write_aat', 'aa_remove_thumbs', 'db_dir_stamp']:
    if n in adapters and n not in symbols:
        byaddr[fn(n)] = n

watch = ['read_bmp_fd', 'albumart_decode', 'aa_fill', 'art_cache_dir_hash',
         'art_cache_read_header', 'glyph_cache_load', 'open_locked',
         'usb_stamp_changed', 'debug_log']
watched = {v: n for n in watch for k, v in symbols.items()
           if k == n or k.startswith(n + '.')}

def hook(uc, address, size, _):
    global minimum, nextfd, waits, sources
    minimum = min(minimum, uc.reg_read(UC_ARM_REG_SP))
    if address == stop:
        uc.emu_stop()
        return
    if address in watched:
        n = watched[address]
        visits[n] = visits.get(n, 0) + 1
    n = byaddr.get(address)
    if not n:
        return
    calls[n] = calls.get(n, 0) + 1
    r0, r1, r2, r3 = [uc.reg_read(r) for r in registers]
    if n == 'panicf':
        raise RuntimeError(('panic', cstr(r0), hex(uc.reg_read(UC_ARM_REG_LR))))
    if n == 'rbfs_open':
        path = cstr(r0)
        if a.case == 'background-bmp':
            if path != '/fixture/image.bmp':
                ret(-1)
                return
            data = bmp
        elif a.case == 'audio-prefetch':
            if path.endswith('.key'):
                data = b'fixture\0' if path.endswith('/00000001.key') else b'/fixture/track.mp3\0'
            elif path.endswith('.ref'):
                owner = int(Path(path).stem, 16)
                data = struct.pack('<4I', 10, owner, 1, 0)
                data += struct.pack('<I', checksum(data))
            else:
                data = bytes(header) + b'\0\xf8'
        else:
            data = b''
        fd = nextfd
        nextfd += 1
        fds[fd], positions[fd] = data, 0
        ret(fd)
    elif n == 'rbfs_read':
        data = fds[r0][positions[r0]:positions[r0] + r2]
        if data:
            uc.mem_write(r1, data)
        positions[r0] += len(data)
        ret(len(data))
    elif n == 'rbfs_lseek':
        at = r1 if r1 < 0x80000000 else r1 - 0x100000000
        positions[r0] = at + (positions[r0] if r2 == 1 else len(fds[r0]) if r2 == 2 else 0)
        ret(positions[r0])
    elif n == 'rbfs_ffilesize':
        ret(2 * 1024 * 1024 if a.case == 'storage-log' else len(fds[r0]))
    elif n == 'rbfs_write':
        ret(r2)
    elif n == 'queue_wait_w_tmo':
        waits += 1
        if waits == 2:
            uc.emu_stop()
            return
        word(r1, layout['SYS_TIMEOUT'])
        word(r1 + 4, 0)
        ret()
    elif n in ['palette_dim', 'storage_disk_is_active', 'ata_disk_is_active',
               'aa_save_request', 'art_cache_dir_hash', 'albumart_source_revision',
               'aa_progress', 'aa_write_aat']:
        ret(1)
    elif n == 'tagcache_get_stat':
        ret(scratch + 0x5000)
    elif n == 'get_time':
        ret(scratch + 0x4000)
    elif n == 'db_dir_stamp':
        ret(2)
    elif n == 'albumart_next_source':
        sources += 1
        if sources > 1:
            ret()
            return
        out = struct.unpack('<I', bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_SP), 4)))[0]
        uc.mem_write(out, bytes(layout['sizeof(struct albumart_source)']))
        for field, value in [('kind', layout['AA_SOURCE_FILE']), ('pos', 0), ('size', len(bmp)), ('revision', 1)]:
            word(out + layout['offsetof(struct albumart_source, %s)' % field], value)
        uc.mem_write(out + layout['offsetof(struct albumart_source, path)'], b'/fixture/image.bmp\0')
        word(r3, 1)
        ret(1)
    else:
        ret()

u.hook_add(UC_HOOK_CODE, hook)

def run(root, args=(), extra=()):
    global minimum
    u.reg_write(UC_ARM_REG_SP, stacktop)
    u.reg_write(UC_ARM_REG_LR, stop)
    for r, v in zip(registers, args):
        u.reg_write(r, v & 0xffffffff)
    for i, v in enumerate(extra):
        word(stacktop + i * 4, v)
    minimum = stacktop
    try:
        u.emu_start(root, stop + 4, count=10000000)
    except Exception as exc:
        raise RuntimeError((hex(u.reg_read(UC_ARM_REG_PC)),
                            hex(u.reg_read(UC_ARM_REG_LR)), calls, visits)) from exc
    assert u.reg_read(UC_ARM_REG_PC) == stop or (a.case == 'storage-log' and waits == 2), (
        'fixture did not finish', hex(u.reg_read(UC_ARM_REG_PC)), calls, visits)

if a.case == 'audio-prefetch':
    u.mem_write(symbols['global_settings'] + layout['offsetof(struct user_settings, dynamic_colors)'], b'\x01')
    word(symbols['palette_size_idx'], 0)
    u.mem_write(scratch + layout['offsetof(struct mp3entry, path)'], b'/fixture/track.mp3\0')
    word(scratch + 0x2000 + layout['offsetof(struct track_event, id3)'], scratch)
    event = layout['PLAYBACK_EVENT_TRACK_BUFFER']
    run(symbols['add_event'], [event, symbols['prefetch_cb']])
    run(symbols['send_event'], [event, scratch + 0x2000])
    assert visits.get('art_cache_dir_hash', 0) >= 2 and visits.get('art_cache_read_header', 0) == 2, (calls, visits)
elif a.case == 'background-bmp':
    u.mem_write(scratch, b'/fixture/track.mp3\0')
    run(fn('aa_cache_dir'), [scratch, 1, scratch + 0x2000, 0],
        [0xffffffff, work, 0x100000, scratch + 0x3000])
    assert visits.get('read_bmp_fd', 0) and calls.get('aa_write_aat', 0), (calls, visits)
elif a.case == 'audio-font':
    u.mem_write(scratch, b'/fixture/font.fnt\0')
    word(scratch + 0x2000 + layout['offsetof(struct font, fd)'], 3)
    word(scratch + 0x2000 + layout['offsetof(struct font, cache._capacity)'], 256)
    run(symbols['glyph_cache_load'], [scratch, scratch + 0x2000])
    assert calls.get('rbfs_open', 0) and calls.get('font_get_bits', 0), calls
else:
    # A changed post-USB database discards pending writes and emits a held log.
    # Fill that log to force its rotation through the actual logging code.
    u.mem_write(local['tagcache.c', 'usb_stamp_taken'], b'\x01')
    word(local['tagcache.c', 'usb_stamp'], 1)
    u.mem_write(local['debug_log.c', 'ready'], b'\x01')
    u.mem_write(symbols['global_settings'] + layout['offsetof(struct user_settings, debug_log_tagcache)'], b'\x01')
    # The first member is a struct mutex; the next size_t is four-byte
    # aligned on this ABI. Force a full held buffer to exercise rotation.
    assert layout['sizeof(struct mutex)'] % 4 == 0
    word(local['debug_log.c', 'logs'] + layout['sizeof(struct mutex)'], 4090)
    event = layout['DISK_EVENT_SPINUP']
    run(symbols['add_event'], [event, symbols['command_queue_sync_callback']])
    calls.clear()
    visits.clear()
    run(symbols['storage_thread'])
    assert calls.get('rbfs_rename', 0) and visits.get('usb_stamp_changed', 0), (calls, visits)

measured = stacktop - minimum
total = measured + a.caller_reserve
result = dict(case=a.case, elf=str(a.elf), measured_body_bytes=measured,
    caller_reserve_bytes=a.caller_reserve, fixture_with_callers_bytes=total,
    budget_bytes=a.budget, within_budget=total <= a.budget,
    adapters=calls, visits=visits,
    scope='real ARM fixture; adapted FS/metadata/scheduler; caller reserve separate; not a global bound')
print(json.dumps(result))
raise SystemExit(0 if result['within_budget'] else 1)
