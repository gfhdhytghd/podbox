#!/usr/bin/env python3
"""HerdRemote: the herdr agent dashboard and remote for System > Agents.

Finds the iPod's PodLink tty, keeps a PodLink session open whenever the
player is attached with the Agents screen running, and keeps the screen's
picture of herdr current: workspaces, tabs, the selected tab's agents,
live pane detail, and choice questions. Commands from the player
focus local herdr clients (all clients on older servers) or answer the question.

    python3 tools/herdremote/herdremote.py            # run until stopped
    python3 tools/herdremote/herdremote.py --once     # print the model once

Dependencies: Python 3.9+, pyserial. Logs go to stdout.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import logging
import os
import queue
import re
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import podlink  # noqa: E402  tools/podlink.py: the PodLink frame codec

import hrdetect  # noqa: E402
import hrdetail  # noqa: E402
import hrherdr  # noqa: E402
import hrorder  # noqa: E402
import hrproto  # noqa: E402
import hrusage  # noqa: E402
import hrvoice  # noqa: E402
import hrkeys  # noqa: E402
import hrusagekeys  # noqa: E402
import hrsession  # noqa: E402
import hrappserver  # noqa: E402
import hrremotelog  # noqa: E402

VERSION = "herdremote-1"
DEVICE = dict(vid=0x05AC, pid=0x1261, manufacturer="PodBox",
              serial="2AA0000000000360")
REFRESH_SECONDS = 1.0
TICK_SECONDS = 2.0
CONFIRM_SECONDS = 5.0
DETAIL_SECONDS = 1.0
DETAIL_VISIBLE_LINES = 5
DETAIL_KEY_SETTLE_SECONDS = 0.075
# A live timer or token count in the working line changes every second and
# must not redraw the player's view; the line still refreshes this often.
DETAIL_VOLATILE_SECONDS = 15.0
VOLATILE = re.compile(rb"\d+(?:\.\d+)?[kKmM]? tokens|\d+(?:\.\d+)?[hms]\b(?: \d+[ms]\b)*")

log = logging.getLogger("herdremote")


def find_device(ports, device=DEVICE):
    """The one port that is this iPod in PodLink mode, or None.

    Every field must match; any other tty, including other ACM devices on
    this machine, is never returned and so never opened.
    """
    found = [p.device for p in ports
             if p.vid == device["vid"] and p.pid == device["pid"]
             and p.manufacturer == device["manufacturer"]
             and p.serial_number == device["serial"]]
    return found[0] if len(found) == 1 else None


class Answerer(threading.Thread):
    """Answers questions off the link thread: keys, then re-reads to confirm."""

    def __init__(self, herdr, results, changed):
        super().__init__(daemon=True)
        self.herdr, self.results, self.changed = herdr, results, changed
        self.jobs = queue.Queue()

    def run(self):
        while True:
            req, question, number = self.jobs.get()
            try:
                ok, text = self.answer(question, number)
            except (hrherdr.HerdrError, ValueError) as error:
                ok, text = False, str(error)
            log.info("answer %s pane=%s option=%s: %s %s", question["id"],
                     question["pane"], number, "ok" if ok else "failed", text)
            self.results.put((req, ok, text))
            self.changed.set()

    def current(self, pane):
        text = self.herdr.read(pane)
        q = hrdetect.find_question(text)
        return q, hrdetect.question_id(pane, q) if q else None

    def answer(self, question, number):
        pane = question["pane"]
        q, qid = self.current(pane)
        if qid != question["id"]:
            return False, "The question has changed"
        if q["method"] == "codex-queued":
            self.herdr.send_keys(pane, ["shift+left"])
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                time.sleep(0.2)
                opened, _ = self.current(pane)
                if opened and opened["method"] == "number":
                    q = opened
                    break
            else:
                return False, "Codex did not open the question"
        keys = hrdetect.answer_keys(q, number)
        before = hrdetect.question_id(pane, q)
        self.herdr.send_keys(pane, keys)
        deadline = time.monotonic() + CONFIRM_SECONDS
        while time.monotonic() < deadline:
            time.sleep(0.25)
            after, after_id = self.current(pane)
            if after is None:
                return True, "Answered"
            if after_id != before:
                return True, "Updated" if q["multi"] else "Answered"
        return False, "The pane did not change"


class Daemon:
    def __init__(self, args):
        self.args = args
        self.herdr = hrherdr.Herdr(args.socket)
        self.order = hrorder.Order(args.state)
        self.changed = threading.Event()
        self.results = queue.Queue()
        self.answerer = Answerer(self.herdr, self.results, self.changed)
        self.model = dict(workspaces=[], agents={}, others={}, questions={},
                          tabs={}, tab_agents={}, panes=set(), pane_meta={})
        self.state_seen = {}
        self.model_time = 0.0
        self.herdr_ok = False
        self.port = self.link = None
        self.stopping = threading.Event()
        self.usage = hrusage.UsageCache()
        self.keys = hrkeys.KeyTaps(hrkeys.KeyTool(getattr(args, "key_tool", None)),
                                  key_scale=getattr(args, "key_scale", None))
        self.usage_keys = hrusagekeys.UsageKeys(self.keys, lambda:
            bool(self.link and self.link.opened and self.sent_ws is not None and
                 self.foreground in (None, 1)))
        self.voice = hrvoice.HoldToTalk(
            self.voice_focus_target,
            voice_focus=getattr(args, "voice_focus", False),
            voice_max_hold=getattr(args, "voice_max_hold", 120),
            voice_method=getattr(args, "voice_method", "HoldStart"))
        self.reader = None
        self.model_job = self.detail_job = None
        self.model_key = None
        self.next_refresh = 0.0
        self.sessions = hrsession.Sessions()
        self.appserver = hrappserver.Reader()
        self.remote_log = hrremotelog.Writer()
        self.reset_session()

    def reset_session(self):
        self.session_generation = getattr(self, "session_generation", 0) + 1
        self.keys.reset()
        self.voice.stop()
        self.sent_ws = None
        self.sent_status = None
        self.sent_agents = None
        self.sent_tabs = None
        self.sent_questions = {}
        self.subscribed = None
        self.subscribed_tab = None
        self.detail_pane = None
        self.sent_detail = None
        self.sent_detail_key = None
        self.sent_detail_at = 0.0
        self.pending_detail = None
        self.detail_log_at = float("-inf")
        self.detail_log_suppressed = 0
        self.next_detail = 0.0
        self.detail_fast_due = None
        self.detail_edit_revision = getattr(self, "detail_edit_revision", 0) + 1
        self.key_details = {}
        self.next_tick = 0.0
        self.foreground = None
        self.usage_keys.update(False)

    # --- herdr ---------------------------------------------------------
    def refresh(self):
        """Synchronous refresh for --once and host callers; run uses a worker."""
        self.apply_model(self.read_model(self.herdr, self.subscribed,
                                         self.subscribed_tab, not self.herdr_ok))

    def read_model(self, client, workspace, tab, probe):
        try:
            if probe:
                client.detect_capabilities()
            snapshot = client.snapshot()

            def read(pane):
                if self.stopping.is_set():
                    raise hrherdr.HerdrError("herdremote stopping")
                return client.read(pane)

            if self.stopping.is_set():
                return None, time.monotonic(), "herdremote stopping"
            model = hrherdr.build_model(snapshot, read, workspace, tab)
            if self.stopping.is_set():
                return None, time.monotonic(), "herdremote stopping"
            return self.order.apply(model), time.monotonic(), None
        except hrherdr.HerdrError as error:
            return None, time.monotonic(), str(error)

    def apply_model(self, update):
        model, sampled, error = update
        if error is None:
            self.model, self.model_time = model, sampled
            seen = {}
            for pane, meta in model.get("pane_meta", {}).items():
                status = meta["status"]
                old = self.state_seen.get(pane)
                since = sampled if old and old[0] != status else old[1] if old else None
                seen[pane] = (status, since)
            self.state_seen = seen
            if not self.herdr_ok:
                log.info("herdr reachable at %s", self.herdr.path)
            self.herdr_ok = True
        else:
            if self.herdr_ok or not self.model_time:
                log.warning("herdr unreachable: %s", error)
            self.herdr_ok = False
            self.state_seen = {}  # transitions during an outage are unknown
            self.model_time = self.model_time or sampled

    def detail_context(self, pane):
        meta = dict(self.model.get("pane_meta", {}).get(pane, {}))
        meta.setdefault("kind", "unknown")
        meta.setdefault("status", "unknown")
        meta["title"] = hrdetail.clean_title(meta.get("title", ""))
        meta["resident"] = self.foreground is not None
        since = self.state_seen.get(pane, (None, None))[1]
        meta["state_age_s"] = -1 if since is None else max(0, int(time.monotonic() - since))
        return meta

    def detail_key(self, pane):
        meta = self.detail_context(pane)
        session = meta.get("agent_session") or {}
        return (meta["kind"], meta["status"], meta["title"],
                session.get("kind"), session.get("value"), self.herdr_ok,
                pane in self.model["panes"], self.detail_edit_revision)

    def detail_limit(self):
        return getattr(self.link, "max_message", hrproto.MAX_MESSAGE)

    def read_detail(self, pane, readable, meta=None, client=None, max_message=None):
        fields = (self.appserver.read(meta or {}) if (meta or {}).get("kind") == "codex"
                  else self.sessions.read(meta or {})) if readable else None
        if fields is not None:
            if not (meta or {}).get("resident"):
                fields.pop("revision", None)
            return hrproto.detail(pane, fields, max_message=self.detail_limit() if max_message is None else max_message)
        fields = {}
        if readable and not self.stopping.is_set():
            client = client or hrherdr.Herdr(self.herdr.path, self.herdr.timeout)
            try:
                record = client.read_record(pane, source="visible")
                # source=visible with no lines cannot request scrollback.
                fields = hrdetail.parse(record["text"], terminal_cursor=record.get("cursor"),
                                        **{k: v for k, v in (meta or {}).items()
                                           if k in ("kind", "status", "title", "state_age_s")})
                fields["say"] = "\n".join(fields.get("say", "").splitlines()[-DETAIL_VISIBLE_LINES:])
            except hrherdr.HerdrError:
                pass
        return hrproto.detail(pane, fields, max_message=self.detail_limit() if max_message is None else max_message)

    def send_detail(self, payload):
        fields = payload.split(b"\t")
        mode = b"known" if int(fields[2]) >= 0 else fields[2]
        # Revision and elapsed seconds are metadata, not visible changes.
        stable = fields[:2] + fields[3:11]
        stable[3] = VOLATILE.sub(b"#", stable[3])
        key = (tuple(stable), mode, self.detail_context(self.detail_pane)["status"])
        now = time.monotonic()
        legacy_due = (len(fields) != 12 and payload != self.sent_detail and
                      now - self.sent_detail_at >= DETAIL_VOLATILE_SECONDS)
        if key == self.sent_detail_key and not legacy_due:
            self.pending_detail = None
            return
        # Editing remains responsive. Streaming body/tool changes share a
        # one-second deadline, including changes caused by fast key reads.
        old = self.sent_detail.split(b"\t") if self.sent_detail else None
        same_body = old is not None and fields[1:2] + fields[3:9] == old[1:2] + old[3:9]
        if old is not None and not same_body and now - self.sent_detail_at < DETAIL_SECONDS:
            self.pending_detail = payload
            return
        self.pending_detail = None
        names = ("type", "pane", "age", "task", "now", "say", "step1",
                 "step2", "step3", "input", "cursor", "revision")
        changed = [names[i] for i, value in enumerate(fields)
                   if old is None or i >= len(old) or old[i] != value]
        args = (now, fields[1].decode("utf-8", "replace"),
                fields[11].decode() if len(fields) == 12 else "-",
                len(payload), ",".join(changed))
        log.debug("D time=%.3f pane=%s revision=%s bytes=%d changed=%s", *args)
        if now - self.detail_log_at >= DETAIL_SECONDS:
            log.info("D time=%.3f pane=%s revision=%s bytes=%d changed=%s suppressed=%d",
                     *args, self.detail_log_suppressed)
            self.detail_log_at, self.detail_log_suppressed = now, 0
        else:
            self.detail_log_suppressed += 1
        self.send(payload)
        self.sent_detail, self.sent_detail_key, self.sent_detail_at = payload, key, now

    def flush_detail(self):
        if self.pending_detail is not None and self.foreground in (None, 1):
            self.send_detail(self.pending_detail)

    def adopt_foreground(self):
        value = getattr(self.link, "foreground", None)
        if value == self.foreground:
            return
        previous, self.foreground = self.foreground, value
        self.detail_edit_revision += 1  # Reject a read begun in another visibility epoch.
        if value in (0, 2):
            self.voice.stop()
        if value == 1 and previous in (0, 2):
            # Keep subscriptions and the last sent picture; only changes go out.
            self.next_detail = 0.0
        if value != 1:
            self.usage_keys.update(False)
            self.detail_fast_due = None
            self.pending_detail = None

    def pump_reads(self):
        """Publish complete snapshots on the link thread, with bounded work.

        One model job and one detail job at most; never queue one per focus.
        Each worker owns its Herdr client, so probes cannot change the focus
        connection's capabilities. Stale subscription/session results are dropped.
        """
        self.flush_detail()
        key = (self.session_generation, self.subscribed, self.subscribed_tab)
        if self.model_job and self.model_job[1].done():
            requested, future = self.model_job
            self.model_job = None
            if requested == key:
                self.apply_model(future.result())
        if self.detail_job and self.detail_job[1].done():
            requested, future = self.detail_job
            self.detail_job = None
            if requested[0] == self.session_generation:
                if (self.foreground in (None, 1) and
                        requested[1] == self.detail_pane and self.sent_ws is not None and
                        requested[2] == self.detail_key(self.detail_pane)):
                    self.send_detail(future.result())
                # Switching detail cannot bypass the one-second send limit.
                self.next_detail = time.monotonic() + DETAIL_SECONDS
        now = time.monotonic()
        if self.model_job is None and (now >= self.next_refresh or key != self.model_key):
            self.changed.clear()
            self.model_key = key
            self.next_refresh = now + REFRESH_SECONDS
            client = hrherdr.Herdr(self.herdr.path, self.herdr.timeout)
            self.model_job = (key, self.reader.submit(
                self.read_model, client, self.subscribed, self.subscribed_tab,
                not self.herdr_ok))
        due = self.detail_fast_due if self.detail_fast_due is not None else self.next_detail
        if (self.foreground in (None, 1) and
                self.sent_ws is not None and self.detail_pane and
                self.detail_job is None and now >= due):
            pane = self.detail_pane
            self.detail_fast_due = None
            self.detail_job = ((self.session_generation, pane, self.detail_key(pane)),
                self.reader.submit(self.read_detail, pane,
                    self.herdr_ok and pane in self.model["panes"], self.detail_context(pane),
                    None, self.detail_limit()))

    # --- PodLink session -----------------------------------------------
    def open_link(self):
        if self.args.port:
            device = self.args.port
        else:
            from serial.tools import list_ports
            device = find_device(list_ports.comports())
        if not device:
            return False
        try:
            self.port = podlink.serial_port(device, 2)
            self.link = podlink.Link(self.port, 2)
            self.link.open()
        except (OSError, RuntimeError, TimeoutError, podlink.LinkClosed) as e:
            log.info("PodLink at %s not ready: %s", device, e)
            self.close_link()
            return False
        log.info("PodLink session open on %s", device)
        self.reset_session()
        self.send(hrproto.hello(VERSION))
        return True

    def close_link(self):
        self.reset_session()
        if self.link and self.link.opened:
            try:
                self.link.close(1001, "herdremote stopping")
            except (OSError, RuntimeError, TimeoutError, podlink.LinkClosed):
                pass
        if self.port:
            try:
                self.port.close()
            except OSError:
                pass
        self.port = self.link = None

    def send(self, payload):
        self.link.send(podlink.TEXT, payload)

    def flush_replies(self):
        """Publish replies before adopting reads that may predate a key batch."""
        if self.sent_ws is None:
            return
        while not self.results.empty():
            self.send(hrproto.result(*self.results.get()))
        for result in self.keys.replies():
            self.send(hrproto.result(*result))
            target = self.key_details.pop(result[0], None)
            if self.detail_pane and target == (self.session_generation, self.detail_pane):
                self.detail_edit_revision += 1
                if self.detail_fast_due is None:
                    self.detail_fast_due = time.monotonic() + DETAIL_KEY_SETTLE_SECONDS

    def push(self):
        """Send whatever the player's picture lacks."""
        if self.sent_ws is None or self.foreground == 2:
            return  # no Agents client has introduced itself
        self.flush_detail()
        self.flush_replies()
        if not self.model_time:
            return  # do not present an empty startup model while a read is pending
        ws = hrproto.workspaces(self.model["workspaces"])
        st = hrproto.status(self.model["workspaces"])
        if ws != self.sent_ws:
            self.send(ws)
            self.sent_ws = ws
            self.sent_status = None
        if st != self.sent_status:
            self.send(st)
            self.sent_status = st
        if self.subscribed:
            tabs = self.model["tabs"].get(self.subscribed, [])
            payload = hrproto.tabs(self.subscribed, tabs)
            if payload != self.sent_tabs:
                self.send(payload)
                self.sent_tabs = payload
            tid = self.subscribed_tab or (tabs[0]["id"] if tabs else None)
            rows = self.model["tab_agents"].get(tid, []) if any(
                t["id"] == tid for t in tabs) else []
            payload = hrproto.agents(self.subscribed, rows)
            if payload != self.sent_agents:
                self.send(payload)
                self.sent_agents = payload
        current = self.model["questions"]
        for qid in list(self.sent_questions):
            if qid not in current:
                self.send(hrproto.question_gone(qid))
                del self.sent_questions[qid]
        for qid, q in list(current.items())[:hrproto.MAX_QUESTIONS]:
            if qid not in self.sent_questions:
                self.send(hrproto.question(q))
                self.sent_questions[qid] = True
        now = time.monotonic()
        due = self.detail_fast_due if self.detail_fast_due is not None else self.next_detail
        if self.foreground in (None, 1) and self.reader is None and self.detail_pane and now >= due:
            # Share one deadline across switches: repeated det cannot bypass
            # the read/send limit. Never read a pane absent from the snapshot.
            self.detail_fast_due = None
            self.send_detail(self.read_detail(self.detail_pane,
                self.herdr_ok and self.detail_pane in self.model["panes"],
                self.detail_context(self.detail_pane), self.herdr))
            # Measure from completion: a slow read/send must not permit two
            # detail frames less than one second apart.
            now = time.monotonic()
            self.next_detail = now + DETAIL_SECONDS
        if now >= self.next_tick:
            self.send(hrproto.tick(now - self.model_time, self.herdr_ok))
            self.next_tick = now + TICK_SECONDS

    def command(self, payload):
        if self.remote_log.submit(payload):
            return
        parsed = hrproto.parse_command(payload)
        if not parsed:
            log.info("ignored message from player: %r", payload[:40])
            return
        name, args = parsed
        if name == "hr":
            self.reset_session()
            self.sent_ws = b""
        elif name == "sub":
            self.subscribed = None if args[0] == "-" else args[0]
            self.subscribed_tab = args[1] if len(args) == 2 else None
            self.sent_agents = None
            self.sent_tabs = None
            self.changed.set()
        elif name == "det":
            pane = None if args[0] == "-" else args[0]
            if pane != self.detail_pane:
                self.sent_detail = None
                self.sent_detail_key = None
                self.pending_detail = None
                self.detail_fast_due = None
                self.key_details.clear()
            self.detail_pane = pane
        elif name == "us":
            self.send(hrproto.usage(self.usage.snapshot(), time.time()))
            self.usage.request()
        elif name == "vw":
            self.usage_keys.update(args[0] == "1")
        elif name in ("fw", "fp", "ft"):
            self.focus(name, *args)
        elif name in ("vd", "vu", "vc"):
            self.voice_command(name, *args)
        elif name == "k":
            self.key_command(*args)
        elif name == "a":
            req, qid, number = args
            q = self.model["questions"].get(qid)
            if not q or not number.isdigit():
                self.send(hrproto.result(req, False, "The question has gone"))
            else:
                self.answerer.jobs.put((req, q, int(number)))

    def key_command(self, req, key, count=1):
        result = ((False, "Session not ready") if self.sent_ws is None else
                  self.keys.request(req, key, count))
        if result is None and self.detail_pane:
            self.key_details[req] = (self.session_generation, self.detail_pane)
        if result is not None:
            self.send(hrproto.result(req, *result))

    def voice_focus_target(self, target):
        params = dict(pane_id=target)
        if target.startswith("w:"):
            wid = target[2:]
            ws = next((w for w in self.model["workspaces"]
                       if w["id"] == wid), None)
            if not ws:
                raise hrvoice.VoiceError("no workspace %s" % wid)
            params = dict(workspace_id=wid)
            if ws.get("tab"):
                params["tab_id"] = ws["tab"]
        try:
            # Voice must never use the legacy all-client focus fallback.
            if not self.herdr._focus_local(**params):
                raise hrvoice.VoiceError("Local herdr focus unavailable")
        except hrherdr.HerdrError as error:
            raise hrvoice.VoiceError(str(error)) from error
        self.changed.set()

    def voice_command(self, name, req, target=None):
        result = (self.voice.press(target) if name == "vd" else
                  self.voice.discard() if name == "vc" else self.voice.release())
        self.send(hrproto.result(req, *result))

    def focus(self, name, req, target, tab_id=None):
        try:
            if name in ("fw", "ft"):
                ws = next((w for w in self.model["workspaces"]
                           if w["id"] == target), None)
                if not ws:
                    raise hrherdr.HerdrError("no workspace %s" % target)
                if name == "ft":
                    tab = next((t for t in self.model["tabs"].get(target, [])
                                if t["id"] == tab_id), None)
                    if not tab:
                        raise hrherdr.HerdrError("no tab %s in workspace %s" %
                                                (tab_id, target))
                    self.herdr.focus_tab(target, tab_id)
                    text = "Focused " + tab["label"]
                else:
                    self.herdr.focus_workspace(target, ws["tab"])
                    text = "Focused " + ws["label"]
            else:
                self.herdr.focus_pane(target)
                text = "Focused pane"
            self.send(hrproto.result(req, True, text))
            log.debug("%s %s", text, target)
        except hrherdr.HerdrError as error:
            self.send(hrproto.result(req, False, str(error)))
            log.warning("focus %s failed: %s", target, error)

    def run(self):
        self.usage_keys.start()
        self.usage.start()
        hrherdr.Watcher(self.herdr, self.changed).start()
        self.answerer.start()
        self.reader = ThreadPoolExecutor(max_workers=2, thread_name_prefix="herdremote-read")
        try:
            self.run_loop()
        finally:
            self.stopping.set()
            self.remote_log.close()
            self.usage_keys.close()
            self.keys.reset()
            self.voice.stop()
            if self.link:
                self.close_link()
            self.reader.shutdown(wait=True, cancel_futures=True)
            self.reader = None
            self.usage.close()

    def run_loop(self):
        next_probe = 0.0
        while not self.stopping.is_set():
            now = time.monotonic()
            if not self.link:
                self.pump_reads()
                if now >= next_probe:
                    next_probe = now + 1.0
                    self.open_link()
                time.sleep(0.05)
                continue
            try:
                self.link.poll()
                while self.link.frames:
                    frame = self.link.frames.popleft()
                    if frame.type == podlink.TEXT:
                        self.command(frame.payload)
                self.adopt_foreground()
                self.usage_keys.poll()
                self.flush_replies()
                self.pump_reads()
                self.push()
            except podlink.LinkClosed as error:
                log.info("PodLink closed (%s): %s", error.code, error.reason)
                self.link.opened = False
                self.close_link()
                continue
            except (OSError, TimeoutError, ValueError) as error:
                log.info("PodLink lost: %s", error)
                self.close_link()
                continue
            time.sleep(0.02)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--socket", help="herdr API socket "
                        "(default: $HERDR_SOCKET_PATH or ~/.config/herdr)")
    parser.add_argument("--port", help="use this tty instead of finding the "
                        "iPod (testing only)")
    parser.add_argument("--state", default=hrorder.default_path(),
                        help="file that keeps the list order across restarts "
                        "(default: %(default)s)")
    parser.add_argument("--once", action="store_true",
                        help="print the model as the player would get it, "
                        "then exit")
    parser.add_argument("--usage-dump", action="store_true",
                        help="print one read-only usage frame; no herdr/serial access")
    parser.add_argument("--usage-wait", type=float, default=6,
                        help="usage-dump indexing budget in seconds (default: 6)")
    hrvoice.add_arguments(parser)
    hrkeys.add_arguments(parser)
    parser.add_argument("--debug", action="store_true", help="log metadata for every detail frame")
    args = parser.parse_args()
    logging.basicConfig(stream=sys.stdout, level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if args.usage_dump:
        cache = hrusage.UsageCache()
        try:
            dump_started = time.time()
            cache.request()
            deadline = time.monotonic() + max(0, args.usage_wait)
            while time.monotonic() < deadline:
                rows = cache.snapshot()
                if all(r.get("updated_at",dump_started) >= dump_started and
                       not r.get("indexing") and
                       not r["note"].startswith("indexing ") for r in rows):
                    break
                cache.request()
                time.sleep(0.02)
            print(hrproto.usage(cache.snapshot(), time.time()).decode())
        finally:
            cache.close()
        return
    daemon = Daemon(args)
    if args.once:
        daemon.refresh()
        for record in hrproto.decode(hrproto.workspaces(
                daemon.model["workspaces"])):
            print("\t".join(record))
        for record in hrproto.decode(hrproto.status(
                daemon.model["workspaces"])):
            print("\t".join(record))
        for q in daemon.model["questions"].values():
            print(hrproto.question(q).decode())
        return
    # systemd stops the service with SIGTERM: close the session cleanly
    signal.signal(signal.SIGTERM, lambda *_: daemon.stopping.set())
    try:
        daemon.run()
    except KeyboardInterrupt:
        pass
    finally:
        daemon.close_link()
        daemon.usage.close()


if __name__ == "__main__":
    main()
