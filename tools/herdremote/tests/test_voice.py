"""Hold-to-talk tests: every D-Bus mutation is mocked, never real recording."""
import argparse
from collections import deque
import os
import subprocess
import sys
import threading
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import herdremote
import hrherdr
import hrproto
import hrvoice
import podlink


class FakeTimer:
    def __init__(self, seconds, callback, args=()):
        self.seconds, self.callback, self.args = seconds, callback, args
        self.cancelled = self.started = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.callback(*self.args)


class VoiceFixture:
    def setUp(self):
        # A guard against accidental real bus access in all controller tests.
        self.subprocess_guard = mock.patch.object(
            hrvoice.subprocess, "run", side_effect=AssertionError("real D-Bus forbidden"))
        self.subprocess_guard.start()
        self.addCleanup(self.subprocess_guard.stop)
        self.bus = mock.Mock(spec=hrvoice.SessionBus)
        self.bus.status.side_effect = [(0, "idle"), (23, "recording")]
        self.focus = mock.Mock()
        self.events = mock.Mock()
        self.events.attach_mock(self.focus, "focus")
        self.events.attach_mock(self.bus, "bus")
        self.voice = hrvoice.HoldToTalk(self.focus, voice_focus=True,
                                       bus=self.bus, timer=FakeTimer)
        self.addCleanup(self.voice.stop)

    def methods(self):
        return [c.args[0] for c in self.bus.call.call_args_list]


