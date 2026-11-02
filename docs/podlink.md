# PodLink

PodLink connects a running PodBox application to a computer over USB CDC-ACM.
It uses RAM for messages. It does not use disk files or USB mass storage for
communication. The default USB mode still provides mass storage. The simulator has no PodLink
USB transport or PodLink menu entry.

This PodBox revision has no loadable plugin system. The demo is a native screen
at **System > PodLink**. The reusable message API is `firmware/export/podlink.h`;
its protocol version is 1. No `.rock` file or plugin ABI is part of this build.

## Connect

1. Unplug the USB cable.
2. Open **System > PodLink** on the iPod.
3. Connect USB. The screen shows `USB up / waiting OPEN` until a host connects.
4. Run the host client:

```sh
python3 -m venv .venv-podlink
.venv-podlink/bin/pip install pyserial websockets
.venv-podlink/bin/python tools/podlink.py ping
.venv-podlink/bin/python tools/podlink.py info
.venv-podlink/bin/python tools/podlink.py echo 'hello'
.venv-podlink/bin/python tools/podlink.py latency --samples 100
.venv-podlink/bin/python tools/podlink.py bench --bytes 1048576
```

The client finds a tty with VID `05ac` and PID `1261`. To select a port, pass
`--port /dev/serial/by-id/<identified-PodBox-device>` before the subcommand.
Check its USB manufacturer and serial before selecting it; ttyACM numbering
alone does not identify a device. The OS user needs access to that tty. Each
command opens and cleanly closes its own message session.

Press Menu on the screen to close and exit. USB then re-enumerates using the
normal USB setting. With the default setting, the disk becomes available.
Unplugging while the screen is open reports a close event. Reconnect USB and
run the host again; restarting the screen is unnecessary.

## WebSocket

```sh
.venv-podlink/bin/python tools/podlink.py serve --listen-port 8765
```

Connect one client to `ws://127.0.0.1:8765/`. Text and binary messages retain
boundaries and content; each becomes one PodLink frame. The demo echoes them.
The text `INFO` returns a JSON description. The server binds only to localhost.
A second simultaneous client receives close code 1013. The server closes the
WebSocket when the USB session ends. After replug, open a new WebSocket; the
server discovers the tty again. It does not replay messages from a lost session.

`free_ram_bytes` is the firmware core allocator's currently available bytes;
`free_ram_scope` is `core_allocator`. It is not the device's physical RAM total.

## Message API

Call `podlink_open()` with USB unplugged, then call `podlink_poll(&message)`
regularly. A poll never waits for USB data. A positive result contains one whole
message; its data pointer remains valid until the next poll. `podlink_send`
copies an entire payload or returns `PODLINK_BUSY` without accepting it. Retry
busy sends after polling. At most two application frames can be queued; a
third slot is reserved for control traffic. Messages are at most 4096 bytes.

`podlink_state()` distinguishes waiting for USB, handshaking, open and closing.
Call `podlink_close()` and continue polling until closed. It sends CLOSE and
releases USB mode after an acknowledgement or one second. A remote CLOSE ends
only the message session; the screen waits for another OPEN. USB unplug produces
one CLOSE event with code 1006. The screen retains link mode until it exits.

## Wire v1 (internal reference)

All integers are little-endian. CRC32 uses the IEEE polynomial and standard
initial/final inversion (compatible with Python `zlib.crc32`).

| Offset | Bytes | Value |
| --- | --- | --- |
| 0 | 4 | ASCII `PLNK` |
| 4 | 1 | Type |
| 5 | 1 | Flags, zero |
| 6 | 2 | Reserved, zero |
| 8 | 4 | Sequence |
| 12 | 4 | Payload length, 0..4096 |
| 16 | length | Payload |
| 16 + length | 4 | CRC32 of bytes 4 through the end of the payload |

Types are OPEN=1, TEXT=2, BINARY=3, PING=4, PONG=5, CLOSE=6, HELLO=7.
HELLO and OPEN contain version `u16` followed by maximum message size `u32`.
The device sends HELLO when USB configures. The host sends OPEN; the device
acknowledges OPEN with its sequence and negotiated limit. PONG retains the
PING sequence and payload. Both ends send an idle keepalive after five seconds
and time out an unanswered keepalive after fifteen seconds. CLOSE starts with
a `u16` code, followed by a UTF-8 reason. Bad CRC or magic triggers scanning for
the next valid frame. An oversized length triggers CLOSE 1009 without allocating
memory for that length. The two sides reset their byte queues on USB reconnect.

Demo throughput commands use TEXT `BENCH TX N` / `BENCH RX N`. After TEXT
`READY TX N` / `READY RX N`, exactly N bytes travel in BINARY messages of up to
4096 bytes. Byte at offset i is `i % 251`. Final TEXT is `OK TX N errors=0` or
`OK RX N`. A valid BENCH command replaces any active transfer. A rejected size
preserves the active transfer. Both directions verify the complete pattern.
Latency measures protocol PING/PONG and 64-byte binary echo separately.

