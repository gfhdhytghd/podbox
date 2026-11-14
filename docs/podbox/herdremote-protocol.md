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

`D` is one record with eleven legacy tab-separated fields, including empty
fields. Resident firmware additionally accepts a twelfth field, `revision`
(1–16 lowercase hexadecimal characters). Send it only after receiving the
resident `PLFG` notice below. `cursor` is `0` for empty input.

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
other than eleven (or twelve with a valid resident revision), and invalid
age/cursor values. Without `PLFG`, the daemon sends the legacy eleven fields. Other v1 messages retain
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
| `vc <req>` | Discard this session's voice hold without committing text |
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

`vc` checks that the current addon token is still this session's recording,
calls `Cancel`, then `HoldStop` to reset the addon's held-key state. Success
is `R <req> ok Discarded`. Cancel clears the addon session and the deferred
voice bridge commits no text for its `Cancelled` event. Unmatched/repeated
`vc` never cancels an unowned recording. A failed Cancel is retried without
calling HoldStop first, so a failed discard cannot become a submission.
After Cancel succeeds, only a failed HoldStop is retried. A new hold waits
until that reset completes. Ordinary link loss/reset/shutdown retain their
existing release behavior; a discard already pending keeps its discard intent.

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

`vd`, `vu` and `vc` each occupy exactly one record. Request ids are nonempty and
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

## Resident foreground notice and content revisions (Follow-up 8)

An ordinary PodLink PING payload is `PLFG` plus one byte: `0` background,
`1` Agents, `2` another foreground client. The receiver still replies with
an ordinary PONG. Old hosts ignore the payload and keep their existing
behavior. New hosts learn resident/revision capability from the notice;
old firmware sends no notice and continues to receive eleven-field D.

A foreground change does not send OPEN, HELLO, CLOSE, `hr`, `sub` or `det`.
The resident session, subscriptions and sent-content fingerprints survive.
Background Agents receives changed list/status/question frames and heartbeat
only. Another foreground client suppresses Agents pushes. Detail queries
and D frames resume only for foreground Agents, with only changed content.
The optional D revision identifies the rendered message/item state, not
elapsed seconds. Identical content and age-only updates do not redraw the
player. Scrolled-up content retains its offset; the bottom follows new
content. A link generation change uses the existing fresh-fetch path.

Codex uses read-only `thread/items/list` on the existing app-server daemon,
limited to five recent items by `agent_session` UUID. It sends the latest
message's last five lines, not a history. Claude uses its read-only transcript.
No thread resume, turn, lifecycle or input method is called. Unavailable
sources fall back to one visible-viewport herdr read with no line count.
The adapter refuses scrollback and line-count requests before socket I/O.

Deploy the matching `herdremote.py`, `hrappserver.py`, `hrsession.py`,
`hrherdr.py`, `hrproto.py`, sibling `tools/podlink.py` and Python dependencies.
This extension supersedes the older source/window and full-refresh-on-return
rules while retaining the v1 voice, focus, key and result semantics.

## Streaming detail stability (Follow-up 9)

The optional 16-hex Codex revision has an eight-hex message-identity prefix
and eight-hex visible-content suffix. Other sources may retain opaque
revisions. Players/hosts still accept old revisions; no frame field or
protocol version changes. Revision-only and elapsed-second changes do not
trigger redraw. A known/unknown/fallback age-mode change does.

The host coalesces message/tool changes to at most one D per second. A
pending candidate is replaced by the newest candidate and discarded on
pane/session/visibility changes. Input/cursor-only changes retain the fast
key-read path. D logs expose only metadata (time, pane, revision, byte length,
changed field names): INFO at most once/second and DEBUG for every send.

The player retains at most 4096 message bytes, 128 fixed line boundaries,
the previous bounded wire window and three tool rows (6288 host-test bytes,
ARM size measured in the report). Streaming suffix overlap appends without
reflowing closed lines. A scrolled-up view freezes that snapshot and its
body geometry until the reader reaches the bottom. Tools occupy spare rows
and never contribute to message scroll maximum. Cached return still needs
zero host round trips for its first frame.

