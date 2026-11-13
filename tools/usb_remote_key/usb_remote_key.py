#!/usr/bin/env python3
"""Forward Apple IR Menu to PodBox EP0. The IR serial port is read-only."""
import argparse
import glob
import logging
import os
import re
import select
import signal
import termios
import time

LOG = logging.getLogger('podbox-usbkey')
REQUEST = 0x5B
MENU = 1
SELECT = 2
MENU_HOLD_SECONDS = 0.300
MENU_HOLD_REPEATS = 3
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
    def __init__(self, usb):
        self.usb = usb
        self.down = False
        self.first = self.last = 0.0
        self.repeats = 0
        self.sent = False

    def pulse(self, key, now):
        self.usb.send(True, now, key)
        self.usb.send(False, now, key)

    def frame(self, frame, now, span_seconds=0.0):
        self.poll(now)
        if frame is None:
            return
        menu = (len(frame) == 4 and frame[:2] == b'\xee\x87'
                and frame[2] in (0x02, 0x03))
        if menu or (frame == b'' and self.down):
            if not self.down:
                self.down, self.sent = True, False
                # Hold age starts at the first edge, not 68 ms later when
                # the complete frame can be decoded. Silence uses last below.
                self.first, self.repeats = now - span_seconds, 0
            else:
                self.repeats += 1
            self.last = now
            # A timer alone cannot establish a hold: a continuing IR stream
            # at/after the named threshold is required.
            if (not self.sent and self.repeats >= MENU_HOLD_REPEATS and
                    now - self.first + 1e-9 >= MENU_HOLD_SECONDS):
                self.pulse(SELECT, now)
                self.sent = True
        elif frame:
            self.release(now)

    def release(self, now):
        if self.down:
            if not self.sent:
                self.pulse(MENU, now)
            self.down = False

    def poll(self, now):
        if self.down and now - self.last >= RELEASE_SECONDS:
            self.release(now)


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
                    frame = decoder.line(line)
                    forwarder.frame(frame, time.monotonic(), decoder.span_us / 1e6)
            except OSError as exc:
                LOG.warning('IR unavailable: %s', exc)
                forwarder.release(time.monotonic())
                os.close(fd)
                fd, buffer, decoder = None, b'', NECDecoder()
    finally:
        forwarder.release(time.monotonic())
        remote.close()
        if fd is not None:
            os.close(fd)


if __name__ == '__main__':
    main()
