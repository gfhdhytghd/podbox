# HerdRemote protocol, version 1

HerdRemote is what **System > Agents** speaks to `tools/herdremote/` on the
computer. It runs over a PodLink session: every message is one PodLink TEXT
frame. Read this to write another computer-side program for the screen, or
to change the messages.

## Framing

- A message is UTF-8 text of at most 4096 bytes.
- Records are separated by `\n`, fields by `\t`. The first field of the first
  record names the message.
- Field text never contains `\n`, `\t` or any other control character. The
  sender replaces them with spaces.
- Every text field has a byte budget (below). The sender cuts longer text on
  a UTF-8 character boundary. The player cuts again to its own field size,
  also on a boundary, so an over-long field is shortened, never rejected.
- A message the player does not recognise, or whose record has the wrong
  number of fields, is ignored whole. The screen's picture stays as it was.

`id`, `pane` and `ws` are herdr's own ids (`w1`, `w1:p2`), at most 15 bytes.
`req` is a request number the player chose, at most 7 bytes; a result
carries it back.

## Session

1. The player opens a PodLink session and sends `hr`.
2. The computer sends `HR` and then everything the screen lacks: `W` and
   `S`, every current `Q`, and `T`.
3. From then on the computer sends only changes, plus `T` every 2 seconds.
4. When the player sends `hr` again (it reopened the screen), the computer
   forgets what it sent and starts again at step 2.

A player that hears nothing for 6 seconds says so on screen. `T` is what
keeps an idle session from looking dead.

## Computer to player

### `HR` — hello

```
HR <version> <program>
```

`version` is `1`. A player that does not know the version ignores
everything else.

### `W` — workspaces

```
W <n>
<id> <f|-> <working> <waiting> <done> <idle> <label>     (n records)
```

