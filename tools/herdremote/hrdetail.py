"""Small-screen agent activity, parsed from read-only recent terminal text.

No tools are executed here. Terminal/user/tool-output blocks are not assistant
messages. Unknown layouts use a deliberately conservative, grey fallback.
"""
import re
import unicodedata

import hrdetect

SPINNERS = '✻✽✶✳✢✦✧◐◑◒◓·*'
ANSI = re.compile(r'\x1b(?:\][\s\S]*?(?:\x07|\x1b\\)|'
                  r'[PX^_][\s\S]*?\x1b\\|\[[0-?]*[ -/]*[@-~]|[ -/]*[@-~])')
MARKER = re.compile(r'^\s{0,1}([●⏺•])\s+(.*)$')
SPINNER = re.compile(r'^\s{0,1}[' + re.escape(SPINNERS) + r'\u2800-\u28ff]\s+(.*)$')
TOOL = re.compile(r'^(?:Bash|Read|Edit|Write|Update|UpdateTodos|Search|Grep|Glob|'
                  r'WebFetch|WebSearch|Task|Agent|NotebookEdit|Tool|[\w.-]+__\w+)\(')
STEP = re.compile(r'^(?:Ran|Running|Explored|Edited|Read|Searched|Updated|Exited|'
                  r'Searched for|Listed|Wrote)\b')
SUMMARY = re.compile(r'^(?:Read|Ran|Running|Edited|Updated|Searched|Explored)\s+\d+\b')
BORDER = re.compile(r'^[\s─━═╌┄┈┉┊┋│┃║┌┐└┘├┤┬┴┼╭╮╰╯+|=_-]+$')
FOOTER = re.compile(
    r'(?i)(?:bypass permissions|accept[- ]edits|accept edits|for shortcuts|'
    r'esc to interrupt|↑/↓ navigate|press up to edit queued|'
    r'\b(?:gpt-[\w.-]+|claude[- ](?:opus|sonnet|haiku)|gemini \d)\b.*[·|]|'
    r'^(?:← for agents|\? for shortcuts|Tip:|\+ Show details|\+ \d+ lines|'
    r'Worked for |○ Thought for |▸ |/\w)|⚠.*warnings?)')
DURATION = re.compile(r'(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+(?:\.\d+)?)s)\b|'
                      r'(\d+)h(?:\s*(\d+)m)?|(?<!\w)(\d+)m\b|(?<!\d)(\d+):(\d{2})(?!\d)')


def strip_ansi(text):
    text = ANSI.sub('', text)
    # C1 variants of CSI/OSC also occur in terminal captures.
    text = re.sub(r'\x9b[0-?]*[ -/]*[@-~]|\x9d[^\x07\x9c]*(?:\x07|\x9c)', '', text)
    return text


def plain(text):
    return ' '.join(''.join(' ' if unicodedata.category(c) == 'Cc' else c
                            for c in text).split())


def clean_title(title):
    return re.sub(r'^[' + re.escape(SPINNERS) + r'\u2800-\u28ff]+\s*|\s*[' +
                  re.escape(SPINNERS) + r'\u2800-\u28ff]+$', '', plain(strip_ansi(title))).strip()


def seconds(text):
    match = DURATION.search(text)
    if not match:
        return -1
    h, m, s, h2, m2, m3, minutes, secs = match.groups()
    if minutes is not None:
        return int(minutes) * 60 + int(secs)
    return int(float(h or h2 or 0) * 3600 + float(m or m2 or m3 or 0) * 60 + float(s or 0))


def working(text):
    text = re.sub(r'\s*[·•]\s*esc to interrupt.*$', '', text, flags=re.I)
    text = re.sub(r'\s*\(?esc to interrupt\)?.*$', '', text, flags=re.I)
    text = re.sub(r'\s*\(ctrl\+o to expand\).*$', '', text, flags=re.I)
    return plain(text.replace('(', ' ').replace(')', ' '))


def chrome(line):
    s = line.strip()
    return (not s or bool(BORDER.fullmatch(s)) or bool(FOOTER.search(s)) or
            s.startswith(('─', '━', '═', '┌', '┐', '└', '╭', '╰', '⎿', '│', '├', '┬', '┴')))



def _cell_column_to_chars(line, column):
    """Fallback for cursor producers without col_chars; prefer col_chars."""
    cells = 0
    for index, char in enumerate(line):
        width = 0 if unicodedata.combining(char) or unicodedata.category(char) in ('Mn', 'Me', 'Cf') else (
            2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1)
        if cells + width > column:
            return index
        cells += width
        if cells > column:
            return index
    return len(line)


