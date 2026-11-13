"""Precise captured EDGE replay through the real decoder and Menu bridge."""
import re
import unittest
from test_usb_remote_key import k, ROOT, Recorder

FIXTURES = ROOT / 'tests/fixtures'
MENU_FRAME = bytes.fromhex('ee870259')


def decoded_raw():
    decoder = k.NECDecoder()
    events = []
    first_us = None
    for line in (FIXTURES / 'ir-gestures-raw.log').read_text().splitlines():
        match = re.fullmatch(r'([\d.]+) (EDGE (\d+) ([01]))', line)
        if not match:
            continue  # STATE heartbeats are not edges.
        host, raw, timestamp, level = match.groups()
        if not decoder.edges and level == '0':
            first_us = int(timestamp)
        frame = decoder.line(raw.encode())
        if frame is not None:
            # main() supplies host monotonic time when decode completes.
            events.append((float(host), first_us, frame, decoder.span_us))
    return events


def groups():
    result = []
    for event in decoded_raw():
        if event[2]:
            result.append([event])
        else:
            assert result, 'orphan repeat in capture'
            result[-1].append(event)
    return result


def replay(events, as_menu=False):
    port = Recorder()
    forward = k.MenuForwarder(port)
    for now, first_us, frame, span_us in events:
        forward.frame(MENU_FRAME if as_menu and frame else frame, now, span_us / 1e6)
    forward.poll(events[-1][0] + .151)
    return port.events


class CapturedGestures(unittest.TestCase):
    def test_raw_separates_every_merged_first_repeat(self):
        actual = decoded_raw()
        cursor = 0
        merged = 0
        lines = (FIXTURES / 'ir-gestures.log').read_text().splitlines()
        for line in lines:
            match = re.fullmatch(
                r't=[\d.]+ (NEC ([0-9a-f ]+)|REPEAT) edges=(\d+) '
                r'span_us=(\d+) first_us=(\d+)', line)
            self.assertIsNotNone(match, line)
            label, payload, edges, span, first_us = match.groups()
            expected = bytes.fromhex(payload) if payload else b''
            self.assertEqual(actual[cursor][1:3], (int(first_us), expected))
            cursor += 1
            if int(edges) == 72:
                merged += 1
                self.assertGreater(int(span), 115000)
                self.assertEqual(actual[cursor][2], b'')
                delay = actual[cursor][1] - int(first_us)
                self.assertGreater(delay, 105000)
                self.assertLess(delay, 112000)
                cursor += 1
            else:
                self.assertEqual(int(edges), 68 if payload else 4)
        self.assertEqual(cursor, len(actual))
        self.assertGreater(merged, 0)

    def test_all_menu_taps_and_five_one_second_holds(self):
        # The instructed tap/double/one-second-hold blocks precede t=55s.
        menu = [g for g in groups() if g[0][0] < 55 and g[0][2] == MENU_FRAME]
        taps = [g for g in groups() if g[0][2] == MENU_FRAME and len(g) <= 3]
        holds = [g for g in menu if len(g) > 3]
        self.assertEqual(len(taps), 16)
        self.assertEqual(len(holds), 5)
        self.assertEqual([len(g)-1 for g in holds], [7, 9, 10, 8, 8])
        for group in taps:
            with self.subTest(first_us=group[0][1]):
                self.assertEqual([(key, down) for key, down, t in replay(group)],
                                 [(k.MENU, True), (k.MENU, False)])
        for group in holds:
            with self.subTest(first_us=group[0][1]):
                result = replay(group)
                self.assertEqual([(key, down) for key, down, t in result],
                                 [(k.SELECT, True), (k.SELECT, False)])
                self.assertEqual(result[0][2], group[3][0],
                                 "Select must arrive on the third decoded repeat")
        # Continuous replay also covers adjacent full-frame doubles and timeout.
        events = [e for e in decoded_raw() if e[0] < 55]
        keys = [key for key, down, t in replay(events) if down]
        self.assertEqual(keys, [k.MENU]*14 + [k.SELECT]*5)

    def test_supplemental_capture_tail(self):
        # The late capture includes another three-repeat Menu stream, two
        # Play streams, and two separate full Menu frames. Apply the same
        # rule without treating the extra stream as a sixth one-second hold.
        events = [e for e in decoded_raw() if e[0] > 180]
        result = replay(events)
        self.assertEqual([(key, down) for key, down, t in result],
                         [(k.SELECT, True), (k.SELECT, False),
                          (k.MENU, True), (k.MENU, False),
                          (k.MENU, True), (k.MENU, False)])

    def test_both_collapsed_doubles_cannot_become_select(self):
        by_start = {g[0][1]: g for g in groups()}
        # Menu t=23.881 and Play t=40.914, including their merged repeat.
        for first_us, repeat_count in ((695086291, 1), (712118050, 2)):
            group = by_start[first_us]
            self.assertEqual(len(group)-1, repeat_count)
            # Apply the Play timing pattern to Menu classification too. The
            # actual bridge must continue to ignore the original Play command.
            self.assertEqual([(key, down) for key, down, t in replay(group, as_menu=True)],
                             [(k.MENU, True), (k.MENU, False)])
        self.assertEqual(replay(by_start[712118050]), [])
        play = [e for g in groups() if g[0][2] != MENU_FRAME for e in g]
        self.assertEqual(replay(play), [])


if __name__ == '__main__':
    unittest.main()
