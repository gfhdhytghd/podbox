# HerdRemote protocol, version 1

HerdRemote is what **System > Agents** speaks to `tools/herdremote/` on the
computer. It runs over a PodLink session: every message is one PodLink TEXT
frame. Read this to write another computer-side program for the screen, or
to change the messages.

## Framing

- A message is UTF-8 text bounded by the negotiated PodLink maximum:
  16384 bytes with current peers, or 4096 with a legacy peer.
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
agents. First tabs and the explicitly subscribed tab are read for questions; in
other tabs `b` is herdr's `blocked` alone (see Navigation v2). A record whose `id` is not in the last `W`
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

### `A` — agents of one workspace tab

```
A <ws> <n>
<pane> <kind> <status> <question> <name> <title> <last>  (n records)
```

Sent only for the workspace/tab the player subscribed to with `sub`, and
again whenever it changes. The header and agent rows retain their v1 shape.
Records come in priority order (see Order).

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

### Navigation v2 — PROTOCOL READY

This section is the frozen NAV2 contract (2026-11-06). Protocol version
remains 1. Unknown commands/messages and malformed frames are ignored.
Changes to this section after PROTOCOL READY require a `QUESTION:`.

#### `T` — subscribed workspace tabs (distinct from the tick)

One TEXT frame contains the complete tab list, one seven-field record per
tab, in herdr's tab order (never priority order):

```
T <ws> <n> <i> <tab_id> <label> <status>   (n records)
```

`n` is the number of transmitted tabs (0–32). `i` is the zero-based index
(0 through n-1). Every record repeats `ws` and `n`. `tab_id` has the same
15-byte budget as pane/workspace ids. `label` is at most 40 UTF-8 bytes;
use herdr's tab label, or the tab id when empty. `status` is the dominant
agent state: `b` > `w` > `d` > `i` > `u`, or `-` with no agents.
As with `S`, non-first/unsubscribed tabs use herdr's agent states alone;
the subscribed tab also uses detected questions. First tabs are always read.
Only the first 32 tabs are exposed. Tabs keep herdr's order even when the
workspace or agent lists reorder.

An empty or removed workspace sends one record with exactly these fields:
`T <ws> 0 0 - <empty-label> -` (the label field is empty).
Send the whole list on `sub` and whenever any tab id, order, label or state
changes. Send `T` before the corresponding `A`. A subscription without an
explicit tab follows the current first tab. An explicit missing/removed tab
produces an empty `A`, without falling back to a different tab. A removed
workspace produces empty `T` and `A`. `sub -` sends neither.

The heartbeat remains exactly `T <age> <herdr_ok>`: one three-field record.
Parsers distinguish it by field count. Existing firmware ignores the new
seven-field records and continues to receive the unchanged tick. `A` has
no tab field; the player clears its agent list on tab switch and applies
subsequent `A` to the current subscription. Wire messages are ordered.

#### `ft` — focus one tab

```
ft <req> <ws> <tab_id>
```

Validate that the tab belongs to this workspace in the current model.
A missing workspace/tab returns `R <req> err <reason>` without focus.
Use the same capability probe and local focus path as `fw`/`fp`:
`client.focus` with `workspace_id`, `tab_id`, `clients: "local"` when
supported. Without that capability, use `workspace.focus` followed by
`tab.focus`. Failed probes, local focus and local-client checks never
retry via legacy calls. `moved: []` uses the same `client.list` check.
Reply with `R <req> ok Focused <label>` or `R <req> err <reason>`.
The player handles successful `fw`/`fp`/`ft` silently; errors use the band.
Focus does not change subscriptions, and subscriptions never focus.

#### Typed detail — DETAIL READY 2 (Amendments 4/5)

Change note for nav2-detail: DESIGN READY was received for
`/home/wilf/data/PodJS/ipod/DETAIL-DESIGN.md`. That document, owned by
nav2-detail (Claude), is authoritative for fields, limits, truncation and
per-state content. This section replaces the Amendment 2 nine-field `D`.
No new presentation choices are made here. `det` and wire version 1 remain
unchanged. Changes after DETAIL READY 2 require a `QUESTION:`.

```
det <pane>
det -
D <pane> <age> <task> <now> <say> <step1> <step2> <step3> <input> <cursor>
```

