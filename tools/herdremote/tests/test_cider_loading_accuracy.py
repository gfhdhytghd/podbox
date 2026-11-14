"""FU28b loading follows the final selection and state-only audio edges."""
import sys,tempfile,threading
from pathlib import Path
from unittest.mock import Mock
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import hrcider
from hrcider_events import AudioStream

def state(track='a',pos=1000,rate=1000):
 return dict(id=track,position=pos,rate=rate,duration=100000,volume=20,shuffle=0,repeat=0,art='-',bg='181818',title='',artist='',album='',template='')

def test_context_alone_never_starts_loading():
 x=hrcider.Loading();assert not x.begin('native-context','2|album/x',state(),0);assert not x.value

def test_stream_start_clears_before_one_second_clock_tick():
 x=hrcider.Loading();x.stream(False,-1);x.sample(state(),0)
 x.begin('native-play','play/library-songs/b',state(),.01);x.expected({'b'})
 x.sample(state('b',0),.06);x.rpc_returned(.07)
 assert x.value
 x.stream(True,.10);assert not x.value

def test_first_track_blip_does_not_clear_selected_jump():
 x=hrcider.Loading();x.stream(False,-1);x.begin('native-play','play/library-songs/target',state(),0);x.expected({'target'})
 x.sample(state('first',0),.05);x.stream(True,.06);x.sample(state('first',100),.1);assert x.value
 x.final_leg(.15);x.rpc_returned(.16);x.sample(state('target',0),.18);assert x.value
 x.stream(False,.19);x.stream(True,.22);assert not x.value

def test_same_id_restart_and_advance_can_finish_pending():
 x=hrcider.Loading();x.sample(state(pos=50000),0);x.begin('next',0,state(pos=50000),.1);x.rpc_returned(.11)
 assert x.sample(state(pos=0),.15)
 assert not x.sample(state(pos=250),.4)

def test_seek_does_not_clear_from_pre_seek_clock():
 x=hrcider.Loading();x.stream(True,-1);x.sample(state(pos=1000),0)
 x.begin('seek',50000,state(pos=1000),.01);x.rpc_returned(.03)
 assert x.sample(state(pos=1250),.04)
 x.sample(state(pos=50000),.07);x.stream(False,.08);x.stream(True,.10);assert not x.value

def test_one_hz_clock_with_running_stream_does_not_flicker_stall():
 x=hrcider.Loading();x.stream(True,0)
 for n in range(100):assert not x.sample(state(pos=(n//20)*1000),n*.05)

def test_metadata_stream_filter_never_uses_an_audio_source():
 rows=[{'index':1,'corked':False,'properties':{'application.process.binary':'Firefox'}},
       {'index':2,'corked':True,'properties':{'application.process.binary':'Cider'}}]
 import json
 run=Mock(return_value=Mock(stdout=json.dumps(rows)))
 s=AudioStream(run);assert not s.read()['running'];rows[1]['corked']=False
 run.return_value.stdout=json.dumps(rows);assert s.read()['running']
 assert all(c.args[0]==['pactl','-f','json','list','sink-inputs'] for c in run.call_args_list)

@pytest.mark.parametrize('playing',[True,False])
def test_host_same_song_guard_never_requeues_or_loads(playing):
 with tempfile.TemporaryDirectory() as d:
  c=Mock();c.same_song.return_value=True
  b=hrcider.Bridge(client=c,cache=hrcider.Cache(d),volume=Mock());b.available=True;b.state=state(rate=1000 if playing else 0)
  b.command(b'ci\t1\t131072\tloading');b.library.play=Mock(side_effect=lambda _:b.stop.set() or {})
  b.controls.put((b.generation,1,'native-play','play/library-songs/a'))
  def guard(*args):b.stop.set();return True
  c.same_song.side_effect=guard;b._control()
  assert not b.loading.value;b.library.play.assert_not_called();c.same_song.assert_called_once()
  b.close()


def test_new_metadata_cannot_finish_on_old_audio_clock():
 x=hrcider.Loading();x.stream(True,0);x.sample(state('old',67000),0)
 x.begin('native-play','play/library-songs/new',state('old',67000),.01)
 assert x.sample(state('new',67000),.05)
 assert x.sample(state('new',68000),1.05)
 assert x.sample(state('new',0),1.10)
 assert not x.sample(state('new',250),1.35)

@pytest.mark.parametrize('playing',[True,False])
def test_real_client_same_song_aliases_resume_only(playing):
 c=hrcider.Client();c.request=Mock(return_value={'data':{'state':'playing' if playing else 'paused',
  'nowPlaying':{'durationInMillis':100000,'playParams':{'id':'i.same','catalogId':'123'}},'time':{'currentTime':12}}})
 assert c.same_song('play/songs/123')
 writes=[v.args[0] for v in c.request.call_args_list if len(v.args)>1]
 assert writes==([] if playing else ['/api/v1/playback/play'])
 assert c.request.call_args_list[0].args[0]=='/api/v2/playback'


def test_stream_edge_pushes_clear_cn_without_waiting_for_poll():
 with tempfile.TemporaryDirectory() as d:
  c=Mock();b=hrcider.Bridge(client=c,cache=hrcider.Cache(d),volume=Mock());b.state=state();b.available=True
  b.command(b'ci\t1\t131072\tloading');b.loading.stream(False,hrcider.time.monotonic())
  b._loading_begin('native-play','play/library-songs/b');before=b.state_seq
  c.transport_snapshot.return_value=dict(state('b',0),ids={'b'})
  b._on_stream({'running':True},hrcider.time.monotonic())
  assert not b.loading.value and b.state_seq==before+1
  frames=[];b.poll(lambda _,p:frames.append(p));cn=next(p for p in frames if p.startswith(b'CN\t'));assert cn.split(b'\t')[-1]==b'0'
  b.close()


def test_malformed_transport_state_is_a_value_error():
 c=hrcider.Client();c.request=Mock(return_value={'data':{'nowPlaying':'broken'}})
 with pytest.raises(ValueError,match='Malformed Cider transport'):c.transport_snapshot()


def test_collection_first_track_restarting_old_id_cannot_finish_jump():
 x=hrcider.Loading();x.stream(False,-1);x.sample(state('first',50000),0)
 x.begin('native-play','play/library-songs/target',state('first',50000),.01)
 assert x.sample(state('first',0),.05)
 x.stream(True,.06);assert x.value
 assert x.sample(state('first',250),.3)
 x.final_leg(.4);x.sample(state('target',0),.45);assert x.value
 x.stream(False,.46);x.stream(True,.5);assert not x.value


def test_old_event_snapshot_cannot_arm_a_new_command():
 with tempfile.TemporaryDirectory() as d:
  c=Mock();b=hrcider.Bridge(client=c,cache=hrcider.Cache(d),volume=Mock());b.state=state();b.available=True
  def snapshot():
   b._loading_begin('native-play','play/library-songs/b')
   return dict(state('b',0),ids={'b'})
  c.transport_snapshot.side_effect=snapshot;b._fast_sample()
  assert b.loading.pending['point'] is None and not b.loading.pending['selected']
  assert b.state['id']=='a'
  b.close()