## Out-of-band remote log (Follow-up 13)

An optional device-to-host TEXT payload starts with the four bytes `LOG `.
The remaining bytes are one diagnostic line, without a terminal LF/NUL.
The maximum line is 767 bytes including the prefix. This is not an HR
application command and has no application reply or acknowledgement.
There is no version/handshake change. It can arrive with foreground 0, 1 or
2, including before any HR greeting. The resident cable owner sends it;
enabling logging does not acquire/release a client or reconnect.

Examples: `LOG 42 tick=123 +0.12s flags=0 L2/00 len=4 rx=4 stored=4
02 00 01 00  PLAY` (one payload), `LOG VOL 9 tick=123 previous=03
target=06 dir=1 decision=P`, `LOG HELD tick=123 PLAY`,
`LOG PLAY tick=153 hold context=1`, `LOG EP0 tick=123 SELECT down`,
`LOG PAGE tick=123 up button=06010000`, and `LOG LOST 7`.
Play context values are 0=other, 1=Agents, 2=Now Playing. VOL decisions
retain their existing S/P/R/E meanings (suppressed/press/repeat/emitted).

The Debug switch defaults OFF in RAM. Its bounded, allocation-free producer
ring drops new records and saturates the loss counter. LOST notices alternate
with retained records under continuous overflow, so neither starves. Only
the resident worker formats records and attempts nonblocking TEXT sends.
Peers negotiating below a complete line receive `LOG LOST 1` instead; below
10 bytes the worker retains the bounded queue until it can send a notice.
Disconnects are best-effort, not lossless delivery. Existing file logging
has its own history/counters and is unchanged.

HerdRemote recognizes this prefix before HR parsing. Its bounded disk worker
appends UTC wall and monotonic receipt times to
`~/.local/state/podbox/remote-live.log`, rotating at 4 MiB with one `.1`
backup. Payload control characters are escaped. Old hosts safely ignore
unknown TEXT with no action/reply; old firmware emits none. Deploy the
matching `hrremotelog.py` with `herdremote.py` and the existing runtime files.

## Volume source diagnostics (Follow-up 14)

The existing LOG TEXT channel carries `LOG KEY tick=... source=PC|DOCK
key=VOL+|VOL- decision=used|fallback|dropped-dup press=N pc_tick=P dock_tick=D`.
P/D use the iPod clock (HZ=100): the EP0 event tick and matching L3/0E
handler tick. Missing counterparts are `-`, not zero. The Dock press/group
number binds paired rows; an unmatched PC event initially uses group 0.
Matched-group anchors are retained on repeat rows; `tick` remains the row's
own event/decision tick. PC-first and Dock-first matches both expose P/D;
a fallback retains its original Dock event tick. These are the timestamps
used by the named, user-accepted 80 ms window. No host-time subtraction or
cross-session alignment is required for later calibration. EP0 volume reports identify
`frame`/`repeat`; heartbeat reports identify `alive`. Raw serial frames and
VOL traces identify DOCK, and PAGE emits identify the selected PC/DOCK
source. Outside-Agents/stale PC volume reports are logged as ignored.
HerdRemote routes these through its existing log writer, without a command,
voice or tty ownership change. See `tools/usb_remote_key/README.md` for EP0
IDs 3/4 (volume steps), 5 (receiver heartbeat), fallback timing and matching
bridge/firmware deployment. Left/Right/Play remain Dock-only.

## Connected Agents volume policy (Follow-up 15)

This supersedes Follow-up 14's volume arbitration, including Usage.
While `computer_connected(now)` is true, Agents discards every Dock L3/0E
volume request with `decision=ignored-pc-connected` in both KEY and VOL
records. Only PC EP0 reports drive page steps. S2 below supersedes the initial
Usage audio mapping: connected Usage has no local page/audio effect. No WAIT, 80 ms matching, per-press fallback or Dock repeat runs in
this mode. PC reports remain FIFO and retain their existing source/tick logs.
The old paired fields are now independent event fields, not matched anchors.

