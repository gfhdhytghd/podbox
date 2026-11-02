"""Source contracts for the PodLink simulator, guide and new C files."""
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]


class SourceTests(unittest.TestCase):
    def test_simulator_keeps_podlink_over_its_pty_shim(self):
        # This tree keeps PodLink in the simulator, so System > Agents can
        # be tested there; apps-ipod/sim/shim-podlink.c stands in for USB.
        source = '#include "config/ipod6g.h"\n#include "config/sim.h"\n'
        result = subprocess.run(["cc", "-E", "-dM", "-x", "c", "-",
            "-I" + str(ROOT / "firmware/export")], input=source, text=True,
            capture_output=True, check=True)
        self.assertIn("#define HAVE_USB_PODLINK", result.stdout)
        shim = (ROOT / "apps-ipod/sim/shim-podlink.c").read_text()
        header = (ROOT / "firmware/usbstack/usb_serial.h").read_text()
        for name in re.findall(r"\b(usb_serial_\w+)\(", header):
            if name in ("usb_serial_init", "usb_serial_send"):
                continue
            self.assertRegex(shim, r"\b%s\(" % name, name)

    def test_guide_describes_this_pr(self):
        guide = (ROOT / "docs/podlink.md").read_text()
        for term in ("Agents", "herdremote", "spaces view", "MENU/LEFT",
                     "System > Battery", "pending a physical Menu press"):
            self.assertFalse(term in guide, f"out-of-scope guide text: {term}")
        self.assertIn("Settings > Battery & Power", guide)
        self.assertIn("System > PodLink", guide)
        self.assertIn("Press Menu", guide)

    def test_new_c_files_follow_repository_style(self):
        # These files are new in the PR, so no pre-existing style is rewritten.
        files = ["firmware/usbstack/podlink.c", "firmware/usbstack/podlink_wire.c",
                 "firmware/export/podlink.h", "firmware/export/podlink_stats.h",
                 "apps-ipod/screens/system/podlink_screen.c",
                 "apps-ipod/screens/system/podlink_screen.h"]
        files += [str(p.relative_to(ROOT)) for p in
                  (ROOT / "tools/tests").glob("*.c")
                  if p.name.startswith(("podlink_", "usb_profile_", "usb_charging_"))]
        for directory in ("podlink_stubs", "podlink_screen_stubs", "charging_stubs"):
            files += [str(p.relative_to(ROOT)) for p in
                      (ROOT / "tools/tests" / directory).rglob("*.h")]
        for name in files:
            source = (ROOT / name).read_text()
            for n, line in enumerate(source.splitlines(), 1):
                self.assertLessEqual(len(line), 80, f"{name}:{n}")
                self.assertNotIn("\t", line, f"{name}:{n}")
                self.assertIsNone(re.search(r"\)\s*\{.+[;}]", line), f"{name}:{n}")


if __name__ == "__main__":
    unittest.main()
