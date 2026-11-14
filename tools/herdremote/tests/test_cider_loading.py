"""FU28: deterministic loading inference and negotiated CN records."""
import tempfile
from unittest.mock import Mock
import pytest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import hrcider


def state(track='a', pos=0, rate=1000):
    return dict(id=track, position=pos, rate=rate, duration=100000, volume=20,
                shuffle=0, repeat=0, art='-', bg='181818', title='T', artist='A', album='B')


def test_track_change_needs_new_id_and_advance():
    x=hrcider.Loading();x.sample(state(),0);x.begin('next',0,state(),.1)
    assert x.sample(state('a',500),.5)
    assert x.sample(state('b',0),.75)
    assert x.sample(state('b',0),1)
    assert not x.sample(state('b',250),1.25)


def test_stall_pause_stop_resume_and_track_reset():
    x=hrcider.Loading()
    for t in (0,.25,.5,.75):assert not x.sample(state(pos=100),t)
    assert x.sample(state(pos=100),1)
    assert not x.sample(state(pos=101),1.25)
    assert not x.sample(state(pos=101,rate=0),3)
    assert not x.sample(state(pos=101),3.25)
    assert not x.sample(state('b',0),3.5)
    assert not x.sample(state('b',0,0),5)


@pytest.mark.parametrize('command',['play-item','native-play','next','previous','play-collection','queue-jump'])
def test_timeout_and_pending_commands(command):
    x=hrcider.Loading();x.sample(state(),0);assert x.begin(command,'songs:x',state(),.1)
    assert x.sample(state(pos=500),8.099)
    assert not x.sample(state(pos=750),8.1)


def test_seek_waits_for_new_point_and_then_advance():
    x=hrcider.Loading();x.sample(state(pos=1000),0);x.begin('seek','50000',state(pos=1000),.1)
    assert x.sample(state(pos=1500),.25)
    assert x.sample(state(pos=50000),.5)
    assert x.sample(state(pos=50000),.75)
    assert not x.sample(state(pos=50250),1)
    x.begin('seek',1000,state(pos=50250),1.1)
    assert x.sample(state(pos=50500),1.25)
    assert x.sample(state(pos=1000),1.5)
    assert not x.sample(state(pos=1250),1.75)


def test_ordinary_controls_and_failed_command():
    x=hrcider.Loading();assert not x.begin('shuffle',1,state(),0)
    x.begin('next',0,state(),0);x.cancel();assert not x.sample(state(pos=250),.25)


@pytest.mark.parametrize('caps,count', [('',16),('cn18',18),('cn18,modes5',19),('loading',20),('cn18,loading',20),('cn18,modes5,loading',20)])
def test_capabilities(caps,count):
    with tempfile.TemporaryDirectory() as d:
        b=hrcider.Bridge(client=Mock(),cache=hrcider.Cache(d),volume=Mock())
        b.state=state();b.available=True;b.loading.value=True
        b.command(('ci\t1\t131072'+ ('\t'+caps if caps else '')).encode())
        rows=[];b.poll(lambda _,p:rows.append(p))
        cn=next(p for p in rows if p.startswith(b'CN\t')).split(b'\t')
        assert len(cn)==count
        if 'loading' in caps:assert cn[-1]==b'1'
        b.close()


def test_transition_forces_cn_inside_throttle():
    with tempfile.TemporaryDirectory() as d:
        b=hrcider.Bridge(client=Mock(),cache=hrcider.Cache(d),volume=Mock())
        b.state=state();b.available=True;b.command(b'ci\t1\t131072\tcn18,modes5,loading')
        b.last_state=hrcider.time.monotonic();seq=b.state_seq
        with b.lock:b._loading_begin('next',0)
        assert b.loading.value and b.state_seq==seq+1
        with b.lock:
            b._loading_cancel()
        assert not b.loading.value and b.state_seq==seq+2
        b.close()


def test_mock_poll_transitions_and_quarter_second_schedule(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        client=Mock();client.ab_tick.return_value=None
        b=hrcider.Bridge(client=client,cache=hrcider.Cache(d),volume=Mock())
        b.volume.read.return_value=(20,False)
        clock=[0.0];polls=[]
        monkeypatch.setattr(hrcider.time,'monotonic',lambda:clock[0])
        snapshots=[state(pos=100)]*5+[state(pos=350),state(pos=350,rate=0)]
        for s in snapshots:s['template']=''
        def snapshot():
            polls.append(clock[0]);v=snapshots[len(polls)-1]
            if len(polls)==len(snapshots):b.stop.set()
            return dict(v)
        client.snapshot.side_effect=snapshot
        b.stop.wait=lambda seconds:clock.__setitem__(0,round(clock[0]+seconds,6))
        b.command(b'ci\t1\t131072\tloading')
        published=[]
        original=b._state
        def publish(force=False):
            published.append((clock[0],b.loading.value,force));original(force)
        b._state=publish;b._poll()
        assert polls==[0,.25,.5,.75,1,1.25,1.5]
        assert (1,True,True) in published and (1.25,False,True) in published
        b.close()


@pytest.mark.parametrize('command',['next','previous','play-item','native-play','seek'])
def test_control_publishes_before_rpc_and_failure_restores(command):
    with tempfile.TemporaryDirectory() as d:
        client=Mock();b=hrcider.Bridge(client=client,cache=hrcider.Cache(d),volume=Mock())
        b.state=state();b.available=True;b.command(b'ci\t1\t131072\tloading')
        value='50000' if command=='seek' else 'songs:x'
        b.controls.put((b.generation,1,command,value))
        def called(*args):
            assert b.loading.value
            b.stop.set()
            raise OSError('mock failed')
        client.control.side_effect=called;b.library.play=called;b.library.set_context=called
        b._control();assert not b.loading.value
        b.close()


def test_unrelated_failed_command_keeps_pending_track_change():
    with tempfile.TemporaryDirectory() as d:
        client=Mock();b=hrcider.Bridge(client=client,cache=hrcider.Cache(d),volume=Mock())
        b.state=state();b.available=True;b.command(b'ci\t1\t131072\tloading')
        b._loading_begin('next',0)
        b.controls.put((b.generation,1,'shuffle','1'))
        def failed(*args):b.stop.set();raise OSError('mock failed')
        client.control.side_effect=failed
        b._control();assert b.loading.value and b.loading.pending
        b.close()


def test_poll_started_before_command_cannot_arm_its_new_track(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        client=Mock();client.ab_tick.return_value=None
        b=hrcider.Bridge(client=client,cache=hrcider.Cache(d),volume=Mock())
        b.state=state();b.available=True;b.volume.read.return_value=(20,False)
        monkeypatch.setattr(hrcider.time,'monotonic',lambda:1.0)
        def snapshot():
            b._loading_begin('next',0);b.stop.set()
            return dict(state('b',100),template='')
        client.snapshot.side_effect=snapshot;b._poll()
        assert b.loading.value and b.loading.pending['point'] is None
        assert b.loading.sample(state('b',350),1.25)
        assert not b.loading.sample(state('b',600),1.5)
        b.close()
