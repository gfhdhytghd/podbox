"""The approved DETAIL-DESIGN limits, escapes and cursor window on both caps."""
import random
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hrproto


def unescape(value):
    chars = []
    i = 0
    while i < len(value):
        if value[i] == '\\':
            i += 1
            if i == len(value) or value[i] not in ('n', '\\'):
                raise AssertionError('incomplete or unknown input escape')
            chars.append('\n' if value[i] == 'n' else '\\')
        else:
            chars.append(value[i])
        i += 1
    return ''.join(chars)


class InputWire(unittest.TestCase):
    def fields(self, text, cursor, maximum=16384):
        payload = hrproto.detail('p', dict(input=text, cursor=cursor), max_message=maximum)
        self.assertLessEqual(len(payload), maximum)
        fields = hrproto.decode(payload)[0]
        self.assertEqual(len(fields), 11)
        return unescape(fields[9]), int(fields[10]), fields[9]

    def test_backslash_newline_spaces_and_unicode_survive_whole(self):
        text = '  first\\n\n第二行😀  '
        value, cursor, encoded = self.fields(text, 8)
        self.assertEqual(value, text)
        self.assertEqual(cursor, 8)
        self.assertNotIn('\n', encoded)
        self.assertIn('\\\\n', encoded)
        self.assertIn('\\n', encoded)

    def test_legacy_start_middle_and_end_windows_match_design(self):
        text = ''.join(str(i % 10) for i in range(1000))
        for cursor, expected, adjusted in [
                (0, text[:100], 0), (500, '…' + text[101:600], 400),
                (1000, '…' + text[501:], 500)]:
            with self.subTest(cursor=cursor):
                value, actual, _ = self.fields(text, cursor, 4096)
                self.assertEqual(value, expected)
                self.assertEqual(actual, adjusted)

    def test_large_profile_sends_4000_plain_characters_whole(self):
        value, cursor, _ = self.fields('a' * 4000, 2000)
        self.assertEqual(value, 'a' * 4000)
        self.assertEqual(cursor, 2000)

    def test_large_profile_windows_over_4000_characters(self):
        text = 'a' * 5000
        value, cursor, _ = self.fields(text, 4500)
        self.assertEqual(value, '…' + text[601:4600])
        self.assertEqual(cursor, 3900)

    def test_wide_character_byte_budget_is_stricter_than_character_budget(self):
        value, cursor, encoded = self.fields('😀' * 4000, 4000)
        self.assertEqual(value, '…' + '😀' * 2999)
        self.assertEqual(cursor, 3000)
        self.assertLessEqual(len(encoded.encode()), 12000)
        value, cursor, encoded = self.fields('😀' * 1000, 1000, 4096)
        self.assertEqual(value, '…' + '😀' * 374)
        self.assertEqual(cursor, 375)
        self.assertLessEqual(len(encoded.encode()), 1500)

    def test_byte_budget_counts_escaped_bytes_and_cursor_counts_decoded_characters(self):
        for text in ('\\' * 4000, '\n' * 4000):
            value, cursor, encoded = self.fields(text, len(text))
            self.assertEqual(value, text)
            self.assertEqual(cursor, 4000)
            self.assertEqual(len(encoded.encode()), 8000)

    def test_all_text_budgets_fit_both_negotiated_profiles(self):
        for maximum, count, byte_budget in [(4096, 500, 1500), (16384, 4000, 12000)]:
            with self.subTest(maximum=maximum):
                fields = dict(task='中' * 80, now='中' * 80, say='中' * 300,
                              steps=['中' * 80] * 3, input='中' * count, cursor=count,
                              state_age_s=2147483647)
                payload = hrproto.detail('p' * 15, fields, max_message=maximum)
                row = hrproto.decode(payload)[0]
                self.assertLessEqual(len(payload), maximum)
                self.assertEqual(len(row), 11)
                self.assertEqual(len(row[9].encode()), byte_budget)
                self.assertEqual(int(row[10]), count)

    def test_random_unicode_windows_are_atomic_and_maximal(self):
        rng = random.Random(7)
        for maximum, char_limit, byte_limit in [(4096, 500, 1500), (16384, 4000, 12000)]:
            for length in (25, 600, 5000):
                text = ''.join(rng.choice('A中😀\\\ne\u0301') for _ in range(length))
                for original_cursor in (0, length // 2, length):
                    with self.subTest(maximum=maximum, length=length, cursor=original_cursor):
                        value, cursor, encoded = self.fields(text, original_cursor, maximum)
                        self.assertLessEqual(len(value), char_limit)
                        self.assertLessEqual(len(encoded.encode()), byte_limit)
                        self.assertGreaterEqual(cursor, 0)
                        self.assertLessEqual(cursor, len(value))
                        if value == text:
                            self.assertEqual(cursor, original_cursor)
                            continue
                        end = min(length, original_cursor + 100)
                        if value.startswith('…'):
                            start = original_cursor - cursor + 1
                            self.assertEqual(value, '…' + text[start:end])
                            earlier = ('…' if start > 1 else '') + text[start - 1:end]
                            earlier_wire = earlier.replace('\\', '\\\\').replace('\n', '\\n')
                            self.assertTrue(len(earlier) > char_limit or len(earlier_wire.encode()) > byte_limit)
                        else:
                            self.assertEqual(value, text[:end])
                        self.assertEqual(value[cursor:], text[original_cursor:end])

    def test_missing_cursor_defaults_to_input_end_and_invalid_offsets_are_clamped(self):
        self.assertEqual(self.fields('abc', None)[:2], ('abc', 3))
        self.assertEqual(self.fields('abc', -20)[:2], ('abc', 0))
        self.assertEqual(self.fields('abc', 99)[:2], ('abc', 3))


if __name__ == '__main__':
    unittest.main()
