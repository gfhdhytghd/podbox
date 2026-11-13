"""Resident state, bounded read-only detail sources and no viewport scrolling."""
from collections import deque
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock
from test_herdremote import herdremote, hrproto, podlink
import hrappserver
import hrsession

SID = '00000000-1111-2222-3333-444444444444'
META = dict(kind='codex', status='working', agent_session=dict(kind='id', value=SID), state_age_s=1, resident=True)

class Socket:
    def __init__(self):
        self.calls, self.replies = [], deque()
        self.items = [dict(item=dict(type='agentMessage', id='m1', text='a\nb\nc\nd\ne\nf', completedAtMs=1))]
    def send(self, raw):
        request=json.loads(raw);self.calls.append(request)
        if 'id' in request:
            self.replies.append(json.dumps(dict(id=request['id'], result={} if request['method']=='initialize' else dict(data=self.items))))
    def recv(self, timeout):return self.replies.popleft()
    def close(self):pass

class Sources(unittest.TestCase):
    def test_codex_read_only_latest_five_and_timer_stability(self):
        socket=Socket();reader=hrappserver.Reader(connect=lambda *a,**kw:socket)
        first=reader.read(META)
        self.assertEqual(first['say'],'b\nc\nd\ne\nf')
        for value in range(30):
            socket.items.append(dict(item=dict(type='reasoning',id='r'+str(value),summary=['timer '+str(value)])))
            socket.items=socket.items[:5]
            self.assertEqual(reader.read(META)['revision'], first['revision'])
        socket.items[0]['item']['text']='new message'
        self.assertNotEqual(reader.read(META)['revision'],first['revision'])
        for call in socket.calls:
            self.assertIn(call['method'],('initialize','initialized','thread/items/list'))
            if call['method']=='thread/items/list':
                self.assertEqual(call['params'],dict(threadId=SID,limit=5,sortDirection='desc'))
        self.assertFalse(any('resume' in c['method'] or c['method'].startswith('turn/') for c in socket.calls))

    def test_reasoning_only_window_keeps_the_last_message_without_revision_churn(self):
        socket=Socket();reader=hrappserver.Reader(connect=lambda *a,**kw:socket)
        first=reader.read(META)
        socket.items=[dict(item=dict(type='reasoning',id='r'+str(i),summary=['timer'])) for i in range(5)]
        again=reader.read(META)
        self.assertEqual(again['say'],first['say']);self.assertEqual(again['revision'],first['revision'])

    def test_codex_never_reads_rollouts_and_refuses_unkeyed_sessions(self):
        reader=hrappserver.Reader(connect=lambda *a,**kw:self.fail('must not connect'))
        self.assertIsNone(reader.read(dict(kind='codex',agent_session=dict(kind='id',value='../bad'))))
        self.assertIsNone(hrsession.Sessions().read(META))

    def test_claude_live_append_partial_record_and_tool_completion(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'.claude/projects/project'/f'{SID}.jsonl';path.parent.mkdir(parents=True)
            path.write_text('')
            reader=hrsession.Sessions(root);meta=dict(META,kind='claude')
            initial=reader.read(meta)['revision']
            with path.open('a') as out:out.write(json.dumps(dict(type='assistant',uuid='a1',message=dict(role='assistant',content=[dict(type='text',text='Hello')]))))
            self.assertEqual(reader.read(meta)['revision'],initial)
            with path.open('a') as out:out.write('\n')
            first=reader.read(meta);self.assertIn('Hello',first['say'])
            with path.open('a') as out:out.write(json.dumps(dict(type='assistant',uuid='a2',message=dict(role='assistant',content=[dict(type='tool_use',id='t1',name='Read')])) )+'\n')
            tool=reader.read(meta);self.assertNotEqual(tool['revision'],first['revision'])
            with path.open('a') as out:out.write(json.dumps(dict(type='user',uuid='u1',message=dict(role='user',content=[dict(type='tool_result',tool_use_id='t1',content='not displayed')])) )+'\n')
            done=reader.read(meta);self.assertNotEqual(done['revision'],tool['revision']);self.assertEqual(done['steps'],['Read done'])
            self.assertEqual(reader.read(meta)['revision'],done['revision'])

class Foreground(unittest.TestCase):
    def setUp(self):
        self.daemon=herdremote.Daemon(types.SimpleNamespace(socket='/nonexistent/fake.sock',state=None))
        self.daemon.command(b'hr\t1');self.messages=[];self.daemon.send=self.messages.append
        self.daemon.model_time=1;self.daemon.model['workspaces']=[]
        self.daemon.link=types.SimpleNamespace(foreground=1)
    def tearDown(self):self.daemon.usage.close()
    def test_background_keeps_list_deltas_no_detail_and_resume_no_full_refresh(self):
        d=self.daemon;d.push();self.messages.clear();d.detail_pane='p1'
        d.sent_detail=b'cached';d.sent_detail_key=('cached',);generation=d.session_generation
        detail_epoch=d.detail_edit_revision
        d.link.foreground=0
        with mock.patch.object(d.voice,'stop') as stop:
            d.adopt_foreground();stop.assert_called_once()
        self.assertGreater(d.detail_edit_revision,detail_epoch)
        with mock.patch.object(d,'read_detail',side_effect=AssertionError('background detail read')):
            d.push()
        self.assertFalse(any(p.startswith(b'D\t') for p in self.messages))
        self.messages.clear();d.link.foreground=1;d.adopt_foreground()
        self.assertEqual(d.session_generation,generation);self.assertEqual(d.sent_detail,b'cached');self.assertEqual(d.sent_ws,b'W\t0')
        with mock.patch.object(d,'read_detail',return_value=hrproto.detail('p1',dict(say='updated'))):d.push()
        self.assertFalse(any(p.startswith((b'W\t',b'S\t')) for p in self.messages))
    def test_real_message_changes_equal_structured_payload_count(self):
        d=self.daemon;socket=Socket();d.appserver=hrappserver.Reader(connect=lambda *a,**kw:socket)
        d.detail_pane='p1';d.foreground=1;d.model['panes']={'p1'}
        d.model['pane_meta']={'p1':dict(META,title='')}
        def sample(age):
            meta=dict(META,state_age_s=age)
            d.send_detail(d.read_detail('p1',True,meta))
        with mock.patch.object(herdremote.time,'monotonic',return_value=100) as clock:
            sample(0)
            for tick in range(1,40):clock.return_value=100+tick;sample(tick)
            self.assertEqual(len(self.messages),1)
            socket.items[0]['item']['text']='appended message';sample(40)
            socket.items[0]['item']['completedAtMs']=2;sample(41)
            self.assertEqual(len(self.messages),3)

    def test_scroll_request_is_refused_before_socket_io(self):
        with mock.patch.object(self.daemon.herdr, 'call') as call:
            with self.assertRaises(herdremote.hrherdr.HerdrError):
                self.daemon.herdr.read_record('p1', source='recent_unwrapped', lines=240)
            call.assert_not_called()
    def test_fallback_visible_only_no_line_request(self):
        calls=[]
        client=types.SimpleNamespace(read_record=lambda *a,**kw:(calls.append((a,kw)) or dict(text='• latest message\n› typed')))
        self.daemon.read_detail('p1',True,dict(kind='unknown'),client)
        self.assertEqual(calls,[(('p1',),dict(source='visible'))])
    def test_structured_primary_suppresses_all_terminal_reads(self):
        with mock.patch.object(self.daemon.appserver,'read',return_value=dict(say='latest',revision='1')):
            client=types.SimpleNamespace(read_record=lambda *a,**kw:self.fail('terminal read'))
            payload=self.daemon.read_detail('p1',True,META,client)
        self.assertEqual(len(hrproto.decode(payload)[0]),12)
    def test_ping_notice_and_legacy_ping_are_compatible(self):
        class Port:
            def read(self,n):return self.data.pop(0) if self.data else b''
            def write(self,b):return len(b)
        port=Port();port.data=[podlink.encode(podlink.PING,7,b'PLFG\x00')]
        link=podlink.Link(port);link.poll();self.assertEqual(link.foreground,0);self.assertFalse(link.frames)
        port.data=[podlink.encode(podlink.PING,8,b'old ping')];link.poll();self.assertEqual(link.foreground,0)

if __name__=='__main__':unittest.main()
