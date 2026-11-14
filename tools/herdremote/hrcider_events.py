"""Cider Socket.IO and Pulse stream metadata. Never opens an audio source."""
import json
import logging
import os
import select
import shutil
import subprocess
import time

log=logging.getLogger('herdremote.cider')


class AudioStream:
    def __init__(self,run=subprocess.run):
        self.run=run;self.last=None
    def read(self):
        result=self.run(['pactl','-f','json','list','sink-inputs'],capture_output=True,
                        text=True,timeout=.6,check=True)
        rows=json.loads(result.stdout)
        if not isinstance(rows,list):raise ValueError('Invalid Pulse stream metadata')
        streams=[]
        for row in rows:
            props=row.get('properties',{})
            binary=props.get('application.process.binary','').lower()
            name=props.get('application.name','').lower()
            if binary.rsplit('/',1)[-1] not in ('cider','cider2') and 'cider' not in name:continue
            streams.append((row['index'],bool(row.get('corked',True))))
        return dict(running=any(not corked for _,corked in streams),streams=streams)
    def watch(self,stop,callback):
        if not shutil.which('pactl'):return
        # Subscribe to state changes. No PCM monitor, recording or audio bytes.
        proc=subprocess.Popen(['pactl','subscribe'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        try:
            while not stop.is_set():
                try:
                    state=self.read();stamp=time.monotonic()
                    key=(state['running'],tuple(state['streams']))
                    if key!=self.last:
                        self.last=key;callback(state,stamp)
                except (OSError,ValueError,subprocess.SubprocessError) as e:
                    log.debug('Cider stream metadata unavailable: %s',type(e).__name__)
                # The subscription wakes on cork/uncork and new/removed streams.
                while not stop.is_set():
                    ready,_,_=select.select([proc.stdout],[],[],.1)
                    if ready:
                        if not os.read(proc.stdout.fileno(),65536):return
                        break
        finally:
            proc.terminate()
            try:proc.wait(timeout=1)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=1)


def playback_events(client,stop,callback):
    """Read public API:Playback events using the existing websockets dependency."""
    try:from websockets.sync.client import connect
    except ImportError:
        log.warning('Cider event monitor unavailable: websockets missing');return
    url='ws'+client.base[4:]+'/socket.io/?EIO=4&transport=websocket'
    headers={'apptoken':client.token} if client.token else {}
    while not stop.is_set():
        try:
            with connect(url,additional_headers=headers,open_timeout=2,close_timeout=.3,max_size=1024*1024) as ws:
                while not stop.is_set():
                    try:packet=ws.recv(timeout=.1)
                    except TimeoutError:continue
                    if not isinstance(packet,str):continue
                    if packet.startswith('0'):ws.send('40')
                    elif packet=='2':ws.send('3')
                    elif packet.startswith('42'):
                        event=json.loads(packet[2:])
                        if len(event)==2 and event[0]=='API:Playback' and isinstance(event[1],dict):
                            callback(event[1],time.monotonic())
        except Exception as e:
            # A stopped Cider must not stop the daemon; metadata polling remains.
            if stop.is_set():break
            log.debug('Cider event monitor reconnect: %s',type(e).__name__);stop.wait(1)
