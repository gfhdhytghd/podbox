"""Read an agent pane's visible text: the choice dialog it shows, if any, and
the line that best says what it is doing.

Every rule here was checked against panes captured from Claude Code 2.1 and
Codex CLI 0.166 (tests/samples). A dialog is recognised only when a numbered
or cursor-marked option list sits directly above one of the dialog footers
those programs print, so a numbered list in the transcript is never taken
for a question.
"""
import re
import zlib

CURSORS = "❯›>"
OPTION_RE = re.compile(r"^(\s*)([❯›>]\s*)?(\d{1,2})\.\s+(\S.*?)\s*$")
FOOTER_RE = re.compile(
    r"(?i)enter to (select|confirm|continue)|press enter to confirm"
    r"|esc to cancel|enter submit|enter continue|tab to amend"
    r"|↑/↓ to navigate")
SOLID_RULE_RE = re.compile(r"^\s*─{8,}")
DASHED_RULE_RE = re.compile(r"^\s*[╌┄]{8,}\s*$")
TRANSCRIPT_RE = re.compile(r"^[•●⏺✻✽✶✳·*]\s")
CHECKBOX_RE = re.compile(r"^\[( |✔|x|X|✓)\]\s*")
UNANSWERABLE_RE = re.compile(r"(?i)^(type something\.?|chat about this|other)$")
CODEX_QUEUED_RE = re.compile(r"shift\+← to answer")
NOISE_RE = re.compile(
    r"(?i)^(⎿\s*)?tip:|/effort\s*$|^※ recap|^\(?ctrl\+|queued follow-up"
    r"|shift\+← to answer|^\? \d+ questions?$|esc to interrupt\)?$|for shortcuts"
    r"|^✻ \S+ for \d|^worked for \d")


def _strip_marks(text):
    return re.sub(r"^[☐☒✔]\s*", "", text.strip())


def _question_lines(lines, top):
    """Up to six lines of the dialog above row index top, nearest kept."""
    found, blanks = [], 0
    for line in reversed(lines[:top]):
        text = line.strip()
        if not text:
            blanks += 1
            if blanks >= 3 and found:
                break
            continue
        blanks = 0
        if SOLID_RULE_RE.match(line) or TRANSCRIPT_RE.match(text):
            break
        if DASHED_RULE_RE.match(line) or text.startswith("←") or \
                text.lower().startswith("tip:"):
            continue
        found.append(_strip_marks(text))
        if len(found) == 6:
            break
    return list(reversed(found))


def _numbered_block(lines, footer):
    """The numbered option list ending above row footer, or None.

    Walks up from the footer to option 1. Blank lines and solid rules may
    sit between options (Claude rules off "Chat about this"); any other line
    must be an indented continuation of the option above it.
    """
    numbered = []
    i = footer - 1
    while i >= 0 and footer - i <= 40:
        line = lines[i]
        m = OPTION_RE.match(line)
        if m:
            numbered.append((i, m))
            if m.group(3) == "1":
                break
        elif line.strip() and not SOLID_RULE_RE.match(line) and \
                numbered and not line.startswith(" "):
            return None
        i -= 1
    if len(numbered) < 2 or numbered[-1][1].group(3) != "1":
        return None
    numbered.reverse()
    numbers = [int(m.group(3)) for _, m in numbered]
    if numbers != list(range(1, len(numbers) + 1)):
        return None
    options, cursor = [], None
    for idx, (row, m) in enumerate(numbered):
        nxt = numbered[idx + 1][0] if idx + 1 < len(numbered) else footer
        detail = [lines[r].strip() for r in range(row + 1, nxt)
                  if lines[r].strip() and not SOLID_RULE_RE.match(lines[r])
                  and not FOOTER_RE.search(lines[r])]
        options.append((int(m.group(3)), m.group(4), detail))
        if m.group(2):
            cursor = int(m.group(3))
    if cursor is None:
        return None
    return numbered[0][0], options, cursor


def _plain_block(lines, footer):
    """A cursor-marked option list without numbers (Claude's trust check)."""
    i = footer - 1
    while i >= 0 and not lines[i].strip():
        i -= 1
    rows = []
    while i >= 0 and lines[i].strip() and not SOLID_RULE_RE.match(lines[i]):
        rows.append(i)
        i -= 1
    rows.reverse()
    if len(rows) < 2:
        return None
    marked = [r for r in rows if lines[r].lstrip()[:1] in CURSORS]
    if len(marked) != 1:
        return None
    options = [(n + 1, lines[r].strip().lstrip(CURSORS).strip(), [])
               for n, r in enumerate(rows)]
    cursor = rows.index(marked[0]) + 1
    return rows[0], options, cursor