The connection predicate is a current configured resident PodLink host
session (2AA profile + negotiated OPEN, regardless of foreground client),
OR the existing fresh receiver heartbeat. The resident snapshot is IRQ-safe
and rejects disconnected/reconfigured USB stream generations immediately.
Heartbeat freshness remains three seconds. With both signals absent, FU13
Dock paging and Usage's real-volume setter operate unchanged. Outside Agents
behavior remains unchanged. S2 below adds an explicit view lease and host IPC.

## Agents MENU exit and USB consent (Follow-up 15 S1)

A confirmed MENU exit from Agents reopens the ordinary USB popup on a
configured computer connection: SELECT enters Disk Mode; MENU keeps charge
only. The existing Agents leave-confirmation remains. Wheel MENU and the
forwarded remote Menu use the same exit path. Remote Play double-tap and
SELECT+PLAY use the music-switch result and do not reopen the popup.

Reopening or cancelling this popup does not detach USB, reset the resident
PodLink session, or invalidate the cached Agents view. Selecting Disk Mode
uses the existing exclusive-storage transition. Unplugged, charger-only,
stale cable-epoch, accessory and already-prompting cases do not reopen it.
FU8 made client close leave the resident USB profile intact; that removed
the former close/re-enumeration prompt. S1 explicitly re-arms consent only
after a MENU exit, without relying on a transport reset.

## Usage remote arrow keys (Follow-up 15 S2)

This replaces the initial FU15 Usage audio-volume decision. In connected
Usage, neither Dock volume nor PC EP0 volume changes iPod audio or pages.
PC EP0 reports in other Agents views still page one step each. With neither
computer signal, Usage retains its original Dock audio-volume setter.

The player sends `vw\t1` (Usage) or `vw\t0` (any other Agents view), as a
single PodLink TEXT frame. A direct, nonqueued refresh runs every 250 ms;
a view change sends immediately on the next UI loop and exit sends 0.
Failed sends retry without enqueueing old leases. HerdRemote accepts only
those two exact records, requires an introduced open Agents session, and
invalidates on reset, foreground loss, link close or shutdown. Older hosts
ignore vw; old firmware sends no lease, so S2 stays inactive.

HerdRemote owns `$XDG_RUNTIME_DIR/podbox-usage-keys/` (0700; default root
`/run/user/<uid>`). It atomically publishes `view.json`: version 1, boolean
usage, random per-view/session token and monotonic expiry, 750 ms after the
last valid view record. `keys.sock` is a nonblocking Unix datagram endpoint,
0600. An exclusive owner.lock prevents a second daemon from removing an
active endpoint. The bridge reads this state for each counted volume
report, and sends nothing when absent, malformed, false or expired. It sends
`UK1 <token> <monotonic-send-time> <left|right|up|down> <count>` to the socket.
HerdRemote rechecks token, open session, view expiry and report age under
100 ms, including immediately before a deferred key worker starts. Buffered
old datagrams are discarded; no command queue/retry or stale replay exists.
Lost view-leave messages expire within 750 ms; detected link loss disables
immediately. A stopped/hung daemon cannot turn a fresh state file into keys.

Each full volume frame starts a new hold and sends exactly one horizontal
tap. The first REPEAT is skipped (`VOL_IGNORE_FIRST_REPEATS = 1`). Every
later associated REPEAT produces one batch by age from the full frame's
first IR edge. Final named thresholds in usb_remote_key.py are:

| Hold age | Batch per counted report | Constant starting tier |
| --- | --- | --- |
| <0.5 s | 1 Left/Right | initial |
| 0.5–1.0 s | 3 Left/Right | USAGE_THREE_CHARS_SECONDS = 0.5 |
| 1.0–4.0 s | 10 Left/Right | USAGE_TEN_CHARS_SECONDS = 1.0 |
| 4.0–7.0 s | 1 Up/Down | USAGE_VERTICAL_SECONDS = 4.0 |
| >=7.0 s | 3 Up/Down (cap) | USAGE_THREE_LINES_SECONDS = 7.0 |