All of herdr's workspaces, in the order the player lists them (see
[Order](#order)). `f` marks the one the computer
shows. The four counts are agents in all of that workspace's tabs by
status; `waiting` counts agents that herdr reports blocked or that show a
question. `label` is at most 40 bytes. At most 32 records.

### `S` — where in each workspace the agents are busy

```
S <n>
<id> <tab1> <other>     (n records)
```

Sent after every `W`, and again whenever it changes. `tab1` is the state of
the workspace's first tab and `other` that of all its other tabs, each the
most pressing of its agents' states, as one letter: `b` asking (a question,
or herdr's `blocked`), `w` working, `d` done, `i` idle, `u` unknown, `-` no
agents. Only first-tab panes are read for questions; in the other tabs
`b` is herdr's `blocked` alone. A record whose `id` is not in the last `W`
is ignored, and so is a letter not in this list.

A player that does not know `S` ignores it and works from `W`'s counts. A
player that gets no `S` takes `tab1` from the counts and `other` as `-`.

#### Status dots

The player shows each workspace's `S`, and each agent's status, as a dot.
The first match wins:

| State | Dot |
|---|---|
| `tab1` is `b`; an agent asking | red, blinking |
| `tab1` is `w`; an agent working | green, breathing |
| `other` is `b` | red, with a … badge |
| `other` is `w` | green, with a … badge |
| either is `d` | teal ● |
| either is `i` | green ○ |
| anything else | grey · |

The rest are herdr's own dot indicators (`status_indicators = "dots"`) in
its `catppuccin-latte` colours: blocked red `#d20f39`, done teal `#179299`,
idle green `#40a02b`, unknown grey `#9ca0b0`, working yellow `#df8e1d`.
Working and asking take green and red here instead, and move. Every dot has
a thin white edge, so it shows on any album's colours.

### `A` — agents of one workspace

```
A <ws> <n>
<pane> <kind> <status> <question> <name> <title> <last>  (n records)
```

Sent only for the workspace the player subscribed to with `sub`, and again
whenever it changes. Records come in the order the player lists them.

| Field | Budget | Meaning |
|---|---|---|
| `kind` | 11 | `claude`, `codex`, or what herdr calls the agent |
| `status` | 1 | `w` working, `b` blocked, `d` done, `i` idle, `u` unknown |
| `question` | 1 | `1` when the pane shows a question the player can answer |
| `name` | 31 | herdr's agent name, or empty |
| `title` | 60 | the pane's terminal title |
| `last` | 90 | the last line of activity above the agent's prompt |

At most 16 records.

### Order

The computer sends both lists in priority order, and the player shows them
as sent:

1. Agents that need a person: a question, or herdr's `blocked`. The oldest
   question comes first.
2. Agents that are working, in herdr's order.
3. All others, the most recent status change first. An agent with no
   recorded change keeps herdr's order, after the ones with one.

A workspace ranks as its best agent in any of its tabs; a workspace with
no agents goes with the last group. The order changes only when an agent's status changes. The
player keeps its selection on the same workspace or agent when the order
changes. Questions (`Q`) are sent oldest first.

### `Q` — a question

```
Q <qid> <pane> <ws> <m|-> <agent>
? <line>                                                 (0 to 4 records)
o <number> <label>                                       (1 to 8 records)
```

`qid` changes whenever the question on screen changes, including a ticked
box in a multi-select. A `Q` with a known `qid` replaces that question.
`m` marks a multi-select, which the player shows with a **Submit** row.
`agent` (40 bytes) names the asker, as `<workspace> · <kind>`. Question
lines are at most 120 bytes; option labels at most 90. `number` is what an
answer sends back, and is the number the agent's own dialog shows.

The player keeps at most 6 questions; a seventh pushes out the oldest.

### `X` — a question has gone

```
X <qid>
```

The question was answered, here or on the computer, or the agent moved on.

### `R` — result of a request

```
R <req> <ok|err> [<text>]
```

`text` (120 bytes) is what the player shows: `Focused <label>`,
`Answered`, `Updated` (a multi-select box changed), `Talking` (voice hold
started), `Sent` (voice hold released), or why it failed. `Sent` confirms
`HoldStop`, not completion of transcription or text commit.
Keyboard-tap success is exactly `R <req> ok` (three fields, no text).
The player handles that success silently. Keyboard errors retain a fourth
field containing the reason, which the player shows briefly.
For a local focus with no moved clients, the computer checks `client.list`.
A connected client with `origin: "local"` means an already-focused view and
returns success. Without a local client, the computer sends
`R <req> err No local herdr window`. The existing screen shows the reason
as a focus failure; no new message type is needed.

### `T` — tick

```
T <age> <herdr_ok>
```

`age` is how many seconds old the computer's picture of herdr is. `herdr_ok`
is `0` while herdr cannot be reached.

### `U`, `P`, `N` — subscription usage

Reply to `us` in one TEXT frame. Record/field separators are newline/tab,
as above. There are exactly four `P` rows in this order: `codex`, `claude`,
`grok`, `gemini`.

```
U <age> <ok>
P <svc> <state> <h5_pct> <h5_reset> <wk_pct> <wk_reset> <tokens> <usd_milli> <note>
P ...
P ...
P ...
N <tokens> <usd_milli>
```

- `age`: integer seconds since the oldest service sample. `ok` is `0` when
  every collector failed, otherwise `1`. This flag is independent of `T`'s
  herdr status.
- `state`: `ok` for limits and tokens, `tok` for tokens without limits,
  `none` for no service data, `err` for collector failure.
  Failed quota probes retain unexpired last-good windows as `ok`, with their
  original sample time and a `limits Xm old` note. Expired windows are omitted.
- `h5_pct`, `wk_pct`: used percent, integer 0–100; `-` for unavailable.
- `h5_reset`, `wk_reset`: integer seconds from **now** until reset, at
  least zero; `-` for unavailable. The iPod clock is not trusted.
- `tokens`: disjoint input, cached input, cache write, output and reasoning
  tokens in the service's current weekly window (weekly reset minus seven
  days). Where no current weekly window is known, use the last seven days.
  Cached input/reasoning that are subsets of other counters count once.