class VoiceTests(VoiceFixture, unittest.TestCase):
    def owned_recording(self):
        self.assertEqual(self.voice.press("-"), (True, "Talking"))
        self.bus.status.side_effect = None
        self.bus.status.return_value = (23, "recording")

    def test_discard_cancels_then_resets_hold_and_allows_next_press(self):
        self.owned_recording()
        self.assertEqual(self.voice.discard(), (True, "Discarded"))
        self.assertEqual(self.methods(), ["HoldStart", "Cancel", "HoldStop"])
        self.assertFalse(self.voice.engaged or self.voice.down)
        self.assertEqual(self.voice.discard(), (True, "Discarded"))
        self.assertEqual(self.methods(), ["HoldStart", "Cancel", "HoldStop"])
        self.bus.status.side_effect = [(0, "idle"), (24, "recording")]
        self.assertEqual(self.voice.press("-"), (True, "Talking"))

    def test_unmatched_discard_never_cancels_unowned_recording(self):
        self.assertEqual(self.voice.discard(), (True, "Discarded"))
        self.bus.assert_not_called()
        self.assertEqual(self.bus.mock_calls, [])

    def test_discard_does_not_cancel_a_replacement_session(self):
        self.owned_recording()
        self.bus.status.return_value = (24, "recording")
        self.assertEqual(self.voice.discard(), (True, "Discarded"))
        self.assertEqual(self.methods(), ["HoldStart", "HoldStop"])

    def test_failed_cancel_never_submits_and_retries_before_new_press(self):
        self.owned_recording()
        def fail_cancel(method):
            if method == "Cancel":
                raise hrvoice.VoiceError(hrvoice.NOT_RUNNING)
        self.bus.call.side_effect = fail_cancel
        self.assertEqual(self.voice.discard(), (False, hrvoice.NOT_RUNNING))
        self.assertEqual(self.methods(), ["HoldStart", "Cancel"])
        self.assertEqual(self.voice.press("-"), (False, hrvoice.NOT_RUNNING))
        self.bus.call.side_effect = None
        self.voice.timer.fire()
        self.assertEqual(self.methods(), ["HoldStart", "Cancel", "Cancel", "HoldStop"])

    def test_failed_hold_reset_does_not_repeat_successful_cancel(self):
        self.owned_recording()
        def fail_stop(method):
            if method == "HoldStop":
                raise hrvoice.VoiceError(hrvoice.NOT_RUNNING)
        self.bus.call.side_effect = fail_stop
        self.assertEqual(self.voice.discard(), (False, hrvoice.NOT_RUNNING))
        self.bus.call.side_effect = None
        self.bus.status.return_value = (24, "recording")
        self.voice.timer.fire()
        self.assertEqual(self.methods(), ["HoldStart", "Cancel", "HoldStop", "HoldStop"])

    def test_default_keeps_current_cursor_for_any_target(self):
        voice = hrvoice.HoldToTalk(self.focus, bus=self.bus, timer=FakeTimer)
        self.addCleanup(voice.stop)
        self.assertFalse(voice.voice_focus)
        for target in ("w1:p2", "w:w1", "-"):
            with self.subTest(target=target):
                self.bus.status.side_effect = [(0, "idle"), (23, "recording")]
                self.assertEqual(voice.press(target), (True, "Talking"))
                self.assertEqual(voice.release(), (True, "Sent"))
                self.focus.assert_not_called()

    def test_press_release_and_focus_order(self):
        self.assertEqual(self.voice.press("w1:p2"), (True, "Talking"))
        self.assertEqual(self.events.mock_calls, [
            mock.call.focus("w1:p2"), mock.call.bus.ready(),
            mock.call.bus.status(), mock.call.bus.call("HoldStart"),
            mock.call.bus.status()])
        timer = self.voice.timer
        self.assertEqual(timer.seconds, 120)
        self.assertTrue(timer.started)
        self.assertEqual(self.voice.release(), (True, "Sent"))
        self.assertTrue(timer.cancelled)
        self.assertEqual(self.methods(), ["HoldStart", "HoldStop"])

    def test_duplicates_do_not_refocus_restart_or_extend_deadline(self):
        self.voice.press("w1:p2")
        timer = self.voice.timer
        self.assertEqual(self.voice.press("w9:p8"), (True, "Talking"))
        self.assertIs(self.voice.timer, timer)
        self.focus.assert_called_once_with("w1:p2")
        self.assertEqual(self.methods(), ["HoldStart"])
        self.voice.release()
        self.assertEqual(self.voice.release(), (True, "Sent"))
        self.assertEqual(self.methods(), ["HoldStart", "HoldStop"])

    def test_release_without_press_does_not_touch_unowned_recording(self):
        self.assertEqual(self.voice.release(), (True, "Sent"))
        self.assertEqual(self.bus.mock_calls, [])

    def test_timeout_stops_and_duplicate_stays_stopped_until_release(self):
        self.voice.press("-")
        timer = self.voice.timer
        timer.fire()
        self.assertEqual(self.methods(), ["HoldStart", "HoldStop"])
        self.assertEqual(self.voice.press("-"), (False, "Voice hold timed out"))
        self.assertEqual(self.methods(), ["HoldStart", "HoldStop"])
        self.voice.release()
        self.bus.status.side_effect = [(0, "idle"), (24, "recording")]
        self.assertEqual(self.voice.press("-"), (True, "Talking"))
        # A cancelled callback from the previous hold must not stop this hold.
        timer.fire()
        self.assertEqual(self.methods(), ["HoldStart", "HoldStop", "HoldStart"])

    def test_real_timer_stops_without_link_loop(self):
        self.voice.timer_factory = threading.Timer
        self.voice.voice_max_hold = .02
        stopped = threading.Event()
        self.bus.call.side_effect = lambda method: stopped.set() if method == "HoldStop" else None
        self.voice.press("-")
        self.assertTrue(stopped.wait(1), "watchdog did not stop independently")
        self.assertEqual(self.methods(), ["HoldStart", "HoldStop"])

    def test_tool_missing(self):
        self.bus.ready.side_effect = hrvoice.VoiceError(hrvoice.NOT_RUNNING)
        self.assertEqual(self.voice.press("-"), (False, hrvoice.NOT_RUNNING))
        self.assertEqual(self.methods(), [])

    def test_start_error_or_lost_reply_still_stops(self):
        self.bus.call.side_effect = [hrvoice.VoiceError(hrvoice.NOT_RUNNING), None]
        self.assertEqual(self.voice.press("-"), (False, hrvoice.NOT_RUNNING))
        self.assertEqual(self.methods(), ["HoldStart", "HoldStop"])

    def test_silent_addon_refusal_is_not_talking(self):
        self.bus.status.side_effect = [(0, "idle"), (0, "idle")]
        self.assertEqual(self.voice.press("-"), (False, hrvoice.NOT_RUNNING))
        self.assertEqual(self.methods(), ["HoldStart", "HoldStop"])

    def test_existing_recording_is_not_claimed_or_stopped(self):
        self.bus.status.side_effect = [(42, "recording")]
        self.assertEqual(self.voice.press("-"), (False, "Voice input busy"))
        self.voice.stop()
        self.assertEqual(self.methods(), [])

    def test_stop_error_retries_before_accepting_new_hold(self):
        self.voice.press("-")
        self.bus.call.side_effect = hrvoice.VoiceError(hrvoice.NOT_RUNNING)
        self.assertEqual(self.voice.release(), (False, hrvoice.NOT_RUNNING))
        self.assertEqual(self.voice.press("-"), (False, hrvoice.NOT_RUNNING))
        retry = self.voice.timer
        self.assertEqual(retry.seconds, 1)
        self.bus.call.side_effect = None
        retry.fire()
        self.assertFalse(self.voice.engaged)
        self.assertEqual(self.methods(), ["HoldStart", "HoldStop", "HoldStop"])

    def test_focus_failure_never_starts(self):
        self.focus.side_effect = hrvoice.VoiceError("No local herdr window")
        self.assertEqual(self.voice.press("w1:p2"), (False, "No local herdr window"))
        self.assertEqual(self.bus.mock_calls, [])

    def test_focus_disabled_and_raw_override(self):
        self.voice.voice_focus = False
        self.voice.voice_method = "HoldStartRaw"
        self.assertEqual(self.voice.press("w1:p2"), (True, "Talking"))
        self.focus.assert_not_called()
        self.assertEqual(self.methods(), ["HoldStartRaw"])


