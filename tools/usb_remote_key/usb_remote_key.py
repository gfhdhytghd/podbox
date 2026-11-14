#!/usr/bin/env python3
"""Forward Apple IR Menu and volume to EP0. IR serial is read-only."""
import argparse
import glob
import json
import logging
import os
import re
import select
import signal
import socket
import termios
import time

LOG = logging.getLogger('podbox-usbkey')
REQUEST = 0x5B
MENU = 1
SELECT = 2
VOL_UP = 3
VOL_DOWN = 4
HEARTBEAT = 5
HEARTBEAT_SECONDS = 1.0
VOL_IGNORE_FIRST_REPEATS = 1
USAGE_THREE_CHARS_SECONDS = 0.5
USAGE_TEN_CHARS_SECONDS = 1.0
USAGE_VERTICAL_SECONDS = 4.0
USAGE_THREE_LINES_SECONDS = 7.0
USAGE_VIEW_LEASE_SECONDS = 0.75
RECEIVER_SERIAL = '2543030725032a2a'
IPOD_SERIAL = re.compile(r'.AA0000000000360\Z', re.DOTALL)
RELEASE_SECONDS = 0.150


def near(value, target, tolerance):
    return abs(value - target) <= target * tolerance


class NECDecoder:
    """Decode active-low EDGE lines; b'' is a NEC repeat, bytes a full frame."""
    def __init__(self):
        self.edges = []
        self.span_us = 0

    def edge(self, timestamp, level):
        if level not in (0, 1):
            self.edges.clear()
            return None
        if self.edges:
            dt = (timestamp - self.edges[-1][0]) % (1 << 30)
            if self.edges[-1][1] == level or dt > 12000:
                self.edges.clear()
        if not self.edges and level != 0:
            return None
        self.edges.append((timestamp, level))
        d = [((b[0] - a[0]) % (1 << 30), a[1])
             for a, b in zip(self.edges, self.edges[1:])]
        if len(d) == 1 and not near(d[0][0], 9000, .2):
            self.edges.clear()
        elif len(d) >= 2:
            repeat = near(d[1][0], 2250, .3)
            if not repeat and not near(d[1][0], 4500, .25):
                self.edges.clear()
            elif repeat and len(d) == 3:
                self.span_us = (timestamp - self.edges[0][0]) % (1 << 30)
                self.edges.clear()
                return b'' if near(d[2][0], 560, .6) else None
            elif not repeat and len(d) == 67:
                self.span_us = (timestamp - self.edges[0][0]) % (1 << 30)
                self.edges.clear()
                bits = []
                for i in range(2, 66, 2):
                    if not near(d[i][0], 560, .6):
                        return None
                    space = d[i + 1][0]
                    if near(space, 560, .45):
                        bits.append(0)
                    elif near(space, 1690, .35):
                        bits.append(1)
                    else:
                        return None
                if not near(d[66][0], 560, .6):
                    return None
                return bytes(sum(bits[k * 8 + b] << b for b in range(8))
                             for k in range(4))
        return None

    def line(self, line):
        fields = line.split()
        if len(fields) != 3 or fields[0] != b'EDGE':
            return None
        try:
            timestamp, level = int(fields[1]), int(fields[2])
        except ValueError:
            return None
        return self.edge(timestamp, level)


class MenuForwarder:
    """Forward down/refresh/up; firmware supplies normal Menu repeats."""
    def __init__(self, usb):
        self.usb = usb
        self.down = False
        self.last = 0.0

    def frame(self, frame, now, span_seconds=0.0):
        self.poll(now)
        if frame is None:
            return
        menu = (len(frame) == 4 and frame[:2] == b'\xee\x87'
                and frame[2] in (0x02, 0x03))
        if menu or (frame == b'' and self.down):
            self.down = True
            self.last = now
            self.usb.send(True, now, MENU)
        elif frame:
            self.release(now)

    def release(self, now):
        if self.down:
            self.usb.send(False, now, MENU)
            self.down = False

    def poll(self, now):
        if self.down and now - self.last >= RELEASE_SECONDS:
            self.release(now)


class UsageVolume:
    """Nonblocking local batches; only HerdRemote can execute keyboard tools."""
    def __init__(self, directory=None):
        root = os.environ.get('XDG_RUNTIME_DIR', '/run/user/%d' % os.getuid())
        self.directory = directory or os.path.join(root, 'podbox-usage-keys')

    def report(self, direction, age, now):
        try:
            with open(os.path.join(self.directory, 'view.json')) as stream:
                state = json.load(stream)
            token = state['token']
            enabled = ((state['version'] == 1 and state['usage'] is True) or
                       (state['version'] == 2 and state.get('view') == 'detail'))
            if (not enabled or
                    not isinstance(token, str) or not re.fullmatch('[0-9a-f]{32}', token) or
                    not now < state['expires'] <= now + USAGE_VIEW_LEASE_SECONDS):
                return
            if age < USAGE_THREE_CHARS_SECONDS:
                count = 1
            elif age < USAGE_TEN_CHARS_SECONDS:
                count = 3
            elif age < USAGE_VERTICAL_SECONDS:
                count = 10
            else:
                count = 1 if age < USAGE_THREE_LINES_SECONDS else 3
            key = ('left' if direction == VOL_UP else 'right') if age < USAGE_VERTICAL_SECONDS else (
                   'up' if direction == VOL_UP else 'down')
            data = f'UK1 {token} {now!r} {key} {count}'.encode('ascii')
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
                channel.setblocking(False)
                channel.sendto(data, os.path.join(self.directory, 'keys.sock'))
        except (OSError, ValueError, KeyError, TypeError):
            return  # Missing daemon, stale/unknown state or busy socket: no keys.


