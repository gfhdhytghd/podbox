# HerdRemote

HerdRemote puts the agents that [herdr](https://herdr.dev) runs on this
computer onto the iPod, in **System > Agents**. From the player you can:

- see every herdr workspace, with how many of its agents are working,
  waiting for you, done or idle, in any tab;
- turn the wheel in Spaces to select and focus a workspace at once;
- SELECT enters the Space; LEFT/RIGHT switches its herdr tabs without wrapping;
- turn the wheel inside a Space to select and focus an agent pane at once;
- SELECT opens that agent's detail: task, current activity, latest message and
  up to three tool steps. A nonempty input takes priority in a cursor-following
  block of at most five lines. Ordinary updates are at most once per second;
  completed detail key taps request a fresh read after 75 ms. Input-only
  updates retain that fast path; message/tool changes coalesce to one second.
  MENU returns to the Space;
- hold Play/Pause to dictate with the computer's microphone; release to finish.
  Wheel focus waits while Play is held;
- answer Claude Code questions/permission prompts and Codex approvals/questions.
  New questions pop up oldest first, after any hold-to-talk and its result;
- switch between Spaces and Usage with LEFT/RIGHT. Usage shows each service's
  5-hour and weekly limits, tokens this week and their equivalent API price.

Remote PLAY taps select after the 200 ms double-tap window; double taps
switch screens. Native PLAY taps under 150 ms play/pause music. Remote holds
of at least 300 ms and native holds of at least 150 ms talk; recording starts
on press and its band appears only at the hold threshold. Usage uses real
remote volume. List/detail restore virtual volume paging. Remote Left/Right
retain their ordinary meanings, including seeking outside Agents. The IR
bridge supplies Menu or Select after classifying the Menu stream.

Navigation v2 firmware uses these controls:

| View | Wheel | SELECT | LEFT | RIGHT | MENU |
|---|---|---|---|---|---|
| Spaces | Select/focus workspace | Enter Space | Usage | Usage | Confirm exit |
| Usage | Left/Right keys (words when fast) | Enter key | Backspace key | Spaces | Confirm exit |
| Space | Select/focus pane | Agent detail | Previous tab | Next tab | Spaces |
| Agent detail | Left/Right keys (words when fast) | Enter key | Backspace key | No action | Space |
| Question | Select option | Choose/tick/Submit | No action | No action | Previous view |

Wheel focus sends at once when no focus request is in flight. While waiting
for its matching `R`, the player retains only the newest target and sends
that target after `R`. Play holds suppress focus.

Entering a Space focuses its first tab. Switching tabs focuses the new tab
immediately and selects its first agent, without an extra pane-focus request.
Entering detail focuses its pane first; editing waits for that focus result.
If focus fails, no detail keys are sent. Successful focus is silent; failures
appear in the message band. Question pop-ups never move computer focus.
Answering/dismissing restores the previous view and selection. A dismissed
question stays in the alert row and does not pop up again with the same id.

Top-level MENU opens `Leave Agents?`: SELECT leaves, MENU stays.

Older firmware keeps its existing controls and first-tab workspace list.
The unchanged v1 heartbeat and agent frames remain available. Pre-NAV2
firmware ignores new tab/detail frames. Typed detail ships with the matching eleven-field firmware from
[DETAIL-DESIGN.md](../../../ipod/DETAIL-DESIGN.md). A nine-field-only player
rejects the eleven-field record; other v1 messages keep their shape.

It uses local herdr and Codex app-server sockets, and read-only Claude
transcript files. It never reaches another machine through these readers.

On connection and after a herdr reconnect, HerdRemote checks `ping`'s
`capabilities.client_targeted_focus`. It checks again before each focus
request, in case the server changed between refreshes. When this capability
is `true`, workspace/first-tab, explicit tab and pane selections use `client.focus` with
`clients: "local"`: remote clients keep their workspace/tab views. Pane
focus is shared within a tab, so other clients already viewing that tab
also see its focused pane change. If `client.focus` returns `moved: []`,
HerdRemote checks `client.list`: a connected client with `origin: "local"`
means the view was already focused, so the request succeeds. If no local
client is connected (including remote-only clients), the player shows
**No local herdr window**. A failed list request reports a focus failure;
it never retries through legacy focus calls.

Without the capability, HerdRemote uses the older `workspace.focus` plus
`tab.focus`, or `pane.focus`; these move all herdr clients. A failed probe
or focus request reports a failure rather than retrying with these calls.

## Requirements

- Python 3.9 or later, pyserial and websockets (`pip install -r
  tools/herdremote/requirements.txt`).
- herdr running on this computer.
- Read and write access to the player's tty. A desktop session usually has
  it; if the log says `Permission denied`, add yourself to the group that
  owns `/dev/ttyACM*` (`uucp` on Arch, `dialout` on Debian).

## Running it

```sh
python3 tools/herdremote/herdremote.py
```

Then plug the player in and open **System > Agents**. The program waits for
the player, finds it by its USB identity, and reconnects by itself when the
player is unplugged and plugged back in. It logs to standard output and
writes nothing to the player's disk.

Both lists are in priority order: agents waiting for you first (the oldest
question first), then agents that are working, then the rest with the most
recently active first. A workspace ranks as its best agent in any tab. herdr records
no times, so this program notes when each agent's status changes and keeps
the times in a small state file; a restart keeps the order.

| Option | Use |
|---|---|
| `--socket PATH` | herdr's API socket. Default: `$HERDR_SOCKET_PATH`, else herdr's default socket |
| `--state FILE` | Where the order's times are kept. Default: `order.json` in `$STATE_DIRECTORY`, else `~/.local/state/herdremote/` |
| `--once` | Print the workspaces and questions the player would get, then exit. Checks herdr without a player |
| `--port TTY` | Use this tty instead of finding the player: for testing, or for the simulator, which prints its tty as `PodLink: /dev/pts/N` when Agents opens |
| `--key-tool TOOL` | Usage keyboard taps: `auto` (default), `ydotool` or `wtype`; overrides `$HERDREMOTE_KEY_TOOL` |
| `--key-scale 1..8` | Wheel arrow multiplier (default 1); overrides `$HERDREMOTE_KEY_SCALE` |

## As a user service

`herdremote.service` runs it in the background and logs to the journal:

```sh
cp tools/herdremote/herdremote.service ~/.config/systemd/user/
systemctl --user edit --full herdremote   # point ExecStart at your checkout
systemctl --user enable --now herdremote
journalctl --user -u herdremote -f
```

## Hold to talk

The computer must have the LAIS fcitx5 voice addon and its `lais-voice`
bridge running, plus `busctl` (no extra Python package is required).
HerdRemote calls `HoldStart`/`HoldStop` on the session bus at
`org.fcitx.Fcitx5`, `/org/lais/Voice`, `org.lais.FcitxVoice1`; it does not
synthesise F23. Ordinary F23 uses `HoldStart`. Alt+F23 uses `HoldStartRaw`,
which disables text cleanup; it does not select another microphone.

The player sends `vd <req> <target>` immediately on press. A normal release
sends `vu <req>`; a discarded tap or shortcut sends `vc <req>`, which calls
`Cancel` then `HoldStop` and returns `Discarded` without committing text.
Remote taps shorter than 300 ms and native taps shorter than 150 ms are
discarded. A native SELECT joining PLAY within 150 ms also discards before
switching screens. Use the matching daemon when updating this firmware.
By default Play/Pause behaves like F23: recording starts at the computer's
current cursor without moving herdr, regardless of the selected player row.
Set `voice_focus=on` explicitly to focus the selected pane or workspace's
first tab before recording with `client.focus clients=local`. In this mode,
if local focus is unsupported or fails, recording does not start. This
voice path has no legacy fallback. Target `-` always leaves focus as it is.

| Config key | CLI option | Service environment | Default |
|---|---|---|---|
| `voice_focus` | `--voice-focus on\|off` | `HERDREMOTE_VOICE_FOCUS` | `off` |
| `voice_max_hold` | `--voice-max-hold SECONDS` | `HERDREMOTE_VOICE_MAX_HOLD` | `120` |
| `voice_method` | `--voice-method METHOD` | `HERDREMOTE_VOICE_METHOD` | `HoldStart` |

CLI options override environment values. `voice_max_hold` must be finite
and positive. `voice_method` accepts only `HoldStart` or `HoldStartRaw`.
There is no separate config file. For a user-service override:

```ini
[Service]
Environment=HERDREMOTE_VOICE_FOCUS=off
Environment=HERDREMOTE_VOICE_MAX_HOLD=120
Environment=HERDREMOTE_VOICE_METHOD=HoldStart
```

The systemd user manager and service must inherit `DBUS_SESSION_BUS_ADDRESS`
for the same graphical login as fcitx. Check without opening a microphone:

```sh
systemctl --user show-environment | rg '^DBUS_SESSION_BUS_ADDRESS='
busctl --user introspect org.fcitx.Fcitx5 /org/lais/Voice org.lais.FcitxVoice1
busctl --user call org.fcitx.Fcitx5 /org/lais/Voice org.lais.FcitxVoice1 Status
```

If the manager lacks the variable, import it from the graphical session
with `systemctl --user import-environment DBUS_SESSION_BUS_ADDRESS` before
the next deployment. This checkout's implementation requires `hrvoice.py`
alongside the other `hr*.py` modules when copying the daemon for deployment.

A successful press says **Talking** after `Status` confirms recording. A
successful release says **Sent** after the stop request, before transcription
or text commit necessarily finishes. Missing bridge/addon/bus or D-Bus
errors say **Voice input not running**. If `Status` reports an existing
recording, the backend returns **Voice input busy** without starting or
stopping that recording through D-Bus.

Link loss, session reset and daemon shutdown release the daemon's own hold.
An independent timer releases it after `voice_max_hold` even if herdr stalls.
Duplicate presses do not restart recording or extend this limit. After a
timeout, another press is refused until release/reset. Failed stops retry
once per second and block a new hold. Bus calls have a 2-second timeout and
a 3-second subprocess limit. Starting and checking `Status` can delay a
very short timeout by at most two such calls, followed by the stop call.
An unreachable addon/bus prevents a successful stop. Idle cleanup and
unmatched releases do not stop a recording started elsewhere.

Tests use mocked D-Bus only:

```sh
python3 -m unittest discover -s tools/herdremote/tests -p test_voice.py -v
```

Do not use real `HoldStart`/`HoldStartRaw` as a smoke test: they open the
computer's microphone and can commit text to the user's current input.
Optional `V talk`/`V idle` signals are not emitted by this backend.

## Keyboard editing from Usage and agent detail

Usage and agent detail send keyboard taps to the computer's current
keyboard focus. The key command itself never moves herdr or uses
`pane.send_keys`. Detail first sends `fp` and waits for its successful
result before sending keys. Usage edits at the same cursor used by F23
and hold-to-talk without an initial focus request.

| Usage / detail control | Wire key | Computer key |
|---|---|---|
| LEFT press/repeat | `k <req> bs` | Backspace: delete the character before the cursor |
| Wheel counter-clockwise | `k <req> left` | Left arrow |
| Wheel clockwise | `k <req> right` | Right arrow |
| Fast wheel counter-clockwise | `k <req> wleft` | Ctrl+Left: jump one word left |
| Fast wheel clockwise | `k <req> wright` | Ctrl+Right: jump one word right |
| SELECT press (no repeat) | `k <req> enter` | Enter |

Only these six keys are accepted. `k <req> <key> [<n>]` accepts 1–32 taps;
omitting `n` means one. A noninteger or out-of-range count returns
**Invalid key count**. Success is `R <req> ok` without a message band.
The iPod keeps one request in flight and coalesces pending steps into
ordered runs with counts. It hides **Key busy** and older **Key rate limit**
responses; real failures display their reason. RIGHT returns Usage to Spaces;
MENU opens the top-level exit confirmation from Usage. In detail, MENU returns to the Space and RIGHT
has no action.

`HERDREMOTE_KEY_TOOL` or `--key-tool` selects `auto`, `ydotool`, or `wtype`.
In `auto`, HerdRemote prefers the installed ydotool plus its socket;
otherwise it uses wtype. Explicit choices use only that tool. ydotool
requires the user's ydotool service and a usable uinput device. Its socket
is `$YDOTOOL_SOCKET`, or `$XDG_RUNTIME_DIR/.ydotool_socket` (with
`/run/user/<uid>` as the default runtime directory). HerdRemote passes the
resolved path to ydotool. wtype requires the graphical session's Wayland
environment. No new Python package is required.

| Config key | CLI option | Service environment | Default |
|---|---|---|---|
| `key_tool` | `--key-tool auto\|ydotool\|wtype` | `HERDREMOTE_KEY_TOOL` | `auto` |
| `key_scale` | `--key-scale 1..8` | `HERDREMOTE_KEY_SCALE` | `1` |

`key_scale` multiplies only `left` and `right` counts. The effective count
is capped at 32 after multiplication; `bs`, `enter`, `wleft` and `wright`
are never scaled.
For example, scale 3 and `k 7 right 4` produce 12 Right taps, whereas
`k 8 bs 4` produces four Backspace taps. CLI options override environment.
For a service override, use:

```ini
[Service]
Environment=HERDREMOTE_KEY_TOOL=auto
Environment=HERDREMOTE_KEY_SCALE=1
```

When copying this daemon for deployment, include the new `hrkeys.py`.
Read-only socket inspection is safe; never run a real key command as a
test against the user's desktop.

One batch runs in one tool call: ydotool gets all down/up pairs with
`--key-delay=2` (2 ms between events); wtype gets repeated `-k <symbol>`
arguments. Word keys wrap each arrow tap in its own Ctrl press/release:
ydotool sends `29:1 105:1 105:0 29:0` for `wleft` (106 for `wright`);
wtype sends `-M ctrl -k Left -m ctrl` (Right for `wright`). Ctrl is released
after every tap, including between taps in the same batch. Older daemons
return **Unsupported key** for word keys, which the player shows as a failure.
The old 30-tap/second timer and 100 ms scheduling expiry are
removed. A completed batch can be followed immediately by the next one.
There is still at most one outstanding batch, with no computer-side queue.
An overlapping request from old firmware returns **Key busy**.

Session reset, link loss and shutdown invalidate unstarted work and old
replies. An already running tool can finish its bounded batch. The timeout
is `0.5 + 0.01 * effective_count` seconds (0.51–0.82 s). Failed tools get
one release-only cleanup with a 0.5-second timeout, with no repeated batch
or runtime fallback to another tool. An unreachable tool can also prevent
cleanup. Word-key cleanup releases both the arrow and Ctrl: ydotool gets
`105:0 29:0` (106 for right), and wtype gets `-p Left -m ctrl` (Right for
right). Every tap remains a complete press/release sequence.

Errors include **Unsupported key**, **Invalid key count**, **Key busy**,
**Key tool unavailable**, **Key tool failed**, and **Session not ready**.
Success confirms the tool's exit status; it does not inspect the input
field. The keyboard tests mock every input tool:

```sh
python3 -m unittest discover -s tools/herdremote/tests -p test_keys.py -v
```

## What the player shows

On the Scrim theme, a dot left of each workspace and agent shows its state.
A workspace's dot is for its first tab, unless only another tab is busy:

| Dot | Meaning |
|---|---|
| red, blinking | waiting for you |
| green, breathing | working |
| red with a … badge | waiting for you in another tab |
| green with a … badge | working in another tab |
| teal ● | done |
| green ○ | idle |
| grey · | unknown, or no agents |

The last three are herdr's own dots and colours (`catppuccin-latte`).
Each dot has a thin white edge, so it shows on any album's colours.
Other themes show the row's text only.

A detected question puts a ⚠ row at the top of every list. New question ids
also open a pop-up with Navigation v2 firmware; Play holds and pending talk
results defer it. Select the alert row to revisit a dismissed question. When an answer reaches the agent, the player says
**Answered**; a multi-select box that changed says **Updated**. If the
question changed on the computer first, the answer is refused and no key
reaches the agent.

A row at the top says when something is missing:

| Row | Meaning |
|---|---|
| Connect USB to the computer | No USB connection |
| Start herdremote on the computer | USB is up; this program is not running |
| Waiting for herdremote… | The session is open; this program has not answered yet |
| herdr is not running | This program cannot reach herdr's socket |
| No update for N s | The player has heard nothing for N seconds |
| herdr data N s old | herdr has stopped answering this program |

The navigation table above defines MENU and LEFT for each view.

## Resident link and cached return

On resident firmware, the first computer connection still asks for disk
consent. MENU keeps a stable `2AA0000000000360` CDC profile. Agents, Now
Playing and the PodLink demo attach or detach locally; opening, switching
and MENU exit do not reconnect USB or close the host session. SELECT grants
the disk exclusive access without CDC. USB Mode Charge is pure charge and
does not run the resident link. The first non-disk profile already includes
CDC while the consent popup is waiting. Existing USB charging negotiation
and the optional accessory configuration stay in use.

The `PLFG` notice rides in an ordinary PodLink PING: byte 0 means background,
1 means Agents, 2 means another foreground client. PONG handling stays
unchanged. In the background the daemon sends only changed list/state
frames and cheap heartbeats; detail reads and pushes stop. Returning to
Agents keeps subscriptions and sent-content revisions, so unchanged frames
are not resent. The firmware retains the current list, selection, detail,
usage and scrolling state and draws it before a host round trip. A session
generation change invalidates the cache and uses the normal fetch path.
The model is bounded to 32 KiB; resident mailbox/worker storage is bounded
to 64 KiB, with no heap allocation in the device transport.

An old host ignores the PING payload safely, but may keep sending detail in
the background. The device drops those detail pushes while keeping its
cached page. A new host with old firmware sees no `PLFG` capability and
retains its legacy screen-owned connection and eleven-field detail shape.
Only resident firmware gets the optional twelfth D field, a content
revision. No wire-version bump or host serial-matcher change is needed.

PLAY still sends `vd` immediately. The recording band stays hidden before
remote 300 ms or native 150 ms, even if a host recording reply arrives.
Short release sends `vc` silently; a confirmed hold shows the band and ends
with `vu`. Leaving the foreground finishes its outstanding hold before the
notice changes. No runtime service is installed or restarted by packaging.

## Navigation v2 wire contract

[The protocol](../../docs/podbox/herdremote-protocol.md#navigation-v2--protocol-ready)
contains the frozen `PROTOCOL READY` contract. No version bump is needed.

- `sub <ws> [<tab_id>]` subscribes to that tab's `A` rows. Omit the tab to
  follow the first tab. `sub -` stops workspace/tab lists.
- Each subscribed tab list is one frame containing seven-field
  `T <ws> <n> <i> <tab_id> <label> <status>` records, in herdr order, up to
  32 tabs. This is separate from the unchanged three-field heartbeat `T`.
- `ft <req> <ws> <tab_id>` validates membership and focuses the tab through
  the same local-client path and legacy capability fallback as `fw`/`fp`.
- `det <pane>` subscribes to the eleven-field typed record
  `D <pane> <age> <task> <now> <say> <step1> <step2> <step3> <input> <cursor>`.
  Empty slots are retained. SAY keeps the message head (300 characters,
  900 bytes); TASK/NOW/steps keep at most 80 characters and 240 bytes each.
  A cut field ends with `…` within both limits.
- Input preserves hard line breaks as `\n` and literal backslashes as `\\`.
  Cursor offsets count decoded Unicode code points; escaped-field byte
  limits include escapes. Full input is sent when it fits. Otherwise the
  design's window ends 100 characters after the cursor and extends backward
  to fill its budget, with one leading `…` for an omitted prefix.
- `link.max_message` selects the negotiated profile: input is at most 4000
  decoded characters / 12000 escaped bytes on a 16384-byte link, or 500
  characters / 1500 bytes on a 4096-byte link. Complete budget sums are
  14148 and 3648 bytes respectively. Encoding rejects oversized records.
- Age `-1` is unknown. Age `-2` marks grey fallback; otherwise it is elapsed
  state seconds, incremented locally by the player. Age changes alone do not
  force frames. Missing/failed/empty panes send all text empty with age `-1`
  and cursor `0`. `det -` stops detail. See `DETAIL READY 2` for exact rules.
- First tabs and the selected tab are read for questions/activity; other
  tabs use herdr's states. Tab order stays independent of agent priority.
- Subscriptions never focus or type. Reset, link loss and shutdown clear
  subscriptions and detail caches. Keyboard and voice ownership are unchanged.

Tests use a private fake herdr socket, mocked key/voice tools and pseudo
terminals. They never focus real panes, inject real keys, or open a microphone:

```sh
python3 -m unittest discover -s tools/herdremote/tests -v
```

These host tests do not establish on-device UI behavior or deployed-service
acceptance. Deployment and iPod installation are separate from Part B.

## Detail sources

Codex detail uses the existing app-server daemon's Unix WebSocket. The
reader sends only initialization and `thread/items/list` requests keyed by
herdr's `agent_session` UUID. It asks for at most five recent items and
keeps the latest message's last five lines. It never resumes a thread,
starts a turn, subscribes to the TUI or reads Codex rollout files. Working
state comes from herdr's `agent_status`. `PODBOX_CODEX_SOCKET` can name a
nondefault local control socket. This program never starts the daemon.

Claude detail tails its session transcript under `~/.claude/projects/`,
read only. Both sources provide a stable content revision and compact tool
steps. New message/item content or completion changes that revision;
terminal spinners, wrapping and timer counters do not.

If a structured source is unavailable, `hrdetail.py` parses a single
`pane.read source=visible` reply, with no `lines` parameter. The herdr
adapter rejects scrollback sources and explicit line counts before any
socket I/O. Only the last five message lines go to the player. Optional
cursor metadata from this same visible reply describes the input block;
there is no second or extended read. This keeps the computer's pane still.

agy's captured layout uses black message/tool bullets and braille working
spinners. Its historical captured window contains tools and a working line but
no assistant message start; SAY stays empty rather than showing user input
or thought text. A message outside the visible viewport is unavailable to the fallback.
No grok pane was present during capture; grok and other unknown layouts use
the last four non-chrome lines as grey fallback. Known kinds also fall back
when no message, activity, tool step or question is recognized.

Reviewed, anonymized captures live in `tests/fixtures/detail/`; `capture.json`
records read-only provenance, requested-window exceptions and redactions.
They are test data, never executable instructions. The parser does not add a
new live secret filter; fixture redaction is separate from display parsing.

```sh
python3 -m unittest discover -s tools/herdremote/tests -p test_detail.py -v
```

Deploy `hrappserver.py`, `hrsession.py`, `herdremote.py`, `hrherdr.py`,
`hrproto.py` and the sibling `tools/podlink.py` together. Install the listed
Python dependencies. Keep `hrdetail.py` for the visible-only fallback. There is no change to focus,
keyboard or voice commands and no wire-version bump.

Input extraction also preserves the current prompt text and optional cursor
facts. `read_record()` keeps the complete herdr reply: when it contains a
visible `cursor` on an input row, the parser maps its row/column to a Unicode
input offset, preferring `col_chars` for wide cells and combining marks.
Prompt glyphs and frame padding do not count. Missing, invisible, malformed,
or off-input cursor data falls back to the input end. When nonempty input
has no recognized visible cursor, its end is the fallback. The adapter never
requests merged history or moves the computer viewport. The fake herdr can
return either the legacy record or a record containing cursor metadata.
The approved [DETAIL-DESIGN.md](../../../ipod/DETAIL-DESIGN.md) owns all
field order, budgets, escaping, truncation, state wording and presentation.
The backend implements it; it does not choose alternate display rules.

## Scroll latency

The link loop polls and handles incoming commands before publishing dashboard
updates. `fw`/`fp`/`ft` send `R` directly after the herdr reply, before logging.
Successful focus logs use DEBUG, so wheel movement does not write an INFO log
for every target. The per-focus capability probe and the `moved: []` local
client check remain unchanged; missing/remote-only local windows still fail.

Snapshot/pane reads and order-file writes run off the link thread. One model
job and one detail job use two read slots so detail is not queued behind a
model read.
The link thread adopts complete model results, then sends only changed frames.
Regular model reads start at most once per second. A changed subscription can
request an earlier read; only the latest subscription is retained while one
read is in flight. One model job and one detail job are the maximum pending
work. Each job uses a separate herdr client so read-side probes cannot alter
focus capabilities. Old subscription/session results are discarded. Detail
reads retain the one-second polling interval. Completed detail key batches
schedule a fresh read after 75 ms; a burst coalesces pending reads. Replies
are sent before processing old read results, which are invalidated by keys.

The synchronous `--once` path remains available. To measure the command path
without touching a real desktop, use the fake-server benchmark:

```sh
python3 tools/herdremote/tests/bench_focus_latency.py --revision 39fc19ab61 --samples 8
python3 tools/herdremote/tests/bench_focus_latency.py --samples 8
```

`arrival_to_R` includes time waiting for the daemon to poll the fake link;
`receipt_to_R` starts at `command()`. The loaded case injects commands while
an eight-pane refresh is reading (20 ms per pane, 20 ms per snapshot).
The transport is an in-memory fake link; existing integration tests use PTYs.
Neither measurement establishes real USB or iPod rendering latency.

For explicitly authorized real read-only timing, `--real-read-only` restricts
calls to `ping`, `client.list`, `session.snapshot`, and `pane.read`. It writes
only timing/count summaries, never terminal text or snapshots. It never sends
focus/keys, opens a player, starts voice, or controls the deployed service.

## Files

| File | Role |
|---|---|
| `herdremote.py` | The program: finds the player, keeps the session, answers |
| `hrherdr.py` | herdr socket client, and the model the player is sent |
| `hrdetail.py` | Parses typed task/activity/message/steps from recent pane text |
| `hrdetect.py` | Finds a choice question in a pane's text, and the keys that answer it |
| `hrproto.py` | The messages; `docs/podbox/herdremote-protocol.md` describes them |
| `hrvoice.py` | Session-bus voice calls, hold ownership and independent stop watchdog |
| `hrkeys.py` | Whitelisted desktop key taps, counted batches and bounded outstanding work |
| `hrremotelog.py` | Bounded background writer for the player's remote diagnostics |
| `tests/` | `python3 -m unittest discover -s tools/herdremote/tests` |

## Detail stability and diagnostics

Closed display lines keep their breaks during a streaming message. A reader
scrolled above the bottom keeps a bounded snapshot of the message and tools;
returning to the bottom adopts the latest message. Tools use spare display
rows and cannot push a message line out of view. A completion timestamp or
revision without changed visible fields causes no D frame or redraw.

D-send logs contain monotonic time, pane, revision, byte length and changed
field names, never message/input text. INFO is limited to one line per
second, with a suppressed count for fast input frames. `--debug` emits
metadata for every D frame. The app-server stays read-only and fallback
herdr reads stay within the visible viewport.

## Live remote input log

In Debug, select **Stream remote log over PodLink** to toggle the stream.
The ON/OFF splash confirms the new value. It defaults OFF at each firmware
start and remains active when leaving Debug, including in Agents and Now
Playing. The existing Remote input screen's SELECT file logger is unchanged.
Enable the PodLink USB profile and use the existing HerdRemote tty owner.
Opening Agents is not required to stream. Do not open a second tty reader.

Deploy the matching `herdremote.py` **and `hrremotelog.py`** from the package's
`.rockbox/tools/herdremote/` with the other existing host files. This package
does not install or restart a service. A running old process keeps its old
code until its owner deploys/restarts it. Once the matching host is running,
read the regular file live (no disk mode or additional device access):

```sh
tail -F ~/.local/state/podbox/remote-live.log
```

Each received `LOG ` TEXT frame becomes one line with UTC wall time and host
monotonic receipt time. Firmware ticks remain in the payload. Raw frames
retain their sequence, delta, BAD/TRUNC, stored bytes and decoded names.
VOL decisions, held-state changes, Play tap/double/hold, accepted Menu/Select
EP0 requests and Agents volume page emits are included. EP0 events reflect
the bridge's classified key; they cannot expose the original IR repeat
stream. The file rotates at 4 MiB to `remote-live.log.1` (one backup).
Newlines/control characters in payloads are escaped to prevent forged rows.

The firmware has 32 fixed records and drops new events on overflow, reporting
`LOG LOST n`; input never waits for USB or file I/O. The resident worker sends
at most one line per 20 ms step after control traffic, independent of which
screen owns the foreground. A busy transmitter retains one pending line.
The host disk worker has 256 bounded entries and reports `LOG HOST LOST n`
on its next accepted entry if its queue overflows. Both are best-effort;
cable/session loss, process exit or file write errors can also lose logs.
Disabling the stream clears unsent diagnostic records. Already queued USB
frames can still arrive. No history is replayed when enabling the switch.

Compatibility: new host + old firmware creates no log file/worker unless a
LOG frame arrives. New firmware + old host remains protocol v1: the old
host's unknown-TEXT parser ignores LOG frames without executing commands or
replying. LOG frames never enter HerdRemote's application command parser,
subscriptions, detail display or voice path. They do not change Agents' cached
return behavior or the foreground lease.

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
