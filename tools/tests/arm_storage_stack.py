#!/usr/bin/env python3
"""Replay storage-idle palette dispatch in real ARM code, with bounded adapters.

No boot, scheduler, MMIO or device access. The real storage loop dispatches
its idle event with a deterministic scheduler adapter. Track and filesystem
access fail this fixture. Reports instruction SP high-water for this dispatch,
not a universal firmware bound.
Requires pyelftools and Unicorn; compiler flags/layout come from --build.
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
                               UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_CPSR)

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--build', type=Path, required=True)
p.add_argument('--elf', type=Path, required=True)
p.add_argument('--budget', type=int, default=1536,
               help='75%% of the 2048-byte ATA stack by default')
a = p.parse_args()
build = a.build.resolve()
root = Path(next(l.split('=', 1)[1] for l in (build / 'Makefile').read_text().splitlines()
                 if l.startswith('export ROOTDIR=')))
fields = ['offsetof(struct user_settings, dynamic_colors)',
          'offsetof(struct mp3entry, path)', 'DISK_EVENT_SPINUP', 'SYS_TIMEOUT',
          ]
with tempfile.TemporaryDirectory(prefix='arm-storage-layout-') as tmp:
    tmp = Path(tmp)
    plan = subprocess.check_output(['make', '-C', build, '-Bn',
        build / 'apps-ipod/skin/skin_render.o', 'V=1'], text=True)
    cmd = shlex.split(next(l for l in plan.splitlines() if ' -c ' in l and 'skin_render.c' in l))
    if cmd[0].endswith('ccache'):
        cmd.pop(0)
    cmd = cmd[:cmd.index('-c')]
    source, obj = tmp / 'layout.c', tmp / 'layout.o'
    source.write_text('#include <stddef.h>\n#include "settings/settings.h"\n'
        '#include "metadata.h"\n#include "ata_idle_notify.h"\n'
        '#include "metadata/art_cache.h"\n'
        'const unsigned device_layout[]={' + ','.join(fields) + '};\n')
    subprocess.run(cmd + ['-c', str(source), '-o', str(obj)], check=True)
    with obj.open('rb') as f:
        e = ELFFile(f)
        s = e.get_section_by_name('.symtab').get_symbol_by_name('device_layout')[0]
        data = e.get_section(s['st_shndx']).data()[s['st_value']:s['st_value'] + 4 * len(fields)]
        layout = dict(zip(fields, struct.unpack('<%dI' % len(fields), data)))

u = Uc(UC_ARCH_ARM, UC_MODE_ARM)
u.reg_write(UC_ARM_REG_CPSR, 0xdf)  # target threads execute in system mode
u.mem_map(0, 0x10000)
u.mem_map(0x08000000, 0x4000000)
u.mem_map(0x10000000, 0x200000)
symbols = {}
with a.elf.open('rb') as f:
    e = ELFFile(f)
    for seg in e.iter_segments():
        if seg['p_type'] == 'PT_LOAD':
            u.mem_write(seg['p_vaddr'], seg.data())
    for s in e.get_section_by_name('.symtab').iter_symbols():
        if s.name:
            symbols[s.name] = s['st_value']

def word(at, value):
    u.mem_write(at, struct.pack('<I', value & 0xffffffff))

stop, stacktop = 0x10050000, 0x10110000
minimum = stacktop
calls = {}
waits = 0
peeks = 0
posted = 0

def ret(value=0):
    u.reg_write(UC_ARM_REG_R0, value & 0xffffffff)
    u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

def cstr(at):
    return bytes(u.mem_read(at, 260)).split(b'\0', 1)[0].decode()

adapters = {n: symbols[n] for n in [
    'queue_wait_w_tmo', 'ata_event', 'audio_peek_buffered_track', 'palette_dim',
    'audio_is_unrecorded', 'storage_disk_is_active', 'ata_disk_is_active', 'palette_candidates',
    'track_folder_hash', 'art_cache_dir_hash', 'rbfs_open', 'rbfs_read', 'rbfs_lseek', 'rbfs_close',
    'panicf', 'rbfs_ffilesize', 'mutex_lock', 'mutex_unlock', 'usb_host_wants_storage'
] if n in symbols}
byaddr = {v: k for k, v in adapters.items()}

def hook(uc, address, size, _):
    global minimum, waits, peeks, posted
    minimum = min(minimum, uc.reg_read(UC_ARM_REG_SP))
    if address == stop:
        uc.emu_stop()
        return
    if address == symbols.get('bg_task_post'):
        posted += 1
    n = byaddr.get(address)
    if not n:
        return
    calls[n] = calls.get(n, 0) + 1
    r0, r1, r2 = [uc.reg_read(r) for r in [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2]]
    if n == 'panicf':
        raise RuntimeError(('unexpected panic', cstr(r0), hex(uc.reg_read(UC_ARM_REG_LR))))
    if n == 'queue_wait_w_tmo':
        waits += 1
        if waits == 2:
            uc.emu_stop()
            return
        word(r1, layout['SYS_TIMEOUT'])
        word(r1 + 4, 0)
        ret()
    elif n == 'ata_event':
        # TICK leaves trig=0, selecting idle notification and sleep.
        ret()
    else:
        raise RuntimeError(('storage callback entered forbidden work', n))

u.hook_add(UC_HOOK_CODE, hook)
u.mem_write(symbols['global_settings'] + layout['offsetof(struct user_settings, dynamic_colors)'], b'\x01')
word(symbols['palette_size_idx'], 0)
# The zero-initialized settings select ART_SOURCE_FILES, matching the fixture.
# Register the actual callback in the actual firmware event table.
u.reg_write(UC_ARM_REG_SP, stacktop)
u.reg_write(UC_ARM_REG_LR, stop)
u.reg_write(UC_ARM_REG_R0, layout['DISK_EVENT_SPINUP'])
u.reg_write(UC_ARM_REG_R1, symbols['prefetch_idle_cb'])
u.emu_start(symbols['add_event'], stop + 4, count=100000)
assert u.reg_read(UC_ARM_REG_SP) == stacktop
minimum = stacktop
u.reg_write(UC_ARM_REG_LR, stop)
u.emu_start(symbols['storage_thread'], stop + 4, count=1000000)
assert waits == 2, (calls, hex(u.reg_read(UC_ARM_REG_PC)))
assert posted == 1 and peeks == 0, (posted, calls)
used = stacktop - minimum
print(json.dumps(dict(elf=str(a.elf), stack_bytes=used, root='storage_thread',
    budget_bytes=a.budget, within_budget=used <= a.budget,
    dispatch='deferred', adapters=calls,
    scope='real ARM storage dispatch; deterministic scheduler; no track or filesystem access; not a global bound')))
raise SystemExit(0 if used <= a.budget else 1)
