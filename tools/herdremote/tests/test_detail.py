"""Typed small-screen detail from reviewed real captures and negative controls."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hrdetail
import hrproto

FIXTURES = Path(__file__).parent / 'fixtures/detail'


def captured(name):
    meta = next(row for row in json.loads((FIXTURES / 'capture.json').read_text())['fixtures']
                if row['file'] == name + '.txt')
    return hrdetail.parse((FIXTURES / meta['file']).read_text(), meta['kind'], meta['title'], meta['status'])


class Captures(unittest.TestCase):
    def test_claude_working_message_and_tools(self):
        result = captured('claude-working')
        self.assertEqual(result['state_age_s'], 141)
        self.assertEqual(result['now'], 'Synthesizing… 2m 21s · ↓ 6.6k tokens')
        self.assertTrue(result['say'].startswith('进行中：查看 Hypr-Agent-Portal'))
        self.assertEqual(result['steps'][-1], 'Running 2 shell commands…')
        self.assertNotIn('esc to interrupt', str(result))
        self.assertNotIn('You are a tester', result['say'])
        self.assertNotIn('Hyper Agent Protocol', result['say'])

    def test_claude_done_uses_latest_final_message_and_done_age(self):
        result = captured('claude-done')
        self.assertEqual(result['now'], 'done')
        self.assertEqual(result['state_age_s'], 169)
        self.assertTrue(result['say'].startswith('APK 体积代理修完了 R3'))
        self.assertNotIn('Background command', result['say'])
        self.assertNotIn('bypass permissions', str(result))
        self.assertEqual(result['steps'][-1], 'Ran 2 shell commands')

    def test_claude_idle_retains_summary(self):
        result = captured('claude-idle')
        self.assertEqual(result['now'], 'idle')
        self.assertTrue(result['say'].startswith('我会用你那台大的 Windows 虚拟机'))
        self.assertNotIn('esc to interrupt', str(result))

    def test_codex_working_drops_command_output_and_footer(self):
        result = captured('codex-working')
        self.assertEqual(result['now'], 'Working 10m 51s')
        self.assertEqual(result['state_age_s'], 651)
        self.assertTrue(result['say'].startswith('进行中：增加双向输入传输和所有权状态机。'))
        self.assertNotIn('m_nativeReadable', str(result))
        self.assertNotIn('GPT-6', str(result))
        self.assertNotIn('Show details', str(result))
        self.assertEqual(len(result['steps']), 3)

    def test_codex_done_retains_head_and_final_paragraph(self):
        result = captured('codex-done')
        self.assertEqual(result['now'], 'done')
        self.assertEqual(result['state_age_s'], -1)
        self.assertTrue(result['say'].startswith('已完成：R3 修复与聚焦检查。'))
        self.assertTrue(result['say'].endswith('本轮没有 push 或合并。工作树干净。'))
        self.assertNotIn('Worked for', str(result))
        self.assertNotIn('Show details', str(result))

    def test_agy_real_braille_spinner_and_steps_exclude_input_and_thought(self):
        result = captured('agy-working')
        self.assertEqual(result['now'], 'Reading file...')
        self.assertEqual(result['say'], '')  # no assistant message start in this capture
        self.assertEqual(result['steps'][-1], 'Explored 1 file (generate_synthetic_dataset.py)')
        self.assertNotIn('Thought for', str(result))
        self.assertNotIn('strict_exact', str(result))
        self.assertNotIn('用户最新授权', str(result))


class Parsing(unittest.TestCase):
    def test_claude_tools_are_steps_and_read_prose_is_a_message(self):
        text = ('● Bash(make test)\n  ⎿ tool output\n      more output\n'
                '● Read(config.py)\n  ⎿ hidden content\n'
                '● Read this summary carefully.\n  First paragraph.\n\n  Final paragraph.\n'
                '✻ Compiling… (1m 12s · ↓ 2.1k tokens · esc to interrupt)\n'
                '❯ user input\n  wrapped user input\n  accept edits on · esc to interrupt\n')
        result = hrdetail.parse(text, 'claude', '✻ TASK-HERDREMOTE.md ✳', 'working')
        self.assertEqual(result['task'], 'TASK-HERDREMOTE.md')
        self.assertEqual(result['say'], 'Read this summary carefully. First paragraph. Final paragraph.')
        self.assertEqual(result['steps'], ['Bash(make test)', 'Read(config.py)'])
        self.assertEqual(result['state_age_s'], 72)
        self.assertEqual(result['now'], 'Compiling… 1m 12s · ↓ 2.1k tokens')

    def test_codex_nested_bullets_belong_to_one_message(self):
        text = ('• Earlier message\n• Ran very-long-command\n  └ output\n    hidden continuation\n'
                '• Final message\n\n  • first point\n  • second point\n  * third point\n'
                '  Worked for 5m 4s • 1:47 AM\n› Ask Codex to do anything\n'
                '  GPT-6.1-Sol high Fast · ~/project\n  ⚠ 2 warnings · f2 to view\n')
        result = hrdetail.parse(text, 'codex', status='done')
        self.assertEqual(result['say'], 'Final message • first point • second point * third point')
        self.assertEqual(result['steps'], ['Ran very-long-command'])
        self.assertEqual(result['now'], 'done')
        self.assertEqual(result['state_age_s'], -1)

    def test_agy_black_bullets_are_messages_with_tool_steps(self):
        text = '● Explored 3 files\n● Implementation is ready.\n  Checks passed.\n⣟ Reading file...\n'
        result = hrdetail.parse(text, 'agy', status='working')
        self.assertEqual(result['say'], 'Implementation is ready. Checks passed.')
        self.assertEqual(result['steps'], ['Explored 3 files'])
        self.assertEqual(result['now'], 'Reading file...')

    def test_grok_and_unknown_kinds_use_last_four_non_chrome_lines(self):
        text = ('old\nfirst\nsecond\nthird\nfourth\n──────\n'
                '› prompt\n  continuation\n  ? for shortcuts\n')
        for kind in ('grok', 'custom'):
            with self.subTest(kind=kind):
                result = hrdetail.parse(text, kind, '◐ Task ◑', 'working')
                self.assertEqual(result['state_age_s'], -2)
                self.assertEqual(result['task'], 'Task')
                self.assertEqual(result['say'], 'first second third fourth')
                self.assertEqual(result['steps'], [])
                self.assertEqual(result['now'], '')

    def test_unknown_tool_record_survives_fallback_but_tool_output_does_not(self):
        result = hrdetail.parse('● Bash(make check)\n  ⎿ tool output\n    hidden continuation', 'custom')
        self.assertEqual(result['state_age_s'], -2)
        self.assertEqual(result['say'], 'Bash(make check)')
        self.assertEqual(result['steps'], [])

    def test_known_kind_with_unrecognized_layout_falls_back(self):
        result = hrdetail.parse('plain content\nmore content', 'claude')
        self.assertEqual(result['say'], 'plain content more content')
        self.assertEqual(result['state_age_s'], -2)

    def test_chrome_only_clears_all_fields(self):
        text = '╭─────────╮\n│ prompt  │\n╰─────────╯\n› Ask Codex to do anything\n? for shortcuts\n'
        result = hrdetail.parse(text, 'codex', 'some title', 'idle')
        self.assertEqual(hrproto.decode(hrproto.detail('p', result))[0],
                         ['D', 'p', '-1', '', '', '', '', '', '', '', '0'])

    def test_input_only_keeps_task_without_becoming_say(self):
        result = hrdetail.parse('› user input\n  wrapped input\n  ? for shortcuts',
                                'codex', title='Task')
        self.assertEqual(result['task'], 'Task')
        self.assertEqual(result['input'], 'user input\nwrapped input')
        self.assertEqual(result['say'], '')
        self.assertEqual(result['now'], '')
        self.assertEqual(result['state_age_s'], -2)

    def test_real_choice_question_uses_question_text(self):
        text = (Path(__file__).parent / 'samples/claude-ask.txt').read_text()
        result = hrdetail.parse(text, 'claude', status='working', state_age_s=90)
        self.assertEqual(result['now'], '? Asking')
        self.assertEqual(result['say'], 'Colour 你想用哪种颜色? Which colour?')
        self.assertEqual(result['state'], 'blocked')
        self.assertEqual(result['state_age_s'], -1)

    def test_done_elapsed_is_not_previous_work_duration(self):
        text = '● Final summary\n✻ Cogitated for 29m · done 1:47\n'
        self.assertEqual(hrdetail.parse(text, 'claude', status='done')['state_age_s'], 107)
        result = hrdetail.parse('● Final summary\n✻ Cogitated for 29m\n', 'claude')
        self.assertEqual(result['now'], 'done')
        self.assertEqual(result['state_age_s'], -1)

    def test_observed_age_for_idle_and_done_is_retained_without_explicit_elapsed(self):
        for status in ('done', 'idle'):
            result = hrdetail.parse('● Summary', 'claude', status=status, state_age_s=31)
            self.assertEqual(result['state_age_s'], 31)
            self.assertEqual(result['now'], status)

    def test_all_required_codex_tool_prefixes_are_steps_not_say(self):
        text = '• Final summary\n' + ''.join('• '+v+' file\n  └ output\n' for v in
                                           ('Ran', 'Explored', 'Edited', 'Read', 'Searched', 'Updated'))
        result = hrdetail.parse(text, 'codex', status='done')
        self.assertEqual(result['say'], 'Final summary')
        self.assertEqual(result['steps'], ['Read file', 'Searched file', 'Updated file'])

    def test_ansi_csi_osc_c1_and_controls_are_removed(self):
        text = '\x1b]0;title\x07\x1b[32m● Summary\x1b[0m\n  message\x9b31m text\x9b0m\n'
        result = hrdetail.parse(text, 'claude')
        self.assertEqual(result['say'], 'Summary message text')
        self.assertNotIn('\x1b', str(result))
        self.assertNotIn('[32m', str(result))


class Wire(unittest.TestCase):
    def test_exactly_eleven_fields_with_empty_input(self):
        payload = hrproto.detail('p', dict(state_age_s=0, task='task', now='idle', say='summary'))
        self.assertEqual(hrproto.decode(payload), [['D', 'p', '0', 'task', 'idle', 'summary', '', '', '', '', '0']])
        self.assertTrue(payload.endswith(b'\t\t0'))

    def test_300_unicode_characters_preserve_head_and_end_with_ellipsis(self):
        for text in ('关键结论。'+'中'*500, 'First sentence. '+'word '*100, '😀'*400):
            with self.subTest(text=text[:10]):
                fields = hrproto.decode(hrproto.detail('p', dict(say=text)))[0]
                self.assertLessEqual(len(fields[5]), 300)
                self.assertLessEqual(len(fields[5].encode()), 900)
                self.assertTrue(fields[5].endswith('…'))
                self.assertTrue(fields[5].startswith(text[:10]))

    def test_80_unicode_character_fields_and_maximum_frame(self):
        fields = dict(task='😀'*90, now='😀'*90, say='😀'*310,
                      steps=['😀'*90]*3, state_age_s=10**30)
        payload = hrproto.detail('p'*15, fields)
        row = hrproto.decode(payload)[0]
        self.assertEqual(row[2], '2147483647')
        self.assertEqual([len(x) for x in row[3:9]], [60, 60, 225, 60, 60, 60])
        self.assertLessEqual(len(payload), hrproto.MAX_MESSAGE)
        self.assertEqual(len(row), 11)

    def test_text_fields_never_add_records_or_controls(self):
        row = hrproto.decode(hrproto.detail('p', dict(say='A\nB\tC\x00D\x7fE\x85F')))
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0][5], 'A B C D E F')