Vol+ selects Left/Up; Vol- selects Right/Down. A new full frame resets age
and repeat skip even during rapid taps. Silence/orphan repeats do nothing.
The same KeyTaps/KeyTool worker as Usage k performs one tool call per
accepted batch at the current desktop focus (ydotool, wtype fallback).
These batches bypass --key-scale to preserve exact counts. No herdr focus
or pane.send_keys call is involved. A busy worker drops new batches; it
never queues them. A running bounded tool call may finish after release;
an unstarted worker expires before the 150 ms IR release timeout. Error
cleanup sends releases only, with no retry of the batch.

Host files to deploy together, from this ZIP:
- tools/usb_remote_key/usb_remote_key.py and its README.md;
- tools/herdremote/herdremote.py, hrproto.py, hrkeys.py, new hrusagekeys.py,
  and README.md;
- docs/herdremote-protocol.md (this document).

The existing remaining HerdRemote siblings, podlink.py, units, requirements
and udev rules remain compatible and are bundled. The IR serial open remains
O_RDONLY. No installation, key injection, device access or service operation
is performed by the offline build/tests.

## Detail host keys and Cover Flow gestures (Follow-up 16)

T1 extends the FU15 host-arrow lease to detail. The player sends
`vw\tdetail\t<pane-id>` only after that detail's pane-focus reply succeeds;
focus pending/failed, another view and exit send `vw\t0`. Usage still sends
`vw\t1`. Refresh/expiry/report bounds remain 250/750/100 ms. HerdRemote
also requires a successful fp for the currently subscribed detail pane,
invalidates on focus attempts, pane changes and session resets, and rechecks
this authorization before starting a batch. Keys use the same current-focus
backend and exact 0.5/1/4/7 s Usage acceleration, with no herdr send_keys.

The player sends legacy vw=0 before entering/changing detail, revoking an
earlier Usage lease even on an old daemon. The new detail record is
deliberately rejected by FU15 daemons. Detail
view.json uses version 2, view=detail, pane=<id>, usage=false. FU15 bridges
reject it; the new bridge handles it and still accepts version 1 Usage.
The token rotates between Usage/detail/panes, so queued reports cannot cross
a view/focus boundary. Old firmware still supplies Usage only. Connected
detail ignores PC EP0 volume locally and never pages; disconnected detail
retains FU13 Dock paging. List one-item steps remain as installed.

T2' replaces the proposed three-screen cycle. Remote Play double-tap keeps
Agents <-> Now Playing. Three short taps, each next press beginning within
200 ms of the previous release, toggle Cover Flow. Triple in Cover Flow
returns to its caller: cached Agents view/selection after normal client
cleanup/reopen, or the existing WPS album-modal helper's Now Playing frame.
Other action-based screens and raw viewers suspend their caller as well.
Both music switches leave Agents silently without USB consent; MENU exit
still uses S1's popup. SELECT+PLAY retains its existing Agents/WPS toggle
and open-Agents shortcut elsewhere.

A second short release waits for the third-tap window before firing double.
That adds a 200 ms classification window; at HZ=100 the strict inclusive
boundary commits at the first tick after it (210 ms), plus scheduling.
The input timeout is bounded by that deadline, rather than adding another
100 ms poll interval. A third short release commits triple immediately.
Single taps keep their meanings except T3: in ACTIVITY_ALBUMCOVERS a remote
Play tap emits the same native SELECT press/release as the wheel. Double
there opens Agents, and triple returns to the origin. The existing 600 ms
remote Play hold there still opens Now Playing; native Play hold still
performs the carousel's original random-spin action. Agents' 300 ms remote
hold and native 150 ms recording classification are unchanged. LOG PLAY now
also reports triple with its context; older record-kind numbers are retained.

Deploy matching usb_remote_key.py, herdremote.py, hrproto.py and
hrusagekeys.py, with the two READMEs and this protocol document. The existing
hrkeys.py backend and other runtime siblings remain compatible and bundled.
No serial-write, service-operation or installation step is part of this
build-only package. All hardware/desktop gesture delivery requires separately
authorized acceptance; local tests use mocked keys and private IPC endpoints.

