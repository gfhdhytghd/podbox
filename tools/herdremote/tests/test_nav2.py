"""NAV2 contract and daemon tests. Only private fake sockets/PTYs are used."""
import os
import tempfile
import types
import unittest
from unittest import mock

import test_herdremote as fixtures
from test_herdremote import FakeHerdr, sample, herdremote, hrherdr, hrproto
import hrdetail


class Protocol(unittest.TestCase):
    def test_tab_frame_order_empty_and_tick_are_distinct(self):
        rows = [dict(id="w1:t9", label="build", status="w"),
                dict(id="w1:t2", label="review", status="b")]
        self.assertEqual(hrproto.decode(hrproto.tabs("w1", rows)), [
            ["T", "w1", "2", "0", "w1:t9", "build", "w"],
            ["T", "w1", "2", "1", "w1:t2", "review", "b"]])
        self.assertEqual(hrproto.decode(hrproto.tabs("w1", [])),
                         [["T", "w1", "0", "0", "-", "", "-"]])
        self.assertEqual(hrproto.decode(hrproto.tick(4, True)), [["T", "4", "1"]])

    def test_utf8_budgets_and_worst_case_frames(self):
        rows = [dict(id="t" * 15, label="汉\t字\n" * 80, status="b")
                for _ in range(50)]
        payload = hrproto.tabs("w" * 15, rows)
        self.assertLessEqual(len(payload), 4096)
        records = hrproto.decode(payload)
        self.assertEqual(len(records), 32)
        self.assertEqual(records[-1][2:4], ["32", "31"])
        for r in records:
            self.assertEqual(len(r), 7)
            self.assertLessEqual(len(r[5].encode()), 40)
        payload = hrproto.detail("p" * 15, dict(task="😀" * 100, now="😀" * 100,
            say="😀" * 400, steps=["😀" * 100] * 3, state_age_s=12))
        fields = hrproto.decode(payload)[0]
        self.assertEqual(len(fields), 11)
        self.assertLessEqual(len(payload), 4096)
        self.assertLessEqual(len(fields[5]), 300)
        self.assertTrue(all(len(fields[i]) <= 80 and len(fields[i].encode()) <= 240 for i in (3, 4, 6, 7, 8)))
        self.assertEqual(hrproto.decode(hrproto.detail("p", {})),
                         [["D", "p", "-1", "", "", "", "", "", "", "", "0"]])

    def test_commands_and_legacy_subscription(self):
        for payload, expected in (
                (b"sub\tw1", ("sub", ["w1"])),
                (b"sub\tw1\tw1:t2", ("sub", ["w1", "w1:t2"])),
                (b"sub\t-", ("sub", ["-"])),
                (b"ft\t7\tw1\tw1:t2", ("ft", ["7", "w1", "w1:t2"])),
                (b"det\tw1:p1", ("det", ["w1:p1"])),
                (b"det\t-", ("det", ["-"]))):
            with self.subTest(payload=payload):
                self.assertEqual(hrproto.parse_command(payload), expected)

    def test_malformed_navigation_commands_are_ignored(self):
        for payload in (b"sub", b"sub\t", b"sub\t-\tt", b"sub\tw\tt\tx",
                        b"sub\tw\n", b"sub\tw\tt\r", b"det\tp\nextra",
                        b"det\t", b"det\tp\tt", b"ft\t1\tw", b"ft\t\tw\tt",
                        b"ft\t12345678\tw\tt", b"ft\t1\tw\t", b"ft\t1\tw\tt\n",
                        b"det\t" + b"p" * 16, b"sub\t" + b"w" * 16,
                        "ft\t1\tw\t" + "汉" * 6, b"det\tp\x00", b"det\tp\x7f"):
            with self.subTest(payload=payload):
                self.assertIsNone(hrproto.parse_command(payload))

    def test_detail_strips_terminal_escapes_and_keeps_blank_lines(self):
        text = "discard\n" + "\n".join(str(i) for i in range(10))
        text += "\n\x1b[31mred\x1b[0m\n\n\x1b]0;title\x07tail\n \n\n"
        lines = hrherdr.detail_lines(text)
        self.assertEqual(len(lines), 12)
        self.assertEqual(lines[0], "1")
        self.assertEqual(lines[-3:], ["red", "", "tail"])
        self.assertEqual(hrherdr.detail_lines("\x1b]8;;url\x1b\\link\x1b]8;;\x1b\\"),
                         ["link"])
        self.assertEqual(hrherdr.detail_lines("\x1bPsecret\x1b\\safe\x1b(B"), ["safe"])
        self.assertEqual(hrherdr.detail_lines("\n \n"), [])


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = FakeHerdr(os.path.join(self.tmp.name, "fake.sock"))
        layout = self.server.snapshot()
        # A first tab whose numeric id sorts after the second tab; a third tab
        # for bounds and tab-order checks, and a foreign workspace's tab.
        layout["tabs"] = [dict(tab_id="w1:t9", workspace_id="w1", label="build"),
                          dict(tab_id="w1:t2", workspace_id="w1", label="review"),
                          dict(tab_id="w1:t7", workspace_id="w1", label=""),
                          dict(tab_id="w2:t1", workspace_id="w2", label="other")]
        layout["panes"][0]["tab_id"] = "w1:t9"
        layout["panes"][1]["tab_id"] = "w1:t9"
        layout["panes"] += [
            dict(pane_id="w1:p3", tab_id="w1:t2", agent="codex", agent_status="idle"),
            dict(pane_id="w1:p4", tab_id="w1:t2", agent="claude", agent_status="working")]
        self.server.layout = layout
        self.server.texts = {"w1:p3": "old\nnew\n", "w1:p4": "running"}
        self.server.start()
        self.daemon = herdremote.Daemon(types.SimpleNamespace(socket=self.server.path, state=None))
        self.messages = []
        self.daemon.send = self.messages.append
        self.daemon.command(b"hr\t1")
        self.daemon.refresh()
        self.daemon.push()
        self.clear()

    def tearDown(self):
        self.daemon.close_link()
        self.server.stop()
        self.tmp.cleanup()

    def clear(self):
        self.messages.clear()
        self.server.calls.clear()

    def records(self, kind):
        return [hrproto.decode(m) for m in self.messages if hrproto.decode(m)[0][0] == kind]

    def command(self, payload):
        self.daemon.command(payload)
        self.daemon.push()