def extract_input(text, kind='unknown', terminal_cursor=None):
    """Extract input and its Unicode offset, without cropping/display choices.

    Cursor row is relative to returned text. col_chars, when present, is the
    Unicode scalar column; otherwise col counts display cells. Missing,
    invisible, malformed or off-input cursors use the input-end fallback.
    recent_unwrapped keeps terminal soft wrapping out of the logical input.
    """
    lines = strip_ansi(text).split('\n')
    markers = {'claude': '❯', 'codex': '›', 'agy': '>', 'grok': '>❯›'}.get(kind, '>❯›')
    cursor = terminal_cursor if isinstance(terminal_cursor, dict) else {}
    row = cursor.get('row')
    valid_cursor = (cursor.get('visible') is True and type(row) is int and 0 <= row < len(lines)
                    and type(cursor.get('col')) is int and cursor['col'] >= 0)
    if cursor.get('col_chars') is not None:
        valid_cursor = valid_cursor and type(cursor['col_chars']) is int and cursor['col_chars'] >= 0
    active_row = row if valid_cursor else None

    def content(line):
        cell = re.match(r'^\s*[│┃║](.*)[│┃║]\s*$', line)
        start, end = cell.span(1) if cell else (0, len(line))
        # One space next to each vertical edge is frame padding, not input.
        if cell:
            start += int(start < end and line[start] == ' ')
            end -= int(start < end and line[end - 1] == ' ')
        return line[start:end], start

    prompt = re.compile(r'^\s{0,2}([' + re.escape(markers) + r'])(?: |$)(.*)$')
    candidate = None
    for index, line in enumerate(lines):
        body, base = content(line)
        match = prompt.match(body)
        if match and not re.match(r'^/[\w-]+\s{2,}\S', match.group(2)):
            candidate = (index, match.group(2), base + match.start(2))
    empty = dict(input='', cursor=0, cursor_source='end')
    if candidate is None:
        return empty
    index, first, start = candidate
    boxed = (index > 0 and (lines[index - 1].lstrip().startswith(('─', '━', '═', '╭', '┌')) or
                            bool(BORDER.fullmatch(lines[index - 1].strip()))))
    parts = [first]
    spans = [(index, start, len(first))]
    for number in range(index + 1, len(lines)):
        line = lines[number]
        s = line.strip()
        if MARKER.match(line) or SPINNER.match(line) or s.startswith('○ Thought for '):
            return empty  # submitted prompt, followed by assistant activity
        if s and (BORDER.fullmatch(s) or s.startswith(('─', '━', '═', '╰', '└'))):
            break
        if (FOOTER.search(s) or re.search(r'(?i)^(?:esc to cancel|↑/↓|press up to edit|enter to)', s) or
                re.match(r'^\s{2}/[\w-]+\s{2,}\S', line)):
            break
        body, base = content(line)
        if not body.strip():
            parts.append('')
            spans.append((number, base + len(body), 0))
        elif body.startswith('  '):
            parts.append(body[2:])
            spans.append((number, base + 2, len(body) - 2))
        else:
            break
    if not boxed:
        while len(parts) > 1 and not parts[-1] and spans[-1][0] != active_row:
            parts.pop()  # footer padding; preserve a cursor's blank input row
            spans.pop()
    value = '\n'.join(parts)
    value = ''.join(c if c == '\n' or unicodedata.category(c) != 'Cc' else ' ' for c in value)
    if re.match(r'(?i)^(?:Ask Codex to do anything|Try(?:\s|$))', value.strip()):
        return empty
    offset, source = len(value), 'end'
    if valid_cursor:
        preceding = 0
        for input_row, column, length in spans:
            if input_row == active_row:
                chars = cursor.get('col_chars')
                if chars is None:
                    chars = _cell_column_to_chars(lines[input_row], cursor['col'])
                offset = preceding + max(0, min(length, chars - column))
                source = 'herdr'
                break
            preceding += length + 1  # one Unicode character for a hard newline
    return dict(input=value, cursor=offset, cursor_source=source)

