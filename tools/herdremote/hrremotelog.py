"""Out-of-band remote diagnostics. No serial port or application commands."""
from datetime import datetime, timezone
import logging
from pathlib import Path
import queue
import threading
import time

log = logging.getLogger("herdremote.remote-log")
MAX_BYTES = 4 * 1024 * 1024


class Writer:
    """One bounded disk worker, started lazily on the first LOG frame."""

    def __init__(self, path=None, max_bytes=MAX_BYTES):
        self.path = Path(path) if path else (
            Path.home() / ".local/state/podbox/remote-live.log")
        self.max_bytes = max_bytes
        self.jobs = queue.Queue(maxsize=256)
        self.worker = None
        self.stopping = threading.Event()
        self.lost = 0

    def submit(self, payload):
        # Dispatch the prefix before parsing any application commands.
        if not payload.startswith(b"LOG "):
            return False
        if self.stopping.is_set():
            return True
        item = (time.monotonic(), time.time(), bytes(payload), self.lost)
        try:
            self.jobs.put_nowait(item)
            self.lost = 0
        except queue.Full:
            self.lost += 1
        if self.worker is None:
            self.worker = threading.Thread(target=self._run, daemon=True,
                                           name="herdremote-remote-log")
            self.worker.start()
        return True

    def _append(self, mono, wall, payload, lost):
        stamp = datetime.fromtimestamp(wall, timezone.utc).isoformat(
            timespec="microseconds")
        text = payload.decode("utf-8", errors="replace")
        # A device payload must not forge additional host log records.
        text = "".join(c if c >= " " and c != "\x7f" else
                       "\\x%02x" % ord(c) for c in text)
        prefix = f"{stamp} mono={mono:.6f} "
        data = ((prefix + f"LOG HOST LOST {lost}\n") if lost else "")
        data = (data + prefix + text + "\n").encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            size = 0
        if size and size + len(data) > self.max_bytes:
            self.path.replace(self.path.with_name(self.path.name + ".1"))
        with self.path.open("ab") as stream:
            stream.write(data)

    def _run(self):
        while not self.stopping.is_set() or not self.jobs.empty():
            try:
                item = self.jobs.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self._append(*item)
            except OSError as error:
                log.warning("remote log write failed: %s", error)

    def close(self):
        self.stopping.set()
        if self.worker:
            self.worker.join(timeout=2)
