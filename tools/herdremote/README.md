# HerdRemote

HerdRemote puts the agents that [herdr](https://herdr.dev) runs on this
computer onto the iPod, in **System > Agents**. From the player you can:

- see every herdr workspace, with how many of its agents are working,
  waiting for you, done or idle, in any tab;
- turn the wheel in Spaces to select and focus a workspace after 250 ms;
- SELECT enters the Space; LEFT/RIGHT switches its herdr tabs without wrapping;
- turn the wheel inside a Space to select and focus an agent pane after 250 ms;
- SELECT opens that agent's detail, with the last 12 visible pane lines refreshed
  on change, at most once per second; MENU returns to the Space;
- hold Play/Pause to dictate with the computer's microphone; release to finish.
  Wheel focus waits while Play is held;
- answer Claude Code questions/permission prompts and Codex approvals/questions.
  New questions pop up oldest first, after any hold-to-talk and its result;
- switch between Spaces and Usage with LEFT/RIGHT. Usage shows each service's
  5-hour and weekly limits, tokens this week and their equivalent API price.

Navigation v2 firmware uses these controls:

| View | Wheel | SELECT | LEFT | RIGHT | MENU |
|---|---|---|---|---|---|
| Spaces | Select/focus workspace | Enter Space | Usage | Usage | System |
| Usage | Left/Right keys (words when fast) | Enter key | Backspace key | Spaces | System |
| Space | Select/focus pane | Agent detail | Previous tab | Next tab | Spaces |
| Agent detail | Left/Right keys (words when fast) | Enter key | Backspace key | No action | Space |
| Question | Select option | Choose/tick/Submit | No action | No action | Previous view |

Entering a Space focuses its first tab. Switching tabs focuses the new tab
immediately and selects its first agent, without an extra pane-focus request.
Entering detail focuses its pane first; editing waits for that focus result.
If focus fails, no detail keys are sent. Successful focus is silent; failures
appear in the message band. Question pop-ups never move computer focus.
Answering/dismissing restores the previous view and selection. A dismissed
question stays in the alert row and does not pop up again with the same id.

Older firmware keeps its existing controls and first-tab workspace list.
The unchanged v1 heartbeat and agent frames remain available; new tab/detail
frames are ignored by older firmware.

It talks only to herdr's local socket. It never reaches another machine.

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

- Python 3.9 or later, and pyserial (`pip install pyserial`, or your
  distribution's `python-pyserial`).
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

The player sends `vd <req> <target>` on press and `vu <req>` on release.
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
MENU leaves Usage for System. In detail, MENU returns to the Space and RIGHT
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
- `det <pane>` subscribes to `D <pane> <line0> ... <line11>`; each line has
  a 60-byte UTF-8 budget. ANSI sequences are stripped. Unchanged text is
  silent; empty/missing/failed panes send a zero-line detail. `det -` stops it.
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

## Scroll latency

The link loop polls and handles incoming commands before publishing dashboard
updates. `fw`/`fp`/`ft` send `R` directly after the herdr reply, before logging.
Successful focus logs use DEBUG, so wheel movement does not write an INFO log
for every target. The per-focus capability probe and the `moved: []` local
client check remain unchanged; missing/remote-only local windows still fail.

Snapshot/pane reads and order-file writes run on one separate read worker.
The link thread adopts complete model results, then sends only changed frames.
Regular model reads start at most once per second. A changed subscription can
request an earlier read; only the latest subscription is retained while one
read is in flight. One model job and one detail job are the maximum pending
work. Each job uses a separate herdr client so read-side probes cannot alter
focus capabilities. Old subscription/session results are discarded. Detail
reads also use this worker and retain the one-second send interval.

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
| `hrdetect.py` | Finds a choice question in a pane's text, and the keys that answer it |
| `hrproto.py` | The messages; `docs/podbox/herdremote-protocol.md` describes them |
| `hrvoice.py` | Session-bus voice calls, hold ownership and independent stop watchdog |
| `hrkeys.py` | Whitelisted desktop key taps, counted batches and bounded outstanding work |
| `tests/` | `python3 -m unittest discover -s tools/herdremote/tests` |
