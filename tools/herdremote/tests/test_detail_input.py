"""Prompt/input extraction facts; presentation and wire budgets live in the design."""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hrdetail

FIXTURES = Path(__file__).parent / 'fixtures/detail/input'


class Input(unittest.TestCase):
    def sample(self, name, kind):
        return hrdetail.extract_input((FIXTURES / name).read_text(), kind)

    def test_empty_claude_input_leaves_agent_output_available(self):
        self.assertEqual(self.sample('claude-empty.txt', 'claude')['input'], '')
        result = hrdetail.parse((FIXTURES / 'claude-empty.txt').read_text(), 'claude')
        self.assertEqual(result['say'], 'Current assistant output.')
        self.assertEqual(result['cursor'], 0)

    def test_codex_single_line_and_end_fallback(self):
        result = self.sample('codex-single.txt', 'codex')
        self.assertEqual(result['input'], 'continue with this input')
        self.assertEqual(result['cursor'], len(result['input']))
        self.assertEqual(result['cursor_source'], 'end')

    def test_multiline_cjk_and_literal_backslash_n_are_distinct(self):
        result = self.sample('claude-multiline.txt', 'claude')
        expected = 'First input line\n第二行有中文😀\n\nFinal input line with literal \\n text'
        self.assertEqual(result['input'], expected)
        self.assertEqual(result['cursor'], len(expected))
        self.assertNotEqual(result['cursor'], len(expected.encode()))

    def test_codex_placeholder_and_try_placeholders_are_empty(self):
        self.assertEqual(self.sample('codex-placeholder.txt', 'codex')['input'], '')
        for kind, marker in [('claude', '❯'), ('codex', '›'), ('agy', '>')]:
            with self.subTest(kind=kind):
                result = hrdetail.extract_input(marker + ' Try "refactor this file"\n', kind)
                self.assertEqual(result['input'], '')
                self.assertEqual(result['cursor'], 0)

    def test_agy_autocomplete_selection_is_not_the_input(self):
        result = self.sample('agy-single.txt', 'agy')
        self.assertEqual(result['input'], '/stat')
        self.assertEqual(result['cursor'], 5)

    def test_submitted_input_is_not_current_input(self):
        for kind, text in [('claude', '❯ old input\n● Agent reply'),
                           ('codex', '› old input\n• Working (20s • esc to interrupt)'),
                           ('agy', '> old input\n○ Thought for 1s\n● Explored 1 file')]:
            with self.subTest(kind=kind):
                self.assertEqual(hrdetail.extract_input(text, kind)['input'], '')

    def test_latest_prompt_wins_over_history(self):
        text = '❯ submitted\n● Reply\n─────────\n❯ current\n  wrapped\n─────────\n'
        self.assertEqual(hrdetail.extract_input(text, 'claude')['input'], 'current\nwrapped')

    def test_input_is_not_trimmed_or_replaced_with_assistant_output(self):
        text = '• Agent output\n›   leading and trailing  \n\n  ? for shortcuts\n'
        result = hrdetail.parse(text, 'codex')
        self.assertEqual(result['input'], '  leading and trailing  ')
        self.assertEqual(result['say'], 'Agent output')

    def test_framed_cells_and_grok_prompt_equivalent(self):
        text = '╭──────────╮\n│ > 第一行 │\n│   second │\n╰──────────╯\n'
        self.assertEqual(hrdetail.extract_input(text, 'grok')['input'], '第一行\nsecond')

    def test_no_presentation_cut_is_applied_to_extracted_input(self):
        text = '❯ ' + '中' * 1400 + '\n──────────\n'
        result = hrdetail.extract_input(text, 'claude')
        self.assertEqual(len(result['input']), 1400)
        self.assertEqual(result['cursor'], 1400)

    def test_ansi_is_removed_without_merging_hard_lines(self):
        text = '\x1b[32m❯ first\x1b[0m\n  第二行\n──────────\n'
        self.assertEqual(hrdetail.extract_input(text, 'claude')['input'], 'first\n第二行')


