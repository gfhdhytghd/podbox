"""FU21 real captured dialogs and mocked focused custom input; no live writes."""
from pathlib import Path
import queue
import sys
import threading
import types
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hrdetect
import hrdetail
import hrproto
import herdremote

SAMPLES = Path(__file__).parent/'samples'
def sample(name): return (SAMPLES/name).read_text()
def question(name):
    q=hrdetect.find_question(sample(name))
    return dict(q,id=hrdetect.question_id('p1',q),pane='p1',workspace='w1',agent='project · codex',tab='Task')

class Questions(unittest.TestCase):
    def test_real_custom_options_and_legacy_wire(self):
        for name,number in [('claude-ask.txt',4),('claude-ask-multi-q2.txt',4),('codex-approval.txt',3)]:
            with self.subTest(name=name):
                q=question(name)
                self.assertIn(number,q['custom'])
                old=hrproto.decode(hrproto.question(q))
                new=hrproto.decode(hrproto.question(q,extended=True))
                self.assertEqual(len(old[0]),6)
                self.assertEqual(len(new[0]),9)
                self.assertTrue(all(len(r)==3 for r in old[1:] if r[0]=='o'))
                self.assertIn(['o',str(number),next(label for n,label in q['all_options'] if n==number),'c'],new)
    def test_binary_approvals_and_plain_questions(self):
        for name,yes,no in [('claude-perm-bash.txt',1,4),('claude-perm-write.txt',1,3),('codex-approval.txt',1,-1)]:
            q=question(name)
            self.assertEqual((q['yes'],q['no']),(yes,no))
            self.assertEqual(hrdetect.answer_keys(q,no),['esc'] if no==-1 else [str(no)])
        q=hrdetect.find_question('Continue?\n❯ 1. Yes\n  2. No\nEnter to select · Esc to cancel')
        self.assertEqual((q['yes'],q['no']),(1,2))
        for labels in [('Red','Blue'),('Yes','Other…')]:
            q=hrdetect.find_question('Colour?\n❯ 1. '+labels[0]+'\n  2. '+labels[1]+'\nEnter to select · Esc to cancel')
            self.assertEqual((q['yes'],q['no']),(0,0))
    def test_checkbox_identity_and_text_replacement(self):
        text=sample('claude-ask-multi-q2.txt')
        a=hrdetect.find_question(text);b=hrdetect.find_question(text.replace('[ ] Cheese','[x] Cheese'))
        self.assertEqual(hrdetect.question_id('p',a),hrdetect.question_id('p',b))
        c=hrdetect.find_question(text.replace('Cheese topping','New meaning'))
        self.assertNotEqual(hrdetect.question_id('p',a),hrdetect.question_id('p',c))
    def test_capability_command_validation(self):
        self.assertEqual(hrproto.parse_command(b'qx\t1'),('qx',['1']))
        self.assertIsNone(hrproto.parse_command(b'qx\t2'))
        self.assertIsNone(hrproto.parse_command(b'qi\tr\tq\tanything'))
        self.assertEqual(hrproto.parse_command(b'qi\tr\tq\tesc'),('qi',['r','q','esc']))
    def test_inline_editor_cursor_is_echoed_without_choice_label(self):
        for kind, marker in [('claude','❯'),('codex','›')]:
            text=marker+' 4. Type something: reviewed answer'
            result=hrdetail.extract_custom_input(text,kind,dict(visible=True,row=0,col=len(text)))
            self.assertTrue(result['active'])
            self.assertEqual(result['input'],'reviewed answer')
            self.assertEqual(result['cursor'],len('reviewed answer'))
        self.assertFalse(hrdetail.extract_custom_input('❯ 4. Type something.')['active'])

    def test_custom_answer_does_not_submit_text(self):
        q=question('claude-ask.txt')
        client=mock.Mock()
        client.read.side_effect=[sample('claude-ask.txt'),'❯ \nEsc to cancel']
        client.read_record.return_value=dict(text='❯ \nEsc to cancel')
        a=herdremote.Answerer(client,queue.Queue(),threading.Event())
        with mock.patch.object(herdremote.time,'sleep'):
            self.assertEqual(a.answer(q,4),(True,'Input ready'))
        client.send_keys.assert_called_once_with('p1',['4'])
    def test_custom_checkbox_tick_alone_is_not_input_ready(self):
        text=sample('claude-ask-multi-q2.txt');q=question('claude-ask-multi-q2.txt')
        client=mock.Mock();client.read.side_effect=[text,text.replace('[ ] Cheese','[x] Cheese')]
        client.read_record.return_value=dict(text=text)
        a=herdremote.Answerer(client,queue.Queue(),threading.Event())
        with mock.patch.object(herdremote.time,'sleep'), mock.patch.object(herdremote.time,'monotonic',side_effect=[0,0,999]):
            self.assertEqual(a.answer(q,4),(False,'The pane did not change'))
        client.send_keys.assert_called_once_with('p1',['4'])

    def test_multiselect_tick_is_confirmed_without_new_id(self):
        text=sample('claude-ask-multi-q2.txt');q=question('claude-ask-multi-q2.txt')
        client=mock.Mock();client.read.side_effect=[text,text.replace('[ ] Cheese','[x] Cheese')]
        a=herdremote.Answerer(client,queue.Queue(),threading.Event())
        with mock.patch.object(herdremote.time,'sleep'):
            self.assertEqual(a.answer(q,1),(True,'Updated'))

