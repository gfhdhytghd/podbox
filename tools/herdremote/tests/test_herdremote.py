"""HerdRemote host tests: pane parsing against captured panes, the message
schema, device selection, and the whole daemon against a fake herdr socket
and a fake iPod on a pty.

    python3 -m unittest discover -s tools/herdremote/tests -v
"""
import json
import os
import pty
import socket
import struct
import sys
import tempfile
import threading
import time
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

import hrdetect  # noqa: E402
import hrherdr  # noqa: E402
import hrproto  # noqa: E402
import herdremote  # noqa: E402
import podlink  # noqa: E402


def sample(name):
    with open(os.path.join(HERE, "samples", name), encoding="utf-8") as f:
        return f.read()


class Detection(unittest.TestCase):
    def q(self, name):
        return hrdetect.find_question(sample(name))

    def test_claude_ask_user_question(self):
        q = self.q("claude-ask.txt")
        self.assertEqual(q["lines"], ["Colour", "你想用哪种颜色? Which colour?"])
        self.assertEqual([n for n, _ in q["options"]], [1, 2, 3])
        self.assertEqual(q["options"][0][1], "红色 Red — Warm")
        self.assertEqual(q["method"], "number")
        self.assertEqual(hrdetect.answer_keys(q, 3), ["3"])

    def test_unanswerable_rows_are_left_out_but_counted(self):
        q = self.q("claude-ask.txt")
        labels = [label for _, label in q["options"]]
        self.assertNotIn("Type something.", labels)
        self.assertEqual(q["rows"], [1, 2, 3, 4, 5])

    def test_claude_multi_select(self):
        q = self.q("claude-ask-multi-q2.txt")
        self.assertTrue(q["multi"])
        self.assertEqual(q["options"][0][1], "[ ] Cheese — Cheese topping")
        self.assertEqual(hrdetect.answer_keys(q, 2), ["2"])
        # Submit sits under option 4; the cursor is on 1.
        self.assertEqual(hrdetect.answer_keys(q, 0), ["down"] * 4 + ["enter"])

    def test_claude_review_page_uses_arrows(self):
        q = self.q("claude-ask-submit.txt")
        self.assertEqual(q["options"], [(1, "Submit answers"), (2, "Cancel")])
        self.assertEqual(hrdetect.answer_keys(q, 1), ["enter"])
        self.assertEqual(hrdetect.answer_keys(q, 2), ["down", "enter"])

    def test_claude_permission_prompts(self):
        q = self.q("claude-perm-bash.txt")
        self.assertEqual(q["lines"][-1], "Do you want to proceed?")
        self.assertIn("echo herdremote-permission-test > note.txt", q["lines"])
        self.assertEqual(len(q["options"]), 4)
        self.assertTrue(q["options"][1][1].startswith(
            "Yes, and always allow access to — /tmp/"))
        q = self.q("claude-perm-write.txt")
        self.assertEqual(q["lines"][-1], "Do you want to create poem.txt?")
        self.assertEqual([n for n, _ in q["options"]], [1, 2, 3])

    def test_claude_trust_dialog_has_no_numbers(self):
        q = self.q("claude-trust.txt")
        self.assertEqual(q["method"], "arrows")
        self.assertEqual(q["options"], [(1, "No, exit"),
                                        (2, "Yes, I trust this folder")])
        self.assertEqual(hrdetect.answer_keys(q, 2), ["down", "enter"])

    def test_codex_approval_and_choices(self):
        q = self.q("codex-approval.txt")
        self.assertEqual(q["lines"][0],
                         "Would you like to run the following command?")
        self.assertEqual(q["options"][0], (1, "Yes, proceed (y)"))
        self.assertEqual(hrdetect.answer_keys(q, 1), ["1"])
        q = self.q("codex-resume-choice.txt")
        self.assertEqual(len(q["options"]), 4)
        self.assertEqual(q["lines"][0], "Working directory · resume")

    def test_codex_question_queued_then_open(self):
        q = self.q("codex-question2-a.txt")
        self.assertEqual(q["method"], "codex-queued")
        self.assertEqual(q["options"], [(1, "Red"), (2, "Green"), (3, "Blue")])
        q = self.q("codex-question2-b.txt")
        self.assertEqual(q["method"], "number")
        self.assertEqual([label for _, label in q["options"]],
                         ["Red", "Green", "Blue"])
        self.assertIsNone(self.q("codex-question2-c.txt"))

    def test_no_question_in_ordinary_panes(self):
        for name in ("codex-start.txt", "claude-working.txt",
                     "codex-idle-narrow.txt", "claude-idle-done.txt"):
            self.assertIsNone(self.q(name), name)

    def test_last_activity(self):
        self.assertEqual(hrdetect.last_activity(sample("claude-working.txt")),
                         "Deciphering… (2m 34s · ↓ 3.7k tokens)")
        self.assertEqual(
            hrdetect.last_activity(sample("claude-idle-done.txt")),
            "Should I deploy it to Vultr with tools/deploy-vultr.sh 0a4bae1?")
        self.assertEqual(hrdetect.last_activity(sample("codex-idle-narrow.txt")),
                         "Service tier set to default")

    def test_question_id_changes_with_checkbox_state(self):
        a = self.q("claude-ask-multi-q2.txt")
        b = dict(a, options=[(1, "[x] Cheese — Cheese topping")] +
                 a["options"][1:])
        self.assertNotEqual(hrdetect.question_id("p", a),
                            hrdetect.question_id("p", b))