class VolumeForwarder:
    """One step per decoded report, except the named initial repeat skip."""
    def __init__(self, usb, usage=None):
        self.usb = usb
        self.usage = usage
        self.key = None
        self.last = float('-inf')
        self.repeats = 0
        self.first = 0.0

    def frame(self, frame, now, span_seconds=0.0):
        if frame is None:
            return
        if frame:
            self.key = ({0x0b: VOL_UP, 0x0d: VOL_DOWN}.get(frame[2])
                        if len(frame) == 4 and frame[:2] == b'\xee\x87'
                        else None)
            self.repeats = 0
            self.first = now - span_seconds
            if self.key is not None:
                self.usb.send(1, now, self.key)
                if self.usage is not None:
                    self.usage.report(self.key, 0.0, now)
        elif self.key is not None and now - self.last < RELEASE_SECONDS:
            self.repeats += 1
            if self.repeats > VOL_IGNORE_FIRST_REPEATS:
                self.usb.send(2, now, self.key)
                if self.usage is not None:
                    self.usage.report(self.key, max(0.0, now - self.first), now)
        else:
            self.key = None
        self.last = now


class USBRemote:
    def __init__(self, core, util):
        self.core, self.util = core, util
        self.device = None
        self.retry_at = 0.0

    def close(self):
        if self.device is not None:
            self.util.dispose_resources(self.device)
            self.device = None

    def send(self, down, now, key=MENU):
        if self.device is None and now < self.retry_at:
            return False
        try:
            if self.device is None:
                self.retry_at = now + .5
                for dev in self.core.find(find_all=True, idVendor=0x05AC,
                                          idProduct=0x1261):
                    try:
                        serial = dev.serial_number or ''
                        if (IPOD_SERIAL.fullmatch(serial) and
                                dev.get_active_configuration() is not None):
                            self.device = dev
                            LOG.info('connected to %s', serial)
                            break
                    except self.core.USBError:
                        pass
                    finally:
                        if dev is not self.device:
                            self.util.dispose_resources(dev)
            if self.device is None:
                return False
            # No configuration selection, interface claim or driver detach.
            self.device.ctrl_transfer(0x40, REQUEST, key, int(down),
                                      b'', timeout=100)
            return True
        except (self.core.USBError, ValueError) as exc:
            LOG.warning('USB unavailable: %s', exc)
            self.close()
            self.retry_at = now + .5
            return False


class ReceiverHeartbeat:
    def __init__(self, usb):
        self.usb = usb
        self.next_at = float('-inf')

    def line(self, line, now):
        fields = line.split()
        if len(fields) != 3 or fields[0] not in (b'STATE', b'EDGE'):
            return
        try:
            if int(fields[1]) < 0 or int(fields[2]) not in (0, 1):
                return
        except ValueError:
            return
        if now >= self.next_at:
            self.usb.send(1, now, HEARTBEAT)
            self.next_at = now + HEARTBEAT_SECONDS


def receiver_path():
    paths = sorted(glob.glob('/dev/serial/by-id/*' + RECEIVER_SERIAL + '*'))
    return paths[0] if paths else None


def open_receiver(path):
    # O_RDONLY never opens a write channel. termios changes only the host
    # line discipline; do not toggle DTR/RTS or use a serial boot command.
    fd = os.open(path, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        attrs = termios.tcgetattr(fd)
        attrs[0] = attrs[1] = attrs[3] = 0
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[4] = attrs[5] = termios.B115200
        attrs[6][termios.VMIN] = attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
    except BaseException:
        os.close(fd)
        raise
    return fd


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    import usb.core
    import usb.util
    remote = USBRemote(usb.core, usb.util)
    forwarder = MenuForwarder(remote)
    usage_volume = UsageVolume()
    volume = VolumeForwarder(remote, usage_volume)
    heartbeat = ReceiverHeartbeat(remote)
    stopped = False

    def stop(signum, frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    fd, buffer, decoder, retry_at = None, b'', NECDecoder(), 0.0
    try:
        while not stopped:
            now = time.monotonic()
            forwarder.poll(now)
            if fd is None:
                if now >= retry_at:
                    retry_at = now + 1.0
                    path = receiver_path()
                    if path:
                        try:
                            fd = open_receiver(path)
                            decoder, buffer = NECDecoder(), b''
                            LOG.info('reading %s', path)
                        except OSError as exc:
                            LOG.warning('IR unavailable: %s', exc)
                time.sleep(.02)
                continue
            try:
                # STATE records emitted by the receiver prove it is alive,
                # even when no IR button is being pressed. An open but dead
                # serial fd must not keep firmware's heartbeat fresh.
                if not select.select([fd], [], [], .02)[0]:
                    continue
                chunk = os.read(fd, 4096)
                if not chunk:
                    raise OSError('IR receiver disconnected')
                buffer += chunk
                lines = buffer.split(b'\n')
                buffer = lines.pop()
                if len(buffer) > 128:
                    buffer = b''
                for line in lines:
                    heartbeat.line(line, now)
                    frame = decoder.line(line)
                    at = time.monotonic()
                    forwarder.frame(frame, at, decoder.span_us / 1e6)
                    volume.frame(frame, at, decoder.span_us / 1e6)
            except OSError as exc:
                LOG.warning('IR unavailable: %s', exc)
                forwarder.release(time.monotonic())
                os.close(fd)
                fd, buffer, decoder = None, b'', NECDecoder()
                volume = VolumeForwarder(remote, usage_volume)
    finally:
        forwarder.release(time.monotonic())
        remote.close()
        if fd is not None:
            os.close(fd)


if __name__ == '__main__':
    main()