`D` is exactly one record with **eleven tab-separated fields**, including
empty fields. All fields are present. `cursor` is `0` for empty input.

| Field | Unicode code points | UTF-8 bytes as sent |
|---|---:|---:|
| `pane` | — | 15 |
| `age`, `cursor` | decimal integers | 11 each |
| `task`, `now` | 80 each | 240 each |
| `say` | 300 | 900 |
| `step1..3` | 80 each | 240 each |
| `input`, 16384-byte negotiated cap | 4000 | 12000 |
| `input`, 4096-byte negotiated cap | 500 | 1500 |

The full budget, including tag and ten tabs, is 14148 bytes on a 16384-byte
link, or 3648 bytes on a 4096-byte link. Both character and byte limits
apply. Each byte limit measures the UTF-8 field **as sent**, including
escapes. A character means a decoded Unicode code point, not a byte,
UTF-16 unit or grapheme. Input's escaped newline/backslash each decode to
one character and `cursor` counts those decoded characters.

`task` is the terminal title with spinner glyphs and surrounding spaces
removed. `now` is the live working line without its leading spinner and
interrupt/shortcut hints (elapsed time and token counts stay); or exactly
`done`, `idle`, `? Asking` for those states. `say` is the head of the latest
agent-to-user message, or the question text without options. Steps are the
last zero to three tool/summary steps, oldest first, newest last, with
unused slots empty at the end. State is the agent's `A` state. The player
owns colours, wrapping, line priority, five-line input window and wording.

Non-input text is sanitized as in Framing. Shorten on a character boundary
to meet **both** limits; a cut nonempty field ends with `…`, included in its
budgets. Keep the head of SAY. No ANSI/control characters or literal tab
or newline are sent. The input field additionally preserves hard newlines
as the visible escape `\n`, and literal backslashes as `\\`. Escapes are
atomic: never cut inside an escape or UTF-8 character. A typed player
undoes only `\n` and `\\` after splitting the eleven wire fields.

Send input whole when it fits both limits. Otherwise use exactly the
cursor-centred window in DETAIL-DESIGN: end 100 characters after the
cursor (or at the end of the input, if nearer), and extend back as far as
both budgets allow. Replace an omitted prefix by one `…`, counted in both
budgets. `cursor` is adjusted to this sent text, including that leading
ellipsis; it is an integer from 0 through the decoded input length. There
is no additional suffix ellipsis on this input window.

Use optional `result.read.cursor {row, col, col_chars?, visible}` from
herdr. Rows are relative to the returned text; `col` counts terminal cells,
`col_chars` (preferred when present) counts Unicode scalar columns. On a
visible recognized input row, exclude the prompt and its padding when
mapping to the input offset. `recent_unwrapped` merges soft-wrapped rows;
only hard input line breaks count as newlines. Absent, invisible, invalid
or off-input metadata falls back to the input end. Placeholder hints such
as `Ask Codex to do anything` and `Try …` are empty input.

`age` is decimal seconds, capped at 2147483647; `-1` means unknown and `-2`
means fallback. Prefer explicit current-state elapsed time; a completed
work duration (`Cogitated for 29m`) alone is not a done age. Otherwise use
an observed state-change time; first observation and unknown transitions
across an outage have unknown age. Fallback has empty NOW/steps, SAY joins
the last four non-chrome lines, and input/cursor are retained when found.
An empty/missing/failed pane clears all text with age `-1` and cursor `0`.

Background reads use `pane.read source=recent_unwrapped`. Assistant
messages and input are extracted separately; prompts, borders, model bars,
shortcuts, warnings and tool output do not become SAY. No additional live
secret filter is added. Test captures must be reviewed/redacted.

Send only on text, cursor, state, pane or age-mode changes. Age increasing
alone does not force a frame; the player counts it locally. Ordinary polls
are at most once a second. After an accepted detail-pane `k` batch finishes,
send its `R` first, then schedule a fresh read after 75 ms (target about
100 ms to `D` on a responsive herdr). These key-triggered reads are the
exception to the ordinary one-second interval. Coalesce bursts, discard
pre-key read results, and keep model reads off the key/detail response path.

