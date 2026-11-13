"""Bounded hold-to-talk through the local fcitx voice addon's session bus.

No key synthesis. Constructing this module's objects never contacts D-Bus.
The watchdog uses its own timer so a stalled herdr/link cannot extend a hold.
"""
import logging
import math
import os
import shlex
import subprocess
import threading

DEST = "org.fcitx.Fcitx5"
OBJECT = "/org/lais/Voice"
INTERFACE = "org.lais.FcitxVoice1"
NOT_RUNNING = "Voice input not running"
METHODS = ("HoldStart", "HoldStartRaw")
log = logging.getLogger("herdremote.voice")


class VoiceError(RuntimeError):
    pass


def boolean(value):
    if str(value).lower() in ("1", "true", "yes", "on"):
        return True
    if str(value).lower() in ("0", "false", "no", "off"):
        return False
    raise ValueError("voice_focus must be on or off")


def hold_seconds(value):
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("voice_max_hold must be finite and greater than zero")
    return seconds


def add_arguments(parser):
    parser.add_argument("--voice-focus", type=boolean,
                        default=os.environ.get("HERDREMOTE_VOICE_FOCUS", "off"),
                        metavar="on|off", help="focus a local target before voice (default: off)")
    parser.add_argument("--voice-max-hold", type=hold_seconds,
                        default=os.environ.get("HERDREMOTE_VOICE_MAX_HOLD", "120"),
                        metavar="SECONDS", help="maximum voice hold (default: 120 seconds)")
    parser.add_argument("--voice-method", choices=METHODS,
                        default=os.environ.get("HERDREMOTE_VOICE_METHOD", "HoldStart"),
                        help="fcitx hold method (default: HoldStart)")


class SessionBus:
    def _call(self, dest, path, interface, method, *args):
        try:
            result = subprocess.run(
                ["busctl", "--user", "--auto-start=no", "--timeout=2s", "call",
                 dest, path, interface, method, *args],
                capture_output=True, text=True, check=True, timeout=3)
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError) as error:
            log.warning("voice D-Bus %s failed: %s", method, error)
            raise VoiceError(NOT_RUNNING) from error

    def call(self, method):
        if method not in METHODS + ("HoldStop", "Cancel", "Status"):
            raise ValueError("unsupported voice method")
        return self._call(DEST, OBJECT, INTERFACE, method)

    def status(self):
        try:
            signature, token, state = shlex.split(self.call("Status"))
            if signature != "ts":
                raise ValueError("bad signature")
            return int(token), state
        except ValueError as error:
            raise VoiceError(NOT_RUNNING) from error

    def ready(self):
        # The addon stays loaded when the microphone bridge is absent.
        reply = self._call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                           "org.freedesktop.DBus", "NameHasOwner",
                           "s", "org.lais.Voice")
        if reply != "b true":
            raise VoiceError(NOT_RUNNING)


class HoldToTalk:
    def __init__(self, focus, *, voice_focus=False, voice_max_hold=120,
                 voice_method="HoldStart", bus=None, timer=None):
        self.focus = focus
        self.voice_focus = boolean(voice_focus)
        self.voice_max_hold = hold_seconds(voice_max_hold)
        if voice_method not in METHODS:
            raise ValueError("voice_method must be HoldStart or HoldStartRaw")
        self.voice_method = voice_method
        self.bus = bus if bus is not None else SessionBus()
        self.timer_factory = timer if timer is not None else threading.Timer
        self.lock = threading.RLock()
        self.timer = None
        self.down = False
        self.engaged = False  # includes a start whose D-Bus reply was lost
        self.token = 0
        self.discarding = self.cancelled = False
        self.expired = False
        self.generation = 0

    def _arm(self, seconds):
        if self.timer:
            self.timer.cancel()
        self.generation += 1
        self.timer = self.timer_factory(seconds, self._timeout,
                                        args=(self.generation,))
        self.timer.daemon = True
        self.timer.start()

    def _stop(self):
        if self.timer:
            self.timer.cancel()
            self.timer = None
        self.generation += 1
        if not self.engaged:
            return
        try:
            if self.discarding and not self.cancelled:
                token, _state = self.bus.status()
                if token and token == self.token:
                    self.bus.call("Cancel")
                self.cancelled = True
            self.bus.call("HoldStop")
        except VoiceError:
            # Keep ownership and retry: a lost reply must not leave the mic on.
            self._arm(1)
            raise
        self.engaged = False
        self.token = 0
        self.discarding = self.cancelled = False

    def _timeout(self, generation):
        with self.lock:
            if generation != self.generation:
                return
            self.expired = True
            log.info("voice hold limit reached; stopping")
            try:
                self._stop()
            except VoiceError:
                pass  # _stop scheduled another attempt

    def press(self, target):
        with self.lock:
            if self.down:
                return ((False, "Voice hold timed out") if self.expired else
                        (True, "Talking"))
            if self.engaged:
                return False, NOT_RUNNING  # still retrying a failed stop
            try:
                if self.voice_focus and target != "-":
                    self.focus(target)
                self.bus.ready()
                token, state = self.bus.status()
                if token or state != "idle":
                    return False, "Voice input busy"
                self.engaged = True
                self._arm(self.voice_max_hold)
                self.bus.call(self.voice_method)
                token, state = self.bus.status()
                if not token or state != "recording":
                    raise VoiceError(NOT_RUNNING)
                self.token = token
                self.down = True
                self.expired = False
                return True, "Talking"
            except VoiceError as error:
                try:
                    self._stop()
                except VoiceError:
                    pass
                return False, str(error)

    def release(self):
        with self.lock:
            self.down = False
            self.expired = False
            try:
                discarded = self.discarding
                self._stop()
                return True, "Discarded" if discarded else "Sent"
            except VoiceError as error:
                return False, str(error)

    def discard(self):
        with self.lock:
            self.down = self.expired = False
            if self.engaged:
                self.discarding = True
            try:
                self._stop()
                return True, "Discarded"
            except VoiceError as error:
                return False, str(error)

    def stop(self):
        """Release our hold on reset, link loss or shutdown; idle is a no-op."""
        ok, text = self.release()
        if not ok:
            log.warning("voice safety stop failed: %s", text)