def find_question(text):
    """The open choice dialog in a pane's visible text, or None.

    Returns a dict: lines (question text), options [(number, label)],
    cursor (number under the cursor), multi (checkbox list), method
    ('number', 'arrows' or 'codex-queued') and rows (every row number in
    screen order, answerable or not, for arrow arithmetic).
    """
    lines = text.rstrip("\n").split("\n")
    nonblank = [i for i, line in enumerate(lines) if line.strip()]
    tail = nonblank[-8:]
    footer = next((i for i in reversed(tail) if FOOTER_RE.search(lines[i])),
                  None)
    if footer is None and nonblank and OPTION_RE.match(lines[nonblank[-1]]):
        # Claude's answer review page ends at its last option, unfootered.
        footer = nonblank[-1] + 1
        lines.append("")
    if footer is None:
        return _codex_queued(lines, nonblank)
    block = _numbered_block(lines, footer)
    method = "number"
    if block is None:
        block = _plain_block(lines, footer)
        method = "arrows"
    if block is None:
        return None
    top, options, cursor = block
    question = _question_lines(lines, top)
    if any("Ready to submit your answers?" in line for line in question):
        # Claude's review page ignores number keys.
        method = "arrows"
    multi = any(CHECKBOX_RE.match(label) for _, label, _ in options)
    submit = next((n for n, _, detail in options
                   if detail and detail[-1] == "Submit"), None)
    shown = []
    for n, label, detail in options:
        label = CHECKBOX_RE.sub(lambda m: "[x] " if m.group(1) != " " else
                                "[ ] ", label)
        bare = CHECKBOX_RE.sub("", label).strip()
        if UNANSWERABLE_RE.match(bare):
            continue
        detail = " ".join(detail)
        if detail and detail != bare:
            label = label + " — " + detail
        shown.append((n, label))
    if not shown:
        return None
    return dict(lines=question, options=shown, cursor=cursor,
                multi=bool(multi and submit), submit=submit, method=method,
                rows=[n for n, _, _ in options])


def _codex_queued(lines, nonblank):
    """Codex parks a request_user_input question behind shift+←."""
    tail = nonblank[-12:]
    if not any(CODEX_QUEUED_RE.search(lines[i]) for i in tail):
        return None
    for start in reversed(range(len(lines))):
        if lines[start].startswith("? "):
            break
    else:
        return None
    options = []
    for line in lines[start + 1:]:
        m = re.match(r"^\s+•\s+(\S.*?)\s*$", line)
        if not m:
            if options or line.strip():
                break
            continue
        options.append((len(options) + 1, m.group(1)))
    if len(options) < 2:
        return None
    return dict(lines=[lines[start][2:].strip()], options=options, cursor=1,
                multi=False, submit=None, method="codex-queued",
                rows=[n for n, _ in options])


def question_id(pane, q):
    key = "\x1f".join([pane] + q["lines"] +
                      ["%d:%s" % o for o in q["options"]])
    return "%08x" % zlib.crc32(key.encode("utf-8"))


def answer_keys(q, number):
    """Keys that pick option number in dialog q (a find_question result).

    number 0 is the multi-select dialog's Submit row, which Claude draws
    under the option whose number q["submit"] holds.
    """
    if q["method"] == "codex-queued":
        return None  # two steps; see Daemon.answer
    if number == 0:
        if not q["multi"]:
            raise ValueError("submit is only for a multi-select question")
        return ["down"] * (q["submit"] - q["cursor"] + 1) + ["enter"]
    if number not in q["rows"]:
        raise ValueError("no option %d" % number)
    if q["method"] == "number":
        return [str(number)]
    delta = q["rows"].index(number) - q["rows"].index(q["cursor"])
    return ["down" if delta > 0 else "up"] * abs(delta) + ["enter"]


def last_activity(text):
    """The most telling recent line: the newest content above the input box."""
    lines = [line.rstrip() for line in text.split("\n")]
    nonblank = [i for i, line in enumerate(lines) if line.strip()]
    end = len(lines)
    # The input box is the last prompt line ('❯' or '›') near the bottom.
    for i in reversed(nonblank[-8:]):
        if lines[i].strip()[:1] in "❯›" and not OPTION_RE.match(lines[i]):
            end = i
            break
    for line in reversed(lines[:end]):
        text = line.strip()
        if not text or SOLID_RULE_RE.match(line) or DASHED_RULE_RE.match(line):
            continue
        if NOISE_RE.search(text):
            continue
        text = re.sub(r"^[•●⏺✻✽✶✳·*⎿└]\s*", "", text)
        text = re.sub(r"^[◐◑◒◓⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]\s*", "", text)
        if text:
            return text
    return ""
