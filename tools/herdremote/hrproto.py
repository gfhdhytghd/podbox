"""HerdRemote protocol v1: the messages carried in PodLink TEXT frames.

One message is one PodLink TEXT frame of UTF-8. Records are separated by
'\\n' and fields by '\\t'; the first field of the first record names the
message. Field text never contains either separator or any other control
character: encode() replaces them with spaces.
docs/podbox/herdremote-protocol.md is the reference; tools/herdremote/tests
checks this module against it.
"""
import unicodedata

VERSION = 1
MAX_MESSAGE = 16384

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
MAX_TABS = 32
DETAIL_TEXT_CHARS = 80
DETAIL_SAY_CHARS = 300
DETAIL_TEXT_BYTES = 240
DETAIL_SAY_BYTES = 900
USAGE_SERVICES = ("codex", "claude", "grok", "gemini")
USAGE_NOTE_BYTES = 40

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


def encode(records, max_message=MAX_MESSAGE):
    """records: a list of field lists. Returns the frame payload as bytes."""
    payload = "\n".join("\t".join(fields) for fields in records).encode("utf-8")
    maximum = min(MAX_MESSAGE, max_message)
    if len(payload) > maximum:
        raise ValueError("HerdRemote message exceeds %d bytes" % maximum)
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


def status(items):
    """items: dicts with id, tab1 and other, each a status letter or "-".

    Sent after every W; a player that does not know it keeps W's counts.
    """
    rows = [[clean(w["id"], 15), w["tab1"][:1] or "-", w["other"][:1] or "-"]
            for w in items[:MAX_WORKSPACES]]
    return _fit(["S", str(len(rows))], rows)


def agents(workspace_id, items):
    """items: dicts with pane, kind, status, question, name, title, last."""
    rows = [[clean(a["pane"], 15), clean(a["kind"] or "?", 11),
             STATUS_CODES.get(a["status"], "u"),
             "1" if a["question"] else "0", clean(a.get("name") or "", 31),
             clean(a["title"], TITLE_BYTES), clean(a["last"], LAST_BYTES)]
            for a in items[:MAX_AGENTS]]
    return _fit(["A", clean(workspace_id, 15), str(len(rows))], rows)


def tabs(workspace_id, items):
    """A complete tab list; seven-field T records cannot be mistaken for ticks."""
    items = items[:MAX_TABS]
    wid = clean(workspace_id, 15)
    if not items:
        return encode([["T", wid, "0", "0", "-", "", "-"]])
    return encode([["T", wid, str(len(items)), str(i), clean(t["id"], 15),
                    clean(t["label"], LABEL_BYTES), t["status"]]
                   for i, t in enumerate(items)])


def detail_text(text, limit, byte_limit=None):
    text = " ".join("".join(" " if unicodedata.category(c) == "Cc" else c
                            for c in str(text)).split())
    byte_limit = byte_limit if byte_limit is not None else limit * 4
    if len(text) <= limit and len(text.encode("utf-8")) <= byte_limit:
        return text
    head = text[:max(0, limit - 1)].encode("utf-8")[:max(0, byte_limit - 3)]
    return head.decode("utf-8", errors="ignore") + "…"


def detail_input(text, cursor, char_limit, byte_limit):
    """The input window/escapes exactly as DETAIL-DESIGN.md specifies."""
    text = "".join(c if c == "\n" or unicodedata.category(c) != "Cc" else " " for c in str(text))
    cursor = max(0, min(len(text), cursor if type(cursor) is int else len(text)))

    def escape(value):
        return value.replace("\\", "\\\\").replace("\n", "\\n")

    costs = [0]
    for char in text:
        costs.append(costs[-1] + (2 if char in "\\\n" else len(char.encode("utf-8"))))
    if len(text) <= char_limit and costs[-1] <= byte_limit:
        return escape(text), cursor
    end = min(len(text), cursor + 100)
    if end <= char_limit and costs[end] <= byte_limit:
        return escape(text[:end]), cursor
    start = end
    while start > 0:
        candidate = start - 1
        if end - candidate + 1 > char_limit or costs[end] - costs[candidate] + 3 > byte_limit:
            break
        start = candidate
    value = "…" + text[start:end]
    return escape(value), max(0, min(len(value), cursor - start + 1))