`det -`, session reset, link loss and shutdown invalidate old subscriptions
and results. Detail never focuses or injects keys by itself. No `D` before
`hr`. Size for the current session's negotiated maximum; an oversized `D`
is a daemon bug, not a player-side shortening rule.

Compatibility: a nine-field-only player rejects this eleven-field record.
Ship the matching firmware and daemon. The player rejects field counts
other than eleven and invalid age/cursor values. Other v1 messages retain
their existing shape. The daemon can still serve a 4096-byte negotiated
link using its smaller input budget.

`sub`, `ft` and `det` must each occupy exactly one record. Id/target fields
are nonempty, have at most 15 UTF-8 bytes and contain no controls; `ft`
request ids are nonempty and at most 7 bytes. `sub - <tab_id>` is malformed.
Malformed commands are ignored without calling herdr.

#### Player navigation and compatibility

The player sends wheel focus at once when no focus request is in flight.
While waiting for its matching `R`, it retains only the newest target and
sends that target after `R`. It suppresses focus while Play is held. Entering a Space sends `fw`; changing tabs sends `ft` immediately,
clamps at the ends, and selects the first agent without an extra `fp`.
Entering detail sends `fp` and waits for its matching `R ok` before any
`k`; an error prevents keys. Detail uses Usage's same single-in-flight key
queue. Backend `k` validation and desktop-key semantics are unchanged.

New question ids pop up oldest first from any view, deferred while Play is
held or a talk result is pending. Dismissal/answer restores the previous
view and selection. A dismissed id stays reachable through the alert row
but does not pop up again. Pop-up never focuses. `Q`/`X` shapes are unchanged.

Old daemons ignore `ft`, `det`, and the extended `sub`; pre-NAV2 firmware
ignores tab/detail frames. Raw-detail compatibility is specified above. Missing extensions leave the old first-tab `sub <ws>`,
focus, questions, Usage and heartbeat paths usable. The player must not
send detail keys until focus succeeds, regardless of daemon version.

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

The whole message fits the negotiated PodLink maximum (currently 16384 bytes;
4096 with a legacy peer). The computer answers
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
| `sub <ws> [<tab_id>]` | Send `T` tabs and `A` agents for this workspace/tab; omitted tab means first tab; `sub -` stops both |
| `ft <req> <ws> <tab_id>` | Focus this workspace tab on the computer |
| `det <pane>` / `det -` | Subscribe to one pane's `D` detail / stop detail |
| `fw <req> <ws>` | Show this workspace, at its first tab, on the computer |
| `fp <req> <pane>` | Show this pane on the computer |
| `vd <req> <target>` | Start hold-to-talk; target is a pane id, `w:<ws>`, or `-` |
| `vu <req>` | Release hold-to-talk |
| `k <req> <key> [<n>]` | Tap `bs`, `left`, `right`, `enter`, `wleft` or `wright` 1–32 times (default 1) at the computer's current keyboard focus |
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
`k <req> enter` once per press. Fast wheel movement uses `wleft` (Ctrl+Left)
or `wright` (Ctrl+Right), one word jump per tap. Optional `n` batches consecutive taps of
the same key: `k <req> <key> [<n>]`. Missing `n` means 1; otherwise it must
be an unsigned decimal integer from 1 to 32. Noninteger or out-of-range
values return `R <req> err Invalid key count` without running a tool.
The whitelist is exactly `bs`, `left`, `right`, `enter`, `wleft`, `wright`; any other key
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
Each word tap is `29:1 105:1 105:0 29:0` for ydotool (`106` for right),
or `-M ctrl -k Left -m ctrl` for wtype (`Right` for right). The entire
sequence repeats `n` times in that call, releasing Ctrl after each tap.
`key_scale` (`HERDREMOTE_KEY_SCALE`/`--key-scale`, integer 1–8, default 1)
multiplies `left`/`right` only, capped at 32 after scaling.
`bs`/`enter`/`wleft`/`wright` use the original count unchanged.
Older daemons reject word keys as `err Unsupported key`; the player
shows this real failure rather than suppressing it.

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
twice. For word keys, cleanup releases the arrow and Ctrl together:
ydotool `105:0 29:0` (`106` for right), or wtype `-p Left -m ctrl`
(`Right` for right). Tool errors are
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