class Subscription(Harness):
    def test_selected_tab_agents_and_first_tab_default(self):
        self.command(b"sub\tw1\tw1:t2")
        self.daemon.refresh()
        self.daemon.push()
        self.assertEqual([r[0] for r in self.records("A")[-1][1:]], ["w1:p4", "w1:p3"])
        self.assertEqual(self.records("A")[-1][2][-1], "new")
        tabs = self.records("T")[0]
        self.assertEqual([r[4] for r in tabs], ["w1:t9", "w1:t2", "w1:t7"])
        self.assertEqual([r[5] for r in tabs], ["build", "review", "w1:t7"])
        self.assertEqual([r[6] for r in tabs], ["b", "w", "-"])
        self.assertEqual([hrproto.decode(m)[0][0] for m in self.messages[:2]], ["T", "A"])
        self.assertFalse(any("focus" in m for m, _ in self.server.calls))
        self.clear()
        self.command(b"sub\tw1")
        self.assertEqual(self.records("A")[0][1][0], "w1:p1")
        self.clear()
        self.command(b"sub\tw1\tw1:t7")
        self.assertEqual(self.records("A"), [[["A", "w1", "0"]]])

    def test_no_changes_are_silent_and_tab_rename_reorder_are_pushed(self):
        self.command(b"sub\tw1")
        self.clear()
        self.daemon.refresh()
        self.daemon.push()
        self.assertEqual(self.messages, [])
        self.server.layout["tabs"][:2] = self.server.layout["tabs"][1::-1]
        self.server.layout["tabs"][0]["label"] = "new name"
        self.daemon.refresh()
        self.daemon.push()
        self.assertEqual(self.records("T")[0][0][4:6], ["w1:t2", "new name"])
        self.assertEqual(self.records("A")[0][1][0], "w1:p4")

    def test_invalid_removed_tab_and_workspace_are_empty_without_fallback(self):
        for payload in (b"sub\tw1\tw2:t1", b"sub\tw1\tmissing"):
            self.clear()
            self.command(payload)
            self.assertEqual(self.records("A"), [[["A", "w1", "0"]]])
        self.command(b"sub\tw1\tw1:t2")
        self.server.layout["tabs"] = [t for t in self.server.layout["tabs"] if t["tab_id"] != "w1:t2"]
        self.daemon.refresh()
        self.clear()
        self.daemon.push()
        self.assertEqual(self.records("A"), [[["A", "w1", "0"]]])
        self.server.layout["workspaces"] = []
        self.daemon.refresh()
        self.clear()
        self.daemon.push()
        self.assertEqual(self.records("T"), [[["T", "w1", "0", "0", "-", "", "-"]]])
        self.assertEqual(self.records("A"), [])  # empty A is unchanged

    def test_unsubscribe_stops_both_lists(self):
        self.command(b"sub\tw1")
        self.clear()
        self.command(b"sub\t-")
        self.daemon.refresh()
        self.daemon.push()
        self.assertFalse(self.records("A"))
        self.assertFalse(self.records("T"))

    def test_only_first_and_selected_tabs_are_read_and_ranked(self):
        self.command(b"sub\tw1\tw1:t2")
        self.clear()
        self.server.texts["w1:p3"] = sample("claude-ask.txt")
        self.daemon.refresh()
        self.daemon.push()
        reads = [p["pane_id"] for m, p in self.server.calls if m == "pane.read"]
        self.assertCountEqual(reads, ["w1:p1", "w1:p3", "w1:p4", "w2:p9"])
        self.assertEqual(self.records("A")[-1][1][:4], ["w1:p3", "codex", "b", "1"])
        self.assertEqual(self.records("T")[-1][1][-1], "b")
        questions = self.records("Q")
        self.assertEqual(questions[0][0][2], "w1:p3")
        self.assertEqual(self.daemon.model["workspaces"][0]["waiting"], 2)

    def test_malformed_commands_have_no_effect(self):
        self.daemon.command(b"sub\tw1\tw1:t2\nextra")
        self.daemon.command(b"ft\t1\tw1\tw1:t2\nextra")
        self.daemon.command(b"det\tw1:p3\nextra")
        self.assertIsNone(self.daemon.subscribed)
        self.assertIsNone(self.daemon.detail_pane)
        self.assertEqual(self.server.calls, [])


