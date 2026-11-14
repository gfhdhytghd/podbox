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

    def assert_menu_stream(self, group, as_menu=False):
        events=replay(group,as_menu)
        self.assertEqual(events[0],(k.MENU,True,group[0][0]))
        self.assertEqual(events[-1],(k.MENU,False,group[-1][0]+.151))
        self.assertTrue(all(key==k.MENU for key,down,t in events))
        self.assertEqual(sum(down for key,down,t in events),len(group))
        self.assertEqual(sum(not down for key,down,t in events),1)

    def test_all_recorded_menu_taps_and_holds_forward_real_button(self):
        menu=[g for g in groups() if g[0][0]<55 and g[0][2]==MENU_FRAME]
        self.assertEqual(sum(len(g)>3 for g in menu),5)
        for group in menu:
            with self.subTest(first_us=group[0][1]):self.assert_menu_stream(group)
        result=replay([e for e in decoded_raw() if e[0]<55])
        self.assertTrue(all(key==k.MENU for key,down,t in result))
        self.assertEqual(sum(not down for key,down,t in result),19)

    def test_supplemental_capture_tail_is_menu_not_select(self):
        for group in groups():
            if group[0][0]>180 and group[0][2]==MENU_FRAME:self.assert_menu_stream(group)

    def test_collapsed_doubles_and_foreign_play(self):
        by_start={g[0][1]:g for g in groups()}
        for first_us in (695086291,712118050):self.assert_menu_stream(by_start[first_us],True)
        self.assertEqual(replay(by_start[712118050]),[])
        play=[e for g in groups() if g[0][2]!=MENU_FRAME for e in g]
        self.assertEqual(replay(play),[])


if __name__ == '__main__':
    unittest.main()
