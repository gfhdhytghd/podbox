"""FU23 has no custom Cider viewport; Scrim and native drawing stay shared."""
from pathlib import Path
import subprocess,sys
r=Path(__file__).resolve().parents[2]
assert not (r/'apps-ipod/screens/playback/cider_screen.c').exists()
wps=(r/'apps-ipod/screens/playback/wps.c').read_text()
assert 'cider_wps_action(button)' in wps and 'gwps_enter_wps(theme_enabled)' in wps
assert 'skin_update(WPS,' in wps
for path in ['themes/scrim']:
 changed=subprocess.check_output(['git','diff','452968a328','--',path],cwd=r) if (r/'.git').exists() else b''
 assert not changed
for file in ('cider_browser.c','cider_wps.c'):
 s=(r/'apps-ipod/screens/playback'/file).read_text()
 assert 'viewport_set_fullscreen' not in s
print('Unified WPS/native viewport ownership; no Cider pixel renderer; unchanged Scrim PASS')