class Focus(Harness):
    def result(self, payload):
        self.clear()
        self.daemon.command(payload)
        return hrproto.decode(self.messages[-1])[0]

    def test_local_tab_focus_is_silent_in_side_effects_and_preserves_subscription(self):
        self.server.pong["capabilities"] = dict(client_targeted_focus=True)
        self.daemon.command(b"sub\tw1\tw1:t9")
        self.assertEqual(self.result(b"ft\t3\tw1\tw1:t2"), ["R", "3", "ok", "Focused review"])
        self.assertEqual(self.server.calls, [("ping", {}), ("client.focus", dict(
            workspace_id="w1", tab_id="w1:t2", clients="local"))])
        self.assertEqual(self.daemon.subscribed_tab, "w1:t9")

    def test_legacy_tab_focus(self):
        self.assertEqual(self.result(b"ft\t1\tw1\tw1:t2")[2], "ok")
        self.assertEqual(self.server.calls, [("ping", {}),
                         ("workspace.focus", dict(workspace_id="w1")),
                         ("tab.focus", dict(tab_id="w1:t2"))])

    def test_invalid_workspace_tab_and_foreign_tab_never_focus(self):
        for payload in (b"ft\t1\tmissing\tw1:t2", b"ft\t1\tw1\tmissing", b"ft\t1\tw1\tw2:t1"):
            with self.subTest(payload=payload):
                self.assertEqual(self.result(payload)[2], "err")
                self.assertEqual(self.server.calls, [])

    def test_already_focused_local_and_remote_only_checks(self):
        self.server.pong["capabilities"] = dict(client_targeted_focus=True)
        self.server.moved = []
        for clients, expected in (([], "err"), ([dict(origin="remote")], "err"),
                                  ([dict(origin="local")], "ok")):
            self.server.clients = clients
            self.assertEqual(self.result(b"ft\t1\tw1\tw1:t2")[2], expected)
            self.assertEqual([m for m, _ in self.server.calls], ["ping", "client.focus", "client.list"])

    def test_errors_never_retry_legacy(self):
        self.server.pong["capabilities"] = dict(client_targeted_focus=True)
        self.server.moved = []
        for method, methods in (("ping", ["ping"]),
                                ("client.focus", ["ping", "client.focus"]),
                                ("client.list", ["ping", "client.focus", "client.list"])):
            self.server.errors = {method: "failure"}
            self.assertEqual(self.result(b"ft\t1\tw1\tw1:t2")[2], "err")
            self.assertEqual([m for m, _ in self.server.calls], methods)


