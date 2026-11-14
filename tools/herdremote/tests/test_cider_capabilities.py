"""An old firmware gets only the baseline records it advertised."""
import sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import hrcider
class Capabilities(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.cache=hrcider.Cache(self.tmp.name);self.client=Mock();self.client.request.return_value=[]
  self.bridge=hrcider.Bridge(client=self.client,cache=self.cache,volume=Mock())
  self.bridge.state=dict(duration=1000,position=0,rate=0,volume=20,shuffle=0,repeat=0,id='song',art='-',bg='181818',title='Title',artist='Artist',album='Album',muted=True,composer='Writer')
  self.bridge.available=True
 def tearDown(self):self.bridge.close();self.cache.close();self.tmp.cleanup()
 def frames(self):
  out=[];self.bridge.poll(lambda *f:out.append(f));return out
 def test_legacy_ci_is_not_permission_for_extended_state_or_neighbors(self):
  self.bridge.command(b'ci\t1\t131072');self.assertFalse(self.bridge.capabilities)
  rows=[p for _,p in self.frames() if p.startswith(b'CN\t')];self.assertEqual(len(rows[0].split(b'\t')),16)
  gen=self.bridge.generation
  self.bridge.command(f'cw\t{gen}\tM\t'.encode()+b'a'*64);self.assertFalse(self.bridge.warm_window)
  self.bridge.scheduler.push(1,2,f'CP\t{gen}\tsong\t-\t-'.encode());self.assertFalse(any(p.startswith(b'CP\t') for _,p in self.frames()))
  t=threading.Thread(target=self.bridge._neighbors);t.start();self.bridge.threads.append(t);time.sleep(.03);self.client.request.assert_not_called()
 def test_extended_state_and_neighbors_require_explicit_tokens(self):
  self.bridge.command(b'ci\t1\t131072\tcn18,neighbors,warm,play-ack')
  self.assertIn('neighbors',self.bridge.capabilities)
  self.assertEqual(len(next(p for _,p in self.frames() if p.startswith(b'CN\t')).split(b'\t')),18)
  self.bridge.command(b'ci\t1\t131072');self.assertFalse(self.bridge.capabilities)
 def test_unknown_or_malformed_capabilities_never_enable_neighbors(self):
  self.bridge.command(b'ci\t1\t131072\tfuture');self.assertFalse(self.bridge.capabilities)
  self.bridge.command(b'ci\t1\t131072\tneighbors\twrong');self.assertFalse(self.bridge.capabilities)
