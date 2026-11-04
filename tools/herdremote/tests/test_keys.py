"""Usage keyboard taps. All subprocesses/tools are mocked; no real key input."""
import argparse
import os
import stat
import subprocess
import sys
import threading
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import herdremote
import hrkeys
import hrproto


class ToolTests(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(hrkeys.subprocess, "run")
        self.run = patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(hrkeys.shutil, "which", side_effect=lambda name: "/mock/" + name)
        self.which = patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(hrkeys, "socket_exists", return_value=True)
        self.socket = patch.start()
        self.addCleanup(patch.stop)

    def test_auto_prefers_ydotool_and_pairs_every_key(self):
        with mock.patch.dict(os.environ, YDOTOOL_SOCKET="/mock/ydotool.sock"):
            for key, code in (("bs", 14), ("left", 105), ("right", 106), ("enter", 28)):
                with self.subTest(key=key):
                    hrkeys.KeyTool("auto").tap(key)
                    self.assertEqual(self.run.call_args.args[0],
                                     ["/mock/ydotool", "key", str(code) + ":1", str(code) + ":0"])
                    self.assertEqual(self.run.call_args.kwargs["env"]["YDOTOOL_SOCKET"],
                                     "/mock/ydotool.sock")
                    self.assertEqual(self.run.call_args.kwargs["timeout"], .5)
                    self.assertTrue(self.run.call_args.kwargs["check"])
        self.assertNotIn(mock.call("wtype"), self.which.call_args_list)

    def test_auto_falls_back_when_binary_or_socket_missing(self):
        for missing in ("binary", "socket"):
            with self.subTest(missing=missing):
                self.which.side_effect = lambda name: None if name == "ydotool" and missing == "binary" else "/mock/" + name
                self.socket.return_value = missing != "socket"
                hrkeys.KeyTool("auto").tap("bs")
                self.assertEqual(self.run.call_args.args[0], ["/mock/wtype", "-k", "BackSpace"])

    def test_explicit_wtype_pairs_every_key(self):
        for key, symbol in (("bs", "BackSpace"), ("left", "Left"),
                            ("right", "Right"), ("enter", "Return")):
            with self.subTest(key=key):
                hrkeys.KeyTool("wtype").tap(key)
                self.assertEqual(self.run.call_args.args[0], ["/mock/wtype", "-k", symbol])
        self.assertNotIn(mock.call("ydotool"), self.which.call_args_list)

    def test_explicit_ydotool_does_not_switch_when_missing(self):
        self.socket.return_value = False
        with self.assertRaisesRegex(hrkeys.KeyInputError, "Key tool unavailable"):
            hrkeys.KeyTool("ydotool").tap("bs")
        self.run.assert_not_called()
        self.assertNotIn(mock.call("wtype"), self.which.call_args_list)

    def test_tools_missing(self):
        self.which.return_value = None
        self.which.side_effect = None
        with self.assertRaisesRegex(hrkeys.KeyInputError, "Key tool unavailable"):
            hrkeys.KeyTool("auto").tap("enter")
        self.run.assert_not_called()

    def test_runtime_errors_release_only_and_never_retry_tap(self):
        for tool, release in (("ydotool", ["/mock/ydotool", "key", "105:0"]),
                              ("wtype", ["/mock/wtype", "-p", "Left"])):
            for error in (OSError("missing"), subprocess.CalledProcessError(1, tool),
                          subprocess.TimeoutExpired(tool, .5)):
                with self.subTest(tool=tool, error=type(error).__name__):
                    self.run.reset_mock()
                    self.which.reset_mock()
                    self.run.side_effect = [error, None]
                    with self.assertRaisesRegex(hrkeys.KeyInputError, "Key tool failed"):
                        hrkeys.KeyTool(tool).tap("left")
                    self.assertEqual(self.run.call_count, 2)
                    self.assertEqual(self.run.call_args.args[0], release)
                    self.assertNotIn(mock.call("wtype" if tool == "ydotool" else "ydotool"),
                                     self.which.call_args_list)

    def test_release_cleanup_failure_stays_bounded(self):
        self.run.side_effect = subprocess.TimeoutExpired("ydotool", .5)
        with self.assertRaisesRegex(hrkeys.KeyInputError, "Key tool failed"):
            hrkeys.KeyTool("ydotool").tap("bs")
        self.assertEqual(self.run.call_count, 2)
        self.assertEqual(self.run.call_args.args[0], ["/mock/ydotool", "key", "14:0"])

    def test_tool_whitelist_rejects_every_other_key(self):
        for key in ("BS", "delete", "ctrl+a", "a", "", "bs; exit", "Return"):
            with self.subTest(key=key), self.assertRaisesRegex(hrkeys.KeyInputError, "Unsupported key"):
                hrkeys.KeyTool("auto").tap(key)
        self.run.assert_not_called()
        self.which.assert_not_called()

class SocketTests(unittest.TestCase):
    def test_socket_type_and_missing_file(self):
        with mock.patch.object(hrkeys.os, "stat", return_value=types.SimpleNamespace(st_mode=stat.S_IFSOCK)):
            self.assertTrue(hrkeys.socket_exists("/mock/socket"))
        with mock.patch.object(hrkeys.os, "stat", return_value=types.SimpleNamespace(st_mode=stat.S_IFREG)):
            self.assertFalse(hrkeys.socket_exists("/mock/file"))
        with mock.patch.object(hrkeys.os, "stat", side_effect=FileNotFoundError):
            self.assertFalse(hrkeys.socket_exists("/mock/missing"))
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(hrkeys.os, "getuid", return_value=1000):
            self.assertEqual(hrkeys.socket_path(), "/run/user/1000/.ydotool_socket")
        with mock.patch.dict(os.environ, YDOTOOL_SOCKET="/mock/explicit"):
            self.assertEqual(hrkeys.socket_path(), "/mock/explicit")


class DeferredWorker:
    jobs = []

    def __init__(self, *, target, args, daemon):
        self.target, self.args = target, args

    def start(self):
        self.jobs.append(self)

    def finish(self):
        self.target(*self.args)


class TapFixture:
    def setUp(self):
        patch = mock.patch.object(hrkeys.subprocess, "run", side_effect=AssertionError("real key forbidden"))
        patch.start()
        self.addCleanup(patch.stop)
        DeferredWorker.jobs = []
        self.tool = mock.Mock(spec=hrkeys.KeyTool)
        self.now = 0.
        self.taps = hrkeys.KeyTaps(self.tool, clock=lambda: self.now, worker=DeferredWorker)


class TapTests(TapFixture, unittest.TestCase):
    def test_exact_whitelist_and_silent_results(self):
        for index, key in enumerate(("bs", "left", "right", "enter")):
            self.now = index
            self.assertIsNone(self.taps.request(str(index), key))
            DeferredWorker.jobs[-1].finish()
            self.assertEqual(self.taps.replies(), [(str(index), True, None)])
        self.assertEqual(self.tool.tap.call_args_list, [mock.call(k) for k in ("bs", "left", "right", "enter")])
        for key in ("delete", "a", "BackSpace", "BS", ""):
            self.assertEqual(self.taps.request("9", key), (False, "Unsupported key"))
        self.assertEqual(len(DeferredWorker.jobs), 4)

    def test_shared_rate_limit_and_no_queue(self):
        self.assertIsNone(self.taps.request("1", "bs"))
        for index in range(100):
            self.assertEqual(self.taps.request("2", ("left", "right", "enter")[index % 3]),
                             (False, "Key rate limit"))
        self.assertEqual(len(DeferredWorker.jobs), 1)
        DeferredWorker.jobs[0].finish()
        self.assertEqual(self.taps.request("3", "right"), (False, "Key rate limit"))
        self.now = .04
        self.assertIsNone(self.taps.request("4", "enter"))
        DeferredWorker.jobs[-1].finish()
        self.assertEqual(self.tool.tap.call_count, 2)

    def test_maximum_thirty_taps_per_second(self):
        for index in range(1000):
            self.now = index / 1000
            if self.taps.request(str(index), "bs") is None:
                DeferredWorker.jobs[-1].finish()
        self.assertLessEqual(self.tool.tap.call_count, 30)
        self.assertGreater(self.tool.tap.call_count, 0)

    def test_expired_unstarted_tap_is_dropped(self):
        self.taps.request("1", "bs")
        self.now = .101
        DeferredWorker.jobs[-1].finish()
        self.tool.tap.assert_not_called()
        self.assertEqual(self.taps.replies(), [("1", False, "Key request expired")])

    def test_reset_cancels_unstarted_tap_and_stale_replies(self):
        self.taps.request("1", "bs")
        self.taps.reset()
        DeferredWorker.jobs[-1].finish()
        self.tool.tap.assert_not_called()
        self.assertEqual(self.taps.replies(), [])
        self.now = 1
        self.taps.request("2", "left")
        DeferredWorker.jobs[-1].finish()
        self.taps.reset()
        self.assertEqual(self.taps.replies(), [])

    def test_reset_does_not_bypass_busy_or_rate_cap(self):
        self.taps.request("1", "bs")
        self.taps.reset()
        self.assertEqual(self.taps.request("2", "enter"), (False, "Key rate limit"))
        DeferredWorker.jobs[-1].finish()
        self.assertEqual(self.taps.request("3", "enter"), (False, "Key rate limit"))

    def test_tool_error_is_returned(self):
        self.tool.tap.side_effect = hrkeys.KeyInputError("Key tool failed")
        self.taps.request("1", "bs")
        DeferredWorker.jobs[-1].finish()
        self.assertEqual(self.taps.replies(), [("1", False, "Key tool failed")])
        self.assertFalse(self.taps.busy)

    def test_slow_tool_does_not_block_link_or_accumulate_work(self):
        started, finish, done = threading.Event(), threading.Event(), threading.Event()
        def slow(key):
            started.set()
            finish.wait(2)
            done.set()
        self.tool.tap.side_effect = slow
        taps = hrkeys.KeyTaps(self.tool)
        try:
            self.assertIsNone(taps.request("1", "bs"))
            self.assertTrue(started.wait(1))
            for _ in range(100):
                self.assertEqual(taps.request("2", "left"), (False, "Key rate limit"))
            taps.reset()
        finally:
            finish.set()
        self.assertTrue(done.wait(1))
        self.tool.tap.assert_called_once_with("bs")


class ProtocolTests(unittest.TestCase):
    def test_command_and_exact_three_field_success(self):
        for key in ("bs", "left", "right", "enter", "unsupported"):
            self.assertEqual(hrproto.parse_command(("k\t1\t" + key).encode()), ("k", ["1", key]))
        self.assertEqual(hrproto.result("1", True), b"R\t1\tok")
        self.assertEqual(hrproto.result("2", False, "Unsupported key"), b"R\t2\terr\tUnsupported key")
        self.assertEqual(hrproto.result("3", True, "Talking"), b"R\t3\tok\tTalking")

    def test_malformed_commands(self):
        for payload in (b"k", b"k\t1", b"k\t1\tbs\textra", b"k\t\tbs",
                        b"k\t12345678\tbs", b"k\t1\tbs\n", b"k\t1\tbs\x00"):
            with self.subTest(payload=payload):
                self.assertIsNone(hrproto.parse_command(payload))


class ConfigTests(unittest.TestCase):
    def test_defaults_environment_and_cli(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            parser = argparse.ArgumentParser()
            hrkeys.add_arguments(parser)
            self.assertEqual(parser.parse_args([]).key_tool, "auto")
            self.assertEqual(hrkeys.KeyTool().tool, "auto")
        with mock.patch.dict(os.environ, HERDREMOTE_KEY_TOOL="wtype"):
            parser = argparse.ArgumentParser()
            hrkeys.add_arguments(parser)
            self.assertEqual(parser.parse_args([]).key_tool, "wtype")
            self.assertEqual(hrkeys.KeyTool().tool, "wtype")
            self.assertEqual(parser.parse_args(["--key-tool", "ydotool"]).key_tool, "ydotool")
        with self.assertRaises(ValueError):
            hrkeys.KeyTool("arbitrary-command")


class DaemonTests(TapFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        with mock.patch.object(herdremote.hrusage, "UsageCache"):
            self.daemon = herdremote.Daemon(types.SimpleNamespace(socket="/nonexistent", state=None))
        self.daemon.keys = self.taps
        self.daemon.herdr = mock.Mock()
        self.messages = []
        self.daemon.send = self.messages.append
        self.daemon.command(b"hr\t1")

    def test_daemon_key_dispatch_and_silent_reply_without_herdr_focus(self):
        for index, key in enumerate(("bs", "left", "right", "enter")):
            self.now = index
            self.daemon.command(("k\t%d\t%s" % (index, key)).encode())
            DeferredWorker.jobs[-1].finish()
            self.daemon.push()
            self.assertIn(b"R\t%d\tok" % index, self.messages)
        self.assertEqual(self.daemon.herdr.mock_calls, [])

    def test_unsupported_key_returns_error(self):
        self.daemon.command(b"k\t1\tdelete")
        self.assertEqual(self.messages[-1], b"R\t1\terr\tUnsupported key")
        self.tool.tap.assert_not_called()

    def test_session_not_ready_does_not_inject(self):
        self.daemon.reset_session()
        self.daemon.command(b"k\t1\tbs")
        self.assertEqual(self.messages[-1], b"R\t1\terr\tSession not ready")
        self.tool.tap.assert_not_called()

    def test_reset_and_link_close_cancel_unstarted_keys(self):
        for index, reset in enumerate((self.daemon.reset_session, self.daemon.close_link)):
            self.now = index
            self.daemon.command(b"hr\t1")
            self.daemon.command(b"k\t1\tenter")
            reset()
            DeferredWorker.jobs[-1].finish()
            self.tool.tap.assert_not_called()
            self.assertEqual(self.taps.replies(), [])

    def test_shutdown_cancels_unstarted_key(self):
        self.daemon.command(b"k\t1\tbs")
        self.daemon.stopping.set()
        with mock.patch.object(herdremote.hrherdr, "Watcher"), mock.patch.object(self.daemon.answerer, "start"):
            self.daemon.run()
        DeferredWorker.jobs[-1].finish()
        self.tool.tap.assert_not_called()


class KeyLinkTests(unittest.TestCase):
    def setUp(self):
        import test_herdremote as fixtures
        self.fixtures = fixtures
        self.tool = mock.Mock(spec=hrkeys.KeyTool)
        fixtures.EndToEnd.setUp(self)
        self.daemon.keys = hrkeys.KeyTaps(self.tool)
        self.ipod.pump(1)
        self.ipod.send("hr\t1")
        self.ipod.wait_for("W")

    def tearDown(self):
        if hasattr(self, "daemon"):
            self.fixtures.EndToEnd.tearDown(self)
            self.ipod.close()

    def test_all_taps_over_wire_are_silent_and_do_not_target_herdr(self):
        for index, key in enumerate(("bs", "left", "right", "enter")):
            self.ipod.send("k\t%d\t%s" % (index, key))
            self.assertEqual(self.ipod.wait_for("R"), [["R", str(index), "ok"]])
        self.assertEqual(self.tool.tap.call_args_list, [mock.call(k) for k in ("bs", "left", "right", "enter")])
        self.assertFalse(any(method.endswith(".focus") or method == "pane.send_keys"
                             for method, _ in self.herdr.calls))

    def test_unsupported_key_over_wire(self):
        self.ipod.send("k\t1\tdelete")
        self.assertEqual(self.ipod.wait_for("R"), [["R", "1", "err", "Unsupported key"]])
        self.tool.tap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