class Detail(Harness):
    def setUp(self):
        super().setUp()
        self.clock = mock.patch.object(herdremote.time, "monotonic", return_value=100)
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)
        self.daemon.next_tick = 200

    def test_detail_read_change_throttle_and_no_focus(self):
        self.server.texts["w1:p3"] = "old\n\x1b[32mnew\x1b[0m\n"
        self.command(b"det\tw1:p3")
        self.assertEqual(self.records("D"), [[["D", "w1:p3", "-2", "", "", "old new", "", "", "", "", "0"]]])
        self.assertEqual(self.server.calls, [("pane.read", dict(pane_id="w1:p3", source="visible"))])
        self.clear()
        self.server.texts["w1:p3"] = "changed"
        self.now.return_value = 100.9
        self.daemon.push()
        self.assertEqual(self.server.calls, [])
        self.now.return_value = 101
        self.daemon.push()
        self.assertEqual(self.records("D"), [[["D", "w1:p3", "-2", "", "", "changed", "", "", "", "", "0"]]])
        self.clear()
        self.now.return_value = 102
        self.daemon.push()
        self.assertEqual(len(self.server.calls), 1)
        self.assertEqual(self.messages, [])

    def test_switches_cannot_bypass_deadline_and_stop_is_independent(self):
        self.command(b"sub\tw1")
        self.command(b"det\tw1:p3")
        self.clear()
        self.command(b"det\tw1:p4")
        self.assertEqual(self.messages, [])
        self.assertEqual(self.server.calls, [])
        self.now.return_value = 101
        self.daemon.push()
        self.assertEqual(self.records("D"), [[["D", "w1:p4", "-2", "", "", "running", "", "", "", "", "0"]]])
        self.clear()
        self.command(b"det\t-")
        self.now.return_value = 103
        self.daemon.push()
        self.assertEqual(self.server.calls, [])
        self.assertEqual(self.daemon.subscribed, "w1")
        self.command(b"det\tw1:p3")
        self.command(b"sub\t-")
        self.assertEqual(self.daemon.detail_pane, "w1:p3")

    def test_slow_reads_keep_a_full_second_between_detail_frames(self):
        def slow_read(pane, **kwargs):
            self.now.return_value += .8
            return dict(text="slow pane")

        with mock.patch.object(self.daemon.herdr, "read_record", side_effect=slow_read) as read:
            self.command(b"det\tw1:p3")
            self.assertAlmostEqual(self.daemon.next_detail, 101.8)
            self.now.return_value = 101
            self.daemon.push()
            self.assertEqual(read.call_count, 1)
            self.now.return_value = 101.8
            self.daemon.push()
            self.assertEqual(read.call_count, 2)

    def test_missing_empty_read_failure_and_recovery(self):
        self.command(b"det\tmissing")
        self.assertEqual(self.records("D"), [[["D", "missing", "-1", "", "", "", "", "", "", "", "0"]]])
        self.assertEqual(self.server.calls, [])
        self.server.errors["pane.read"] = "closed"
        self.now.return_value = 101
        self.clear()
        self.command(b"det\tw1:p3")
        self.assertEqual(self.records("D"), [[["D", "w1:p3", "-1", "", "", "", "", "", "", "", "0"]]])
        self.server.errors.clear()
        self.server.texts["w1:p3"] = "recovered"
        self.now.return_value = 102
        self.clear()
        self.daemon.push()
        self.assertEqual(self.records("D"), [[["D", "w1:p3", "-2", "", "", "recovered", "", "", "", "", "0"]]])
        self.server.texts["w1:p3"] = "\n"
        self.now.return_value = 103
        self.clear()
        self.daemon.push()
        self.assertEqual(self.records("D"), [[["D", "w1:p3", "-1", "", "", "", "", "", "", "", "0"]]])
        self.daemon.model["panes"].remove("w1:p3")
        self.now.return_value = 104
        self.clear()
        self.daemon.push()
        self.assertEqual(self.server.calls, [])

    def test_session_reset_and_link_loss_clear_subscriptions_and_caches(self):
        for reset in (lambda: self.daemon.command(b"hr\t1"), self.daemon.close_link):
            self.command(b"sub\tw1\tw1:t2")
            self.command(b"det\tw1:p3")
            reset()
            self.assertIsNone(self.daemon.subscribed)
            self.assertIsNone(self.daemon.subscribed_tab)
            self.assertIsNone(self.daemon.detail_pane)
            self.assertIsNone(self.daemon.sent_detail)
            self.assertIsNone(self.daemon.sent_tabs)
            self.assertEqual(self.daemon.next_detail, 0)
            self.clear()
            self.daemon.push()
            self.assertFalse(self.records("D"))
            self.assertEqual(self.server.calls, [])

    def test_no_detail_read_or_send_before_hello(self):
        self.daemon.reset_session()
        self.command(b"det\tw1:p3")
        self.assertEqual(self.messages, [])
        self.assertEqual(self.server.calls, [])