- `usd_milli`: equivalent API list price for those tokens, per model and
  token class, in 1/1000 USD. This is not subscription spend. Unknown-model
  tokens count with cost zero and a note.
- `note`: at most 40 UTF-8 bytes, sanitized like other text fields; can be
  empty. An error note explains failure. Incomplete cold indexing uses
  `ok`/`tok` with `indexing N%`; only real failures use `err`.
- `N`: sums of all four `P` rows, including any retained counters on error.

The whole message fits `MAX_MESSAGE` (4096 bytes). The computer answers
from cache immediately, then schedules a refresh when at least 20 seconds
old. Collectors never block the link thread. The first reply can be the
initial `tok/indexing 0%` cache, or the persisted snapshot after restart.
Claude quota requests have a separate shared 900s interval and 429 backoff;
20s Usage updates continue to refresh local token totals from the index.
Background indexing starts with the daemon, including while Usage is closed.
The next `us` receives the refreshed cache. The player sends `us` on Usage open and every 20 seconds while open;
it counts reset seconds down locally and stops requests on close. Link
down or `ok=0`: retain last data and mark it stale. See
[usage sources and caveats](herdremote-usage.md).

## Player to computer

| Message | Meaning |
|---|---|
| `hr 1` | Start, or restart, the session |
| `sub <ws>` | Send `A` for this workspace from now on; `sub -` stops it |
| `fw <req> <ws>` | Show this workspace, at its first tab, on the computer |
| `fp <req> <pane>` | Show this pane on the computer |
| `vd <req> <target>` | Start hold-to-talk; target is a pane id, `w:<ws>`, or `-` |
| `vu <req>` | Release hold-to-talk |
| `k <req> <key> [<n>]` | Tap `bs`, `left`, `right` or `enter` 1–32 times (default 1) at the computer's current keyboard focus |
| `a <req> <qid> <number>` | Choose option `number` of question `qid` |
| `us` | Send the cached `U/P/N` usage frame now; request a refresh when due |

For `fw` and `fp`, the computer probes herdr's `ping` capability
`client_targeted_focus` on connection, after a herdr reconnect, and before
focus. With the capability set to `true`, `fw` calls `client.focus` with
`workspace_id`, the first `tab_id` when present, and `clients: "local"`;
`fp` uses `pane_id` and `clients: "local"`. Remote clients keep their
workspace/tab views, but pane focus remains shared with clients already
viewing that tab. For `moved: []`, `client.list` distinguishes an
already-focused local client (success) from no local client (failure; see
`R`). Remote-only connections do not qualify. A failed `client.list`
returns an error without legacy fallback. Without the
capability, `fw` uses `workspace.focus` then `tab.focus` when present, and
`fp` uses `pane.focus`; these legacy calls move all clients. Probe and focus
errors return `R <req> err` without retrying through legacy focus calls.

`vd` defaults to `voice_focus=off`: like F23, it calls the fcitx voice
addon's `HoldStart` on the computer's session bus at the current cursor,
without moving herdr, regardless of the target. With `voice_focus=on`, it
focuses the target first; `w:<ws>` selects that workspace's first tab and
`-` keeps the current focused input.
Voice focus uses only `client.focus` with `clients: "local"`. A missing
capability, missing local window or failed focus returns `R <req> err`
without starting voice; voice never uses the legacy all-client fallback.
`voice_focus=off` skips focus. `voice_method=HoldStartRaw` selects raw text
without cleanup; the default `HoldStart` matches unmodified F23.

`vd` success is `R <req> ok Talking`. Missing addon, bridge or session bus,
and D-Bus failures, return `R <req> err Voice input not running`. A busy
voice session returns `Voice input busy` without claiming or stopping it.
The backend checks `Status` after starting; a silent refusal does not
report `Talking`. `vu` calls `HoldStop` for this session's hold and returns
`R <req> ok Sent`, or `err Voice input not running` on a D-Bus failure.
The microphone and committed text both belong to the computer.

