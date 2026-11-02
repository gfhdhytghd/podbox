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
| `other` is `b` | red, three dots in it |
| `other` is `w` | green, three dots in it |
| either is `d` | teal ● |
| either is `i` | green ○ |
| anything else | grey · |

The rest are herdr's own dot indicators (`status_indicators = "dots"`) in
its `catppuccin-latte` colours: blocked red `#d20f39`, done teal `#179299`,
idle green `#40a02b`, unknown grey `#9ca0b0`, working yellow `#df8e1d`.
Working and asking take green and red here instead, and move.

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
R <req> <ok|err> <text>
```

`text` (120 bytes) is what the player shows: `Focused <label>`,
`Answered`, `Updated` (a multi-select box changed), or why it failed.

### `T` — tick

```
T <age> <herdr_ok>
```

`age` is how many seconds old the computer's picture of herdr is. `herdr_ok`
is `0` while herdr cannot be reached.

## Player to computer

| Message | Meaning |
|---|---|
| `hr 1` | Start, or restart, the session |
| `sub <ws>` | Send `A` for this workspace from now on; `sub -` stops it |
| `fw <req> <ws>` | Show this workspace, at its first tab, on the computer |
| `fp <req> <pane>` | Show this pane on the computer |
| `a <req> <qid> <number>` | Choose option `number` of question `qid` |

`a` with `number` `0` presses a multi-select's **Submit**. An answer to a
`qid` that is no longer on screen fails with `R <req> err`, and no key
reaches the agent. A success comes only after the computer has seen the
pane change, which takes up to 5 seconds.
