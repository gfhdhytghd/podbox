"""Menu is a held button, never a classified Select pulse."""
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import usb_remote_key as k

class MenuButton(unittest.TestCase):
    def test_down_immediate_hold_refresh_release(self):
        events=[]
        class Port:
            def send(self,down,now,key=k.MENU):events.append((key,down,now));return True
        f=k.MenuForwarder(Port())
        f.frame(bytes.fromhex('ee870259'),0)
        self.assertEqual(events,[(k.MENU,True,0)])
        for t in (.108,.216,.324,.432,1.0):
            # Start a fresh stream after the large gap, still never SELECT.
            f.frame(b'',t)
        f.poll(1.151)
        self.assertTrue(all(e[0]==k.MENU for e in events))
        self.assertFalse(events[-1][1])
    def test_silence_shutdown_foreign_release(self):
        for how in ('silence','shutdown','foreign'):
            events=[]
            class Port:
                def send(self,down,now,key=k.MENU):events.append((key,down,now));return True
            f=k.MenuForwarder(Port());f.frame(bytes.fromhex('ee870259'),2)
            if how=='silence':f.poll(2.151)
            elif how=='shutdown':f.release(2.05)
            else:f.frame(bytes.fromhex('ee870459'),2.05)
            self.assertEqual([(key,down) for key,down,t in events],[(k.MENU,True),(k.MENU,False)])