class Protocol(unittest.TestCase):
    def test_round_trip_and_cleaning(self):
        payload = hrproto.agents("w1", [dict(
            pane="w1:p1", kind="claude", status="working", question=False,
            title="标题\twith tab", last="line\nbreak")])
        records = hrproto.decode(payload)
        self.assertEqual(records[0], ["A", "w1", "1"])
        self.assertEqual(records[1], ["w1:p1", "claude", "w", "0", "",
                                      "标题 with tab", "line break"])

    def test_utf8_cut_on_boundary(self):
        self.assertEqual(hrproto.clean("汉字汉字", 7), "汉字")

    def test_frames_never_exceed_podlink_maximum(self):
        rows = [dict(pane="w1:p%d" % i, kind="claude", status="idle",
                     question=False, title="题" * 60, last="行" * 60)
                for i in range(40)]
        payload = hrproto.agents("w1", rows)
        self.assertLessEqual(len(payload), hrproto.MAX_MESSAGE)
        records = hrproto.decode(payload)
        self.assertEqual(int(records[0][2]), len(records) - 1)
        ws = [dict(id="w%d" % i, label="空间" * 30, focused=False,
                   working=1, waiting=0, done=0, idle=0) for i in range(80)]
        self.assertLessEqual(len(hrproto.workspaces(ws)), hrproto.MAX_MESSAGE)

    def test_question_message(self):
        q = hrdetect.find_question(sample("claude-ask.txt"))
        q.update(id="abcd1234", pane="w1:p1", workspace="w1", agent="ws · c")
        records = hrproto.decode(hrproto.question(q))
        self.assertEqual(records[0], ["Q", "abcd1234", "w1:p1", "w1", "-",
                                      "ws · c"])
        self.assertEqual(records[1:3], [["?", "Colour"],
                                        ["?", "你想用哪种颜色? Which colour?"]])
        self.assertEqual(records[3], ["o", "1", "红色 Red — Warm"])

    def test_commands(self):
        self.assertEqual(hrproto.parse_command(b"a\t7\tabcd1234\t2"),
                         ("a", ["7", "abcd1234", "2"]))
        self.assertIsNone(hrproto.parse_command(b"a\t7"))
        self.assertIsNone(hrproto.parse_command(b"HR\t1\tx"))
        self.assertIsNone(hrproto.parse_command(b"\xff"))


class DeviceSelection(unittest.TestCase):
    def port(self, device, **kw):
        fields = dict(vid=0x05AC, pid=0x1261, manufacturer="PodBox",
                      serial_number="2AA0000000000360")
        fields.update(kw)
        return types.SimpleNamespace(device=device, **fields)

    def test_only_the_ipod_in_link_mode(self):
        ports = [types.SimpleNamespace(device="/dev/ttyACM0", vid=0x2E8A,
                                       pid=5, manufacturer="x",
                                       serial_number="1"),
                 self.port("/dev/ttyACM3")]
        self.assertEqual(herdremote.find_device(ports), "/dev/ttyACM3")

    def test_rejects_near_misses(self):
        for kw in (dict(serial_number="9AA0000000000360"),
                   dict(manufacturer="Apple Inc."), dict(pid=0x1260)):
            self.assertIsNone(herdremote.find_device([self.port("/x", **kw)]))
        self.assertIsNone(herdremote.find_device(
            [self.port("/a"), self.port("/b")]))