class BusTests(unittest.TestCase):
    def test_exact_bus_calls(self):
        bus = hrvoice.SessionBus()
        with mock.patch.object(hrvoice.subprocess, "run") as run:
            run.return_value.stdout = "b true\n"
            bus.ready()
            self.assertEqual(run.call_args.args[0][-3:], ["NameHasOwner", "s", "org.lais.Voice"])
            run.return_value.stdout = 'ts 12 "recording"\n'
            self.assertEqual(bus.status(), (12, "recording"))
            bus.call("HoldStart")
            self.assertEqual(run.call_args, mock.call(
                ["busctl", "--user", "--auto-start=no", "--timeout=2s", "call",
                 hrvoice.DEST, hrvoice.OBJECT, hrvoice.INTERFACE, "HoldStart"],
                capture_output=True, text=True, check=True, timeout=3))
            bus.call("HoldStop")
            self.assertEqual(run.call_args.args[0][-1], "HoldStop")
            bus.call("Cancel")
            self.assertEqual(run.call_args.args[0][-1], "Cancel")

    def test_missing_busctl_failed_call_timeout_and_bad_status(self):
        bus = hrvoice.SessionBus()
        for error in (FileNotFoundError(), subprocess.CalledProcessError(1, "busctl"),
                      subprocess.TimeoutExpired("busctl", 3)):
            with self.subTest(error=type(error).__name__), mock.patch.object(
                    hrvoice.subprocess, "run", side_effect=error):
                with self.assertRaisesRegex(hrvoice.VoiceError, hrvoice.NOT_RUNNING):
                    bus.call("HoldStart")
        for output in ("", "invalid", 'ss 1 "idle"', 'ts nope "idle"'):
            with self.subTest(output=output), mock.patch.object(
                    hrvoice.subprocess, "run", return_value=types.SimpleNamespace(stdout=output)):
                with self.assertRaises(hrvoice.VoiceError):
                    bus.status()
        with mock.patch.object(hrvoice.subprocess, "run", return_value=types.SimpleNamespace(stdout="b false")):
            with self.assertRaises(hrvoice.VoiceError):
                bus.ready()


