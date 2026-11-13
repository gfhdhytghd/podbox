"""Offline IR/USB tests. No serial, USB, service or input device access."""
import fnmatch
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('usb_remote_key', ROOT / 'usb_remote_key.py')
k = importlib.util.module_from_spec(spec)
spec.loader.exec_module(k)


def edges(frame, start=0, scale=1.0):
    durations = [9000, 2250 if frame == b'' else 4500]
    if frame:
        for value in frame:
            for bit in range(8):
                durations += [560, 1690 if value & (1 << bit) else 560]
    durations += [560]
    timestamp, level = start, 0
    result = [(timestamp, level)]
    for duration in durations:
        timestamp = (timestamp + round(duration * scale)) % (1 << 30)
        level = 1 - level
        result.append((timestamp, level))
    return result


def decode(frame, **kwargs):
    decoder = k.NECDecoder()
    result = None
    for timestamp, level in edges(frame, **kwargs):
        value = decoder.line(f'EDGE {timestamp} {level}'.encode())
        if value is not None:
            result = value
    return result


class DecoderTests(unittest.TestCase):
    def test_all_pairing_ids_and_both_commands(self):
        for command in (2, 3):
            for pairing in range(256):
                frame = bytes([0xee, 0x87, command, pairing])
                self.assertEqual(decode(frame), frame)

    def test_repeat(self):
        self.assertEqual(decode(b''), b'')

    def test_tick_wrap(self):
        self.assertEqual(decode(b'\xee\x87\x02\x59', start=(1 << 30)-40000),
                         b'\xee\x87\x02\x59')

    def test_jitter(self):
        for scale in (.85, 1.15):
            self.assertEqual(decode(b'\xee\x87\x03\xff', scale=scale),
                             b'\xee\x87\x03\xff')

    def test_noise_partial_and_bad_space(self):
        decoder = k.NECDecoder()
        for line in (b'noise', b'EDGE x 0', b'EDGE 4 9', b'EDGE 1 0',
                     b'EDGE 2 1', b'EDGE 3 0', b'EDGE 4 1'):
            self.assertIsNone(decoder.line(line))
        for timestamp, level in edges(b'\xee\x87\x02\x59')[:50]:
            self.assertIsNone(decoder.edge(timestamp+100000, level))
        for timestamp, level in edges(b'\xee\x87\x02\x59', start=300000):
            value = decoder.edge(timestamp, level)
        self.assertEqual(value, b'\xee\x87\x02\x59')
        ev = edges(b'\xee\x87\x02\x59')
        # Space of 1000 us is neither a valid zero nor a valid one.
        offset = 1000 - (ev[4][0] - ev[3][0])
        ev[4:] = [(t+offset, level) for t, level in ev[4:]]
        decoder = k.NECDecoder()
        self.assertTrue(all(decoder.edge(t, level) is None for t, level in ev))

    def test_captured_decoded_log(self):
        # The supplied capture contains decoded frames, not raw EDGE lines.
        # Replay each captured byte tuple through synthetic NEC timings.
        count = menu = 0
        for line in (ROOT/'tests/fixtures/ir-menu.log').read_text().splitlines():
            fields = line.split()
            if len(fields) >= 6 and fields[1] == 'NEC':
                frame = bytes.fromhex(' '.join(fields[2:6]))
                self.assertEqual(decode(frame), frame)
                recorder = Recorder()
                forward = k.MenuForwarder(recorder)
                forward.frame(decode(frame), 1)
                expected = frame[:2] == b'\xee\x87' and frame[2] in (2, 3)
                self.assertEqual(recorder.events, [(True, 1)] if expected else [])
                count += 1
                menu += expected
        self.assertEqual(count, 31)
        self.assertEqual(menu, 14)


class Recorder:
    def __init__(self):
        self.events = []

    def send(self, down, now):
        self.events.append((down, now))
        return True


class ForwardTests(unittest.TestCase):
    def setUp(self):
        self.usb = Recorder()
        self.f = k.MenuForwarder(self.usb)

    def test_short_release(self):
        self.f.frame(b'\xee\x87\x02\x59', 0)
        self.f.poll(.149)
        self.assertTrue(self.f.down)
        self.f.poll(.151)
        self.assertEqual(self.usb.events, [(True, 0), (False, .151)])

    def test_repeat_hold_and_release(self):
        self.f.frame(b'\xee\x87\x03\xff', 0)
        for i in range(1, 11):
            self.f.frame(b'', i/10)
        self.assertTrue(self.f.down)
        self.assertEqual(len(self.usb.events), 11)
        self.f.poll(1.151)
        self.assertEqual(self.usb.events[-1], (False, 1.151))

    def test_orphan_repeat_and_noise(self):
        self.f.frame(b'', 0)
        self.assertEqual(self.usb.events, [])
        self.f.frame(b'\xee\x87\x02\x59', 1)
        self.f.frame(None, 1.1)
        self.f.frame(b'', 1.2)
        self.assertFalse(self.f.down)
        self.assertEqual(self.usb.events[-1], (False, 1.2))

    def test_other_key_owns_repeats(self):
        for frame in (b'\xee\x87\x04\x59', b'\x01\x02\x02\x59'):
            self.setUp()
            self.f.frame(b'\xee\x87\x02\x59', 0)
            self.f.frame(frame, .05)
            self.f.frame(b'', .1)
            self.assertEqual(self.usb.events, [(True, 0), (False, .05)])

    def test_duplicate_down_is_keepalive(self):
        self.f.frame(b'\xee\x87\x02\x59', 0)
        self.f.frame(b'\xee\x87\x02\x59', .1)
        self.assertEqual(self.usb.events, [(True, 0), (True, .1)])


