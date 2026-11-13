"""Synthetic app-server deltas, no daemon or private session access."""
import types
import unittest
from unittest import mock
from test_resident import Socket, META
import herdremote
import hrappserver

class DetailStream(unittest.TestCase):
    def setUp(self):
        self.d=herdremote.Daemon(types.SimpleNamespace(socket='/nonexistent',state=None))
        self.d.detail_pane='p1';self.sent=[];self.d.send=lambda p:self.sent.append((self.now,p))
        self.now=100.0
    def tearDown(self):self.d.usage.close()
    def test_stream_coalesced_and_completion_only_silent(self):
        socket=Socket();reader=hrappserver.Reader(connect=lambda *a,**k:socket)
        with mock.patch.object(herdremote.time,'monotonic',side_effect=lambda:self.now):
            for i in range(31):
                self.now=100+i/10
                socket.items=[dict(item=dict(type='agentMessage',id='m1',text='word '*70+'delta '*i)),dict(item=dict(type='commandExecution',command='test',status='inProgress' if i<15 else 'completed'))]
                self.d.send_detail(herdremote.hrproto.detail('p1',reader.read(META)))
            self.assertLessEqual(len(self.sent),4)
            self.assertTrue(all(b[0]-a[0]>=1 for a,b in zip(self.sent,self.sent[1:])))
            self.now=104;socket.items[0]['item']['completedAtMs']=4
            self.d.send_detail(herdremote.hrproto.detail('p1',reader.read(META)))
            self.assertEqual(len(self.sent),4)
    def test_revision_only_silent_input_still_fast(self):
        with mock.patch.object(herdremote.time,'monotonic',side_effect=lambda:self.now):
            for rev in ('1','2'):
                self.d.send_detail(herdremote.hrproto.detail('p1',dict(say='same',revision=rev)))
            self.assertEqual(len(self.sent),1)
            self.now+=.075
            self.d.send_detail(herdremote.hrproto.detail('p1',dict(say='same',input='x',cursor=1,revision='3')))
            self.assertEqual(len(self.sent),2)

    def test_latest_pending_flush_and_background_drop(self):
        d=self.d
        with mock.patch.object(herdremote.time,'monotonic',side_effect=lambda:self.now):
            d.send_detail(herdremote.hrproto.detail('p1',dict(say='first',revision='1')))
            self.now+=.1
            d.send_detail(herdremote.hrproto.detail('p1',dict(say='second',revision='2')))
            self.now+=.1
            d.send_detail(herdremote.hrproto.detail('p1',dict(say='latest',revision='3')))
            self.assertEqual(len(self.sent),1)
            self.now=101;d.flush_detail()
            self.assertEqual(self.sent[-1][1].split(b'\t')[5],b'latest')
            self.now+=.1;d.send_detail(herdremote.hrproto.detail('p1',dict(say='hidden',revision='4')))
            d.link=types.SimpleNamespace(foreground=0);d.adopt_foreground()
            self.now=103;d.flush_detail();self.assertEqual(len(self.sent),2)

    def test_D_metadata_logs_no_private_text(self):
        with mock.patch.object(herdremote.time,'monotonic',side_effect=lambda:self.now):
            with self.assertLogs(herdremote.log,level='DEBUG') as logs:
                for i in range(4):
                    self.now=100+i*.075
                    self.d.send_detail(herdremote.hrproto.detail('p1',dict(say='private text',input='x'*i,cursor=i,revision=str(i))))
            debug=[line for line in logs.output if line.startswith('DEBUG')]
            info=[line for line in logs.output if line.startswith('INFO')]
            self.assertEqual(len(debug),4);self.assertEqual(len(info),1)
            for line in debug:
                self.assertIn('pane=p1 revision=',line);self.assertIn('bytes=',line);self.assertIn('changed=',line)
                self.assertNotIn('private text',line)