class CrossLanguage(unittest.TestCase):
    """The player's C parser reads what this daemon writes."""

    def test_c_model_reads_python_messages(self):
        import shutil
        import subprocess
        cc = shutil.which("cc") or shutil.which("gcc")
        if not cc:
            self.skipTest("no host C compiler")
        root = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
        src = os.path.join(root, "apps-ipod", "screens", "system")
        with tempfile.TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "hrmodel")
            subprocess.check_call([cc, "-std=gnu11", "-Wall", "-Werror",
                "-I" + src, os.path.join(root, "tools", "tests",
                                         "herdremote_model_test.c"),
                os.path.join(src, "herdremote_model.c"), "-o", exe])
            q = hrdetect.find_question(sample("claude-perm-bash.txt"))
            q.update(id="0badf00d", pane="w1:p1", workspace="w1",
                     agent="项目 · claude")
            messages = [
                hrproto.hello("herdremote-1"),
                hrproto.workspaces([dict(id="w1", label="项目" * 20,
                    focused=True, working=1, waiting=1, done=0, idle=2)]),
                hrproto.agents("w1", [dict(pane="w1:p1", kind="claude",
                    status="blocked", question=True, name=None,
                    title="标题", last="行" * 40)]),
                hrproto.question(q),
                hrproto.result("5", True, "Answered"),
                hrproto.tick(3, True)]
            path = os.path.join(tmp, "messages")
            with open(path, "wb") as f:
                for m in messages:
                    f.write(struct.pack("<H", len(m)) + m)
            out = subprocess.check_output([exe, path]).decode("utf-8")
        lines = out.splitlines()
        self.assertEqual(lines[0], "hello=1 herdr_ok=1 age=3")
        # The daemon cuts to its byte budgets on a character boundary, and
        # each budget fits the player's field.
        self.assertEqual(lines[1], "W w1 1 1 1 0 2 " + "项目" * 6 + "项")
        self.assertEqual(lines[2], "A w1 w1:p1 claude b 1 |标题|" + "行" * 30)
        self.assertEqual(lines[3], "Q 0badf00d w1:p1 w1 0 项目 · claude")
        self.assertIn("? Do you want to proceed?", lines)
        self.assertIn("o 4 No", lines)
        self.assertEqual(lines[-1], "R 5 1 Answered")


