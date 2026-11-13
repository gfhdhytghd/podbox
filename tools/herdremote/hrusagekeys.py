"""Expiring Usage-view lease and local volume batches. No device/GUI access here."""
import fcntl
import json
import logging
import os
from pathlib import Path
import socket
import time
import uuid

VIEW_LEASE_SECONDS = 0.75
REPORT_MAX_AGE_SECONDS = 0.10  # Less than the bridge's 150 ms release timeout.
log = logging.getLogger('herdremote.usagekeys')


def runtime_dir():
    root = os.environ.get('XDG_RUNTIME_DIR', '/run/user/%d' % os.getuid())
    return Path(root) / 'podbox-usage-keys'


class UsageKeys:
    def __init__(self, keys, alive, *, directory=None, clock=time.monotonic):
        self.keys, self.alive, self.clock = keys, alive, clock
        self.directory = Path(directory) if directory else runtime_dir()
        self.socket = self.lockfile = None
        self.active, self.expires = False, 0.0
        self.token = uuid.uuid4().hex

    def start(self):
        """Only the running daemon owns endpoints; constructors are inert."""
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = self.directory.stat()
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise OSError('Usage runtime directory must be private')
            self.lockfile = (self.directory / 'owner.lock').open('a')
            fcntl.flock(self.lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if self.lockfile:
                self.lockfile.close()
                self.lockfile = None
            log.warning('Usage keys disabled: %s', error)
            return
        try:
            endpoint = self.directory / 'keys.sock'
            endpoint.unlink(missing_ok=True)  # Exclusive lock owns stale endpoint.
            self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            self.socket.setblocking(False)
            self.socket.bind(str(endpoint))
            os.chmod(endpoint, 0o600)
            self.update(False)
        except OSError as error:
            log.warning('Usage keys disabled: %s', error)
            self.close()

    def update(self, active):
        active = bool(active and self.alive())
        if active != self.active:
            self.token = uuid.uuid4().hex
        self.active = active
        self.expires = self.clock() + VIEW_LEASE_SECONDS if active else 0.0
        if self.socket is not None:
            try:
                temporary = self.directory / 'view.tmp'
                temporary.write_text(json.dumps(dict(version=1, token=self.token,
                    expires=self.expires, usage=active)))
                temporary.replace(self.directory / 'view.json')
            except OSError as error:
                self.active = False
                log.warning('Usage lease unavailable: %s', error)

    def valid(self, token, sent):
        now = self.clock()
        return (self.active and self.alive() and token == self.token and
                now < self.expires and 0 <= now - sent < REPORT_MAX_AGE_SECONDS)

    def poll(self):
        if self.socket is None:
            return
        # Bound work and reject buffered old reports; never wait on a tool.
        for _ in range(32):
            try:
                data = self.socket.recv(256)
            except BlockingIOError:
                break
            try:
                version, token, timestamp, key, number = data.decode('ascii').split()
                sent, count = float(timestamp), int(number)
                allowed = count in ((1, 3) if key in ('up', 'down') else (1, 3, 10))
                if (version != 'UK1' or key not in ('left', 'right', 'up', 'down') or
                        not allowed or not self.valid(token, sent)):
                    continue
            except (ValueError, UnicodeError):
                continue
            # Same worker/backend as Usage k. It rejects busy work rather than
            # queueing. A deferred thread rechecks the lease/report deadline.
            self.keys.request('', key, count, scaled=False, silent=True,
                guard=lambda token=token, sent=sent: self.valid(token, sent))

    def close(self):
        self.active = False
        self.expires = 0.0
        if self.socket is not None:
            self.socket.close()
            self.socket = None
        if self.lockfile is not None:
            for name in ('view.json', 'view.tmp', 'keys.sock'):
                (self.directory / name).unlink(missing_ok=True)
            self.lockfile.close()
            self.lockfile = None
