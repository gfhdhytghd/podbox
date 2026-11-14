"""The local herdr server, over its socket API, and the model the iPod shows.

Only the local socket is used: no --machine, --remote or ssh. One request per
connection, newline-delimited JSON, as `herdr api schema` describes.
"""
import json
import os
import select
import socket
import threading

import hrdetect
import hrdetail


class HerdrError(RuntimeError):
    pass


def default_socket():
    return os.environ.get("HERDR_SOCKET_PATH") or \
        os.path.expanduser("~/.config/herdr/herdr.sock")


class Herdr:
    def __init__(self, path=None, timeout=3.0):
        self.path = path or default_socket()
        self.timeout = timeout
        self.counter = 0
        self.client_targeted_focus = False

    def call(self, method, **params):
        self.counter += 1
        request = {"id": "hr%d" % self.counter, "method": method,
                   "params": params}
        sock = socket.socket(socket.AF_UNIX)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.path)
            sock.sendall(json.dumps(request).encode() + b"\n")
            data = b""
            while not data.endswith(b"\n"):
                chunk = sock.recv(1 << 20)
                if not chunk:
                    break
                data += chunk
        except OSError as error:
            raise HerdrError("herdr socket %s: %s" % (self.path, error))
        finally:
            sock.close()
        try:
            reply = json.loads(data)
        except ValueError:
            raise HerdrError("herdr sent no reply to %s" % method)
        if "error" in reply:
            raise HerdrError("%s: %s" % (method, reply["error"].get("message")))
        return reply["result"]

    def detect_capabilities(self):
        # A failed probe must not retain a previous server's capabilities.
        self.client_targeted_focus = False
        capabilities = self.call("ping").get("capabilities") or {}
        self.client_targeted_focus = \
            capabilities.get("client_targeted_focus") is True

    def snapshot(self):
        return self.call("session.snapshot")["snapshot"]

    def read(self, pane_id, source="visible", lines=None):
        return self.read_record(pane_id, source, lines)["text"]

    def read_record(self, pane_id, source="visible", lines=None):
        """Read only the visible viewport, preserving optional cursor metadata."""
        if source != "visible" or lines is not None:
            raise HerdrError("viewport-only read: scrollback and line counts are forbidden")
        return self.call("pane.read", pane_id=pane_id,
                         source=source, **({"lines": lines} if lines is not None else {}))["read"]

    def send_keys(self, pane_id, keys):
        self.call("pane.send_keys", pane_id=pane_id, keys=list(keys))

    def focus_workspace(self, workspace_id, tab_id=None):
        if self._focus_local(workspace_id=workspace_id,
                             **({"tab_id": tab_id} if tab_id else {})):
            return
        self.call("workspace.focus", workspace_id=workspace_id)
        if tab_id:
            self.call("tab.focus", tab_id=tab_id)

    def focus_pane(self, pane_id):
        if self._focus_local(pane_id=pane_id):
            return
        self.call("pane.focus", pane_id=pane_id)

    def focus_tab(self, workspace_id, tab_id):
        self.focus_workspace(workspace_id, tab_id)

    def _focus_local(self, **target):
        # Each API request uses a fresh connection. Probe again before focus
        # so a server replacement between refreshes cannot leave stale support.
        self.detect_capabilities()
        if not self.client_targeted_focus:
            return False
        result = self.call("client.focus", clients="local", **target)
        if not result["moved"]:
            # moved lists changed views, not every selected connection.
            clients = self.call("client.list")["clients"]
            if not any(client.get("origin") == "local" for client in clients):
                raise HerdrError("No local herdr window")
        return True


class Watcher(threading.Thread):
    """Sets event whenever herdr reports a change, so a poll can start early.

    The subscription is a hint, never the source of truth: every refresh
    reads a whole snapshot.
    """
    KINDS = ["workspace.created", "workspace.updated", "workspace.renamed",
             "workspace.closed", "workspace.focused", "workspace.reordered",
             "workspace.moved", "tab.created", "tab.closed", "tab.focused",
             "tab.moved", "tab.renamed", "pane.created", "pane.closed",
             "pane.focused", "pane.moved", "pane.exited",
             "pane.agent_detected", "pane.updated"]

    def __init__(self, herdr, event):
        super().__init__(daemon=True)
        self.herdr, self.event = herdr, event
        self.stopping = threading.Event()

    def run(self):
        while not self.stopping.is_set():
            try:
                self._listen()
            except OSError:
                pass
            self.stopping.wait(2)

    def _listen(self):
        sock = socket.socket(socket.AF_UNIX)
        try:
            sock.connect(self.herdr.path)
            request = {"id": "watch", "method": "events.subscribe",
                       "params": {"subscriptions":
                                  [{"type": k} for k in self.KINDS]}}
            sock.sendall(json.dumps(request).encode() + b"\n")
            while not self.stopping.is_set():
                ready, _, _ = select.select([sock], [], [], 1)
                if ready:
                    if not sock.recv(65536):
                        return
                    self.event.set()
        finally:
            sock.close()


