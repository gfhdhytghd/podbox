#!/usr/bin/env python3
"""Execute real ARM skin_render_line instructions on a synthetic nested skin.

Requires unicorn and pyelftools. No firmware boot, MMIO, USB or device access.
The conditional evaluator is a deterministic adapter. All renderer instructions
and their stack accesses come from the specified ELF. This is a recursion
stress test, not a reconstruction of the user's missing panic first line.
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
from unicorn.arm_const import (UC_ARM_REG_PC, UC_ARM_REG_SP, UC_ARM_REG_LR,
                               UC_ARM_REG_R0, UC_ARM_REG_R1)

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--build', type=Path, required=True,
               help='configured ARM build for ABI layout and compiler flags')
p.add_argument('--elf', type=Path, required=True)
p.add_argument('--depth', type=int, default=52)
p.add_argument('--budget', type=int, default=8192)
a = p.parse_args()
build = a.build.resolve()
root = Path(next(l.split('=', 1)[1].strip() for l in
                 (build / 'Makefile').read_text().splitlines()
                 if l.startswith('export ROOTDIR=')))
fields = ['sizeof(struct skin_element)',
          *['offsetof(struct skin_element, %s)' % n for n in
            ['next', 'children', 'data', 'children_count', 'type']],
          'sizeof(struct skin_draw_info)',
          *['offsetof(struct skin_draw_info, %s)' % n for n in
            ['gwps', 'skin_vp', 'refresh_type', 'force_redraw']],
          'offsetof(struct skin_viewport, output_to_backdrop_buffer)',
          'LINE', 'CONDITIONAL', 'COMMENT']
with tempfile.TemporaryDirectory(prefix='arm-skin-layout-') as d:
    d = Path(d)
    plan = subprocess.check_output(['make', '-C', build, '-Bn',
        build / 'apps-ipod/skin/skin_render.o', 'V=1'], text=True)
    line = next(l for l in plan.splitlines() if ' -c ' in l and
                'skin_render.c' in l)
    cmd = shlex.split(line)
    if cmd[0].endswith('ccache'):
        cmd = cmd[1:]
    cmd = cmd[:cmd.index('-c')]
    source, obj = d / 'layout.c', d / 'layout.o'
    source.write_text('#include <stddef.h>\n#include ' +
        json.dumps(str(root / 'apps-ipod/skin/skin_render.c')) +
        '\nconst unsigned device_layout[]={' + ','.join(fields) + '};\n')
    subprocess.run(cmd + ['-c', source, '-o', obj], check=True)
    with obj.open('rb') as f:
        e = ELFFile(f)
        sym = e.get_section_by_name('.symtab').get_symbol_by_name('device_layout')[0]
        section = e.get_section(sym['st_shndx'])
        data = section.data()[sym['st_value']:sym['st_value'] + 4 * len(fields)]
        layout = dict(zip(fields, struct.unpack('<%dI' % len(fields), data)))

u = Uc(UC_ARCH_ARM, UC_MODE_ARM)
u.mem_map(0, 0x10000)
u.mem_map(0x08000000, 0x4000000)
u.mem_map(0x10000000, 0x200000)
symbols = {}
with a.elf.open('rb') as f:
    e = ELFFile(f)
    for seg in e.iter_segments():
        if seg['p_type'] == 'PT_LOAD':
            u.mem_write(seg['p_vaddr'], seg.data())
    filename = None
    for sym in e.get_section_by_name('.symtab').iter_symbols():
        if sym['st_info']['type'] == 'STT_FILE':
            filename = sym.name
        if sym.name == 'skin_buffer' and filename == 'skin_render.c':
            symbols['skin_buffer'] = sym['st_value']
        if sym.name in ['skin_render_line', 'evaluate_conditional']:
            symbols[sym.name] = sym['st_value']
assert len(symbols) == 3, symbols
base, info, vp, stop = 0x10000000, 0x10040000, 0x10041000, 0x10050000
stacktop = 0x10110000

def word(at, value):
    u.mem_write(at, struct.pack('<I', value & 0xffffffff))

def element(off, kind, children=-1, data=-1, count=0):
    at = base + off
    for field, value in [('next', -1), ('children', children),
                         ('data', data), ('type', kind)]:
        word(at + layout['offsetof(struct skin_element, %s)' % field], value)
    u.mem_write(at + layout['offsetof(struct skin_element, children_count)'],
                struct.pack('<H', count))

# One LINE per level, each containing a CONDITIONAL selecting the next LINE.
# A COMMENT terminates it; no drawing or string adapters are required.
for i in range(a.depth + 1):
    off = 0x100 + i * 0x100
    element(off, layout['LINE'], off + 0x80, count=1)
    word(base + off + 0x80, off + 0x20)
    if i == a.depth:
        element(off + 0x20, layout['COMMENT'])
    else:
        element(off + 0x20, layout['CONDITIONAL'], off + 0x90,
                off + 0xa0, 2)
        word(base + off + 0x90, off + 0x100)
        word(base + off + 0x94, off + 0x100)
        word(base + off + 0xa0, 0)  # last_value, unchanged
word(symbols['skin_buffer'], base)
word(info + layout['offsetof(struct skin_draw_info, skin_vp)'], vp)
word(info + layout['offsetof(struct skin_draw_info, refresh_type)'], 0)
minimum, calls = stacktop, 0

def hook(uc, address, size, user):
    global minimum, calls
    minimum = min(minimum, uc.reg_read(UC_ARM_REG_SP))
    if address == symbols['evaluate_conditional']:
        calls += 1
        uc.reg_write(UC_ARM_REG_R0, 0)
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
    elif address == stop:
        uc.emu_stop()

u.hook_add(UC_HOOK_CODE, hook)
u.reg_write(UC_ARM_REG_SP, stacktop)
u.reg_write(UC_ARM_REG_LR, stop)
u.reg_write(UC_ARM_REG_R0, base + 0x100)
u.reg_write(UC_ARM_REG_R1, info)
u.emu_start(symbols['skin_render_line'], stop + 4, count=1000000)
assert calls == a.depth and u.reg_read(UC_ARM_REG_SP) == stacktop
used = stacktop - minimum
print(json.dumps({'elf': str(a.elf), 'depth': a.depth, 'conditional_calls': calls,
                  'stack_bytes': used, 'budget_bytes': a.budget,
                  'within_budget': used <= a.budget}))
raise SystemExit(0 if used <= a.budget else 1)
