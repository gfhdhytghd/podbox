"""Live diagnostics use only the existing tty owner; no devices or services."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import herdremote
import hrproto
import hrremotelog


class RemoteLog(unittest.TestCase):
    def test_off_and_old_firmware_create_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "absent/live.log"
            w = hrremotelog.Writer(p)
            for payload in (b"hr\t1", b"sub\tw1", b"LOG", b"LOGX data"):
                self.assertFalse(w.submit(payload))
            w.close()
            self.assertIsNone(w.worker)
            self.assertFalse(p.parent.exists())

    def test_received_timestamps_and_single_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "nested/live.log"
            w = hrremotelog.Writer(p)
            with mock.patch.object(hrremotelog.time, "monotonic", return_value=12.25), \
                 mock.patch.object(hrremotelog.time, "time", return_value=0):
                self.assertTrue(w.submit(b"LOG PLAY tick=42 tap\nLOG fake\x00"))
            w.close()
            text = p.read_text()
            self.assertTrue(text.startswith("1970-01-01T00:00:00.000000+00:00 mono=12.250000 LOG PLAY"))
            self.assertEqual(len(text.splitlines()), 1)
            self.assertIn("\\x0aLOG fake\\x00", text)

    def test_rotation_keeps_one_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "live.log"
            w = hrremotelog.Writer(p, max_bytes=100)
            for n in range(10):
                w.submit(f"LOG RAW {n}".encode())
            w.close()
            self.assertIn("LOG RAW 9", p.read_text())
            self.assertIn("LOG RAW 8", p.with_name("live.log.1").read_text())
            self.assertEqual(len(list(Path(tmp).iterdir())), 2)
            self.assertLessEqual(p.stat().st_size, 100)

    def test_log_bypasses_all_application_commands(self):
        # Exercise the real Daemon.command entry point, without sockets/workers.
        d = object.__new__(herdremote.Daemon)
        d.remote_log = mock.Mock()
        d.remote_log.submit.return_value = True
        with mock.patch.object(hrproto, "parse_command", side_effect=AssertionError("app parse")):
            d.command(b"LOG vd\t8\tp1")
        d.remote_log.submit.assert_called_once_with(b"LOG vd\t8\tp1")

    def test_old_host_parser_ignores_new_firmware(self):
        # This parser is unchanged from the pre-stream host.
        for payload in (b"LOG LOST 4", b"LOG EP0 tick=2 SELECT down",
                        b"LOG PLAY tick=3 double context=0"):
            self.assertIsNone(hrproto.parse_command(payload))

    def test_bounded_queue_reports_host_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = hrremotelog.Writer(Path(tmp) / "live.log")
            # Hold off the disk worker deterministically, then fill the queue.
            w.worker = mock.Mock()
            for _ in range(300):
                w.submit(b"LOG RAW")
            self.assertEqual(w.jobs.qsize(), 256)
            self.assertEqual(w.lost, 44)
            w.jobs.get_nowait()
            w.submit(b"LOG LOST 2")
            items = []
            while not w.jobs.empty():
                items.append(w.jobs.get_nowait())
            self.assertEqual(items[-1][2:], (b"LOG LOST 2", 44))
            w._append(*items[-1])
            self.assertIn("LOG HOST LOST 44", w.path.read_text())

    def test_disk_error_does_not_escape_receive_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = hrremotelog.Writer(tmp)  # Directory cannot be opened for append.
            with self.assertLogs("herdremote.remote-log", level="WARNING"):
                self.assertTrue(w.submit(b"LOG VOL data"))
                w.close()


if __name__ == "__main__":
    unittest.main()