## Native Cover Flow chord and real Dock replay (Follow-up 17)

SELECT+MENU on the iPod body invokes the same Cover Flow toggle as remote
Play triple-tap in Agents, Now Playing and Cover Flow. Both native buttons
down fire once. The chord consumes their repeats and both releases, even
if the latest held sample is already zero while releases remain queued.
Cover Flow returns to its caller/origin. Agents exits silently with its
cache and resident PodLink preserved; ordinary MENU exit keeps S1 consent.
The original SELECT+PLAY chord implementation and remote tap decoder are
unchanged. A three-key combination retains SELECT+PLAY precedence.

Use a short SELECT+MENU press. Holding it roughly six seconds causes the
hardware reset; holding through the bootloader for roughly twelve seconds
can enter DFU. Firmware cannot inhibit either hardware action. Software
repeat suppression does not protect against those actions.

The reported failed triple was running the older 64bf49d6bf image, per the
user's version/uptime correction. Exact captured RAW/HELD timelines from
remote-live.log, including 130 ms taps, fast gaps and the captured third
pulse, pass the FU16 decoder in offline replay. It emits one triple with
no preceding double. Thresholds and remote behavior therefore remain as
FU16: 200 ms inclusive inter-tap window; double commits only after the
third-tap window expires. No merged third press is present in those traces.
This is replay evidence, not physical acceptance of a rebooted FU16 image.

No host runtime or wire-protocol update is required. Continue using the
matching FU16 bridge and HerdRemote files. This firmware-only package omits
unchanged host runtimes and includes this updated protocol/control document.

## Remote-entry WPS MENU (Follow-up 18)

Now Playing reached by a remote Play double-tap from Agents makes MENU open
Cover Flow. Returning to an existing WPS by remote Play triple-tap inside
Cover Flow also arms this behavior. Wheel and forwarded remote MENU use the
same ACTION_WPS_MENU arm. Cover Flow remains modal over that WPS: ordinary
exit or triple returns to the same playing screen. Its MENU flag persists
while that WPS invocation remains active, and disappears on any actual WPS
exit. Pending entry provenance is consumed before USB-audio/no-audio refusal.

Remote double/triple actions are distinct from native SELECT+PLAY and
SELECT+MENU actions. Only the remote double entry and explicit remote-triple
child return arm the new flag. Normal menus/native PLAY keep their previous
WPS MENU setting. SELECT+PLAY from Agents keeps its previous MENU-to-Agents
behavior, rather than gaining the remote-only Cover Flow override. A native
SELECT+MENU return from Cover Flow does not arm an ordinary WPS. All existing
silent Agents music exits and the explicit Agents MENU USB popup remain.

MENU hold still opens QuickScreen, or Shortcuts when configured. The host
IR bridge's existing Menu-hold-to-Select classification is unchanged. The
new override applies only to MENU tap. The ordinary album carousel API
still returns GO_TO_PREVIOUS for all callers; a separate per-call output
carries remote-triple provenance to WPS, without a global child-return latch.

Host code and protocol are unchanged. The package restores the complete
.rockbox/tools host tree (including HerdRemote, bridge, tests/fixtures and
podlink.py), preserving the previous full package's tool paths for fast_swap.
No installation or fast_swap operation is executed by this build-only task.

## Nonrecursive screen shortcuts (Follow-up 18 V2)

Shortcut routing no longer calls a new Agents/WPS/Carousel controller from
inside an existing screen. Public screen wrappers register live instances;
only one instance of each screen is admitted. A deferred target is retained
while child menus receive Cancel (native-only raw readers receive MENU).
The screen owner then runs its ordinary cleanup before the next screen can
open. A scratch-buffer owner outside these three screens also unwinds to the
root dispatcher before the pending target runs. No buffer is stolen, copied,
or silently shared; the existing ownership assertions remain intact.

