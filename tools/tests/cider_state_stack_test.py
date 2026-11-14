#!/usr/bin/env python3
"""struct cider_state is ~930 bytes; a copy on the 8 KiB UI stack (skin,
status, WPS) overflowed it into __thread_entries (FU24). Only statics may
hold one; callers use struct cider_status."""
import re, sys
from pathlib import Path
root = Path(__file__).resolve().parents[2]
bad = []
for path in sorted((root / 'apps-ipod').rglob('*.c')):
    for n, line in enumerate(path.read_text(errors='replace').splitlines(), 1):
        if re.search(r'^\s+struct cider_state\s+\w+\s*;', line) and 'static' not in line:
            bad.append(f'{path.relative_to(root)}:{n}: {line.strip()}')
print('\n'.join(bad) if bad else 'no automatic struct cider_state copies: PASS')
sys.exit(1 if bad else 0)
