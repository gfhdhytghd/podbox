"""The order the player lists workspaces, agents and questions in.

Agents that need a person come first, oldest question first; then agents
that are working; then the rest, the most recently active first. A
workspace ranks as its best agent. herdr's panes carry no times, so this
module records them: when each pane began asking, and when its status last
changed. A sort key changes only when a status does, so a list never
reorders between polls without a reason.

The times are kept in a small JSON file, so a restart keeps the order. A
pane with no recorded change keeps herdr's order, after the ones that have.
"""
import json
import logging
import os
import time

log = logging.getLogger("herdremote")

ASKING, WORKING, OTHER = 0, 1, 2


def default_path():
    base = os.environ.get("STATE_DIRECTORY") or os.path.join(
        os.environ.get("XDG_STATE_HOME") or
        os.path.expanduser("~/.local/state"), "herdremote")
    return os.path.join(base.split(":")[0], "order.json")


def rank(row):
    if row["question"] or row["status"] == "blocked":
        return ASKING
    return WORKING if row["status"] == "working" else OTHER


class Order:
    def __init__(self, path=None, clock=time.time):
        self.path = path
        self.clock = clock
        self.panes = {}
        self.warned = False
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    panes = json.load(f).get("panes", {})
                self.panes = {p: r for p, r in panes.items()
                              if isinstance(r, dict)}
            except (OSError, ValueError, AttributeError):
                pass

    def note(self, rows):
        """Record status changes; True when anything changed."""
        now = self.clock()
        changed = False
        seen = set()
        for row in rows:
            pane, r = row["pane"], rank(row)
            seen.add(pane)
            rec = self.panes.get(pane)
            if rec is None:
                # First sight: when it last changed is unknown
                rec = self.panes[pane] = dict(status=row["status"],
                                              active=None, asked=None)
                if r == ASKING:
                    rec["asked"] = now
                changed = True
                continue
            if rec.get("status") != row["status"]:
                rec["status"] = row["status"]
                rec["active"] = now
                changed = True
            if r == ASKING and rec.get("asked") is None:
                rec["asked"] = now
                changed = True
            elif r != ASKING and rec.get("asked") is not None:
                rec["asked"] = None
                changed = True
        for pane in set(self.panes) - seen:
            del self.panes[pane]
            changed = True
        return changed

    def key(self, row, index):
        """Sort key of one agent row; index is its place in herdr's order."""
        rec = self.panes.get(row["pane"], {})
        r = rank(row)
        if r == ASKING:
            asked = rec.get("asked")
            return (r, asked if asked is not None else float("inf"), index)
        if r == WORKING:
            return (r, 0, index)
        active = rec.get("active")
        return (r, -active if active is not None else float("inf"), index)

    def apply(self, model):
        """Sort model in place and return it."""
        rows = [row for ws in model["workspaces"]
                for row in model["agents"].get(ws["id"], [])]
        if self.note(rows):
            self.save()
        best = {}
        for wid, agents in model["agents"].items():
            keyed = sorted(((self.key(row, i), row)
                            for i, row in enumerate(agents)),
                           key=lambda kr: kr[0])
            agents[:] = [row for _, row in keyed]
            if keyed:
                best[wid] = keyed[0][0][:2]
        none = (OTHER, float("inf"))
        order = sorted(enumerate(model["workspaces"]),
                       key=lambda iw: best.get(iw[1]["id"], none) + (iw[0],))
        model["workspaces"][:] = [ws for _, ws in order]
        asked = {p: r.get("asked") for p, r in self.panes.items()}
        model["questions"] = dict(sorted(
            model["questions"].items(),
            key=lambda kv: asked.get(kv[1]["pane"]) or float("inf")))
        return model

    def save(self):
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(dict(panes=self.panes), f)
            os.replace(tmp, self.path)
        except OSError as error:
            if not self.warned:
                log.warning("cannot save the order to %s: %s",
                            self.path, error)
                self.warned = True
