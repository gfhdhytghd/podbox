# HerdRemote

HerdRemote puts the agents that [herdr](https://herdr.dev) runs on this
computer onto the iPod, in **System > Agents**. From the player you can:

- see every herdr workspace, with how many agents in its first tab are
  working, waiting for you, done or idle;
- open a workspace, which also shows it in herdr on this computer, and see
  its first tab's agents with each one's last activity;
- select an agent, which shows its pane in herdr;
- answer a choice question an agent is asking: Claude Code's questions and
  permission prompts, and Codex's approvals and questions.

It talks only to herdr's local socket. It never reaches another machine.

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

| Option | Use |
|---|---|
| `--socket PATH` | herdr's API socket. Default: `$HERDR_SOCKET_PATH`, else herdr's default socket |
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

| Glyph | Agent |
|---|---|
| ◐ | working |
| ⚠ | waiting for you |
| ● | done |
| ○ | idle |

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
| herdr is not running on the computer | This program cannot reach herdr's socket |
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
