"""FU23 has no custom Cider viewport; Scrim and native drawing stay shared."""
from pathlib import Path
import subprocess,sys
r=Path(__file__).resolve().parents[2]
assert not (r/'apps-ipod/screens/playback/cider_screen.c').exists()
wps=(r/'apps-ipod/screens/playback/wps.c').read_text()
assert 'cider_wps_action(button)' in wps and 'gwps_enter_wps(theme_enabled)' in wps
assert 'skin_update(WPS,' in wps
# FU28 splits only the 24px transport cell from the native Scrim icon line.
# The adjacent icons retain the original x coordinates and font.
theme=(r/'themes/scrim/.rockbox/wps/scrim.wps').read_text()
assert '%Vl(Chrome,2,2,24,24,4)' in theme
assert '%Vl(Chrome,26,2,52,24,4)' in theme
assert '%?pZ<%pZ|%?if(' in theme
assert '%?ps<H>%?mm<|J|J|J|J>' in theme
for file in ('cider_browser.c','cider_wps.c'):
 s=(r/'apps-ipod/screens/playback'/file).read_text()
 assert 'viewport_set_fullscreen' not in s
print('Unified WPS/native viewport ownership; shared native WPS; isolated transport cell PASS')
