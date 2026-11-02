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

    def snapshot(self):
        return self.call("session.snapshot")["snapshot"]

    def read(self, pane_id):
        return self.call("pane.read", pane_id=pane_id,
                         source="visible")["read"]["text"]

    def send_keys(self, pane_id, keys):
        self.call("pane.send_keys", pane_id=pane_id, keys=list(keys))

    def focus_workspace(self, workspace_id, tab_id=None):
        self.call("workspace.focus", workspace_id=workspace_id)
        if tab_id:
            self.call("tab.focus", tab_id=tab_id)

    def focus_pane(self, pane_id):
        self.call("pane.focus", pane_id=pane_id)


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


def build_model(snapshot, read):
    """The dashboard model from a herdr snapshot.

    read(pane_id) returns a pane's visible text. Tab 1 is a workspace's first
    tab in herdr's order, which is the order its tab bar shows; its own
    number is a creation count and is not used.
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
    workspaces, agents, questions = [], {}, {}
    for ws in snapshot["workspaces"]:
        wid = ws["workspace_id"]
        tab = first_tab.get(wid)
        counts = dict(working=0, waiting=0, done=0, idle=0)
        rows = []
        for pane in by_tab.get(tab, []):
            try:
                text = read(pane["pane_id"])
            except HerdrError:
                text = ""
            q = hrdetect.find_question(text)
            status = pane.get("agent_status", "unknown")
            last = hrdetect.last_activity(text)
            if q:
                qid = hrdetect.question_id(pane["pane_id"], q)
                agent_label = "%s · %s" % (ws.get("label") or wid,
                                           pane.get("agent"))
                questions[qid] = dict(q, id=qid, pane=pane["pane_id"],
                                      workspace=wid, agent=agent_label)
                last = " ".join(q["lines"][-1:]) or last
            if q or status == "blocked":
                counts["waiting"] += 1
            elif status in counts:
                counts[status] += 1
            rows.append(dict(pane=pane["pane_id"], kind=pane.get("agent"),
                             status="blocked" if q else status,
                             question=bool(q), name=names.get(pane["pane_id"]),
                             title=pane.get("terminal_title_stripped") or "",
                             last=last))
        workspaces.append(dict(id=wid, label=ws.get("label") or wid,
                               focused=wid == focused_ws, tab=tab, **counts))
        agents[wid] = rows
    return dict(workspaces=workspaces, agents=agents, questions=questions)