## Host verification

```sh
.venv-podlink/bin/python -m unittest discover -s tools/tests -p 'test_podlink*.py' -v
for test in podlink_firmware podlink_reset podlink_wire; do
  cc -std=gnu11 -Wall -Wextra -Werror -g -fsanitize=address,undefined \
    -Itools/tests/podlink_stubs -Ifirmware/export \
    tools/tests/${test}_test.c firmware/usbstack/podlink_wire.c \
    -o /tmp/${test}_test
  /tmp/${test}_test
done
cc -std=gnu11 -Wall -Wextra -Werror -g -fsanitize=address,undefined \
  -ffunction-sections -fdata-sections -Wl,--gc-sections \
  -Itools/tests/podlink_screen_stubs -Itools/tests/podlink_stubs \
  -Ifirmware/export tools/tests/podlink_screen_test.c -o /tmp/podlink-screen-test
/tmp/podlink-screen-test
cc -std=c99 -Wall -Wextra -Werror -g -fsanitize=address,undefined \
  -Ifirmware/export tools/tests/usb_profile_test.c -o /tmp/podlink-usb-profile-test
/tmp/podlink-usb-profile-test
```

These checks use mock serial/controller state and a real local WebSocket. They
cannot establish physical USB, iPod startup, or real-device throughput.

## Charging in PodLink

PodLink and normal storage use the same saved **Charge During USB Connection**
setting (**Settings > Battery & Power**). With `on` or `force`, both request 500 mA
(`bMaxPower` byte 250, in 2 mA units). With `off`, both request 100 mA
(`bMaxPower` byte 50) and do not enable battery charging from an enumerated
computer. Select the setting before reconnecting: a host's existing 100 mA
grant is not raised merely because the setting changed.

Before configuration, the device limits USB input to 100 mA. A configured
500 mA source enables the iPod6g charger and reports a charging source to the
battery status bar, without waiting for a storage-mode handover. Suspend
disables USB input and battery charging; resume restores the existing grant.
Unplug clears the grant. A bus reset requires configuration again. The
LTC4066 still controls battery charge termination and the battery's charge
current (the 500 mA limit includes device consumption).

Run the production USB core and iPod6g power code against RAM-backed hardware:

```sh
cc -std=gnu11 -Wall -Wextra -Werror -g -fsanitize=address,undefined \
  -ffunction-sections -fdata-sections -Wl,--gc-sections \
  -Itools/tests/charging_stubs -Ifirmware/export -Ifirmware/usbstack \
  tools/tests/usb_charging_test.c -o /tmp/podlink-usb-charging-test
/tmp/podlink-usb-charging-test
```

Physical acceptance remains separate. With a partially discharged battery,
select `on`, unplug, enter **System > PodLink**, and reconnect.
Read the host descriptor using `lsusb -v -d 05ac:1261`: expect `bMaxPower 500mA`,
one configuration, and CDC interfaces in link mode. Find the matching USB
device directory under `/sys/bus/usb/devices` by its `idVendor=05ac` and
`idProduct=1261`; read its `bMaxPower` and `bConfigurationValue` (expect `500mA`
and `1`). Record the directory and serial. Do not select a tty by number.

Confirm the battery animation while the link is active. For a separate
diagnostic run, enable the Debug menu and open System > Debug > Battery;
record `USB current limit` and the hardware charging state. Opening that
screen requires leaving PodLink, so do not label it a simultaneous observation
of PodLink charging. A full battery may correctly stop charging.
Repeat after the screen/backlight has been off for at least 15 seconds.
Check unplug/replug without leaving PodLink. Press Menu to return to storage.
Confirm storage enumeration and charging.
Repeat with `off` (100 mA, no host battery charging) and `force` (500 mA after
configuration). For each setting, unplug/replug before reading descriptors.

Run a separate host suspend/resume check.
During suspend, confirm charger input/charge stops with a USB power meter;
after resume, confirm the prior grant, battery animation and link recovery.
Keep the normal storage volume unmounted for its suspend comparison.
Do not infer physical charge current or link throughput from host-only tests.

## RAM diagnostics

`tools/podlink.py stats --reset` clears performance counters. `stats` returns
counters and the last 16 lifecycle events. `profile --bytes 1048576 --samples
100` collects transfer and latency counters separately. Instrumentation overhead
is included in these measurements. Host-only tests do not predict USB rates.

Trace events: 1 API open, 2 API close, 3 Menu button, 4 unexpected storage event,
5 USB lease acquired, 6 lease release queued, 7 release applied, 8 USB extraction,
9 peer CLOSE, 11 transport lost, 12 transport configured, 13 descriptor driver
mask. Event 16 records a refused exclusive-storage request. Events 5/6/7 carry
the lease generation. Trace is retained across message-session resets and
screen re-entry.

The USB mode lease lasts until the screen exits. Cable removal or USB
reconfiguration resets the byte queues and message handshake. A queued close
from an older lease cannot release a newer screen's lease. Normal USB profiles
return after the current lease closes.
