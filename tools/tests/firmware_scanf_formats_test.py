"""FU24: the firmware's own sscanf (firmware/libc/sscanf.c) knows only
%d %x %s %n %ld %lx (with * and a width). Any other conversion is taken as a
literal, its argument is never consumed, and every later argument shifts by
one -- the FU23-fix "%lu\\t%79s" wrote a track id over a 4-byte stack slot and
crashed the PodLink thread. Refuse such formats anywhere in firmware code."""
from pathlib import Path
import re, sys
R = Path(__file__).resolve().parents[2]
OK = re.compile(r'%\*?\d*(?:l?[dx]|[sn])')
bad = []
for root in ('apps-ipod', 'firmware'):
    for path in (R / root).rglob('*.c'):
        if path.match('firmware/libc/sscanf.c'):
            continue
        text = path.read_text(errors='replace')
        for m in re.finditer(r'\bsscanf\s*\([^;]*?"((?:[^"\\]|\\.)*)"', text, re.S):
            fmt = m.group(1)
            for c in re.finditer(r'%%|%[^%]*?[a-zA-Z\[]', fmt):
                if c.group() == '%%' or OK.fullmatch(c.group()):
                    continue
                line = text.count('\n', 0, m.start()) + 1
                bad.append(f'{path.relative_to(R)}:{line}: {c.group()!r} in "{fmt}"')
if bad:
    print('\n'.join(bad)); sys.exit(1)
print('firmware sscanf formats use only supported conversions: PASS')
