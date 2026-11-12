"""Host-only contract tests; no real device access or performance claims."""
import asyncio
import ctypes
import importlib.util
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("podlink", ROOT / "tools/podlink.py")
p = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = p
spec.loader.exec_module(p)


class FakeSerial:
    """Protocol peer, fragmented reads/writes; not a USB device emulator."""
    def __init__(self, maximum=p.MAX_MESSAGE):
        self.decoder, self.rx = p.Decoder(), bytearray()
        self.maximum = maximum
        self.negotiated = maximum
        self.upload = None
        self.closed = False
        self.fail = False
        self.close_count = 0
        self.port = "mock"
        self.reply(p.HELLO, 0, struct.pack("<HI", 1, maximum))

    def reply(self, kind, seq, payload):
        self.rx.extend(p.encode(kind, seq, payload))

    def read(self, count):
        if self.fail: raise OSError("USB unplugged (mock)")
        count = min(count, 37)
        data = bytes(self.rx[:count]); del self.rx[:count]
        return data

    def write(self, data):
        if self.fail: raise OSError("USB unplugged (mock)")
        data = data[:97]
        for f in self.decoder.feed(data):
            if f.type == p.OPEN:
                self.negotiated = min(self.maximum, struct.unpack("<HI", f.payload)[1])
                self.reply(p.OPEN, f.seq, struct.pack("<HI", 1, self.negotiated))
            elif f.type == p.PING: self.reply(p.PONG, f.seq, f.payload)
            elif f.type == p.CLOSE:
                self.close_count += 1
                self.reply(p.CLOSE, f.seq, f.payload)
            elif f.type == p.BINARY and self.upload is not None:
                goal, offset = self.upload
                assert f.payload == p.pattern(offset, len(f.payload))
                offset += len(f.payload)
                self.upload = goal, offset
                if offset == goal:
                    self.reply(p.TEXT, f.seq, f"OK TX {goal} errors=0".encode())
                    self.upload = None
            elif f.type == p.TEXT and f.payload.startswith(b"BENCH "):
                _, direction, amount = f.payload.split()
                count = int(amount)
                self.reply(p.TEXT, f.seq, b"READY " + direction + b" " + amount)
                if direction == b"TX": self.upload = count, 0
                else:
                    for offset in range(0, count, self.negotiated):
                        self.reply(p.BINARY, f.seq, p.pattern(offset, min(self.negotiated, count-offset)))
                    self.reply(p.TEXT, f.seq, f"OK RX {count}".encode())
            elif f.type == p.TEXT and f.payload == b"INFO":
                self.reply(p.TEXT, f.seq, b'{"firmware":"mock","battery_percent":50}')
            else: self.reply(f.type, f.seq, f.payload)
        return len(data)

    def close(self): self.closed = True


class CMessage(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint8), ("flags", ctypes.c_uint8),
                ("seq", ctypes.c_uint32), ("length", ctypes.c_uint32),
                ("data", ctypes.POINTER(ctypes.c_ubyte))]


class CParser(ctypes.Structure):
    _fields_ = [("bytes", ctypes.c_ubyte * (p.MAX_MESSAGE + 20)), ("used", ctypes.c_uint),
                ("consumed", ctypes.c_uint)]


class WireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        lib = Path(cls.tmp.name) / "wire.so"
        subprocess.run(["cc", "-std=c99", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
                        "-I" + str(ROOT / "firmware/export"),
                        str(ROOT / "firmware/usbstack/podlink_wire.c"), "-o", str(lib)], check=True)
        cls.c = ctypes.CDLL(str(lib))
        cls.c.podlink_encode.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint32,
                                         ctypes.c_void_p, ctypes.c_uint]
        cls.c.podlink_decode.argtypes = [ctypes.POINTER(CParser), ctypes.c_ubyte, ctypes.POINTER(CMessage)]

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def test_cross_language_wire_and_random_fragmentation(self):
        rng = random.Random(721)
        for length in [0, 1, 4, 31, 512, 4095, 4096, 16383, 16384]:
            for kind in range(1, 8):
                payload = rng.randbytes(length)
                out = ctypes.create_string_buffer(p.MAX_MESSAGE + 20)
                n = self.c.podlink_encode(out, kind, 0x12345678, payload, length)
                expected = p.encode(kind, 0x12345678, payload)
                self.assertEqual(out.raw[:n], expected)
                parser, msg = CParser(), CMessage()
                for byte in expected[:-1]: self.assertEqual(self.c.podlink_decode(ctypes.byref(parser), byte, ctypes.byref(msg)), 0)
                self.assertEqual(self.c.podlink_decode(ctypes.byref(parser), expected[-1], ctypes.byref(msg)), 1)
                self.assertEqual(ctypes.string_at(msg.data, msg.length), payload)
                decoder, frames = p.Decoder(), []
                for i in range(0, n, 17): frames += decoder.feed(expected[i:i+17])
                self.assertEqual(frames, [p.Frame(kind, 0x12345678, payload)])

    def test_bulk_decoder_preserves_fragmentation_and_message_boundaries(self):
        self.c.podlink_decode_bytes.argtypes = [ctypes.POINTER(CParser), ctypes.c_void_p,
            ctypes.c_uint, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(CMessage)]
        corrupt = bytearray(p.encode(p.TEXT, 9, b"bad crc")); corrupt[-1] ^= 1
        messages = [p.Frame(p.BINARY, 10, p.pattern(0,p.MAX_MESSAGE)),
                    p.Frame(p.TEXT, 11, b""), p.Frame(p.TEXT, 12, b"last")]
        stream = b"noise" + corrupt + b"".join(p.encode(f.type,f.seq,f.payload) for f in messages)
        for chunk_size in [1,7,64,512,4096,p.MAX_MESSAGE,p.MAX_MESSAGE+20]:
            parser, msg, actual = CParser(), CMessage(), []
            for offset in range(0,len(stream),chunk_size):
                chunk = bytes(stream[offset:offset+chunk_size])
                used = 0
                while used < len(chunk):
                    consumed = ctypes.c_uint()
                    rc = self.c.podlink_decode_bytes(ctypes.byref(parser), chunk[used:],
                        len(chunk)-used, ctypes.byref(consumed), ctypes.byref(msg))
                    self.assertTrue(consumed.value or rc)
                    used += consumed.value
                    if rc == 1:
                        actual.append(p.Frame(msg.type,msg.seq,ctypes.string_at(msg.data,msg.length)))
            self.assertEqual(actual,messages)

    def test_resync_retains_trailing_frames_and_drains_without_input(self):
        self.c.podlink_decode_bytes.argtypes = [ctypes.POINTER(CParser), ctypes.c_void_p,
            ctypes.c_uint, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(CMessage)]
        expected = [p.Frame(p.TEXT, 2, b"first"), p.Frame(p.TEXT, 3, b"second")]
        for outer_length in (32, 47):
            stream = p.HEADER.pack(p.MAGIC, p.TEXT, 0, 0, 1, outer_length)
            stream += b"".join(p.encode(f.type, f.seq, f.payload) for f in expected)
            self.assertEqual(p.Decoder().feed(stream), expected)
            for chunk_size in (1, 7, 64, 4096):
                parser, msg, actual = CParser(), CMessage(), []
                for offset in range(0, len(stream), chunk_size):
                    chunk = stream[offset:offset + chunk_size]
                    used = 0
                    # Include zero-input calls to drain frames buffered during recovery.
                    for _ in range(len(chunk) + 100):
                        consumed = ctypes.c_uint()
                        rc = self.c.podlink_decode_bytes(ctypes.byref(parser), chunk[used:],
                            len(chunk) - used, ctypes.byref(consumed), ctypes.byref(msg))
                        used += consumed.value
                        if rc == 1:
                            actual.append(p.Frame(msg.type, msg.seq,
                                ctypes.string_at(msg.data, msg.length)))
                        if rc == 0 and used == len(chunk):
                            break
                    else:
                        self.fail("decoder did not drain")
                self.assertEqual(actual, expected, (outer_length, chunk_size))

    def test_crc_resync_oversize_and_bounded_garbage(self):
        bad = bytearray(p.encode(p.BINARY, 1, b"broken")); bad[-1] ^= 8
        decoder = p.Decoder()
        frames = decoder.feed(b"junk" + bad + p.encode(p.TEXT, 2, b"good"))
        self.assertEqual(frames, [p.Frame(p.TEXT, 2, b"good")])
        self.assertEqual(decoder.crc_errors, 1)
        for _ in range(100):
            decoder.feed(b"X" * 4096)
            self.assertLessEqual(len(decoder.buffer), 3)
        header = p.HEADER.pack(p.MAGIC, p.TEXT, 0, 0, 1, p.MAX_MESSAGE + 1)
        with self.assertRaises(p.LinkClosed): decoder.feed(header)
        self.assertEqual(decoder.feed(p.encode(p.TEXT, 3, b"after")), [p.Frame(p.TEXT, 3, b"after")])

    def test_16385_rejected_by_both_codecs(self):
        payload = p.pattern(0, 16385)
        with self.assertRaises(ValueError):
            p.encode(p.BINARY, 1, payload)
        out = ctypes.create_string_buffer(p.MAX_MESSAGE + 20)
        self.assertEqual(self.c.podlink_encode(out, p.BINARY, 1, payload, len(payload)), 0)
        parser, msg = CParser(), CMessage()
        header = p.HEADER.pack(p.MAGIC, p.BINARY, 0, 0, 1, len(payload))
        for byte in header[:-1]:
            self.assertEqual(self.c.podlink_decode(ctypes.byref(parser), byte, ctypes.byref(msg)), 0)
        self.assertEqual(self.c.podlink_decode(ctypes.byref(parser), header[-1], ctypes.byref(msg)), -2)

    def test_negotiation_legacy_and_larger_peers(self):
        for own, peer in [(16384, 4096), (4096, 16384), (16384, 16384), (16384, 65536)]:
            with self.subTest(own=own, peer=peer), patch.object(p, "MAX_MESSAGE", own), \
                    patch.object(p, "HELLO_DATA", struct.pack("<HI", 1, own)):
                serial = FakeSerial(peer)
                link = p.Link(serial, 2)
                link.open()
                limit = min(own, peer)
                self.assertEqual(link.max_message, limit)
                payload = p.pattern(0, limit)
                link.send(p.BINARY, payload)
                self.assertEqual(link.expected(p.BINARY).payload, payload)
                with self.assertRaises(ValueError):
                    link.send(p.BINARY, payload + b"x")
                result = link.bench(limit * 2 + 13)
                self.assertEqual(result["host_to_ipod"]["message_bytes"], limit)
                self.assertEqual(result["ipod_to_host"]["integrity"], "PASS")
                link.close()

    def test_receive_rejects_negotiated_oversize(self):
        serial = FakeSerial(4096)
        link = p.Link(serial, 2)
        link.open()
        serial.reply(p.BINARY, 10, p.pattern(0, 4097))
        with self.assertRaises(p.LinkClosed) as closed:
            link.receive()
        self.assertEqual(closed.exception.code, 1009)

    def test_coalesced_open_and_oversize_obey_negotiated_limit(self):
        serial = FakeSerial(4096)
        # No HELLO; ACK and application data are queued by the same poll.
        serial.rx.clear()
        serial.read = lambda count: self._read_all(serial, count)
        serial.reply(p.OPEN, 1, struct.pack("<HI", 1, 4096))
        serial.reply(p.BINARY, 10, p.pattern(0, 4097))
        link = p.Link(serial, 2)
        with self.assertRaises(p.LinkClosed) as closed:
            link.open()
        self.assertEqual(closed.exception.code, 1009)

    @staticmethod
    def _read_all(serial, count):
        data = bytes(serial.rx[:count])
        del serial.rx[:count]
        return data

    def test_cli_session_commands_and_close(self):
        serial = FakeSerial(); link = p.Link(serial, .5); link.open()
        self.assertEqual(link.ping(), "PONG")
        self.assertEqual(link.info()["firmware"], "mock")
        self.assertEqual(link.echo("hello 世界"), "hello 世界")
        result = link.bench(4096 * 3 + 13)
        self.assertEqual(result["host_to_ipod"]["integrity"], "PASS")
        self.assertEqual(result["ipod_to_host"]["integrity"], "PASS")
        self.assertEqual(link.latency(3)["echo_64B"]["samples"], 3)
        link.close();self.assertEqual(serial.close_count, 1)

    def test_no_port_and_multiple_ports(self):
        with self.assertRaises(RuntimeError): p.find_port([])
        from types import SimpleNamespace
        port = SimpleNamespace(device="mock", vid=p.VID, pid=p.PID)
        self.assertEqual(p.find_port([port]), "mock")
        with self.assertRaises(RuntimeError): p.find_port([port, port])


class WebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_backpressure_ping_keeps_loop_and_ws_close_responsive(self):
        from websockets.asyncio.server import serve
        from websockets.asyncio.client import connect

        class Port:
            def __init__(self):
                self.started = threading.Event()
                self.release = threading.Event()
                self.ping_sent = threading.Event()
                self.pong_sent = False
                self.closed = False

            def write(self, data):
                if data[4] == p.BINARY:
                    self.started.set()
                    self.release.wait(2)
                if data[4] == p.PONG:
                    self.pong_sent = True
                assert not self.closed
                return len(data)

            def read(self, count):
                assert not self.closed
                if self.started.is_set() and not self.ping_sent.is_set():
                    self.ping_sent.set()
                    return p.encode(p.PING, 19, b"keepalive")
                return b""

        port = Port()
        link = p.Link(port, 1)
        link.opened = True
        done = asyncio.Event()

        async def handler(ws):
            try:
                await p.bridge_session(ws, link)
            finally:
                port.closed = True
                done.set()

        timer = threading.Timer(.5, port.release.set)
        timer.start()
        try:
            async with serve(handler, "127.0.0.1", 0) as server:
                uri = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/"
                async with connect(uri) as ws:
                    await ws.send(b"application data")
                    while not port.started.is_set():
                        await asyncio.sleep(.001)
                    start = time.monotonic()
                    await asyncio.sleep(.02)
                    self.assertLess(time.monotonic() - start, .15)
                    await asyncio.wait_for(ws.close(), .15)
                    self.assertFalse(port.release.is_set())
                    # Cleanup must wait for outstanding serial work before port close.
                    self.assertFalse(port.closed)
                port.release.set()
                await asyncio.wait_for(done.wait(), 2)
                self.assertTrue(port.ping_sent.is_set())
                self.assertTrue(port.pong_sent)
        finally:
            port.release.set()
            timer.cancel()
            timer.join()

    async def test_real_websocket_text_binary_exclusivity_unplug_reconnect(self):
        from websockets.asyncio.server import serve
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed
        peers = []
        def factory(device, timeout):
            peer = FakeSerial();peers.append(peer);return peer
        with patch.object(p, "serial_port", factory):
            async with serve(p.bridge_handler(None, 2), "127.0.0.1", 0, max_size=p.MAX_MESSAGE) as server:
                port = server.sockets[0].getsockname()[1]
                uri = f"ws://127.0.0.1:{port}/"
                async with connect(uri) as ws:
                    await ws.send("websocket 世界")
                    self.assertEqual(await asyncio.wait_for(ws.recv(), 2), "websocket 世界")
                    await ws.send(p.pattern(0,p.MAX_MESSAGE))
                    self.assertEqual(await asyncio.wait_for(ws.recv(), 2), p.pattern(0,p.MAX_MESSAGE))
                    async with connect(uri) as second:
                        with self.assertRaises(ConnectionClosed): await second.recv()
                        self.assertEqual(second.close_code, 1013)
                await asyncio.sleep(.05)
                self.assertTrue(peers[0].closed)
                self.assertEqual(peers[0].close_count, 1)
                async with connect(uri) as ws:
                    await ws.send("before unplug"); await ws.recv()
                    peers[-1].fail = True
                    with self.assertRaises(ConnectionClosed): await asyncio.wait_for(ws.recv(),2)
                await asyncio.sleep(.05)
                async with connect(uri) as ws:
                    await ws.send("after replug");self.assertEqual(await ws.recv(), "after replug")


if __name__ == "__main__": unittest.main()