class Cursor(unittest.TestCase):
    def extract(self, text, row, col, **extra):
        return hrdetail.extract_input(text, 'codex', dict(row=row, col=col, visible=True, **extra))

    def test_ascii_start_middle_and_end(self):
        text = '• Output\n› abcdef\n\n  ? for shortcuts\n'
        for column, expected in [(2, 0), (5, 3), (8, 6)]:
            with self.subTest(column=column):
                result = self.extract(text, 1, column)
                self.assertEqual(result['cursor'], expected)
                self.assertEqual(result['cursor_source'], 'herdr')

    def test_col_chars_handles_cjk_emoji_and_combining_characters(self):
        for text, col, chars, expected in [
                ('› 中😀ab', 6, 4, 2), ('› a\u0301b', 4, 5, 3),
                ('› 中😀ab', 6, 3, 1)]:  # col_chars is authoritative
            with self.subTest(text=text, chars=chars):
                result = self.extract(text, 0, col, col_chars=chars)
                self.assertEqual(result['cursor'], expected)
                self.assertEqual(result['cursor_source'], 'herdr')

    def test_display_cell_columns_without_col_chars(self):
        for column, expected in [(2, 0), (4, 1), (6, 2), (8, 4)]:
            with self.subTest(column=column):
                self.assertEqual(self.extract('› 中😀ab', 0, column)['cursor'], expected)

    def test_multiline_offset_counts_each_hard_newline_once(self):
        text = '• Output\n› one\n  第二行😀\n  final\n\n  ? for shortcuts'
        result = self.extract(text, 2, 6, col_chars=4)
        self.assertEqual(result['input'], 'one\n第二行😀\nfinal')
        self.assertEqual(result['cursor'], len('one\n第二'))

    def test_cursor_blank_line_is_preserved_instead_of_footer_padding(self):
        result = self.extract('› a\n  \n\n  ? for shortcuts', 1, 2)
        self.assertEqual(result['input'], 'a\n')
        self.assertEqual(result['cursor'], 2)
        self.assertEqual(result['cursor_source'], 'herdr')

    def test_invisible_absent_bad_and_off_input_cursor_fall_back_to_end(self):
        text = '• Output\n› abc\n\n  ? for shortcuts'
        bad = [None, {}, dict(row=1, col=3, visible=False), dict(row=0, col=2, visible=True),
               dict(row=3, col=0, visible=True), dict(row=99, col=0, visible=True),
               dict(row=-1, col=0, visible=True), dict(row=True, col=2, visible=True),
               dict(row=1, col=-1, visible=True), dict(row=1, col=0, col_chars='2', visible=True)]
        for cursor in bad:
            with self.subTest(cursor=cursor):
                result = hrdetail.extract_input(text, 'codex', cursor)
                self.assertEqual(result['cursor'], 3)
                self.assertEqual(result['cursor_source'], 'end')

    def test_box_prompt_and_cell_padding_do_not_count_as_input(self):
        text = '╭──────────╮\n│ > 第一行 │\n│   second │\n╰──────────╯'
        result = hrdetail.extract_input(text, 'grok', dict(row=1, col=6, col_chars=5, visible=True))
        self.assertEqual(result['input'], '第一行\nsecond')
        self.assertEqual(result['cursor'], 1)

    def test_ansi_sequences_do_not_count_as_cursor_columns(self):
        text = '\x1b[32m› abc\x1b[0m'
        result = self.extract(text, 0, 4)
        self.assertEqual(result['cursor'], 2)

    def test_placeholder_with_metadata_still_has_empty_input_and_zero_cursor(self):
        result = self.extract('› Ask Codex to do anything', 0, 4)
        self.assertEqual(result['input'], '')
        self.assertEqual(result['cursor'], 0)


# The fake API returns the optional record exactly as a newer server would.
from test_nav2 import Harness, hrproto


class CursorAPI(Harness):
    def test_optional_cursor_survives_fake_socket_and_read_record(self):
        self.server.texts['w1:p3'] = '• Output\n› 中😀ab\n\n  ? for shortcuts'
        meta = dict(row=1, col=6, col_chars=4, visible=True)
        self.server.cursors['w1:p3'] = meta
        record = self.daemon.herdr.read_record('w1:p3', source='visible')
        self.assertEqual(record['cursor'], meta)
        with mock.patch.object(hrproto, 'detail', side_effect=lambda pane, fields, **kwargs: fields):
            result = self.daemon.read_detail('w1:p3', True, self.daemon.detail_context('w1:p3'))
        self.assertEqual(result['input'], '中😀ab')
        self.assertEqual(result['cursor'], 2)
        self.assertEqual(result['cursor_source'], 'herdr')
        self.server.cursors.clear()
        with mock.patch.object(hrproto, 'detail', side_effect=lambda pane, fields, **kwargs: fields):
            result = self.daemon.read_detail('w1:p3', True, self.daemon.detail_context('w1:p3'))
        self.assertEqual(result['cursor'], 4)
        self.assertEqual(result['cursor_source'], 'end')

    def test_viewport_cursor_is_used_when_merged_history_has_no_cursor(self):
        self.server.texts['w1:p3'] = '• Output\n› 中😀ab\n\n  ? for shortcuts'
        self.server.cursors_by_source[('w1:p3', 'recent_unwrapped')] = None
        self.server.cursors_by_source[('w1:p3', 'visible')] = dict(row=1, col=6, col_chars=4, visible=True)
        self.server.calls.clear()
        with mock.patch.object(hrproto, 'detail', side_effect=lambda pane, fields, **kwargs: fields):
            result = self.daemon.read_detail('w1:p3', True, self.daemon.detail_context('w1:p3'))
        self.assertEqual(result['cursor'], 2)
        self.assertEqual(result['cursor_source'], 'herdr')
        self.assertEqual([p['source'] for m, p in self.server.calls], ['visible'])
        self.assertEqual(result['say'], 'Output')

    def test_failed_viewport_read_keeps_recent_activity_and_input(self):
        import hrherdr
        records = [dict(text='• Output\n› draft'), hrherdr.HerdrError('fake viewport failure')]
        with mock.patch.object(self.daemon.herdr, 'read_record', side_effect=records), \
             mock.patch.object(hrproto, 'detail', side_effect=lambda pane, fields, **kwargs: fields):
            result = self.daemon.read_detail('w1:p3', True, self.daemon.detail_context('w1:p3'), self.daemon.herdr)
        self.assertEqual(result['input'], 'draft')
        self.assertEqual(result['cursor'], 5)
        self.assertEqual(result['cursor_source'], 'end')
        self.assertEqual(result['say'], 'Output')


if __name__ == '__main__':
    unittest.main()