class ProtocolTests(unittest.TestCase):
    def test_voice_commands(self):
        for text, expected in (("vd\t1\tw1:p2", ("vd", ["1", "w1:p2"])),
                               ("vd\t2\tw:w1", ("vd", ["2", "w:w1"])),
                               ("vd\t3\t-", ("vd", ["3", "-"])),
                               ("vu\t4", ("vu", ["4"]))):
            self.assertEqual(hrproto.parse_command(text.encode()), expected)

    def test_malformed_voice_commands(self):
        for text in ("vd\t1", "vd\t1\t", "vd\t1\tw:", "vd\t\t-", "vu",
                     "vu\t1\t-", "vd\t12345678\t-", "vd\t1\t-\nvu\t2",
                     "vu\t1\n", "vd\t1\tw1:p2\x00", "vd\t1\t" + "x" * 18):
            with self.subTest(text=text):
                self.assertIsNone(hrproto.parse_command(text.encode()))


class ConfigTests(unittest.TestCase):
    def test_defaults_env_and_cli(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            parser = argparse.ArgumentParser()
            hrvoice.add_arguments(parser)
            args = parser.parse_args([])
            self.assertEqual(vars(args), dict(voice_focus=False, voice_max_hold=120,
                                             voice_method="HoldStart"))
        with mock.patch.dict(os.environ, HERDREMOTE_VOICE_FOCUS="off",
                             HERDREMOTE_VOICE_MAX_HOLD="3.5",
                             HERDREMOTE_VOICE_METHOD="HoldStartRaw"):
            parser = argparse.ArgumentParser()
            hrvoice.add_arguments(parser)
            args = parser.parse_args([])
            self.assertEqual(vars(args), dict(voice_focus=False, voice_max_hold=3.5,
                                             voice_method="HoldStartRaw"))
            self.assertTrue(parser.parse_args(["--voice-focus", "on"]).voice_focus)
        with mock.patch.dict(os.environ, HERDREMOTE_VOICE_FOCUS="on"):
            parser = argparse.ArgumentParser()
            hrvoice.add_arguments(parser)
            self.assertTrue(parser.parse_args([]).voice_focus)
            self.assertFalse(parser.parse_args(["--voice-focus", "off"]).voice_focus)
        for value in ("0", "-1", "nan", "inf"):
            with self.assertRaises(ValueError):
                hrvoice.hold_seconds(value)
        with self.assertRaises(ValueError):
            hrvoice.HoldToTalk(mock.Mock(), voice_method="Start")


class DaemonTests(VoiceFixture, unittest.TestCase):
    # Use the strict subprocess guard and controller fixture, but install
    # the controller in a real daemon with a mocked herdr and in-memory link.
    def setUp(self):
        super().setUp()
        with mock.patch.object(herdremote.hrusage, "UsageCache"):
            self.daemon = herdremote.Daemon(types.SimpleNamespace(socket="/nonexistent", state=None))
        self.daemon.voice = self.voice
        self.daemon.herdr = mock.Mock(spec=hrherdr.Herdr)
        self.daemon.herdr._focus_local.return_value = True
        self.daemon.model["workspaces"] = [dict(id="w1", tab="w1:t3")]
        self.messages = []
        self.daemon.send = self.messages.append

    def result(self, payload):
        self.daemon.command(payload)
        return hrproto.decode(self.messages[-1])[0]

    def test_daemon_command_responses(self):
        self.assertEqual(self.result(b"vd\t7\t-"), ["R", "7", "ok", "Talking"])
        self.assertEqual(self.result(b"vu\t8"), ["R", "8", "ok", "Sent"])
        self.bus.ready.side_effect = hrvoice.VoiceError(hrvoice.NOT_RUNNING)
        self.assertEqual(self.result(b"vd\t9\t-"), ["R", "9", "err", hrvoice.NOT_RUNNING])

    def test_daemon_discard_command(self):
        self.assertEqual(self.result(b"vd\t7\t-"), ["R", "7", "ok", "Talking"])
        self.bus.status.side_effect = None
        self.bus.status.return_value = (23, "recording")
        self.assertEqual(self.result(b"vc\t8"), ["R", "8", "ok", "Discarded"])
        self.assertEqual(self.methods(), ["HoldStart", "Cancel", "HoldStop"])
        self.assertEqual(hrproto.parse_command(b"vc\t8"), ("vc", ["8"]))
        for bad in (b"vc", b"vc\t", b"vc\t12345678", b"vc\t1\t-", b"vc\t1\n", b"vc\t1\nvu\t2"):
            self.assertIsNone(hrproto.parse_command(bad), bad)

    def test_daemon_default_voice_does_not_focus(self):
        with mock.patch.object(herdremote.hrusage, "UsageCache"):
            daemon = herdremote.Daemon(types.SimpleNamespace(socket="/nonexistent", state=None))
        self.addCleanup(daemon.voice.stop)
        daemon.herdr = mock.Mock(spec=hrherdr.Herdr)
        daemon.voice.bus = self.bus
        daemon.voice.timer_factory = FakeTimer
        daemon.send = self.messages.append
        self.assertFalse(daemon.voice.voice_focus)
        daemon.command(b"vd\t1\tw:w1")
        self.assertEqual(hrproto.decode(self.messages[-1])[0], ["R", "1", "ok", "Talking"])
        daemon.herdr.assert_not_called()
        self.assertEqual(daemon.herdr.mock_calls, [])
        daemon.command(b"vu\t2")

    def test_daemon_local_pane_and_workspace_focus(self):
        self.voice.focus = self.daemon.voice_focus_target
        events = mock.Mock()
        events.attach_mock(self.daemon.herdr, "herdr")
        events.attach_mock(self.bus, "bus")
        self.assertEqual(self.result(b"vd\t1\tw1:p2"), ["R", "1", "ok", "Talking"])
        self.assertEqual(events.mock_calls[:2], [mock.call.herdr._focus_local(pane_id="w1:p2"),
                                                mock.call.bus.ready()])
        self.daemon.command(b"vu\t2")
        self.bus.status.side_effect = [(0, "idle"), (24, "recording")]
        self.assertEqual(self.result(b"vd\t3\tw:w1"), ["R", "3", "ok", "Talking"])
        self.daemon.herdr._focus_local.assert_called_with(workspace_id="w1", tab_id="w1:t3")
        self.daemon.herdr.focus_workspace.assert_not_called()
        self.daemon.herdr.focus_pane.assert_not_called()

    def test_no_legacy_focus_or_start_when_capability_or_window_missing(self):
        self.voice.focus = self.daemon.voice_focus_target
        for response in (False, hrherdr.HerdrError("No local herdr window")):
            self.daemon.herdr._focus_local.side_effect = (
                response if isinstance(response, Exception) else None)
            self.daemon.herdr._focus_local.return_value = response
            self.assertEqual(self.result(b"vd\t1\tw1:p2")[2], "err")
            self.assertEqual(self.bus.mock_calls, [])
            self.daemon.herdr.call.assert_not_called()

    def test_reset_and_link_loss_stop(self):
        for reset in (lambda: self.daemon.command(b"hr\t1"), self.daemon.close_link):
            with self.subTest(reset=reset):
                self.bus.status.side_effect = [(0, "idle"), (23, "recording")]
                self.daemon.command(b"vd\t1\t-")
                reset()
                self.assertEqual(self.methods()[-2:], ["HoldStart", "HoldStop"])
                self.assertFalse(self.voice.engaged)

    def test_run_link_exception_stops(self):
        self.daemon.refresh = mock.Mock()
        self.daemon.push = mock.Mock()
        link = mock.Mock(opened=True, frames=deque())
        self.daemon.link = link
        def lost():
            self.daemon.stopping.set()
            raise podlink.LinkClosed(1001, "fake unplug")
        link.poll.side_effect = lost
        self.daemon.command(b"vd\t1\t-")
        with mock.patch.object(herdremote.hrherdr, "Watcher"), mock.patch.object(self.daemon.answerer, "start"):
            self.daemon.run()
        self.assertEqual(self.methods(), ["HoldStart", "HoldStop"])
        self.assertIsNone(self.daemon.link)

    def test_daemon_shutdown_stops_even_without_link(self):
        self.daemon.command(b"vd\t1\t-")
        self.daemon.stopping.set()
        with mock.patch.object(herdremote.hrherdr, "Watcher"), mock.patch.object(self.daemon.answerer, "start"):
            self.daemon.run()
        self.assertEqual(self.methods(), ["HoldStart", "HoldStop"])


class VoiceLinkTests(unittest.TestCase):
    """Real PodLink frames on a fake pty + private herdr socket, mocked bus."""
    def setUp(self):
        import test_herdremote as fixtures
        self.fixtures = fixtures
        self.bus = mock.Mock(spec=hrvoice.SessionBus)
        self.bus.status.side_effect = [(0, "idle"), (23, "recording")]
        self.stopped = threading.Event()
        self.focus_on = False
        def call(method):
            if method == "HoldStart":
                if self.focus_on:
                    self.assertIn(("client.focus", {"pane_id": "w1:p1", "clients": "local"}),
                                  self.herdr.calls)
                else:
                    self.assertFalse(any(method.endswith(".focus")
                                         for method, _ in self.herdr.calls))
            if method == "HoldStop":
                self.stopped.set()
        self.bus.call.side_effect = call
        patch = mock.patch.object(hrvoice, "SessionBus", return_value=self.bus)
        patch.start()
        self.addCleanup(patch.stop)
        fixtures.EndToEnd.setUp(self)
        self.herdr.pong["capabilities"] = dict(client_targeted_focus=True)
        self.ipod.pump(1)
        self.ipod.send("hr\t1")
        self.ipod.wait_for("W")

    def tearDown(self):
        if hasattr(self, "daemon"):
            self.fixtures.EndToEnd.tearDown(self)
            try:
                self.ipod.close()
            except OSError:
                pass

    def press(self):
        self.ipod.send("vd\t1\tw1:p1")
        self.assertEqual(self.ipod.wait_for("R")[0], ["R", "1", "ok", "Talking"])

    def test_press_release_over_wire_focus_then_start(self):
        self.daemon.voice.voice_focus = True
        self.focus_on = True
        self.press()
        self.ipod.send("vu\t2")
        self.assertEqual(self.ipod.wait_for("R")[0], ["R", "2", "ok", "Sent"])
        self.assertTrue(self.stopped.is_set())
        self.assertEqual(self.bus.call.call_args_list, [mock.call("HoldStart"), mock.call("HoldStop")])

    def test_press_release_over_wire_default_keeps_cursor(self):
        self.assertFalse(self.daemon.voice.voice_focus)
        self.press()
        self.ipod.send("vu\t2")
        self.assertEqual(self.ipod.wait_for("R")[0], ["R", "2", "ok", "Sent"])
        self.assertTrue(self.stopped.is_set())

    def test_unplug_over_wire_stops(self):
        self.press()
        self.ipod.close()
        self.assertTrue(self.stopped.wait(3), "unplug did not stop the hold")

    def test_shutdown_with_live_hold_stops(self):
        self.press()
        self.daemon.stopping.set()
        self.thread.join(4)
        self.assertFalse(self.thread.is_alive())
        self.assertTrue(self.stopped.is_set())


if __name__ == "__main__":
    unittest.main()