class Wire(unittest.TestCase):
    setUp = fixtures.EndToEnd.setUp

    def tearDown(self):
        fixtures.EndToEnd.tearDown(self)
        self.ipod.close()

    def test_navigation_over_podlink_with_fake_server(self):
        ipod = self.ipod
        ipod.pump(.5)
        ipod.send("hr\t1")
        ipod.wait_for("W")
        ipod.send("sub\tw2\tw2:t1")
        tabs = ipod.wait_for("T")
        if len(tabs[0]) == 3:  # unchanged heartbeat can precede the list
            tabs = ipod.wait_for("T")
        self.assertEqual([r[4] for r in tabs], ["w2:t3", "w2:t1"])
        agents = ipod.wait_for("A")
        self.assertEqual(agents[0], ["A", "w2", "1"])
        self.assertEqual(agents[1][0], "w2:p9")
        self.herdr.pong["capabilities"] = dict(client_targeted_focus=True)
        ipod.send("ft\t9\tw2\tw2:t1")
        self.assertEqual(ipod.wait_for("R")[0], ["R", "9", "ok", "Focused w2:t1"])
        self.assertIn(("client.focus", dict(workspace_id="w2", tab_id="w2:t1", clients="local")), self.herdr.calls)
        ipod.send("det\tw2:p9")
        detail = ipod.wait_for("D")[0]
        self.assertEqual(detail, hrproto.decode(hrproto.detail("w2:p9", hrdetail.parse(sample("codex-idle-narrow.txt"), kind="codex", status="working")))[0])
        ipod.send("det\t-")
        ipod.send("hr\t1")
        ipod.wait_for("W")
        self.assertIsNone(self.daemon.detail_pane)
        self.assertIsNone(self.daemon.subscribed)
        self.assertFalse(any(m == "pane.send_keys" for m, _ in self.herdr.calls))


class NegotiatedDetail(unittest.TestCase):
    setUp = fixtures.EndToEnd.setUp
    max_message = 4096

    def tearDown(self):
        fixtures.EndToEnd.tearDown(self)
        self.ipod.close()

    def test_long_input_respects_the_real_pty_link_negotiation(self):
        self.herdr.texts['w2:p9'] = '• Summary\n› ' + '中' * 2000
        self.ipod.pump(.3)
        self.ipod.send('hr\t1')
        self.ipod.wait_for('W')
        self.ipod.send('det\tw2:p9')
        row = self.ipod.wait_for('D')[0]
        self.assertEqual(self.daemon.link.max_message, self.max_message)
        self.assertEqual(len(row), 11)
        self.assertLessEqual(len(hrproto.encode([row])), self.max_message)
        if self.max_message == 4096:
            self.assertEqual(row[9], '…' + '中' * 499)
            self.assertEqual(row[10], '500')
        else:
            self.assertEqual(row[9], '中' * 2000)
            self.assertEqual(row[10], '2000')
            self.assertGreater(len(hrproto.encode([row])), 4096)


class NegotiatedDetail16K(NegotiatedDetail):
    max_message = 16384


if __name__ == "__main__":
    unittest.main()