# --- end to end ---------------------------------------------------------
class FakeHerdr(threading.Thread):
    """A herdr socket with one workspace, one Claude agent and a dialog."""

    def __init__(self, path):
        super().__init__(daemon=True)
        self.path = path
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(path)
        self.server.listen(8)
        self.pane_text = sample("claude-ask.txt")
        self.calls = []

    def snapshot(self):
        return dict(
            focused_workspace_id="w1",
            agents=[dict(pane_id="w1:p1", name="asker")],
            workspaces=[dict(workspace_id="w1", label="项目"),
                        dict(workspace_id="w2", label="other")],
            tabs=[dict(tab_id="w1:t1", workspace_id="w1"),
                  dict(tab_id="w2:t3", workspace_id="w2"),
                  dict(tab_id="w2:t1", workspace_id="w2")],
            panes=[dict(pane_id="w1:p1", tab_id="w1:t1", agent="claude",
                        agent_status="blocked",
                        terminal_title_stripped="问题"),
                   dict(pane_id="w1:p2", tab_id="w1:t1", agent=None),
                   dict(pane_id="w2:p9", tab_id="w2:t1", agent="codex",
                        agent_status="working")])

    def run(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                data = b""
                while not data.endswith(b"\n"):
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                if not data:
                    continue
                request = json.loads(data)
                method, params = request["method"], request["params"]
                self.calls.append((method, params))
                if method == "events.subscribe":
                    conn.sendall(b'{"id":"watch","result":{}}\n')
                    time.sleep(0.2)
                    continue
                if method == "session.snapshot":
                    result = dict(snapshot=self.snapshot())
                elif method == "pane.read":
                    text = self.pane_text if params["pane_id"] == "w1:p1" \
                        else sample("codex-idle-narrow.txt")
                    result = dict(read=dict(text=text))
                elif method == "pane.send_keys":
                    if params["keys"] == ["3"]:
                        self.pane_text = sample("claude-idle-done.txt")
                    result = {}
                else:
                    result = {}
                conn.sendall(json.dumps(dict(id=request["id"],
                                             result=result)).encode() + b"\n")


class FakeIpod:
    """The device end of PodLink on a pty master."""

    def __init__(self):
        self.master, slave = pty.openpty()
        self.path = os.ttyname(slave)
        self.slave = slave
        self.decoder = podlink.Decoder()
        self.messages = []
        self.seq = 1
        self.write(podlink.HELLO, struct.pack("<HI", 1, 4096))

    def write(self, kind, payload=b"", seq=None):
        if seq is None:
            seq, self.seq = self.seq, self.seq + 1
        os.write(self.master, podlink.encode(kind, seq, payload))

    def pump(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            import select
            ready, _, _ = select.select([self.master], [], [], 0.05)
            if not ready:
                continue
            for frame in self.decoder.feed(os.read(self.master, 4096)):
                if frame.type == podlink.OPEN:
                    self.write(podlink.OPEN, frame.payload, seq=frame.seq)
                elif frame.type == podlink.PING:
                    self.write(podlink.PONG, frame.payload, seq=frame.seq)
                elif frame.type == podlink.TEXT:
                    self.messages.append(hrproto.decode(frame.payload))

    def wait_for(self, kind, seconds=5):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            for m in self.messages:
                if m[0][0] == kind:
                    self.messages.remove(m)
                    return m
            self.pump(0.1)
        raise AssertionError("no %s message; have %r" % (kind, self.messages))

    def send(self, text):
        self.write(podlink.TEXT, text.encode())

    def close(self):
        os.close(self.master)
        os.close(self.slave)


@unittest.skipUnless(hasattr(pty, "openpty"), "needs a pty")
class EndToEnd(unittest.TestCase):
    def setUp(self):
        try:
            import serial  # noqa: F401
        except ImportError:
            self.skipTest("pyserial not installed")
        self.tmp = tempfile.TemporaryDirectory()
        self.herdr = FakeHerdr(os.path.join(self.tmp.name, "herdr.sock"))
        self.herdr.start()
        self.ipod = FakeIpod()
        args = types.SimpleNamespace(socket=self.herdr.path,
                                     port=self.ipod.path, once=False)
        self.daemon = herdremote.Daemon(args)
        herdremote.CONFIRM_SECONDS = 2
        self.thread = threading.Thread(target=self.daemon.run, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.daemon.stopping.set()
        self.thread.join(5)
        self.herdr.server.close()
        self.tmp.cleanup()

    def test_session(self):
        ipod = self.ipod
        ipod.pump(1.0)
        self.assertEqual(ipod.messages.pop(0)[0], ["HR", "1", "herdremote-1"])
        ipod.send("hr\t1")
        ws = ipod.wait_for("W")
        self.assertEqual(ws[0], ["W", "2"])
        self.assertEqual(ws[1], ["w1", "f", "0", "1", "0", "0", "项目"])
        # w2's codex sits in w2:t1, which is not w2's first tab.
        self.assertEqual(ws[2], ["w2", "-", "0", "0", "0", "0", "other"])
        q = ipod.wait_for("Q")
        qid = q[0][1]
        self.assertEqual(q[0][2:], ["w1:p1", "w1", "-", "项目 · claude"])

        ipod.send("sub\tw1")
        agents = ipod.wait_for("A")
        self.assertEqual(agents[0], ["A", "w1", "1"])
        self.assertEqual(agents[1][:5], ["w1:p1", "claude", "b", "1", "asker"])

        ipod.send("fw\t1\tw1")
        self.assertEqual(ipod.wait_for("R")[0], ["R", "1", "ok", "Focused 项目"])
        self.assertIn(("workspace.focus", {"workspace_id": "w1"}),
                      self.herdr.calls)
        self.assertIn(("tab.focus", {"tab_id": "w1:t1"}), self.herdr.calls)

        ipod.send("a\t2\t%s\t3" % qid)
        self.assertEqual(ipod.wait_for("R")[0], ["R", "2", "ok", "Answered"])
        self.assertIn(("pane.send_keys", {"pane_id": "w1:p1", "keys": ["3"]}),
                      self.herdr.calls)
        self.assertEqual(ipod.wait_for("X")[0], ["X", qid])

        ipod.send("a\t3\t%s\t1" % qid)
        self.assertEqual(ipod.wait_for("R")[0][2], "err")
        self.assertEqual(ipod.wait_for("T")[0][2], "1")

    def test_reconnect_after_unplug(self):
        self.ipod.pump(1.0)
        self.assertEqual(self.ipod.messages.pop(0)[0][0], "HR")
        self.ipod.close()
        time.sleep(0.5)
        self.assertIsNone(self.daemon.link)
        replugged = FakeIpod()
        self.daemon.args.port = replugged.path
        replugged.pump(2.5)
        self.assertEqual(replugged.messages.pop(0)[0][0], "HR")
        replugged.send("hr\t1")
        self.assertEqual(replugged.wait_for("W")[0], ["W", "2"])
        replugged.close()


if __name__ == "__main__":
    unittest.main()