Cover Flow now handles double/native SELECT+PLAY by returning Agents, and
remote Play hold by returning WPS, after releasing its buffer. Its triple
and native SELECT+MENU return to the origin. If WPS is the existing caller,
its carousel helper resumes that WPS instead of entering another instance;
an Agents destination propagates through the WPS cleanup first. The modal
shortcut controller is one loop over the destinations, not mutually recursive
screen calls. V1 MENU uses the same safe WPS-owned carousel path. Existing
cached Agents selection, silent music exits and explicit MENU USB consent
remain. Software navigation state is bounded independently of input length.

## Computer clock sync (Follow-up 19)

HerdRemote sends a standalone PodLink TEXT payload:

```text
TS\t1\t2026-11-13T19:02:03\t-18000
```

The separators are literal TABs, with no terminal newline. Fields are `TS`,
record version `1`, the host's local civil date/time (`YYYY-MM-DDTHH:MM:SS`),
and signed UTC offset in seconds east of UTC. The offset is metadata; do not
apply it to the civil fields again. The player's RTC stores local time.
UTC offsets from -50400 to +50400 are accepted. Dates are restricted to
2000..2037, including real calendar/leap-day checks, so the existing signed
32-bit log/scrobble timestamps remain representable. Seconds are 0..59.
Unknown versions and malformed records are ignored. Older firmware ignores
`TS`; an older daemon sends none. No PodLink or HerdRemote version bump is
required for this optional extension.

The resident host session sends on open, every 600 monotonic seconds, when
wall time minus monotonic time differs by more than 2 seconds from the last
send, or when the host UTC offset changes. This covers NTP steps, timezone/DST
changes and suspend on systems whose monotonic clock excludes suspend.
On systems where monotonic includes suspend, the periodic deadline covers
long suspends. Gradual slew accumulates against the last sent sample.
No new process, service or privilege is needed.

The resident player worker intercepts `TS` independently of the foreground
Agents client. It keeps the newest record in a 64-byte mailbox and posts one
redraw wakeup while that mailbox is pending. Disconnect/session invalidation
clears it. The worker never accesses RTC. The UI consumes it after input
waits (including raw UI readers), rejects records older than 5 seconds, and
adds whole elapsed seconds to fresh records. The UI compares a fresh RTC read, bypassing the display clock cache.
Only drift strictly greater than 2 seconds causes an RTC write; an invalid current RTC is corrected.
**Settings > Time & Date > Sync time from computer** defaults to On. Off
consumes and ignores incoming records; it does not disconnect PodLink.

With Debug > Stream remote log over PodLink enabled, `LOG TIME` lines in
`~/.local/state/podbox/remote-live.log` report `applied`, `ignored-off`,
`ignored-invalid`, `ignored-stale`, `ignored-range`, `ignored-drift` or
`ignored-rtc-error`, plus tick, signed drift, offset, record age and source
record. Ticks, remote gesture timing and sleep deadlines do not change.
With a valid previous date, debug-log expiry retains its remaining duration
across an applied step. An invalid previous RTC starts a fresh expiry interval.
Existing historical log/play timestamps are not rewritten. New play/log
entries use the corrected local time; database last-played ordering uses
its existing serial numbers. A backward civil-time correction can therefore
produce a lower wall timestamp, without changing elapsed playback duration.

Deploy the matching `herdremote.py` and `hrproto.py` together; the bridge and
remaining host runtime files are unchanged. This build package retains the
complete `.rockbox/tools/` tree.

## Cover Flow navigation (Follow-up 19 X1/X2)

A short native SELECT+MENU chord toggles Cover Flow from all UI screens,
including menus, browsers, settings, dialogs and raw-input game/viewer screens.
The UI-thread input filter consumes the chord and its releases; audio/decoder
threads bypass it. Child/modal screens unwind and app-buffer owners release
before the root dispatcher enters Cover Flow. Native SELECT+PLAY is unchanged.
The hardware reset/DFU warning for a held SELECT+MENU chord still applies.