Repeated `vd` while held neither refocuses nor extends the deadline.
Repeated/unmatched `vu` succeeds without touching an unrelated recording.
Link loss, `hr` session reset and daemon shutdown release an outstanding
hold. An independent watchdog also stops it after `voice_max_hold`
(default 120 seconds); a duplicate `vd` after timeout returns
`err Voice hold timed out` until `vu` or a session reset. Failed stops
retain ownership and retry once per second. A new hold cannot start
while a stop is pending. Each D-Bus call is bounded (2-second bus timeout,
3-second subprocess limit). The startup call and its confirming `Status`
hold the controller lock for at most two such calls; a very short configured
limit can expire during these calls. The watchdog then stops immediately
after that bounded sequence, with a separately bounded stop call. Stops
require a reachable bus/addon.

`vd` and `vu` each occupy exactly one record. Request ids are nonempty and
at most 7 bytes; targets are nonempty and at most 17 bytes (the `w:` prefix
plus a 15-byte workspace id). Control characters are rejected.

### Keyboard taps from Usage

In Usage, LEFT press/repeat sends `k <req> bs`, wheel counter-clockwise
sends `k <req> left`, clockwise sends `k <req> right`, and SELECT sends
`k <req> enter` once per press. Optional `n` batches consecutive taps of
the same key: `k <req> <key> [<n>]`. Missing `n` means 1; otherwise it must
be an unsigned decimal integer from 1 to 32. Noninteger or out-of-range
values return `R <req> err Invalid key count` without running a tool.
The whitelist is exactly `bs`, `left`, `right`, `enter`; any other key
returns `R <req> err Unsupported key`. Each command occupies one record
with three or four fields, a nonempty request id of at most 7 bytes, and
no control characters. Malformed frames are ignored as above. A command
before `hr` returns `err Session not ready` without running a tool.

These taps never focus herdr and never use `pane.send_keys`: they reach
the computer's current keyboard focus, like physical keys. The backend
prefers `ydotool key --key-delay=2` followed by all `n` down/up pairs
(codes 14, 105, 106, 28 respectively), falling back to wtype with `n`
`-k <symbol>` arguments (`BackSpace`, `Left`, `Right`, `Return`)
when ydotool or its socket is absent. `HERDREMOTE_KEY_TOOL`/`--key-tool`
select `auto` (default), `ydotool`, or `wtype`. Each batch is one tool call.
`key_scale` (`HERDREMOTE_KEY_SCALE`/`--key-scale`, integer 1–8, default 1)
multiplies `left`/`right` only, capped at 32 after scaling. `bs`/`enter`
use the original count unchanged.

The player keeps one `k` in flight and coalesces pending steps into ordered
same-key runs. There is no fixed 30-tap/second cooldown or scheduling expiry.
The next batch can start immediately after the previous reply. At most one
batch runs on the computer; an overlapping old-firmware request returns
`R <req> err Key busy` without queuing it. The player suppresses `Key busy`
and legacy `Key rate limit` band messages, while showing real failures.
Reset, link loss and shutdown invalidate unstarted batches and old replies.
A tool call already in flight can finish its batch, with each tap a complete
down/up pair. Its timeout is `0.5 + 0.01 * effective_count` seconds.
On a tool failure, release-only cleanup has a 0.5-second limit; the backend
never retries the batch via another tool because that could type or delete
twice. Tool errors are
`Key tool unavailable` or `Key tool failed`. A successful subprocess exit
confirms the tap request, not the contents of the focused input field.

The optional computer-to-player message `V <talk|idle>` is reserved for
recording signals. This backend does not emit `V`; it returns `R` after
checking the start state. An older player ignores voice commands/results
it does not recognise. No protocol version change is required.

`a` with `number` `0` presses a multi-select's **Submit**. An answer to a
`qid` that is no longer on screen fails with `R <req> err`, and no key
reaches the agent. A success comes only after the computer has seen the
pane change, which takes up to 5 seconds.
