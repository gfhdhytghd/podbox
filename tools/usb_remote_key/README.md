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
It accepts only VID `05ac`, PID `1261`, and serial
`1AA0000000000360` or `9AA0000000000360`. It retries after absence, USB
reset, unplug or disk mode. It never selects a USB configuration, claims an
interface, detaches a driver, or sends a key saved while disconnected.
Disk mode and firmware without this request receive no working Menu input.

A full Menu frame presses Menu. NEC repeats and repeated full Menu frames
keep it held. After 150 ms without a valid Menu frame or repeat, the bridge
releases Menu. A different complete frame immediately releases Menu.
On shutdown or receiver disconnect, the bridge attempts a release.
The firmware releases after 1 second without a down/up request, even if the
bridge crashes. Key debounce adds at most two firmware ticks to transitions.

## EP0 protocol

The firmware protocol is independent of interfaces and USB configurations:

| Setup field | Value |
| --- | --- |
| bmRequestType | `0x40` (vendor, device, OUT) |
| bRequest | `0x5b` |
| wValue | `1` (Menu) |
| wIndex | `1` (down/keepalive) or `0` (up) |
| wLength | `0` |

Only configured normal iPod Classic firmware accepts the request. All other
key IDs, request directions, recipients, states and payload lengths STALL.
Bootloader and simulator omit the feature. No descriptor changes are needed.
The native button sampler merges the state with the dock button state;
its existing queue emits `BUTTON_RC_MENU`, repeats, and `BUTTON_REL`.
`button_status()` includes the held key for the remote hold rules.

Run offline tests without opening either device:

```sh
python3 -m unittest discover -s tools/usb_remote_key/tests -v
python3 tools/tests/usb_remote_key_test.py build-usbkey/probe
```