class CustomFlow(unittest.TestCase):
    def setUp(self):
        self.d=herdremote.Daemon(types.SimpleNamespace(socket='/not-a-real-socket',state=None))
        self.d.send=mock.Mock();self.d.herdr=mock.Mock();self.d.voice=mock.Mock();self.d.keys=mock.Mock()
        self.d.command(b'hr\t1');self.d.command(b'qx\t1')
        self.d.voice.press.return_value=self.d.voice.release.return_value=(True,"Voice")
        self.d.detail_pane=self.d.detail_keys_pane='p1'
        self.d.custom_input=('qid','p1')
        self.d.answerer.current=mock.Mock(return_value=(None,None))
    def tearDown(self): self.d.usage.close()
    def test_focus_echo_voice_review_then_explicit_submit(self):
        d=self.d
        d.appserver.read=mock.Mock(return_value=dict(say='structured message',input='stale',cursor=5))
        d.herdr.read_record.return_value=dict(text='› reviewed answer\n',cursor=dict(visible=True,row=0,col=8))
        payload=d.read_detail('p1',True,dict(kind='codex'),client=d.herdr)
        fields=hrproto.decode(payload)[0]
        self.assertEqual(fields[5],'structured message')
        self.assertEqual(fields[9],'reviewed answer')
        d.herdr.read_record.assert_called_once_with('p1',source='visible')
        d.voice_command('vd','voice','p1');d.voice_command('vu','voice')
        d.voice.press.assert_called_once_with('p1');d.voice.release.assert_called_once_with()
        d.herdr.send_keys.assert_not_called()
        d.command(b'qi\tr\tqid\tenter')
        d.herdr.send_keys.assert_called_once_with('p1',['enter'])
        self.assertIsNone(d.custom_input)
    def test_cancel_sends_only_escape_for_each_kind(self):
        for kind in ('claude','codex'):
            self.d.custom_input=('qid','p1');self.d.herdr.send_keys.reset_mock()
            self.d.command(b'qi\tr\tqid\tesc')
            self.d.herdr.send_keys.assert_called_once_with('p1',['esc'])
    def test_stale_input_or_wrong_focus_never_sends(self):
        for qid in ('other','qid'):
            if qid=='qid':self.d.detail_keys_pane='other'
            self.d.command(('qi\tr\t'+qid+'\tenter').encode())
        self.d.herdr.send_keys.assert_not_called()
    def test_replacement_question_never_gets_cancel(self):
        self.d.answerer.current.return_value=({},'new')
        # Non-empty parsed replacement dict is required.
        self.d.answerer.current.return_value=({'lines':['new']},'new')
        self.d.command(b'qi\tr\tqid\tesc')
        self.d.herdr.send_keys.assert_not_called()
    def test_focus_command_arms_only_matching_detail(self):
        d=self.d;d.detail_keys_pane=None
        d.command(b'det\tp1');d.command(b'fp\tf1\tp1')
        d.herdr.focus_pane.assert_called_once_with('p1')
        self.assertEqual(d.detail_keys_pane,'p1')
        self.assertIn(b'R\tf1\tok',d.send.call_args[0][0])
    def test_codex_direct_no_esc_without_custom_session(self):
        d=self.d;q=question('codex-approval.txt');d.model['questions']={q['id']:q}
        d.command(('a\tr\t'+q['id']+'\t-1').encode())
        req,queued,n=d.answerer.jobs.get_nowait()
        self.assertEqual(n,-1);self.assertFalse(d.custom_pending)
        client=mock.Mock();client.read.side_effect=[sample('codex-approval.txt'),'› ']
        a=herdremote.Answerer(client,queue.Queue(),threading.Event())
        with mock.patch.object(herdremote.time,'sleep'):
            self.assertEqual(a.answer(queued,n),(True,'Answered'))
        client.send_keys.assert_called_once_with('p1',['esc'])
    def test_replaced_pending_editor_gets_error_reply(self):
        d=self.d;d.custom_input=None
        d.custom_pending['r']=('old','p1',d.session_generation)
        d.model['questions']={'new':dict(id='new',pane='p1')}
        d.results.put(('r',True,'Input ready'));d.keys.replies.return_value=[]
        d.flush_replies();self.assertIsNone(d.custom_input)
        self.assertIn(b'R\tr\terr',d.send.call_args[0][0])

    def test_negotiation_and_custom_pending_lifetime(self):
        d=self.d;q=question('claude-ask.txt');d.model['questions']={q['id']:q}
        d.command(('a\tr\t'+q['id']+'\t4').encode())
        self.assertIn('r',d.custom_pending)
        d.custom_input=None;d.results.put(('r',True,'Input ready'));d.keys.replies.return_value=[]
        d.flush_replies();self.assertEqual(d.custom_input,(q['id'],'p1'))
        d.reset_session();self.assertIsNone(d.custom_input);self.assertFalse(d.question_ext)

if __name__=='__main__': unittest.main()
