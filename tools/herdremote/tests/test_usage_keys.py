"""S2: real local IPC, fake clock/tool; never inject desktop keys."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'usb_remote_key'))
import herdremote
import hrkeys
import hrproto
import hrusagekeys
import usb_remote_key as bridge


class Deferred:
    pending = []
    def __init__(self, *, target, args, daemon):
        self.target, self.args = target, args
    def start(self):
        self.pending.append(self)
    @classmethod
    def finish(cls):
        for item in cls.pending:
            item.target(*item.args)
        cls.pending.clear()


class UsageKeysTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now, self.alive = 100.0, True
        self.tool = mock.Mock()
        self.keys = hrkeys.KeyTaps(self.tool, worker=Deferred, key_scale=8)
        self.gate = hrusagekeys.UsageKeys(self.keys, lambda: self.alive,
            directory=Path(self.temp.name)/'runtime', clock=lambda: self.now)
        self.gate.start()
        self.addCleanup(self.gate.close)
        self.addCleanup(Deferred.finish)
        self.client = bridge.UsageVolume(str(self.gate.directory))
        self.gate.update(True)

    def report(self, direction=bridge.VOL_UP, age=0):
        self.client.report(direction, age, self.now)
        self.gate.poll()
        Deferred.finish()

    def test_schedule_boundaries_both_directions_exact_unscaled_batches(self):
        for age,count,vertical in [(0,1,False),(.499,1,False),(.5,3,False),
                (.999,3,False),(1,10,False),(3.999,10,False),(4,1,True),
                (6.999,1,True),(7,3,True),(100,3,True)]:
            for direction in (bridge.VOL_UP,bridge.VOL_DOWN):
                self.report(direction,age)
                key = ('up' if direction==bridge.VOL_UP else 'down') if vertical else (
                       'left' if direction==bridge.VOL_UP else 'right')
                self.tool.tap.assert_called_with(key,count)
        self.assertEqual(self.tool.tap.call_count,20)
        self.assertEqual(self.keys.replies(),[])  # No unsolicited R frames.

    def test_real_forwarder_tap_first_repeat_skip_and_full_hold(self):
        usb=mock.Mock();forward=bridge.VolumeForwarder(usb,self.client)
        forward.frame(bytes.fromhex('ee870b59'),self.now,.068)
        self.gate.poll();Deferred.finish()
        self.now+=.108;forward.frame(b'',self.now);self.gate.poll();Deferred.finish()
        self.assertEqual(self.tool.tap.call_args_list,[mock.call('left',1)])
        for i in range(2,76):
            self.now=100+i*.108;self.gate.update(True)
            forward.frame(b'',self.now);self.gate.poll();Deferred.finish()
            age=i*.108+.068
            expected=('left',1) if age<.5 else ('left',3) if age<1 else (
                     'left',10) if age<4 else ('up',1) if age<7 else ('up',3)
            self.tool.tap.assert_called_with(*expected)
        self.assertEqual(self.tool.tap.call_count,75)
        # Release silence cannot generate more keys. A new full frame resets age.
        self.now+=1;self.gate.update(True);forward.frame(b'',self.now)
        self.gate.poll();Deferred.finish();self.assertEqual(self.tool.tap.call_count,75)
        forward.frame(bytes.fromhex('ee870d59'),self.now,.068)
        self.gate.poll();Deferred.finish();self.tool.tap.assert_called_with('right',1)

    def test_off_stale_missing_malformed_state_and_dead_link_no_keys(self):
        for mode in ('off','expired','missing','malformed','dead'):
            self.gate.update(True);self.alive=True
            if mode=='off':self.gate.update(False)
            if mode=='expired':self.now+=1
            if mode=='missing':(self.gate.directory/'view.json').unlink()
            if mode=='malformed':(self.gate.directory/'view.json').write_text('{}')
            if mode=='dead':self.alive=False
            self.report()
        self.tool.tap.assert_not_called()

    def test_daemon_down_and_old_state_cannot_execute(self):
        state=(self.gate.directory/'view.json').read_text()
        self.gate.close()
        (self.gate.directory/'view.json').write_text(state)
        self.report();self.tool.tap.assert_not_called()

    def test_busy_drops_without_queue_and_expired_deferred_worker_drops(self):
        self.client.report(bridge.VOL_UP,1,self.now);self.gate.poll()
        for _ in range(20):
            self.client.report(bridge.VOL_DOWN,7,self.now);self.gate.poll()
        self.assertEqual(len(Deferred.pending),1)
        self.now+=.151;Deferred.finish();self.tool.tap.assert_not_called()
        self.gate.poll();self.tool.tap.assert_not_called()
        self.gate.update(True);self.report();self.tool.tap.assert_called_once_with('left',1)

    def test_view_leave_generation_and_link_loss_cancel_unstarted(self):
        for mode in ('leave','link','lease'):
            self.alive=True;self.gate.update(True)
            self.client.report(bridge.VOL_UP,0,self.now);self.gate.poll()
            if mode=='leave':self.gate.update(False);self.gate.update(True)
            if mode=='link':self.alive=False
            if mode=='lease':self.now+=1
            Deferred.finish()
        self.tool.tap.assert_not_called()

    def test_old_datagrams_and_invalid_batches_rejected(self):
        token=self.gate.token
        with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as channel:
            for message in [f'UK1 {token} {self.now-.151} left 1',
                    f'UK1 {token} {self.now+1} left 1',
                    f'UK1 {token} nan left 1',f'UK1 {token} {self.now} up 10',
                    f'UK1 {token} {self.now} left 32',f'UK1 old {self.now} right 1',
                    'bad payload']:
                channel.sendto(message.encode(),str(self.gate.directory/'keys.sock'))
                self.gate.poll()
        Deferred.finish();self.tool.tap.assert_not_called()

    def test_second_daemon_cannot_remove_first_endpoint(self):
        other=hrusagekeys.UsageKeys(self.keys,lambda:True,directory=self.gate.directory)
        other.start();other.close()
        self.report();self.tool.tap.assert_called_once_with('left',1)

    def test_no_process_when_inactive_constructor_and_strict_wire(self):
        other=hrusagekeys.UsageKeys(self.keys,lambda:True,
                                  directory=Path(self.temp.name)/'unstarted')
        other.update(True);other.poll();other.close()
        self.assertFalse(other.directory.exists())
        self.assertEqual(hrproto.parse_command(b'vw\t1'),('vw',['1']))
        for message in (b'vw',b'vw\t2',b'vw\t1\textra',b'vw\t1\nfoo'):
            self.assertIsNone(hrproto.parse_command(message))

    def test_real_daemon_view_command_and_session_reset(self):
        args=argparse.Namespace(socket=None,state=str(Path(self.temp.name)/'order'))
        daemon=herdremote.Daemon(args)
        self.addCleanup(daemon.remote_log.close)
        daemon.usage_keys=self.gate
        daemon.command(b'vw\t1');self.assertTrue(self.gate.active)
        daemon.command(b'vw\t0');self.assertFalse(self.gate.active)
        daemon.command(b'vw\t1');daemon.reset_session();self.assertFalse(self.gate.active)