def parse(text, kind='unknown', title='', status='unknown', state_age_s=-1, terminal_cursor=None):
    """Return task/now/say/steps plus age and internal state for change detection.

    Claude and agy captures share top-level black bullet records; Codex uses
    white bullets. agy also has braille working spinners. grok currently uses
    fallback because no real grok pane was present during fixture capture.
    """
    text = strip_ansi(text)
    title = clean_title(title)
    fields = dict(task=title, now='', say='', steps=[], state_age_s=state_age_s,
                  state=status)
    recognized_kind = kind in ('claude', 'codex', 'agy')
    message = []
    messages = []
    steps = []
    fallback = []
    lane = None
    live, live_age, done_age = '', -1, -1
    last_signal = None

    def finish():
        if message:
            messages.append(plain(' '.join(message)))
            message.clear()

    for line in text.splitlines():
        s = line.strip()
        marker = MARKER.match(line)
        spinner = SPINNER.match(line)
        # A whole prompt block, including wrapped input, ends only at a new
        # assistant/tool/activity record. Never recycle user input as SAY.
        if s.startswith(('›', '❯', '>')):
            finish()
            lane = 'prompt'
            continue
        if spinner:
            body = spinner.group(1)
            if (re.search(r'\bfor\s+\d.*\bdone\b', body) or
                    re.match(r'(?:Cogitated|Crunched|Brewed|Sautéed|Cooked|Worked) for \d', body)):
                finish()
                lane = None
                done = re.search(r'\bdone\s+(.*)', body)
                done_age = seconds(done.group(1)) if done else -1
                last_signal = 'done'
                continue
            if not chrome(body) or 'esc to interrupt' in body.lower():
                finish()
                live = working(body)
                live_age = seconds(body)
                last_signal = 'working'
                lane = None
                continue
        if marker:
            finish()
            body = marker.group(2).strip()
            if body.startswith('Worked for '):
                last_signal, lane = 'done', None
            elif re.match(r'^Working\b', body):
                live, live_age = working(body), seconds(body)
                last_signal, lane = 'working', None
            elif (TOOL.match(body) or SUMMARY.match(body) or
                  (kind == 'codex' and STEP.match(body)) or body.startswith('Background command ')):
                steps.append(re.sub(r'\s*\(ctrl\+o to expand\).*$', '', body))
                if not recognized_kind:
                    fallback.append(body)  # the tool record is not chrome/output
                lane = 'tool'
            elif recognized_kind:
                message.append(body)
                lane = 'message'
            else:
                fallback.append(body)
                lane = 'fallback'
            continue
        if SUMMARY.match(s):
            finish()
            steps.append(s)
            lane = None
            continue
        if lane in ('prompt', 'tool') or chrome(line):
            continue
        if lane == 'message':
            # Nested bullets/paragraphs belong to the same message. Preserve
            # the head; wire truncation supplies the final ellipsis.
            message.append(s)
        else:
            fallback.append(s)
    finish()

    question = hrdetect.find_question(text) if recognized_kind else None
    state = status if status in ('working', 'done', 'idle', 'blocked') else last_signal or status
    if recognized_kind and question:
        fields.update(now='? Asking', say=plain(' '.join(question['lines'])),
                      state='blocked', steps=steps[-3:],
                      state_age_s=state_age_s if status == 'blocked' else -1)
    elif recognized_kind and (messages or steps or live):
        fields['state'] = state
        fields['say'] = messages[-1] if messages else ''
        fields['steps'] = steps[-3:]
        if state == 'working':
            fields['now'] = live
            if live_age >= 0:
                fields['state_age_s'] = live_age
        elif state in ('done', 'idle'):
            fields['now'] = state
            if state == 'done' and last_signal == 'done' and done_age >= 0:
                fields['state_age_s'] = done_age
        elif state == 'blocked':
            fields['now'] = '? Asking'
    else:
        fields.update(say=plain(' '.join(fallback[-4:])), state_age_s=-2, state='fallback')
    fields.update(extract_input(text, kind, terminal_cursor))
    if not any((fields['say'], fields['now'], fields['steps'], fields['input'])):
        # Empty/chrome-only input clears the complete detail, including TASK.
        fields.update(task='', state_age_s=-1, state='unknown')
    return fields


def extract_custom_input(text, kind='unknown', terminal_cursor=None):
    """Current custom editor only: prompt input or a cursor in an inline Other row.

    A numbered choice is never echoed as user input. Cursor coordinates are
    still relative to the visible viewport; no scrollback is requested.
    """
    value = extract_input(text, kind, terminal_cursor)
    lines = strip_ansi(text).split('\n')
    cursor = terminal_cursor if isinstance(terminal_cursor, dict) else {}
    row = cursor.get('row')
    if cursor.get('visible') is True and type(row) is int and 0 <= row < len(lines):
        match = re.match(r'^\s*[❯›>]?\s*\d+\.\s*(?:\[[ x✔✓]\]\s*)?'
                         r'(?:Type something[.…]*|Other[.…]*)(?:\s*[:：]\s?| {2,})(.*)$',
                         lines[row], re.I)
        if match:
            col = cursor.get('col_chars')
            if type(col) is not int:
                col = _cell_column_to_chars(lines[row], cursor.get('col', 0))
            return dict(input=match[1], cursor=max(0, min(len(match[1]), col-match.start(1))),
                        cursor_source='herdr', active=True)
    if re.match(r'^\d+\.\s', value['input']):
        return dict(input='', cursor=0, cursor_source='end', active=False)
    # An empty normal prompt is active too; do not require typed text.
    active = any(re.match(r'^\s{0,2}[❯›>] (?!\d+\.\s)', line) for line in lines[-12:])
    return dict(value, active=active)
