"""Whitelisted keyboard taps at the desktop's current focus, never herdr keys.

At most one tap is outstanding; repeats are rejected rather than queued.
Constructing these objects does not run a tool or inject a key.
"""
import logging
import os
import queue
import shutil
import stat
import subprocess
import threading
import time

TOOLS = ("auto", "ydotool", "wtype")
KEYS = {"bs": (14, "BackSpace"), "left": (105, "Left"),
        "right": (106, "Right"), "enter": (28, "Return")}
MAX_RATE = 30
MAX_AGE = 0.1
TOOL_TIMEOUT = 0.5
log = logging.getLogger("herdremote.keys")


class KeyInputError(RuntimeError):
    pass


def tool_name(value):
    if value not in TOOLS:
        raise ValueError("key tool must be auto, ydotool or wtype")
    return value


def add_arguments(parser):
    parser.add_argument("--key-tool", type=tool_name, choices=TOOLS,
                        default=os.environ.get("HERDREMOTE_KEY_TOOL", "auto"),
                        help="keyboard tap tool (default: auto, ydotool then wtype)")


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

    def tap(self, key):
        if key not in KEYS:
            raise KeyInputError("Unsupported key")
        code, symbol = KEYS[key]
        env = os.environ.copy()
        path = shutil.which("ydotool") if self.tool in ("auto", "ydotool") else None
        if path and socket_exists(socket_path()):
            env["YDOTOOL_SOCKET"] = socket_path()
            command = [path, "key", "%d:1" % code, "%d:0" % code]
            release = [path, "key", "%d:0" % code]
        else:
            path = shutil.which("wtype") if self.tool in ("auto", "wtype") else None
            if not path:
                raise KeyInputError("Key tool unavailable")
            command = [path, "-k", symbol]
            release = [path, "-p", symbol]
        try:
            self._run(command, env)
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
    def _run(command, env):
        subprocess.run(command, env=env, capture_output=True, text=True,
                       check=True, timeout=TOOL_TIMEOUT)


class KeyTaps:
    def __init__(self, tool=None, *, clock=None, worker=None):
        self.tool = tool if tool is not None else KeyTool()
        self.clock = clock if clock is not None else time.monotonic
        self.worker = worker if worker is not None else threading.Thread
        self.lock = threading.Lock()
        self.busy = False
        self.next_tap = float("-inf")
        self.generation = 0
        self.results = queue.Queue()

    def request(self, req, key):
        """None means accepted asynchronously; otherwise (ok, error text)."""
        if key not in KEYS:
            return False, "Unsupported key"
        with self.lock:
            now = self.clock()
            if self.busy or now < self.next_tap:
                return False, "Key rate limit"
            self.busy = True
            self.next_tap = now + 1 / MAX_RATE
            generation = self.generation
        try:
            self.worker(target=self._tap, args=(generation, req, key, now), daemon=True).start()
        except RuntimeError:
            with self.lock:
                self.busy = False
            return False, "Key tool unavailable"
        return None

    def _tap(self, generation, req, key, started):
        try:
            with self.lock:
                if generation != self.generation:
                    return
            if self.clock() - started > MAX_AGE:
                raise KeyInputError("Key request expired")
            self.tool.tap(key)
            ok, text = True, None  # exact silent wire result: R <req> ok
        except KeyInputError as error:
            ok, text = False, str(error)
        finally:
            with self.lock:
                self.busy = False
        self.results.put((generation, req, ok, text))

    def reset(self):
        """Cancel unstarted work and invalidate old replies on reset/loss/exit.

        A tool call already in flight may finish its single down/up pair.
        Keep busy until that call finishes; reset must not bypass the cap.
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
