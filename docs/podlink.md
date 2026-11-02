# PodLink

PodLink connects a running PodBox application to a computer over USB CDC-ACM.
It uses RAM for messages. It does not use disk files or USB mass storage for
communication. The default USB mode still provides mass storage.

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
`OK RX N`. Both directions verify the complete pattern. Latency measures
protocol PING/PONG and 64-byte binary echo separately.

## Host verification

```sh
.venv-podlink/bin/python -m unittest discover -s tools/tests -p test_podlink.py -v
cc -std=c99 -Wall -Wextra -Werror -g -fsanitize=address,undefined \
  -Itools/tests/podlink_stubs -Ifirmware/export \
  tools/tests/podlink_firmware_test.c firmware/usbstack/podlink_wire.c \
  -o /tmp/podlink-firmware-test
/tmp/podlink-firmware-test
cc -std=c99 -Wall -Wextra -Werror -g -fsanitize=address,undefined \
  -Ifirmware/export tools/tests/usb_profile_test.c -o /tmp/podlink-usb-profile-test
/tmp/podlink-usb-profile-test
```

These checks use mock serial/controller state and a real local WebSocket. They
cannot establish physical USB, iPod startup, or real-device throughput.

## Profiling build

`profile-1` records the baseline queue and transfer scheduling with RAM-only
instrumentation. `tools/podlink.py stats --reset` clears performance counters;
`stats` returns counters and the last 16 lifecycle events. `profile --bytes
1048576 --samples 100` collects transfer and latency counters separately.
Instrumentation overhead is included in these measurements.

Trace events: 1 API open, 2 API close, 3 Menu button, 4 unexpected storage event,
5 USB lease acquired, 6 lease release queued, 7 release applied, 8 USB extraction,
9 peer CLOSE, 11 transport lost, 12 transport configured, 13 descriptor driver
mask. Trace is retained across message-session resets and screen re-entry.


`fast-1` uses transport-event wakeups in place of a fixed UI sleep. The screen
checks input on each bounded work turn. Bulk-IN DMA requests carry up to
4096 bytes; bulk-OUT completes one negotiated USB packet at a time so a host
write ending on a full packet never depends on a zero-length packet. The RAM
rings hold 16 KiB each, application reads/parsing use spans, and CRC32 uses a
256-entry IEEE lookup table. CPU boost applies while the CDC function is
configured. `STATS` includes CPU frequency and maximum active UI-turn time.
The host CLI waits for fd readiness instead of imposing a 1-ms sleep after
successful reads. On the tested iPod Classic 6G, 4 KiB messages delivered
1.47-1.58 MB/s to the iPod and 2.51-2.52 MB/s to the host, with PING median
0.17-0.18 ms. Same-screen replug recovery passed. These are measured results
for that device and host; source and host-only tests do not predict USB rates.
Final fast-1 Menu-to-mass-storage acceptance is pending a physical Menu press.

The USB mode lease is independent of USB connection/session lifetime. A queued
lease close carries its acquisition generation; an obsolete close cannot
release a newer screen's lease. While a lease is live, exclusive-storage
requests are refused and legacy serial settings cannot disable CDC. The normal
USB profile is restored only after the current lease closes. Event 16 records
a refused exclusive-storage request. Events 5/6/7 carry the lease generation.
