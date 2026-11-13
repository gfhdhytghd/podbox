"""Volume-only EP0 forwarding; no devices or services."""
import unittest
from test_usb_remote_key import k, Recorder

class Volume(unittest.TestCase):
    def test_frame_and_later_repeats_are_steps(self):
        r=Recorder(); f=k.VolumeForwarder(r)
        f.frame(bytes.fromhex('ee870b59'),0)
        for n in range(1,9): f.frame(b'',n*.108)
        self.assertEqual(len(r.events),8)
        self.assertEqual([key for key,state,t in r.events],[k.VOL_UP]*8)
        self.assertEqual([state for key,state,t in r.events],[1]+[2]*7)
    def test_full_frame_is_new_report_even_when_fast(self):
        r=Recorder();f=k.VolumeForwarder(r)
        for t in [0,.08,.16]: f.frame(bytes.fromhex('ee870d59'),t)
        self.assertEqual(len(r.events),3)
        self.assertTrue(all(key==k.VOL_DOWN and state==1 for key,state,t in r.events))
    def test_only_volume_no_play_or_direction_forwarding(self):
        for key in [2,3,4,7,8,0x77]:
            r=Recorder();f=k.VolumeForwarder(r)
            f.frame(bytes([0xee,0x87,key,0x59]),0);f.frame(b'',.108)
            self.assertFalse(r.events)
    def test_new_frame_resets_repeat_skip_and_direction(self):
        r=Recorder();f=k.VolumeForwarder(r)
        for t,frame in [(0,'ee870b59'),(.108,''),(.216,''),(.25,'ee870d59'),(.358,''),(.466,'')]:
            f.frame(bytes.fromhex(frame),t)
        self.assertEqual([(key,state) for key,state,t in r.events],[(k.VOL_UP,1),(k.VOL_UP,2),(k.VOL_DOWN,1),(k.VOL_DOWN,2)])
    def test_stale_orphan_repeat_and_non_apple(self):
        r=Recorder();f=k.VolumeForwarder(r)
        f.frame(b'',0);f.frame(bytes.fromhex('aa870b59'),.1)
        f.frame(bytes.fromhex('ee870b59'),.2);f.frame(b'',.5)
        self.assertEqual(len(r.events),1)
    def test_skip_zero_enables_first_repeat(self):
        from unittest.mock import patch
        r=Recorder();f=k.VolumeForwarder(r)
        with patch.object(k,'VOL_IGNORE_FIRST_REPEATS',0):
            f.frame(bytes.fromhex('ee870b59'),0);f.frame(b'',.108)
        self.assertEqual(len(r.events),2)

class Heartbeat(unittest.TestCase):
    def test_requires_receiver_records_not_just_open_port(self):
        r=Recorder();h=k.ReceiverHeartbeat(r)
        for line in (b'noise',b'STATE x 1',b'EDGE 2 9',b'EDGE -1 0'):
            h.line(line,0)
        self.assertFalse(r.events)
        h.line(b'STATE 1 1',0);h.line(b'EDGE 1000 0',.5)
        h.line(b'STATE 1001 1',1);h.line(b'STATE 2001 1',2)
        self.assertEqual(r.events,[(k.HEARTBEAT,1,0),(k.HEARTBEAT,1,1),(k.HEARTBEAT,1,2)])
    def test_recorded_ir_cadence_has_one_step_per_later_repeat(self):
        from test_gestures import groups
        for group in groups():
            for command,key in [(0x0b,k.VOL_UP),(0x0d,k.VOL_DOWN)]:
                r=Recorder();f=k.VolumeForwarder(r)
                for now,first_us,frame,span_us in group:
                    f.frame(bytes([0xee,0x87,command,0x59]) if frame else b'',now)
                self.assertEqual(len(r.events),1+max(0,len(group)-2))
                self.assertTrue(all(kid==key for kid,state,t in r.events))

if __name__=='__main__':unittest.main()
