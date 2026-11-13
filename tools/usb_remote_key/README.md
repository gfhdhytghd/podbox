# Apple Remote Menu over USB

This Linux bridge reads `EDGE <microseconds> <level>` from the IR receiver
with serial `2543030725032a2a`. It forwards only Apple (`ee 87`) Menu
commands `02` and `03`; the fourth byte may contain any remote pairing ID.
Other keys continue through the dock. It never writes to or flashes the IR
receiver. Stop any other reader of that receiver before starting this bridge.

Install on the computer connected to the dock:

```sh
python3 -m venv ~/.local/share/podbox-usbkey/venv
~/.local/share/podbox-usbkey/venv/bin/pip install -r tools/usb_remote_key/requirements.txt
sudo cp tools/usb_remote_key/70-podbox-usbkey.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
mkdir -p ~/.config/systemd/user
cp tools/usb_remote_key/podbox-usbkey.service ~/.config/systemd/user/
# Edit ExecStart if this checkout is at another path.
systemctl --user daemon-reload
systemctl --user enable --now podbox-usbkey
journalctl --user -u podbox-usbkey -f
```

Install the host's libusb runtime if PyUSB reports no backend. Replug the
iPod and receiver after installing the rule. `uaccess` grants access to the
active local login; this is not a headless-machine rule. This service is
independent of HerdRemote and does not change it.

The bridge automatically finds the receiver's `/dev/serial/by-id/` entry.
It accepts only VID `05ac`, PID `1261`, and serial matching
`?AA0000000000360`: exactly one leading character, then the fixed suffix.
This includes charge-only `4AA0000000000360` and PodLink `2AA0000000000360`.
The udev rule uses the same single-character wildcard. It retries
after absence, USB reset, unplug or disk mode. It never selects a USB
configuration, claims an interface, detaches a driver, or sends a key saved
while disconnected.
Disk mode and firmware without this request receive no working Menu input.

Menu is classified on the computer. A stream continuing at least 300 ms
(`MENU_HOLD_SECONDS`) after its first frame, with at least three repeats,
sends one Select press/release. Elapsed time without another frame cannot
classify a hold. Otherwise 150 ms without another Menu frame/repeat sends
one Menu press/release. Another complete key ends the current Menu stream;
its repeats cannot extend Menu. Further repeats after Select send nothing.

The third repeat normally starts about 324 ms after the first frame. The
300 ms time gate leaves margin for jitter; both the repeat count and time
gate must pass. Hold age starts at the first edge of the initial frame,
using its decoded microsecond span. The 150 ms silence timer still starts
at the end of the latest event. This keeps the third repeat eligible even
though a full frame takes about 68 ms to decode and a repeat only 12 ms.
Fast taps and collapsed doubles with only one or two repeats remain Menu.
A stopped stream cannot become Select just because time passes.

`tests/fixtures/ir-gestures-raw.log` contains host-timed EDGE lines. Tests
feed these through the production decoder, separating the initial full
frame and repeat even when `ir-gestures.log` reports them together as 72
edges. The fixtures cover Menu taps, collapsed doubles and five one-second
Menu holds. The bridge continues to ignore Play and other dock commands.
Deploy this bridge with firmware accepting key ID 2; older firmware stalls
Select. The firmware keeps a short EP0 pulse down for at least 50 ms so the
button sampler cannot lose its back-to-back down/up requests. Its one-second
safety release still covers a missing up request.

## EP0 protocol

The firmware protocol is independent of interfaces and USB configurations:

| Setup field | Value |
| --- | --- |
| bmRequestType | `0x40` (vendor, device, OUT) |
| bRequest | `0x5b` |
| wValue | `1` (Menu), `2` (Select) |
| wIndex | `1` (down/keepalive) or `0` (up) |
| wLength | `0` |

Only configured normal iPod Classic firmware accepts the request. All other
key IDs, request directions, recipients, states and payload lengths STALL.
Bootloader and simulator omit the feature. No descriptor changes are needed.
The native button sampler merges the state with the dock button state;
its existing queue emits `BUTTON_RC_MENU` or `BUTTON_RC_SELECT` and their
release. Firmware does not classify a Menu hold into Select.

Run offline tests without opening either device:

```sh
python3 -m unittest discover -s tools/usb_remote_key/tests -v
python3 tools/tests/usb_remote_key_test.py build-usbkey/probe
```
