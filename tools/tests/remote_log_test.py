"""Build real remote producers/ring/formatter with offline clock/IRQ stubs."""
from pathlib import Path
import subprocess
import sys

R = Path(__file__).resolve().parents[2]
O = Path(sys.argv[1]).resolve()
O.mkdir(parents=True, exist_ok=True)
(O / 'config.h').write_text('''#ifndef TEST_REMOTE_CONFIG
#define TEST_REMOTE_CONFIG
#define __CONFIG_H__
#define HAVE_USB_PODLINK
#define IPOD_ACCESSORY_PROTOCOL
#define IPOD_6G
#define TIME_BEFORE(a,b) ((long)((unsigned long)(a)-(unsigned long)(b))<0)
#endif
''')
(O / 'button.h').write_text('''#include "firmware/target/arm/ipod/button-target.h"
#define BUTTON_NONE 0
#define BUTTON_REL 0x02000000
#define BUTTON_REPEAT 0x04000000
#define BUTTON_REDRAW 0x20000000
''')
cmd = ['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-g',
       '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
       '-fno-pie', '-no-pie', '-include', str(O/'config.h'),
       '-I'+str(O), '-I'+str(R),
       '-I'+str(R/'tools/tests/podlink_stubs'),
       '-I'+str(R/'firmware/export'), '-I'+str(R/'apps-ipod'),
       str(R/'tools/tests/remote_log_test.c'),
       str(R/'firmware/usbstack/remote_log.c'),
       str(R/'apps-ipod/iap/remote_capture.c'),
       str(R/'apps-ipod/input/agents_remote.c'),
       str(R/'firmware/drivers/button_usb_remote.c'),
       '-o', str(O/'remote-log')]
subprocess.run(cmd, check=True)
subprocess.run([str(O/'remote-log')], check=True)
