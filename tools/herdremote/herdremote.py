#!/usr/bin/env python3
"""HerdRemote: the herdr agent dashboard and remote for System > Agents.

Finds the iPod's PodLink tty, keeps a PodLink session open whenever the
player is attached with the Agents screen running, and keeps the screen's
picture of herdr current: workspaces, the agents in each workspace's first
tab, and any choice question an agent is asking. Commands from the player
focus herdr's local client or answer the question.

    python3 tools/herdremote/herdremote.py            # run until stopped
    python3 tools/herdremote/herdremote.py --once     # print the model once

Dependencies: Python 3.9+, pyserial. Logs go to stdout.
"""
import argparse
import logging
import os
import queue
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import podlink  # noqa: E402  tools/podlink.py: the PodLink frame codec

import hrdetect  # noqa: E402
import hrherdr  # noqa: E402
import hrorder  # noqa: E402
import hrproto  # noqa: E402

VERSION = "herdremote-1"
DEVICE = dict(vid=0x05AC, pid=0x1261, manufacturer="PodBox",
              serial="2AA0000000000360")
REFRESH_SECONDS = 1.0
TICK_SECONDS = 2.0
CONFIRM_SECONDS = 5.0

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
        self.model = dict(workspaces=[], agents={}, questions={})
        self.model_time = 0.0
        self.herdr_ok = False
        self.port = self.link = None
        self.stopping = threading.Event()
        self.reset_session()

    def reset_session(self):
        self.sent_ws = None
        self.sent_agents = None
        self.sent_questions = {}
        self.subscribed = None
        self.next_tick = 0.0

    # --- herdr ---------------------------------------------------------
    def refresh(self):
        try:
            snapshot = self.herdr.snapshot()
            self.model = self.order.apply(
                hrherdr.build_model(snapshot, self.herdr.read))
            self.model_time = time.monotonic()
            if not self.herdr_ok:
                log.info("herdr reachable at %s", self.herdr.path)
            self.herdr_ok = True
        except hrherdr.HerdrError as error:
            if self.herdr_ok or not self.model_time:
                log.warning("herdr unreachable: %s", error)
            self.herdr_ok = False
            self.model_time = self.model_time or time.monotonic()

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

    def push(self):
        """Send whatever the player's picture lacks."""
        if self.sent_ws is None:
            return  # the screen has not said hello yet
        ws = hrproto.workspaces(self.model["workspaces"])
        if ws != self.sent_ws:
            self.send(ws)
            self.sent_ws = ws
        if self.subscribed:
            rows = self.model["agents"].get(self.subscribed, [])
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
        while not self.results.empty():
            self.send(hrproto.result(*self.results.get()))
        now = time.monotonic()
        if now >= self.next_tick:
            self.send(hrproto.tick(now - self.model_time, self.herdr_ok))
            self.next_tick = now + TICK_SECONDS

    def command(self, payload):
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
            self.sent_agents = None
        elif name in ("fw", "fp"):
            self.focus(name, *args)
        elif name == "a":
            req, qid, number = args
            q = self.model["questions"].get(qid)
            if not q or not number.isdigit():
                self.send(hrproto.result(req, False, "The question has gone"))
            else:
                self.answerer.jobs.put((req, q, int(number)))

    def focus(self, name, req, target):
        try:
            if name == "fw":
                ws = next((w for w in self.model["workspaces"]
                           if w["id"] == target), None)
                if not ws:
                    raise hrherdr.HerdrError("no workspace %s" % target)
                self.herdr.focus_workspace(target, ws["tab"])
                text = "Focused " + ws["label"]
            else:
                self.herdr.focus_pane(target)
                text = "Focused pane"
            log.info("%s %s", text, target)
            self.send(hrproto.result(req, True, text))
        except hrherdr.HerdrError as error:
            log.warning("focus %s failed: %s", target, error)
            self.send(hrproto.result(req, False, str(error)))
        self.changed.set()

    def run(self):
        hrherdr.Watcher(self.herdr, self.changed).start()
        self.answerer.start()
        next_refresh = next_probe = 0.0
        while not self.stopping.is_set():
            now = time.monotonic()
            if now >= next_refresh or (self.changed.is_set() and
                                       now >= next_refresh - 0.75):
                self.changed.clear()
                self.refresh()
                next_refresh = now + REFRESH_SECONDS
            if not self.link:
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
        if self.link:
            self.close_link()


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
    args = parser.parse_args()
    logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    daemon = Daemon(args)
    if args.once:
        daemon.refresh()
        for record in hrproto.decode(hrproto.workspaces(
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


if __name__ == "__main__":
    main()