def best_state(statuses):
    """The one state that stands for a group of agents, as a status letter:
    b asking, w working, d done, i idle, u unknown; "-" with no agents."""
    for code in "bwdiu":
        if code in statuses:
            return code
    return "-"


def detail_lines(text):
    """Visible pane tail, without terminal escape sequences or trailing blanks."""
    text = hrdetail.strip_ansi(text)
    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    return lines[-12:]


def build_model(snapshot, read, subscribed=None, tab_id=None):
    """The dashboard model from a herdr snapshot.

    read(pane_id) returns a pane's visible text. Tab 1 is a workspace's first
    tab in herdr's order, which is the order its tab bar shows; its own
    number is a creation count and is not used. First tabs and the selected
    subscription tab are read; other tabs count by herdr's status alone.
    agents/others retain the legacy first-tab model for workspace ranking.
    tab_agents contains every tab, without changing herdr's tab order.
    """
    first_tab = {}
    for tab in snapshot["tabs"]:
        first_tab.setdefault(tab["workspace_id"], tab["tab_id"])
    focused_ws = snapshot.get("focused_workspace_id")
    names = {a["pane_id"]: a.get("name") for a in snapshot.get("agents", [])}
    by_tab = {}
    for pane in snapshot["panes"]:
        if pane.get("agent"):
            by_tab.setdefault(pane["tab_id"], []).append(pane)
    tabs_of = {}
    for t in snapshot["tabs"]:
        tabs_of.setdefault(t["workspace_id"], []).append(t)
    workspaces, agents, others, questions = [], {}, {}, {}
    tabs, tab_agents = {}, {}
    for ws in snapshot["workspaces"]:
        wid = ws["workspace_id"]
        tab = first_tab.get(wid)
        counts = dict(working=0, waiting=0, done=0, idle=0)
        tabs[wid] = []
        for t in tabs_of.get(wid, []):
            tid = t["tab_id"]
            rows = []
            should_read = tid == tab or (wid == subscribed and
                                         tid == (tab_id or tab))
            for pane in by_tab.get(tid, []):
                text = ""
                if should_read:
                    try:
                        text = read(pane["pane_id"])
                    except HerdrError:
                        pass
                q = hrdetect.find_question(text)
                status = pane.get("agent_status", "unknown")
                last = hrdetect.last_activity(text)
                if q:
                    qid = hrdetect.question_id(pane["pane_id"], q)
                    agent_label = "%s · %s" % (ws.get("label") or wid,
                                               pane.get("agent"))
                    questions[qid] = dict(q, id=qid, pane=pane["pane_id"],
                                          workspace=wid, agent=agent_label,
                                          display_agent=names.get(pane["pane_id"]) or pane.get("agent") or "Agent",
                                          tab="%s / %s" % (ws.get("label") or wid, t.get("label") or tid))
                    last = " ".join(q["lines"][-1:]) or last
                key = "waiting" if q or status == "blocked" else status
                if key in counts:
                    counts[key] += 1
                rows.append(dict(pane=pane["pane_id"], kind=pane.get("agent"),
                                 status="blocked" if q else status,
                                 question=bool(q), name=names.get(pane["pane_id"]),
                                 title=pane.get("terminal_title_stripped") or "",
                                 last=last))
            tab_agents[tid] = rows
            tabs[wid].append(dict(id=tid, label=t.get("label") or tid,
                                 status=best_state([status_code(r) for r in rows])))
        rows = tab_agents.get(tab, [])
        elsewhere = [r for t in tabs_of.get(wid, []) if t["tab_id"] != tab
                     for r in tab_agents[t["tab_id"]]]
        code = status_code
        workspaces.append(dict(
            id=wid, label=ws.get("label") or wid, focused=wid == focused_ws,
            tab=tab, tab1=best_state([code(r) for r in rows]),
            other=best_state([code(r) for r in elsewhere]), **counts))
        agents[wid] = rows
        others[wid] = elsewhere
    sessions = {a["pane_id"]: a.get("agent_session") for a in snapshot.get("agents", [])}
    pane_meta = {p["pane_id"]: dict(kind=p.get("agent") or "unknown",
                 agent_session=p.get("agent_session") or sessions.get(p["pane_id"]),
                 status=p.get("agent_status") or "unknown",
                 title=p.get("terminal_title_stripped") or "")
                 for p in snapshot["panes"]}
    for rows in tab_agents.values():
        for row in rows:
            pane_meta[row["pane"]]["status"] = row["status"]
    return dict(workspaces=workspaces, agents=agents, others=others,
                questions=questions, tabs=tabs, tab_agents=tab_agents,
                pane_meta=pane_meta,
                panes={p["pane_id"] for p in snapshot["panes"]})


def status_code(row):
    """A row's status letter, asking when it shows a question"""
    if row["question"]:
        return "b"
    return {"working": "w", "blocked": "b", "done": "d",
            "idle": "i"}.get(row["status"], "u")
