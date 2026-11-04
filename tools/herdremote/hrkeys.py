"""Whitelisted keyboard taps at the desktop's current focus, never herdr keys.

At most one bounded batch is outstanding; concurrent requests are not queued.
Constructing these objects does not run a tool or inject a key.
"""
import logging
import os
import queue
import shutil
import stat
import subprocess
import threading

TOOLS = ("auto", "ydotool", "wtype")
KEYS = {"bs": (14, "BackSpace"), "left": (105, "Left"),
        "right": (106, "Right"), "enter": (28, "Return")}
MAX_COUNT = 32
KEY_DELAY_MS = 2
TOOL_TIMEOUT = 0.5
log = logging.getLogger("herdremote.keys")


class KeyInputError(RuntimeError):
    pass


def tool_name(value):
    if value not in TOOLS:
        raise ValueError("key tool must be auto, ydotool or wtype")
    return value


def _integer(value):
    if type(value) is int:
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    raise ValueError("expected a decimal integer")


def tap_count(value):
    try:
        count = _integer(value)
        if 1 <= count <= MAX_COUNT:
            return count
    except ValueError:
        pass
    raise KeyInputError("Invalid key count")


def parse_key_scale(value):
    scale = _integer(value)
    if not 1 <= scale <= 8:
        raise ValueError("key_scale must be an integer from 1 to 8")
    return scale


def tool_timeout(count):
    # More than the 2 ms delay for each of the batch's two events per tap.
    return TOOL_TIMEOUT + count * 0.01


def add_arguments(parser):
    parser.add_argument("--key-tool", type=tool_name, choices=TOOLS,
                        default=os.environ.get("HERDREMOTE_KEY_TOOL", "auto"),
                        help="keyboard tap tool (default: auto, ydotool then wtype)")
    parser.add_argument("--key-scale", type=parse_key_scale,
                        default=os.environ.get("HERDREMOTE_KEY_SCALE", "1"),
                        metavar="1..8", help="left/right tap multiplier (default: 1)")


def socket_path():
    runtime = os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())
    return os.environ.get("YDOTOOL_SOCKET") or os.path.join(runtime, ".ydotool_socket")


def socket_exists(path):
    try:
        return stat.S_ISSOCK(os.stat(path).st_mode)
    except OSError:
        return False


class KeyTool:
    def __init__(self, tool=None):
        self.tool = tool_name(tool if tool is not None else
                              os.environ.get("HERDREMOTE_KEY_TOOL", "auto"))

    def tap(self, key, count=1):
        if key not in KEYS:
            raise KeyInputError("Unsupported key")
        count = tap_count(count)
        code, symbol = KEYS[key]
        env = os.environ.copy()
        path = shutil.which("ydotool") if self.tool in ("auto", "ydotool") else None
        if path and socket_exists(socket_path()):
            env["YDOTOOL_SOCKET"] = socket_path()
            command = [path, "key", "--key-delay=%d" % KEY_DELAY_MS]
            command += ["%d:1" % code, "%d:0" % code] * count
            release = [path, "key", "%d:0" % code]
        else:
            path = shutil.which("wtype") if self.tool in ("auto", "wtype") else None
            if not path:
                raise KeyInputError("Key tool unavailable")
            command = [path] + ["-k", symbol] * count
            release = [path, "-p", symbol]
        try:
            self._run(command, env, tool_timeout(count))
        except (OSError, subprocess.SubprocessError) as error:
            log.warning("key tool failed: %s", error)
            # A killed tool may have sent the down event. Release only;
            # never retry the tap through another tool and delete twice.
            try:
                self._run(release, env)
            except (OSError, subprocess.SubprocessError):
                log.warning("key release cleanup failed")
            raise KeyInputError("Key tool failed") from error

    @staticmethod
    def _run(command, env, timeout=TOOL_TIMEOUT):
        subprocess.run(command, env=env, capture_output=True, text=True,
                       check=True, timeout=timeout)


class KeyTaps:
    def __init__(self, tool=None, *, worker=None, key_scale=None):
        self.tool = tool if tool is not None else KeyTool()
        self.key_scale = parse_key_scale(
            key_scale if key_scale is not None else
            os.environ.get("HERDREMOTE_KEY_SCALE", "1"))
        self.worker = worker if worker is not None else threading.Thread
        self.lock = threading.Lock()
        self.busy = False
        self.generation = 0
        self.results = queue.Queue()

    def request(self, req, key, count=1):
        """None means accepted asynchronously; otherwise (ok, error text)."""
        if key not in KEYS:
            return False, "Unsupported key"
        try:
            count = tap_count(count)
        except KeyInputError as error:
            return False, str(error)
        if key in ("left", "right"):
            count = min(MAX_COUNT, count * self.key_scale)
        with self.lock:
            if self.busy:
                return False, "Key busy"
            self.busy = True
            generation = self.generation
        try:
            self.worker(target=self._tap, args=(generation, req, key, count), daemon=True).start()
        except RuntimeError:
            with self.lock:
                self.busy = False
            return False, "Key tool unavailable"
        return None

    def _tap(self, generation, req, key, count):
        try:
            with self.lock:
                if generation != self.generation:
                    return
            self.tool.tap(key, count)
            ok, text = True, None  # exact silent wire result: R <req> ok
        except KeyInputError as error:
            ok, text = False, str(error)
        finally:
            with self.lock:
                self.busy = False
        self.results.put((generation, req, ok, text))

    def reset(self):
        """Cancel unstarted work and invalidate old replies on reset/loss/exit.

        A tool call already in flight may finish its bounded batch.
        Keep busy until that call finishes; reset must not overlap batches.
        """
        with self.lock:
            self.generation += 1

    def replies(self):
        replies = []
        with self.lock:
            while True:
                try:
                    generation, req, ok, text = self.results.get_nowait()
                except queue.Empty:
                    return replies
                if generation == self.generation:
                    replies.append((req, ok, text))