class USBError(Exception):
    pass


class Device:
    def __init__(self, serial, configured=True, fail=False):
        self.serial_number, self.configured, self.fail = serial, configured, fail
        self.calls = []

    def get_active_configuration(self):
        if not self.configured:
            raise USBError('not configured')
        return object()

    def ctrl_transfer(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.fail:
            raise USBError('disconnected / STALL')
        return 0


class USBTests(unittest.TestCase):
    def setUp(self):
        self.devices, self.disposed = [], []
        self.core = SimpleNamespace(find=self.find, USBError=USBError)
        self.util = SimpleNamespace(dispose_resources=self.disposed.append)
        self.usb = k.USBRemote(self.core, self.util)

    def find(self, **kwargs):
        self.assertEqual(kwargs, dict(find_all=True, idVendor=0x05ac, idProduct=0x1261))
        return self.devices

    def test_absent_then_replug(self):
        self.assertFalse(self.usb.send(True, 0))
        dev = Device('1AA0000000000360')
        self.devices = [dev]
        self.assertFalse(self.usb.send(True, .1))
        self.assertTrue(self.usb.send(True, .6))
        self.assertTrue(self.usb.send(False, .8))
        self.assertEqual(dev.calls, [((0x40, 0x5b, 1, state, b''), dict(timeout=100))
                                     for state in (1, 0)])

    def test_exact_identity_and_configured_only(self):
        for prefix in [chr(n)+'AA' for n in range(128)]:
            with self.subTest(prefix=prefix):
                self.setUp()
                bad = [Device('AA0000000000360'),
                       Device('44AA0000000000360'),
                       Device('4AB0000000000360'),
                       Device('4AA0000000000360extra'),
                       Device('4AA0000000000360\n'),
                       Device('2AA1111111110360'),
                       Device('9AA1111111110360')]
                bad += [Device(p+'0000000000360', configured=False)
                        for p in ('1AA', '2AA', '4AA', '9AA', 'FAA')]
                good = Device(prefix+'0000000000360')
                self.devices = bad+[good]
                self.assertTrue(self.usb.send(True, 1))
                self.assertTrue(self.usb.send(False, 1.1))
                self.assertEqual(self.disposed, bad)
                self.assertTrue(all(not d.calls for d in bad))
                self.assertEqual(good.calls,
                                 [((0x40, 0x5b, 1, state, b''),
                                   dict(timeout=100)) for state in (1, 0)])

    def test_udev_serial_wildcard_matches_bridge(self):
        rules = (ROOT/'70-podbox-usbkey.rules').read_text().splitlines()
        usb = [line for line in rules if line.startswith('SUBSYSTEM=="usb"')]
        self.assertEqual(len(usb), 1)
        self.assertIn('ATTR{idVendor}=="05ac"', usb[0])
        self.assertIn('ATTR{idProduct}=="1261"', usb[0])
        self.assertIn('ATTR{serial}=="?AA0000000000360"', usb[0])
        serials = [chr(n)+'AA0000000000360' for n in range(128)]
        serials += ['AA0000000000360', '44AA0000000000360',
                    '4AB0000000000360', '4AA1111111110360',
                    '4AA0000000000360extra', '4AA0000000000360\n']
        for serial in serials:
            self.assertEqual(bool(k.IPOD_SERIAL.fullmatch(serial)),
                             fnmatch.fnmatchcase(serial, '?AA0000000000360'))
        self.assertTrue(any('ATTRS{serial}=="2543030725032a2a"' in line
                            for line in rules))

    def test_disconnect_stall_profile_recovery(self):
        old = Device('1AA0000000000360', fail=True)
        self.devices = [old]
        self.assertFalse(self.usb.send(True, 0))
        self.assertIn(old, self.disposed)
        self.devices = [Device('2AA0000000000360', configured=False)]
        self.assertFalse(self.usb.send(True, .6))
        new = Device('2AA0000000000360')
        self.devices = [new]
        self.assertTrue(self.usb.send(True, 1.2))
        self.assertEqual(len(new.calls), 1)

    def test_read_only_open(self):
        attrs = [0, 0, 0, 0, 0, 0, [0]*32]
        with patch.object(k.os, 'open', return_value=4) as opened, \
             patch.object(k.termios, 'tcgetattr', return_value=attrs), \
             patch.object(k.termios, 'tcsetattr'):
            self.assertEqual(k.open_receiver('/fake/receiver'), 4)
        flags = opened.call_args.args[1]
        self.assertEqual(flags & os_access_mask(), k.os.O_RDONLY)


def os_access_mask():
    return k.os.O_ACCMODE


if __name__ == '__main__':
    unittest.main()