Playing music from Cover Flow retains a logical return journey:
origin -> Cover Flow -> selected album's track list -> Now Playing.
MENU in that Now Playing returns to the track list, ahead of FU18's direct
MENU-to-Cover-Flow rule. MENU in the list returns to Cover Flow with that album
centered. Cover Flow's own exit returns to its origin. Direct Play Album and
playing from the cover's context menu retain the same track-list return.
Unrelated navigation clears this journey. Go to Artist retains its independent
browser jump.

The album identity/name/index are copied before carousel cleanup. The list
and WPS are dispatched after the previous screen unwinds; no album buffer is
held as history. Logical history has at most four levels (origin, carousel,
list, WPS). Live registered screen depth remains at most two (WPS with one
modal carousel); the album list has its own single-instance lifetime.

## Global remote controls (Follow-up 20)

Vol+/Vol- now change audio volume only in the actual Now Playing input
context. This overrides older sections that allowed list/Usage volume.
The resident PodLink-or-fresh-bridge connection predicate applies everywhere:
connected uses only PC EP0 reports; Dock L3/0E absolute/mute changes are
acknowledged without changing audio. Non-WPS readback is virtual; WPS
readback continues to report the real audio volume. Disconnected WPS
uses its original Dock absolute-volume path. Other disconnected screens use
the FU13 300 ms press grouping, first repeat after 600 ms, then every 250 ms.
PC reports still count individually, except the first IR repeat skipped by
`VOL_IGNORE_FIRST_REPEATS = 1`. No 80 ms fallback window returns.

| Screen | Vol+ / Vol- |
| --- | --- |
| Now Playing | Native WPS volume step per PC report while connected; original Dock audio volume while disconnected |
| Main menu, browsers, album/playlist/settings lists | Previous / next item, one per accepted report |
| Cover Flow | Previous / next album |
| Agents list | Previous / next item |
| Agents detail / Usage, connected | Existing computer Left/Up and Right/Down batches, with the unchanged 0.5/1/4/7 s hold tiers and view/focus lease |
| Agents detail / Usage, disconnected | Page up / down; Usage no longer changes audio volume |
| Agents confirmation | No list movement |
| QuickScreen | Ignores list movement; no volume shortcut |
| Other action-based non-list dialogs/editors | List previous/next only where handled; otherwise ignored |
| Raw viewers | One native wheel event where supported; no audio shortcut |
| Gameplay, capture-only/debug screens without list movement | No list navigation; capture mode records input without generating steps |

The PC FIFO is bounded at 64 entries. Screen/mode changes and loss of the
computer connection discard stale entries. Dock navigation also wakes a
blocked UI input wait. Remote steps carry no wheel acceleration data; a
previous wheel spin cannot turn one report into several rows. Native wheel
behavior and the physical volume settings remain unchanged.

An album track list, including a Cover Flow album list, gives remote Play
tap the native SELECT down/up after the 200 ms inter-tap window. Double
still switches to Agents and triple toggles Cover Flow; hold still opens
Now Playing. Other lists keep their previous Play meaning: standard lists
already select, while ordinary tree/file browsers can enter Now Playing.
Agents, WPS and carousel tap/hold meanings are unchanged.

### MENU return table

| Current screen / entry | MENU tap | MENU hold |
| --- | --- | --- |
| WPS entered by remote double from Agents, no Cover album playback journey | Cover Flow | Native QuickScreen, or Shortcuts when configured |
| Ordinary WPS, no Cover album playback journey | Previous configured WPS MENU behavior | Native QuickScreen/Shortcuts |
| WPS playing music opened from Cover Flow | That album's track list, ahead of the remote-entry override | Native QuickScreen/Shortcuts |
| Cover album track list | Cover Flow with the same album centered | Existing browser/menu hold behavior |
| Cover Flow entered directly over WPS by triple/V1 MENU/hotkey, or with saved WPS origin | Home (main menu) | Carousel settings |
| Cover Flow with another saved origin | That origin | Carousel settings |
| Cover Flow after the WPS -> album list -> Cover Flow return | Home if the saved origin is WPS; otherwise the saved origin | Carousel settings |
| Agents | Existing confirmed MENU exit and S1 Disk Mode popup | Same handling as its native MENU hold; no Select/Enter |