def detail(pane, fields, max_message=MAX_MESSAGE):
    age = max(-2, min(2147483647, int(fields.get("state_age_s", -1))))
    steps = list(fields.get("steps", []))[-3:]
    steps += [""] * (3 - len(steps))
    large = max_message >= MAX_MESSAGE
    typed_input, cursor = detail_input(fields.get("input", ""), fields.get("cursor"),
                                       4000 if large else 500, 12000 if large else 1500)
    return encode([["D", clean(pane, 15), str(age),
                    detail_text(fields.get("task", ""), DETAIL_TEXT_CHARS, DETAIL_TEXT_BYTES),
                    detail_text(fields.get("now", ""), DETAIL_TEXT_CHARS, DETAIL_TEXT_BYTES),
                    detail_text(fields.get("say", ""), DETAIL_SAY_CHARS, DETAIL_SAY_BYTES)] +
                   [detail_text(s, DETAIL_TEXT_CHARS, DETAIL_TEXT_BYTES) for s in steps] +
                   [typed_input, str(cursor)]], max_message=max_message)


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


def result(req, ok, text=None):
    fields = ["R", clean(req, 7), "ok" if ok else "err"]
    if text is not None:
        fields.append(clean(text, 120))
    return encode([fields])


def tick(age_seconds, herdr_ok):
    return encode([["T", str(int(age_seconds)), "1" if herdr_ok else "0"]])


def usage(items, now):
    """One U frame, four P records, and N. Reset fields are absolute in cache."""
    by_service = {item["svc"]: item for item in items}
    rows = []
    total_tokens = total_usd = 0
    oldest = now
    any_success = False
    # Bound numeric fields as well as text, so all four rows always fit.
    def integer(value):
        return str(max(0, min(10**18 - 1, int(value or 0))))

    for service in USAGE_SERVICES:
        item = by_service.get(service, {})
        state = item.get("state", "none")
        if state not in ("ok", "tok", "none", "err"):
            state = "err"
        any_success |= state != "err"
        # Age is the row's refresh time. A limit sampled long ago stays
        # fresh here: Claude is probed every 15 minutes.
        oldest = min(oldest, item.get("updated_at", item.get("sampled_at", now)))
        fields = ["P", service, state]
        for prefix in ("h5", "wk"):
            pct = item.get(prefix + "_pct")
            reset = item.get(prefix + "_reset")
            fields += ["-" if pct is None else str(max(0, min(100, int(pct)))),
                       "-" if reset is None else integer(reset - now)]
        tokens = int(integer(item.get("tokens")))
        usd = int(integer(item.get("usd_milli")))
        total_tokens += tokens
        total_usd += usd
        rows.append(fields + [str(tokens), str(usd),
                              clean(item.get("note", ""), USAGE_NOTE_BYTES)])
    return encode([["U", integer(now - oldest), "1" if any_success else "0"]]
                  + rows + [["N", str(total_tokens), str(total_usd)]])


def parse_command(payload):
    """An iPod->PC message as (name, args), or None if it is not one."""
    try:
        records = decode(payload)
    except UnicodeDecodeError:
        return None
    fields = records[0]
    name, args = fields[0], fields[1:]
    shapes = {"hr": 1, "sub": 1, "fw": 2, "fp": 2, "a": 3, "us": 0,
              "vd": 2, "vu": 1, "k": 2, "ft": 3, "det": 1}
    if name in ("sub", "ft", "det"):
        valid_lengths = (1, 2) if name == "sub" else (shapes[name],)
        if len(records) != 1 or len(args) not in valid_lengths:
            return None
        for i, arg in enumerate(args):
            limit = 7 if name == "ft" and i == 0 else 15
            if not arg or len(arg.encode("utf-8")) > limit or any(
                    ord(c) < 32 or ord(c) == 127 for c in arg):
                return None
        if name == "sub" and len(args) == 2 and args[0] == "-":
            return None
        return name, args
    if name in ("us", "vd", "vu", "k") and len(records) != 1:
        return None
    if name in ("vd", "vu", "k") and (
            not args or not args[0] or len(args[0].encode("utf-8")) > 7 or
            any(ord(c) < 32 or ord(c) == 127 for a in args for c in a)):
        return None
    if name == "vd" and (len(args) != 2 or not args[1] or
                         len(args[1].encode("utf-8")) > 17 or
                         args[1] == "w:"):
        return None
    if shapes.get(name) != len(args) and not (name == "k" and len(args) == 3):
        return None
    return name, args
