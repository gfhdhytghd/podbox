# HerdRemote

HerdRemote puts the agents that [herdr](https://herdr.dev) runs on this
computer onto the iPod, in **System > Agents**. From the player you can:

- see every herdr workspace, with how many of its agents are working,
  waiting for you, done or idle, in any tab;
- select a workspace, which shows it in herdr on this computer;
- open a workspace with RIGHT (the wheel's right, fast-forward) and see its
  first tab's agents with each one's last activity; this changes nothing in
  herdr;
- select an agent, which shows its pane in herdr;
- answer a choice question an agent is asking: Claude Code's questions and
  permission prompts, and Codex's approvals and questions.

It talks only to herdr's local socket. It never reaches another machine.

On connection and after a herdr reconnect, HerdRemote checks `ping`'s
`capabilities.client_targeted_focus`. It checks again before each focus
request, in case the server changed between refreshes. When this capability
is `true`, workspace/first-tab and pane selections use `client.focus` with
`clients: "local"`: remote clients keep their workspace/tab views. Pane
focus is shared within a tab, so other clients already viewing that tab
also see its focused pane change. If `client.focus` returns `moved: []`,
the player shows **No local herdr window** as a focus failure.

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

## As a user service

`herdremote.service` runs it in the background and logs to the journal:

```sh
cp tools/herdremote/herdremote.service ~/.config/systemd/user/
systemctl --user edit --full herdremote   # point ExecStart at your checkout
systemctl --user enable --now herdremote
journalctl --user -u herdremote -f
```

## What the player shows

On the Scrim theme, a dot left of each workspace and agent shows its state.
A workspace's dot is for its first tab, unless only another tab is busy:

| Dot | Meaning |
|---|---|
| red, blinking | waiting for you |
| green, breathing | working |
| red with three dots | waiting for you in another tab |
| green with three dots | working in another tab |
| teal ● | done |
| green ○ | idle |
| grey · | unknown, or no agents |

The last three are herdr's own dots and colours (`catppuccin-latte`).
Other themes show the row's text only.

A question waiting anywhere puts a ⚠ row at the top of every list. Select
it to open the question. When an answer reaches the agent, the player says
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

**Menu** and **Left** go back one level, and from the workspace list leave
the screen.

## Files

| File | Role |
|---|---|
| `herdremote.py` | The program: finds the player, keeps the session, answers |
| `hrherdr.py` | herdr socket client, and the model the player is sent |
| `hrdetect.py` | Finds a choice question in a pane's text, and the keys that answer it |
| `hrproto.py` | The messages; `docs/podbox/herdremote-protocol.md` describes them |
| `tests/` | `python3 -m unittest discover -s tools/herdremote/tests` |