Cover Flow triple/native SELECT+MENU and Left/back retain their origin-return
meanings. Only its MENU tap changes a direct WPS child or saved WPS-origin route to Home.
A new triple/hotkey from a Cover-playback WPS also goes Home, even if the
older logical journey began in Agents; X2 list-back returns keep that older origin. Screen
instances still unwind before replacement; album-list history is logical,
with no saved app-buffer owner or recursive screen instance. Logical journey
depth remains four and maximum live registered-screen depth remains two.

### Real Menu button over EP0

The bridge removes Menu-to-Select classification. First decoded Menu frame
sends key ID 1 down immediately; continuing Menu frames/NEC repeats refresh
the same down state. After 150 ms release silence, another complete key or
orderly shutdown, it sends up. It emits no key ID 2 for a Menu stream.
Firmware's native sampler generates Menu repeats/long presses. The existing
one-second safety timeout releases a lost down state when the link/bridge
stops refreshing it. IDs and request framing are unchanged; legacy Select
ID 2 remains accepted for compatibility with older tools.

Native-button-only readers receive MENU with the original repeat/release
flags. A tap remains MENU. USB consent still uses MENU for charge-only and
remote Play/native SELECT to accept Disk Mode. Agents retains tap exit and
its existing native hold behavior; it no longer receives a fake Select.
The host bridge reads the IR serial stream only. Deploy its matching
`usb_remote_key.py`; HerdRemote/PodLink runtime files and service units do
not change in FU20. No service or device operation is part of this build.

### Extended questions and reviewed custom input (FU21)

Protocol version stays 1. New players send `qx\t1` immediately after `hr\t1`.
Until that capability arrives, the daemon emits the original six-field Q and
three-field option records, hiding unsupported Claude custom rows as before.
Older daemons ignore `qx`; new players also accept the legacy Q format.
After negotiation, Q has nine header fields:

```
Q <id> <pane> <workspace> <m|-> <agent> <yes> <no> <tab label>
? <question line>
o <number> <label> <c|->
```

Fields use actual tabs. `c` marks a custom input choice. `yes=no=0` means
multi-option. Otherwise they identify the direct binary answers; `no=-1`
means plain denial using Esc (currently Codex approvals with only the custom
No-and-tell row). LEFT on the alert chooses the first allow option; RIGHT
chooses a plain deny option, or that Esc. SELECT focuses the pane (`det`, then
matching `fp` acknowledgment) and opens **all** choices, including custom ones.
Exactly two ordinary Yes/No choices also use LEFT/RIGHT on the answer page;
SELECT on that page does not answer. Approvals retain their policy/custom
option list after SELECT. MENU dismisses; the waiting-question row reopens
it. Volume and wheel do nothing on the alert. Checkbox ticks preserve the
question id; the daemon resends a Q only when its visible payload changes.

Custom selection uses existing `a <req> <qid> <number>`. A successful result
`Input ready` means the option was selected and a visible input editor was
observed. It does **not** submit input. The player stays subscribed to that
pane's D and reuses hold-to-talk (`vd`/`vu`, short press `vc`). The structured
session remains the latest-message source, while the current input/cursor is
overlaid from `pane.read` with `source=visible`, no line count or scrollback.
Inline `Type something:`/`Other:` editors use the visible terminal cursor;
choice labels themselves are never echoed as input.

Only after review: `qi <req> <qid> enter` submits, and `qi <req> <qid> esc`
cancels. The daemon binds this to the selected custom session and focused
pane, resets pending key/voice activity, and sends exactly that targeted key.
LEFT uses existing Backspace. A replaced question, wrong focus, reset or lost
link invalidates the session. MENU never sends Enter. Esc behavior belongs
to the agent: Claude cancels/backtracks its editor; Codex cancels its current
composer/editor (approval RIGHT's Esc denies without choosing custom input).
Draft retention and exact screen after Esc require agent-version acceptance.
