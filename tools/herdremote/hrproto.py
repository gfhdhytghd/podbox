"""HerdRemote protocol v1: the messages carried in PodLink TEXT frames.

One message is one PodLink TEXT frame of UTF-8. Records are separated by
'\\n' and fields by '\\t'; the first field of the first record names the
message. Field text never contains either separator or any other control
character: encode() replaces them with spaces.
docs/podbox/herdremote-protocol.md is the reference; tools/herdremote/tests
checks this module against it.
"""

VERSION = 1
MAX_MESSAGE = 4096

# Field budgets in UTF-8 bytes. They follow what the iPod screen stores and
# shows, so cutting here never loses text the player could have drawn.
LABEL_BYTES = 40
TITLE_BYTES = 60
LAST_BYTES = 90
QUESTION_BYTES = 120
QUESTION_LINES = 4
OPTION_BYTES = 90
MAX_OPTIONS = 8
MAX_WORKSPACES = 32
MAX_AGENTS = 16
MAX_QUESTIONS = 6

STATUS_CODES = {"working": "w", "blocked": "b", "done": "d", "idle": "i",
                "unknown": "u"}


def clean(text, limit):
    """Replace control characters and cut to limit bytes on a UTF-8 boundary."""
    text = "".join(" " if ord(c) < 32 or ord(c) == 127 else c for c in str(text))
    text = " ".join(text.split())
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    return data[:limit].decode("utf-8", errors="ignore")


def encode(records):
    """records: a list of field lists. Returns the frame payload as bytes."""
    payload = "\n".join("\t".join(fields) for fields in records).encode("utf-8")
    if len(payload) > MAX_MESSAGE:
        raise ValueError("HerdRemote message exceeds %d bytes" % MAX_MESSAGE)
    return payload


def decode(payload):
    text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    return [line.split("\t") for line in text.split("\n")]


def _fit(head, rows):
    """Drop trailing rows until head + rows fits one frame."""
    while True:
        try:
            return encode([head] + rows)
        except ValueError:
            if not rows:
                raise
            rows = rows[:-1]
            head = head[:-1] + [str(len(rows))]


def hello(daemon_version):
    return encode([["HR", str(VERSION), clean(daemon_version, 32)]])


def workspaces(items):
    """items: dicts with id, label, focused, working, waiting, done, idle."""
    rows = [[clean(w["id"], 15), "f" if w["focused"] else "-",
             str(w["working"]), str(w["waiting"]), str(w["done"]),
             str(w["idle"]), clean(w["label"], LABEL_BYTES)]
            for w in items[:MAX_WORKSPACES]]
    return _fit(["W", str(len(rows))], rows)


def agents(workspace_id, items):
    """items: dicts with pane, kind, status, question, name, title, last."""
    rows = [[clean(a["pane"], 15), clean(a["kind"] or "?", 11),
             STATUS_CODES.get(a["status"], "u"),
             "1" if a["question"] else "0", clean(a.get("name") or "", 31),
             clean(a["title"], TITLE_BYTES), clean(a["last"], LAST_BYTES)]
            for a in items[:MAX_AGENTS]]
    return _fit(["A", clean(workspace_id, 15), str(len(rows))], rows)


def question(q):
    """q: dict with id, pane, workspace, multi, agent, lines, options.

    options are (number, label) pairs; number is what the answer message
    sends back.
    """
    rows = [["?", clean(line, QUESTION_BYTES)]
            for line in q["lines"][-QUESTION_LINES:] if clean(line, 8)]
    rows += [["o", str(n), clean(label, OPTION_BYTES)]
             for n, label in q["options"][:MAX_OPTIONS]]
    head = ["Q", q["id"], clean(q["pane"], 15), clean(q["workspace"], 15),
            "m" if q["multi"] else "-", clean(q["agent"], LABEL_BYTES)]
    return encode([head] + rows)


def question_gone(qid):
    return encode([["X", qid]])


def result(req, ok, text):
    return encode([["R", clean(req, 7), "ok" if ok else "err",
                    clean(text, 120)]])


def tick(age_seconds, herdr_ok):
    return encode([["T", str(int(age_seconds)), "1" if herdr_ok else "0"]])


def parse_command(payload):
    """An iPod->PC message as (name, args), or None if it is not one."""
    try:
        records = decode(payload)
    except UnicodeDecodeError:
        return None
    fields = records[0]
    name, args = fields[0], fields[1:]
    shapes = {"hr": 1, "sub": 1, "fw": 2, "fp": 2, "a": 3}
    if shapes.get(name) != len(args):
        return None
    return name, args
