"""Cider REST, bounded pull transport and native art, owned by HerdRemote's session."""
import heapq
import io
import json
import logging
import math
import os
import queue
import re
import secrets
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from hrcider_cache import Cache, Index, art_id, canonical_art_url
from hrcider_volume import SystemVolume
from hrcider_library import Library
from hrcider_store import Ledger, Planner, offer_records, parse_rows

log = logging.getLogger('herdremote.cider')
SIZES = {'L':240,'M':128,'S':56,'XS':32}
CHUNK = 12 * 1024
CHUNK_HEADER = struct.Struct('<4s6I')
AAT = struct.Struct('<6H')
DEFAULT_WINDOW = 24 * 1024
RESIDENT_WINDOW = 128 * 1024


def clean(value, limit=120):
    text=' '.join(''.join(' ' if ord(c)<32 or ord(c)==127 else c for c in str(value)).split())
    return text.encode()[:limit].decode('utf-8','ignore')


def capped_url(template):
    url=canonical_art_url(template)
    if not re.search(r'240x240(?=(?:bb|cc|sr)?[.])',url):
        raise ValueError('Artwork URL has no capped size variant')
    parts=urllib.parse.urlsplit(url)
    if parts.scheme!='https' and not (parts.scheme=='http' and parts.hostname in ('127.0.0.1','localhost','::1')):
        raise ValueError('Artwork needs HTTPS')
    return url


_BAYER=(0,8,2,10,12,4,14,6,3,11,1,9,15,7,13,5)
# Native RGB565 components for each ordered-dither phase.
_RED=[bytes((max(0,min(255,v+(d-8)//2))>>3)<<3 for v in range(256)) for d in _BAYER]
_GREEN_HI=[bytes(max(0,min(255,v+(d-8)//4))>>5 for v in range(256)) for d in _BAYER]
_GREEN_LO=[bytes(((max(0,min(255,v+(d-8)//4))>>2)&7)<<5 for v in range(256)) for d in _BAYER]
_BLUE=[bytes(max(0,min(255,v+(d-8)//2))>>3 for v in range(256)) for d in _BAYER]


def render(raw,tier):
    from PIL import Image,ImageOps
    dim=SIZES[tier]
    with Image.open(io.BytesIO(raw)) as source:
        if source.width>240 or source.height>240:raise ValueError('CDN exceeded 240px cap')
        image=ImageOps.fit(source.convert('RGB'),(dim,dim),method=Image.Resampling.LANCZOS)
        column=tier=='M'
        if column:image=image.transpose(Image.Transpose.TRANSPOSE)
        rgb=image.tobytes();red,green,blue=rgb[0::3],rgb[1::3],rgb[2::3]
        phase=bytes(((x&3)*4+(y&3) if column else (y&3)*4+(x&3))
                    for y in range(dim) for x in range(dim))
        pixels=bytearray(dim*dim*2)
        pixels[0::2]=bytes(_GREEN_LO[p][g]|_BLUE[p][b] for p,g,b in zip(phase,green,blue))
        pixels[1::2]=bytes(_RED[p][r]|_GREEN_HI[p][g] for p,r,g in zip(phase,red,green))
    return AAT.pack(0x5441,8,dim,dim,int(column),0)+pixels


def decode_chunk(payload):
    if len(payload)<CHUNK_HEADER.size:raise ValueError('Short art chunk')
    magic,generation,request,offset,total,crc,chunk_crc=CHUNK_HEADER.unpack_from(payload)
    data=payload[CHUNK_HEADER.size:]
    if magic!=b'CAR1' or not data or offset+len(data)>total or zlib.crc32(data)!=chunk_crc:
        raise ValueError('Invalid art chunk')
    return dict(generation=generation,request=request,offset=offset,total=total,crc=crc,chunk_crc=chunk_crc,data=data)


def pack_xs(generation,request,items):
    result=bytearray(struct.pack('<4sIII',b'CXS1',generation,request,len(items)))
    for key,data in items:
        if len(data)!=12+32*32*2:raise ValueError('XS geometry')
        result.extend(bytes.fromhex(key));result.extend(struct.pack('<II',len(data),zlib.crc32(data)));result.extend(data)
    if len(result)>CHUNK:raise ValueError('XS range exceeds frame')
    return bytes(result)


def unpack_xs(payload):
    magic,generation,request,count=struct.unpack_from('<4sIII',payload)
    if magic!=b'CXS1' or count>5:raise ValueError('Invalid XS range')
    items=[];offset=16
    for _ in range(count):
        key=payload[offset:offset+32].hex();size,crc=struct.unpack_from('<II',payload,offset+32);offset+=40
        data=payload[offset:offset+size];offset+=size
        if len(data)!=2060 or zlib.crc32(data)!=crc:raise ValueError('Bad XS entry')
        items.append((key,data))
    if offset!=len(payload):raise ValueError('Trailing XS bytes')
    return generation,request,items


def page_frame(generation,request,page):
    rows=[]
    for item in page['items']:
        key=str(item['id'])
        if len(key.encode())>128 or any(ord(c)<32 for c in key):raise ValueError('Invalid stable id')
        rows.append('\t'.join([key,clean(item['title'],60),clean(item['subtitle'],40),
                              item['art'],str(item['duration']),str(item['count'])]))
    while True:
        head=f"CL\t{generation}\t{request}\t{page['generation']}\t{page['offset']}\t{page['total']}\t{int(page['stale'])}\t{len(rows)}"
        payload='\n'.join([head]+rows).encode()
        if len(payload)<=CHUNK:return payload
        rows.pop()


class Client:
    def __init__(self,base='http://127.0.0.1:10767',token=None,timeout=.8,opener=None):
        url=urllib.parse.urlsplit(base)
        if url.scheme!='http' or url.hostname not in ('127.0.0.1','localhost','::1'):
            raise ValueError('Cider API must be loopback HTTP')
        self.base,self.token,self.timeout=base.rstrip('/'),token,timeout
        # Rockbox's Repeat modes Shuffle/A-B have no Cider equivalent; the host keeps
        # them virtually and drops them when Cider's own repeat/shuffle moves.
        self.mlock=threading.RLock();self.virtual=None;self.expect=None;self.restore_shuffle=0
        self.real=(0,0);self.last_id='';self.ab={'a':None,'b':None,'id':None}
        # Environment HTTP proxies must not forward local credentials.
        self.opener=opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(self,path,body=None,timeout=None,method=None,allow_list=False):
        headers={'Accept':'application/json'}
        if self.token:headers['apptoken']=self.token
        data=None if body is None else json.dumps(body).encode()
        if data is not None:headers['Content-Type']='application/json'
        req=urllib.request.Request(self.base+path,data=data,headers=headers,method=method)
        try:
            with self.opener.open(req,timeout=self.timeout if timeout is None else timeout) as r:
                raw=r.read(2*1024*1024+1)
                if len(raw)>2*1024*1024:raise ValueError('Cider reply too large')
                value=json.loads(raw)
        except urllib.error.HTTPError as e:
            code=e.code;e.close()
            raise OSError('Cider HTTP '+str(code)) from None
        if allow_list and isinstance(value,list):return value
        if not isinstance(value,dict) or value.get('error'):raise OSError('Cider RPC failed')
        return value
    def snapshot(self):
        try:return self._snapshot()
        except (TypeError,AttributeError,KeyError,OverflowError):
            raise ValueError('Malformed Cider state') from None
    def _snapshot(self):
        info=self.request('/api/v1/playback/now-playing').get('info') or {}
        if not isinstance(info,dict):raise ValueError('Invalid Cider now-playing item')
        playing=self.request('/api/v1/playback/is-playing').get('is_playing',False)
        timing=self.request('/api/v2/playback/time').get('data',{})
        volume=0
        repeat=self.request('/api/v1/playback/repeat-mode').get('value',0)
        shuffle=self.request('/api/v1/playback/shuffle-mode').get('value',0)
        artwork=info.get('artwork') or {};params=info.get('playParams') or {}
        duration=max(0,int(info.get('durationInMillis') or 0))
        position=min(duration,max(0,int(float(timing.get('currentTime',timing.get('currentPlaybackTime',0)))*1000)))
        track=str(params.get('id') or info.get('id') or '')
        return dict(id=track,repeat5=self._mode5(int(repeat or 0),int(shuffle or 0),track),title=info.get('name',''),artist=info.get('artistName',''),
                    album=info.get('albumName',''),composer=info.get('composerName',''),duration=duration,
                    position=position,rate=1000 if playing else 0,
                    volume=max(0,min(100,round(float(volume)*100))),repeat=int(repeat or 0),shuffle=int(shuffle or 0),
                    art=art_id(artwork.get('url','')),template=artwork.get('url',''),bg=str(artwork.get('bgColor','181818'))[:6])
    def _mode5(self,repeat,shuffle,track):
        """Rockbox repeat numbering: 0 off, 1 all, 2 one, 3 shuffle, 4 A-B."""
        with self.mlock:
            self.real=(repeat,shuffle);self.last_id=track
            if self.virtual and (repeat,shuffle)!=self.expect:self.virtual=None
            if self.virtual!='ab' or (self.ab['id'] is not None and self.ab['id']!=track):
                self.ab={'a':None,'b':None,'id':None}
            return {'shuffle':3,'ab':4}.get(self.virtual) or {0:0,2:1,1:2}.get(repeat,0)
    @property
    def ab_active(self):
        with self.mlock:return self.virtual=='ab' and self.ab['b'] is not None
    def ab_tick(self,state):
        """Position to seek back to when playback has passed marker B, else None."""
        with self.mlock:
            ab=self.ab
            if self.virtual=='ab' and ab['b'] is not None and ab['id']==state['id'] and state['rate'] and state['position']>=ab['b']:
                return ab['a']
        return None
    def _repeat5(self,number):
        if number not in range(5):raise ValueError('Invalid repeat value')
        put=lambda mode:self.request('/api/v2/playback/repeat',{'mode':mode},method='PUT')
        shuffle=lambda on:self.request('/api/v2/playback/shuffle',{'enabled':bool(on)},method='PUT')
        with self.mlock:
            leaving=self.virtual=='shuffle' and number!=3;restore=self.restore_shuffle;previous=self.virtual
            if number==3:
                if previous!='shuffle':self.restore_shuffle=self.real[1]
                self.virtual='shuffle';self.expect=(2,1)
            elif number==4:
                self.virtual='ab';self.expect=(self.real[0],restore if leaving else self.real[1]);self.ab={'a':None,'b':None,'id':None}
            else:self.virtual=None;self.ab={'a':None,'b':None,'id':None}
        try:
            if number==3:put('all');return shuffle(True)
            if leaving:shuffle(restore)
            if number==4:return {'status':'ok'}
            return put(('none','all','one')[number])
        except BaseException:
            with self.mlock:self.virtual=None
            raise
    def _ab_mark(self,ms):
        seek=None
        with self.mlock:
            if self.virtual!='ab':raise ValueError('A-B repeat is not active')
            ab=self.ab
            if ab['a'] is None:ab.update(a=ms,b=None,id=self.last_id)
            elif ab['b'] is None:
                if ms<=ab['a']:raise ValueError('Marker B must follow A')
                ab['b']=ms;seek=ab['a']
            else:ab.update(a=None,b=None,id=None)
        return self.control('seek',seek) if seek is not None else {'status':'ok'}
    def control(self,command,value=0):
        if command in ('seek','volume') and not math.isfinite(float(value)):
            raise ValueError('Nonfinite Cider value')
        routes={'toggle':('playpause',{}),'next':('next',{}),'previous':('previous',{}),
                'seek':('seek',{})}
        if command in ('repeat5','ab-mark'):
            try:number=int(str(value).strip())
            except ValueError:raise ValueError('Invalid '+command+' value') from None
            return self._repeat5(number) if command=='repeat5' else self._ab_mark(max(0,number))
        if command in ('shuffle','repeat'):
            # Explicit values (never toggles), so a retried press is idempotent.
            # Cider repeatMode: 0 none, 1 one, 2 all; shuffleMode: 0 off, 1 on.
            try:number=int(str(value).strip())
            except ValueError:raise ValueError('Invalid '+command+' value') from None
            if command=='shuffle':
                if number not in (0,1):raise ValueError('Invalid shuffle value')
                return self.request('/api/v2/playback/shuffle',{'enabled':bool(number)},method='PUT')
            if number not in (0,1,2):raise ValueError('Invalid repeat value')
            return self.request('/api/v2/playback/repeat',{'mode':('none','one','all')[number]},method='PUT')
        if command=='play-item':
            kind,key=str(value).split(':',1)
            if not re.fullmatch(r'[a-z][a-z0-9-]{0,31}',kind) or not re.fullmatch(r'[\w.:-]{1,128}',key):raise ValueError('Invalid play item')
            collection=kind.removeprefix('library-')
            collection={'song':'songs','album':'albums','playlist':'playlists'}.get(collection,collection)
            href=('/v1/me/library/' if kind.startswith('library-') else
                  '/v1/catalog/$STOREFRONT/')+collection+'/'+urllib.parse.quote(key,safe='')
            # Cider's lightweight mkPlayItem drops songs without playParams.
            # Its href route resolves the complete resource before playback.
            reply=self.request('/api/v1/amapi/run-v3',{'path':href})
            if isinstance(reply.get('data'),dict):reply=reply['data']
            items=reply.get('data',[])
            if not isinstance(items,list) or not items:raise ValueError('Selected item unavailable')
            item=items[0];params=item.get('attributes',{}).get('playParams',{})
            if collection=='songs' and not params:raise ValueError('Selected song is not playable')
            result=self.request('/api/v1/playback/play-item-href',{'href':href})
            if collection=='songs':
                result['expected_ids']=list({str(v) for v in
                    (item.get('id'),params.get('id'),params.get('catalogId'),params.get('libraryId')) if v})
            return result
        else:
            if command not in routes:raise ValueError('Unknown Cider control')
            route,body=routes[command]
            if command=='seek':body={'position':max(0,float(value))/1000}
        # A control timeout is never retried: toggle/skip are not idempotent.
        return self.request('/api/v1/playback/'+route,body)
    # --- playing a song inside its container (the queue is the container) ---
    @staticmethod
    def aliases(item):
        params=(item.get('attributes') or {}).get('playParams') or {}
        return {str(v) for v in (item.get('id'),params.get('id'),params.get('catalogId'),params.get('libraryId')) if v}
    def song_aliases(self,kind,key):
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,31}',kind) or not re.fullmatch(r'[\w.:-]{1,128}',key):raise ValueError('Invalid play item')
        href=('/v1/me/library/' if kind.startswith('library-') else '/v1/catalog/$STOREFRONT/')+'songs/'+urllib.parse.quote(key,safe='')
        reply=self.request('/api/v1/amapi/run-v3',{'path':href})
        if isinstance(reply.get('data'),dict):reply=reply['data']
        items=reply.get('data',[])
        if not isinstance(items,list) or not items:raise ValueError('Selected item unavailable')
        return self.aliases(items[0])|{key}
    def queue_state(self):
        data=self.request('/api/v2/queue/position',timeout=3).get('data') or {}
        return int(data.get('position',0)),int(data.get('total',0))
    def queue_slice(self,offset,limit):
        data=self.request(f'/api/v2/queue?offset={offset}&limit={limit}',timeout=3).get('data') or {}
        return [i.get('track',i) for i in data.get('items',[])]
    def queue_find(self,wanted,total,near):
        """Queue index of the first track whose ids meet `wanted`, `near` first."""
        if 0<=near<total:
            tracks=self.queue_slice(near,1)
            if tracks and wanted&self.aliases(tracks[0]):return near
        for offset in range(0,min(total,1000),100):
            for k,track in enumerate(self.queue_slice(offset,100)):
                if wanted&self.aliases(track):return offset+k
        return None
    def play_collection(self,ctype,cid,wanted,index):
        """Queue a whole album/playlist and start at row `index`; Cider's /previous
        and /next then walk the container. Verified against the queue by track id."""
        if not re.fullmatch(r'(?:library-)?(?:albums|playlists)',ctype) or not re.fullmatch(r'[\w.:-]{1,128}',cid):raise ValueError('Invalid collection')
        self.request('/api/v2/playback/play-collection',{'type':ctype,'id':cid},timeout=4)
        end=time.monotonic()+4;seen=None;since=time.monotonic()
        while True:
            position,total=self.queue_state()
            if total!=seen:seen=total;since=time.monotonic()
            # Long collections load in pages: wait for the row, or for the queue to settle.
            if total>index or (total and time.monotonic()-since>=.4):break
            if time.monotonic()>=end:raise OSError('Cider queue not ready')
            time.sleep(.05)
        target=self.queue_find(wanted,total,index)
        if target is None:raise ValueError('Selected song is not in the collection')
        if target!=position:self.request('/api/v2/queue/jump',{'index':target},timeout=3)
        return {'status':'ok','expected_ids':sorted(wanted)}
    def fill_queue(self,wanted,before,after,cancel):
        """The song already plays. Append the rows after it in order, then the rows
        before it, and move those in front of it (Cider keeps the position)."""
        def add(kind,key):
            if not re.fullmatch(r'[a-z][a-z0-9-]{0,31}',kind) or not re.fullmatch(r'[\w.:-]{1,128}',key):return False
            _,total=self.queue_state()
            self.request('/api/v2/queue/add-later',{'type':kind,'id':key},timeout=3)
            end=time.monotonic()+3
            while self.queue_state()[1]<=total:
                if cancel.is_set() or time.monotonic()>=end:return False
                time.sleep(.02)
            return True
        try:
            # play-item-href replaces the queue asynchronously: start once it is just this song.
            end=time.monotonic()+3
            while True:
                position,total=self.queue_state()
                if total==1 and self.queue_find(wanted,1,0)==0:break
                if cancel.is_set() or time.monotonic()>=end:return False
                time.sleep(.05)
            for kind,key in list(after)+list(before):
                if cancel.is_set() or not add(kind,key):return False
            if before and not cancel.is_set():
                _,total=self.queue_state()
                here=self.queue_find(wanted,total,0)
                if here is None:return False
                for j in range(len(before)):
                    self.request('/api/v2/queue/move',{'from':total-len(before)+j,'to':here+j},timeout=3)
            return True
        except (OSError,ValueError,TimeoutError):
            return False
    def library(self,kind,offset,limit):
        return self.request('/api/v1/amapi/run-v3',{'path':f'/v1/me/library/{kind}?limit={limit}&offset={offset}'+('&include=albums,catalog' if kind=='songs' else '')},timeout=3)

    def library_path(self,path):
        if not path.startswith('/v1/me/') or len(path)>1024:
            raise ValueError('Invalid library path')
        return self.request('/api/v1/amapi/run-v3',{'path':path},timeout=3)
    def shuffle_library(self):
        self.request('/api/v2/playback/shuffle',{'enabled':True},method='PUT')
        return self.request('/api/v2/playback/play-href',{'href':'/v1/me/library/songs'})


class Scheduler:
    def __init__(self):
        self.lock=threading.RLock();self.reset(0,DEFAULT_WINDOW)
    def reset(self,generation,window=DEFAULT_WINDOW):
        with self.lock:
            self.generation=generation;self.window=min(RESIDENT_WINDOW,max(0,window));self.credit=0
            self.heap=[];self.live={};self.serial=0;self.sent_bytes=0
    def grant(self,count):
        if count<0:raise ValueError('Negative credits')
        with self.lock:self.credit=min(self.window,self.credit+count)
    def push(self,priority,kind,payload,request=None,distance=0,order=None,bulk=False):
        with self.lock:
            if len(self.heap)>=64:raise ValueError('Cider queue full')
            self.serial+=1
            heapq.heappush(self.heap,(priority,distance,self.serial if order is None else order,self.serial,kind,payload,request,bulk))
    def add_art(self,request,key,tier,raw,offset=0,priority=3,distance=0):
        if not 0<=offset<len(raw):raise ValueError('Invalid resume offset')
        with self.lock:
            self.cancel(request);self.live[request]=(key,tier,raw,offset,priority,distance,self.serial+1)
            self._chunk(request)
    def _chunk(self,request):
        key,tier,raw,offset,priority,distance,order=self.live[request]
        data=raw[offset:offset+CHUNK-CHUNK_HEADER.size]
        payload=CHUNK_HEADER.pack(b'CAR1',self.generation,request,offset,len(raw),zlib.crc32(raw),zlib.crc32(data))+data
        self.push(priority,3,payload,request,distance,order,True)
    def cancel(self,request):
        with self.lock:
            self.live.pop(request,None)
            self.heap=[v for v in self.heap if v[6]!=request];heapq.heapify(self.heap)
    def next_frame(self):
        with self.lock:
            if not self.heap:return None
            item=self.heap[0];priority,distance,order,serial,kind,payload,request,bulk=item
            if bulk and len(payload)>self.credit:return None
            heapq.heappop(self.heap)
            if bulk:self.credit-=len(payload);self.sent_bytes+=len(payload)
            if request in self.live:
                key,tier,raw,offset,p,d,o=self.live[request];offset+=len(payload)-CHUNK_HEADER.size
                if offset<len(raw):self.live[request]=(key,tier,raw,offset,p,d,o);self._chunk(request)
                else:self.live.pop(request,None)
            return kind,payload


class Window:
    """Nine contiguous slides; center changes never reset already loaded slots."""
    def update(self,center,velocity):
        result=[center]
        for distance in range(1,5):
            sides=(1,-1) if velocity>0 else (-1,1)
            result.extend(center+distance*d for d in sides if center+distance*d>=0)
        return result
    def tier(self,velocity):return 'XS' if abs(velocity)>=3 else 'M'


class Bridge:
    def __init__(self,client=None,cache=None,fetch=None,volume=None):
        self.client=client or Client(os.environ.get('CIDER_API_URL','http://127.0.0.1:10767'),os.environ.get('CIDER_API_TOKEN'))
        self.fetch=fetch or urllib.request.urlopen
        self.cache=cache or Cache();self.scheduler=Scheduler();self.lock=threading.RLock()
        self.stop=threading.Event();self.requests={};self.jobs=queue.PriorityQueue(32);self.controls=queue.Queue(16)
        self.state=None;self.available=False;self.reason='Starting';self.state_seq=0;self.last_state=0
        self.opened=False;self.state_dirty=False;self.library_sent=0;self.wake=threading.Event();self.failures=0;self.templates={};self.generation=0;self.threads=[]
        self.fill_cancel=threading.Event()
        self.index=Index(self.cache,self.client)
        self.library=Library(self.cache,self.client);self.volume=volume or SystemVolume()
        self.volume_delta=0;self.pages=queue.Queue(1);self.page_token=0
        self.art_locks=[threading.RLock() for _ in range(32)]
        self.prefetch=queue.Queue(10)
        self.warm_window=set();self.warm_queue=set();self.warm_jobs={};self.neighbors=None
        self.capabilities=frozenset()
        self.ledger=Ledger(self.cache);self.planner=Planner(self.cache,self.ledger);self.offer_pairs=set()
    def start(self):
        for target,name in [(self._prefetch_worker,'cider-prefetch-'+str(i)) for i in range(4)]+[(self._poll,'cider-state'),(self._work,'cider-art'),(self._control,'cider-control'),(self._index,'cider-index'),(self._pages,'cider-pages'),(self._neighbors,'cider-neighbors')]:
            t=threading.Thread(target=target,name=name,daemon=True);t.start();self.threads.append(t)
    def close(self):
        self.stop.set()
        self.fill_cancel.set()
        for t in self.threads:t.join(timeout=3)
        if not any(t.is_alive() for t in self.threads):self.cache.close()
    def disconnect(self):
        with self.lock:
            self.warm_window.clear();self.warm_queue.clear();self.neighbors=None;self.capabilities=frozenset()
            self.warm_jobs.clear();self.offer_pairs.clear();self.ledger.pending=None;self.ledger.synced=False;self.planner.reset()
            self.opened=False;self.generation=0;self.requests.clear();self._drop_jobs();self.scheduler.reset(0);self.volume_delta=0
    def command(self,payload):
        try:
            fields=payload.decode().split('\t');name=fields[0]
            if name not in ('ci','cg','cr','cx','cc','cp','ct','cb','cq','cw','ls','la','lw','lv'):return False
            if name=='lv':fields=payload.decode().split('\n',1)[0].split('\t')
            with self.lock:
                if name=='ci':
                    if len(fields) not in (3,4) or fields[1]!='1':raise ValueError('Cider protocol')
                    advertised=fields[3].split(',') if len(fields)==4 else []
                    if len(advertised)>8 or any(not re.fullmatch('[a-z0-9-]{1,24}',c) for c in advertised):raise ValueError('Invalid Cider capabilities')
                    self.capabilities=frozenset(advertised)&{'cn18','neighbors','warm','play-ack','localstore','modes5'}
                    window=int(fields[2]);self.volume_delta=0;self.generation=secrets.randbelow(0xfffffffe)+1;self.scheduler.reset(self.generation,window)
                    self.warm_window.clear();self.warm_queue.clear();self.warm_jobs.clear();self.neighbors=None
                    self.offer_pairs.clear();self.ledger.pending=None;self.ledger.synced=False;self.planner.reset()
                    self.requests.clear();self._drop_jobs();self.opened=True;self.library_sent=0;self.last_state=0;self._state(True)
                    # The store protocol is opt-in on both sides: no CF, no ls/lw/la/lv.
                    if 'localstore' in self.capabilities:self.scheduler.push(1,2,f'CF\t{self.generation}\tlocalstore'.encode())
                    return True
                if len(fields)<2 or int(fields[1])!=self.generation:return True
                if name=='cg' and len(fields)==3:self.scheduler.grant(int(fields[2]))
                elif name=='cx' and len(fields)==3:
                    req=int(fields[2])
                    if req==0:
                        self.requests.clear();self._drop_jobs()
                        for r in list(self.scheduler.live):self.scheduler.cancel(r)
                    else:self.requests.pop(req,None);self.scheduler.cancel(req);self._drop_jobs(req)
                elif name=='cc' and len(fields)==5:
                    req=int(fields[2])
                    if fields[3]=='volume-step':
                        self.volume_delta=max(-100,min(100,self.volume_delta+int(fields[4])))
                    elif fields[3]=='volume':raise ValueError('Cider volume disabled')
                    else:self.controls.put_nowait((self.generation,req,fields[3],fields[4]))
                elif name=='cr' and len(fields)==8:
                    req=int(fields[2]);key,tier=fields[3:5];offset,priority,distance=map(int,fields[5:])
                    if not 0<req<=0xffffffff:raise ValueError('Invalid request id')
                    if not re.fullmatch('[0-9a-f]{64}',key) or tier not in SIZES or not 0<=offset<12+SIZES[tier]**2*2 or priority not in (3,4) or not 0<=distance<=10000:raise ValueError('Invalid art request')
                    if len(self.requests)>=24:raise ValueError('Too many art requests')
                    self.scheduler.cancel(req);self._drop_jobs(req)
                    token=object();self.requests[req]=token
                    self.jobs.put_nowait((priority,distance,time.monotonic_ns(),self.generation,req,key,tier,offset,token))
                    log.info('art request gen=%d req=%d tier=%s offset=%d distance=%d credit=%d',self.generation,req,tier,offset,distance,self.scheduler.credit)
                elif name=='cb' and len(fields)==4:
                    req=int(fields[2]);keys=fields[3].split(',')
                    if not 0<req<=0xffffffff:raise ValueError('Invalid request id')
                    if not 1<=len(keys)<=5 or any(not re.fullmatch('[0-9a-f]{64}',k) for k in keys):raise ValueError('Invalid XS range')
                    self.scheduler.cancel(req);self._drop_jobs(req)
                    token=object();self.requests[req]=token
                    self.jobs.put_nowait((3,0,time.monotonic_ns(),self.generation,req,tuple(keys),'XS',0,token))
                elif name=='cp' and len(fields)==7:
                    req=int(fields[2]);page=self.cache.page(fields[3],int(fields[4]),int(fields[5]),stale=not self.available or self.index.initial or self.index.force)
                    self.scheduler.push(2,2,page_frame(self.generation,req,page),req,bulk=True)
                elif name=='cq' and len(fields)==6:
                    self.page_token+=1
                    try:self.pages.get_nowait()
                    except queue.Empty:pass
                    self.scheduler.heap=[v for v in self.scheduler.heap if v[5][:3]!=b'CL\t'];heapq.heapify(self.scheduler.heap)
                    self.pages.put_nowait((self.generation,int(fields[2]),int(fields[3]),int(fields[4]),fields[5],self.page_token))
                elif name=='cw' and len(fields)==4:
                    if 'warm' not in self.capabilities:raise ValueError('Unadvertised art window')
                    tier=fields[2];keys=fields[3].split(',') if fields[3] else []
                    if tier not in SIZES or len(keys)>7 or any(not re.fullmatch('[0-9a-f]{64}',k) for k in keys):
                        raise ValueError('Invalid visible art window')
                    self.warm_window={(key,tier) for key in keys};self._schedule_warm()
                elif name in ('ls','la','lw','lv'):self._store_command(name,fields,payload)
                elif name=='ct':self.index.request()
            return True
        except (ValueError,UnicodeError,queue.Full):
            log.warning('Invalid or full Cider request')
            return True
    def _store_command(self,name,fields,payload):
        # Caller holds self.lock and has matched the session generation.
        if 'localstore' not in self.capabilities:raise ValueError('Unadvertised local store')
        gen=self.generation
        if name=='ls' and len(fields)==7:
            epoch,count,digest=int(fields[2]),int(fields[3]),int(fields[4])
            if not self.ledger.status(epoch,count,digest):
                self.scheduler.push(4,2,f'CQ\t{gen}\t0'.encode())
            else:self.planner.reset()
        elif name=='lv' and len(fields)==5:
            cursor,nxt,count=int(fields[2]),int(fields[3]),int(fields[4])
            rows=parse_rows(payload.decode().split('\n',1)[1] if b'\n' in payload else '')
            if len(rows)!=count:raise ValueError('Inventory row count')
            again=self.ledger.page(cursor,nxt,rows)
            if again is not None:self.scheduler.push(4,2,f'CQ\t{gen}\t{again}'.encode())
            else:self.planner.reset()
        elif name=='la' and len(fields)==5:
            key,tier,crc=fields[2],fields[3],int(fields[4])
            if not re.fullmatch('[0-9a-f]{64}',key) or tier not in SIZES or not 0<=crc<=0xffffffff:raise ValueError('Invalid store ack')
            self.ledger.ack(key,tier,crc);self.offer_pairs.discard((key,tier))
        elif name=='lw' and len(fields)==3:
            n=max(0,min(5,int(fields[2])))
            if not n or not self.ledger.synced:return        # never offer against an unverified inventory
            urgent=[]
            if self.state and self.state.get('art') not in (None,'-'):urgent.append(self.state['art'])
            if self.neighbors:urgent.extend(k for k in self.neighbors[2:] if k!='-')
            pairs=self.planner.next(n,urgent)
            for record in offer_records(gen,pairs):self.scheduler.push(4,2,record)
            self.offer_pairs=set(pairs)|set(list(self.offer_pairs)[-8:])
            self._schedule_warm()
    def _drop_jobs(self,request=None):
        with self.jobs.mutex:
            self.jobs.queue=[v for v in self.jobs.queue if request is not None and v[4]!=request]
            heapq.heapify(self.jobs.queue)

    def _state(self,force=False):
        self.state_dirty=True
        if not self.opened:return
        now=time.monotonic()
        if now-self.last_state<.25 and not force:return
        self.scheduler.heap=[v for v in self.scheduler.heap if not (v[4]==2 and v[5][:3] in (b'CA\t',b'CN\t'))];heapq.heapify(self.scheduler.heap)
        self.scheduler.push(1,2,f'CA\t{self.generation}\t{int(self.available)}\t{clean(self.reason,80)}'.encode())
        if self.available and self.state:
            s=self.state;self.state_seq+=1
            fields=['CN',self.generation,self.state_seq,int(now*1000)&0xffffffff,s['duration'],s['position'],s['rate'],s['volume'],s['shuffle'],s['repeat'],s['id'],s['art'],s['bg'],s['title'],s['artist'],s['album']]
            if 'cn18' in self.capabilities:fields.extend([int(s.get('muted',False)),s.get('composer','')])
            if 'cn18' in self.capabilities and 'modes5' in self.capabilities:fields.append(s.get('repeat5',0))
            self.scheduler.push(1,2,'\t'.join(clean(v) for v in fields).encode())
        self.last_state=now;self.state_dirty=False
    def _poll(self):
        next_poll=0;published=None;anchor=0
        while not self.stop.is_set():
            now=time.monotonic()
            if self.wake.is_set():self.wake.clear();next_poll=0
            if now<next_poll:self.stop.wait(min(.2,next_poll-now));continue
            try:
                state=self.client.snapshot()
                try:state['volume'],state['muted']=self.volume.read()
                except OSError:state['volume'],state['muted']=0,False
                with self.lock:
                    changed=not self.available or published is None or any(state[k]!=published[k] for k in state if k not in ('position','template'))
                    predicted=published['position']+(now-anchor)*published['rate'] if published else 0
                    changed |= abs(state['position']-predicted)>2000 or now-self.last_state>=15
                    self.available=True;self.reason='';self.state=state;self.library.state=state
                    if state['template']:
                        self.templates[state['art']]=state['template']
                        if len(self.templates)>128:self.templates.pop(next(iter(self.templates)))
                    if changed:published=dict(state);anchor=now;self._state()
                target=self.client.ab_tick(state)
                if target is not None:
                    try:self.client.control('seek',target);self.wake.set()
                    except (OSError,ValueError,TimeoutError):pass
                self.failures=0;next_poll=now+(.25 if self.client.ab_active else 1)
            except (OSError,ValueError,TimeoutError) as error:
                with self.lock:
                    if self.available or self.reason=='Starting':self.available=False;self.reason=clean(str(error),80) or 'Cider unavailable';self._state()
                self.failures=min(8,self.failures+1);next_poll=now+min(30,2**(self.failures-1))
    def _index(self):
        while not self.stop.is_set():
            self.index.step();self.stop.wait(.25)
    def _pages(self):
        while not self.stop.is_set():
            try:
                generation,req,offset,limit,query,token=self.pages.get(timeout=.1)
            except queue.Empty:continue
            with self.lock:
                if generation!=self.generation or not self.opened:continue
            started=time.monotonic()
            try:page=self.library.page(query,offset,limit,stale=not self.available or self.index.initial)
            except (ValueError,OSError,TimeoutError):page=dict(generation=self.cache.generation,offset=offset,total=0,stale=True,items=[])
            with self.lock:
                if generation==self.generation and self.opened and token==self.page_token:
                    self.scheduler.push(2,2,native_page_frame(generation,req,page),req,bulk=True)
                    log.info('page ready gen=%d req=%d offset=%d rows=%d ms=%.1f',generation,req,offset,len(page['items']),1000*(time.monotonic()-started))
    def _control(self):
        while not self.stop.is_set():
            with self.lock:
                delta=self.volume_delta;self.volume_delta=0
            if delta:
                # One aggregated relative command per worker turn; no stale absolute target.
                try:self.volume.step(delta);self.wake.set()
                except OSError:log.warning('Host system volume unavailable')
                self.stop.wait(.04)
            try:generation,req,command,value=self.controls.get(timeout=.04)
            except queue.Empty:continue
            with self.lock:
                if generation!=self.generation or not self.opened:continue
            try:
                if not self.available:raise OSError('Cider unavailable')
                log.info('control request gen=%d req=%d command=%s',generation,req,command)
                if command=='native-play':
                    self.fill_cancel.set();self.fill_cancel=cancel=threading.Event()
                    result=self.library.play(value)
                    expected=result.get('expected_ids',[]) if isinstance(result,dict) else []
                    if expected:
                        deadline=time.monotonic()+2.5
                        while True:
                            info=self.client.request('/api/v1/playback/now-playing').get('info') or {}
                            params=info.get('playParams') or {}
                            ids={str(v) for v in (info.get('id'),params.get('id'),params.get('catalogId'),params.get('libraryId')) if v}
                            if ids.intersection(expected):break
                            if self.stop.is_set() or time.monotonic()>=deadline:
                                raise OSError('Cider did not select the requested song')
                            self.stop.wait(.05)
                    fill=result.get('fill') if isinstance(result,dict) else None
                    if fill:threading.Thread(target=fill,args=(cancel,),daemon=True,name='cider-queue').start()
                elif command=='native-context':
                    self.library.set_context(value)
                elif command=='source-local':
                    if self.client.request('/api/v1/playback/is-playing').get('is_playing'):self.client.control('toggle')
                else:self.client.control(command,value)
                ok=True;text='OK';self.wake.set()
            except (OSError,ValueError,TimeoutError) as error:
                ok=False;text='Control failed; not retried'
                log.warning('control failed gen=%d req=%d command=%s reason=%s',generation,req,command,clean(error,120))
            log.info('control result gen=%d req=%d ok=%s',generation,req,ok)
            with self.lock:
                if generation==self.generation and self.opened:self.scheduler.push(0,2,f'CK\t{generation}\t{req}\t{int(ok)}\t{text}'.encode())
    def artwork(self,key,tier):
        # One CDN download per key even when a visible request joins prefetch.
        with self.art_locks[int(key[:2],16)%len(self.art_locks)]:
            return self._artwork(key,tier)
    def _artwork(self,key,tier):
        cached=self.cache.get_art(key,tier)
        expected=AAT.pack(0x5441,8,SIZES[tier],SIZES[tier],int(tier=='M'),0)
        if cached is not None and len(cached)==12+SIZES[tier]**2*2 and cached[:12]==expected:return cached
        raw=self.cache.get_art(key,'original')
        if raw is None:
            template=self.templates.get(key) or self.cache.template(key)
            if not template:raise ValueError('Unknown artwork id')
            # Never send the RPC token to the CDN.
            with self.fetch(capped_url(template),timeout=2) as response:
                raw=response.read(2*1024*1024+1)
                if len(raw)>2*1024*1024:raise ValueError('Artwork too large')
            from PIL import Image
            with Image.open(io.BytesIO(raw)) as image:
                if image.width>240 or image.height>240:raise ValueError('CDN exceeded cap')
            self.cache.put_art(key,'original',raw)
        result=render(raw,tier);self.cache.put_art(key,tier,result)
        return result
    def _schedule_warm(self):
        desired=self.warm_window|self.warm_queue|self.offer_pairs
        while True:
            try:
                generation,pair=self.prefetch.get_nowait()
                if self.warm_jobs.get(pair)==generation:self.warm_jobs.pop(pair,None)
            except queue.Empty:break
        for pair in list(self.warm_jobs):
            if pair not in desired:self.warm_jobs.pop(pair,None)
        for pair in sorted(desired):
            if pair not in self.warm_jobs:
                self.warm_jobs[pair]=self.generation
                self.prefetch.put_nowait((self.generation,pair))
    def _prefetch_worker(self):
        while not self.stop.is_set():
            try:generation,pair=self.prefetch.get(timeout=.1)
            except queue.Empty:continue
            self._warm(generation,pair)
    def _warm(self,generation,pair):
        key,tier=pair
        with self.lock:
            if generation!=self.generation or not self.opened or pair not in self.warm_window|self.warm_queue|self.offer_pairs:return
        try:
            with self.lock:
                if generation!=self.generation or pair not in self.warm_window|self.warm_queue|self.offer_pairs:return
            self.artwork(key,tier)
        except (OSError,ValueError,TimeoutError):
            with self.lock:
                if self.warm_jobs.get(pair)==generation:self.warm_jobs.pop(pair,None)
    def _neighbors(self):
        while not self.stop.is_set():
            try:
                with self.lock:
                    current=self.state.get('id') if self.state else None
                    generation=self.generation;opened=self.opened
                if current and opened and 'neighbors' in self.capabilities:
                    rows=self.client.request('/api/v1/playback/queue',allow_list=True)
                    if isinstance(rows,dict):rows=rows.get('data',[])
                    if not isinstance(rows,list):raise ValueError('Invalid Cider queue')
                    at=None
                    for i,row in enumerate(rows):
                        params=row.get('attributes',{}).get('playParams',{})
                        if current in {str(v) for v in (row.get('id'),params.get('id'),params.get('catalogId'),params.get('libraryId')) if v}:
                            at=i;break
                    keys=[];templates={}
                    for i in ((at-1,at+1) if at is not None else (-1,-1)):
                        template=rows[i].get('attributes',{}).get('artwork',{}).get('url','') if 0<=i<len(rows) else ''
                        key=art_id(template);keys.append(key)
                        if template:templates[key]=template
                    signature=(generation,current,*keys)
                    with self.lock:
                        if generation==self.generation and self.opened and self.state.get('id')==current:
                            self.templates.update(templates)
                            while len(self.templates)>128:self.templates.pop(next(iter(self.templates)))
                            self.warm_queue={(key,'L') for key in keys if key!='-'} if any(tier=='L' for _,tier in self.warm_window) else set()
                            self._schedule_warm()
                            if signature!=self.neighbors:
                                self.neighbors=signature
                                self.scheduler.push(1,2,('CP\t'+'\t'.join(map(str,signature))).encode())
            except (OSError,ValueError,TimeoutError,AttributeError,TypeError):
                pass
            self.stop.wait(1)
    def _work(self):
        while not self.stop.is_set():
            try:priority,distance,order,generation,req,key,tier,offset,token=self.jobs.get(timeout=.2)
            except queue.Empty:continue
            started=time.monotonic()
            with self.lock:
                if generation!=self.generation or self.requests.get(req) is not token:continue
            try:
                if isinstance(key,tuple):
                    items=[]
                    for item in key:
                        with self.lock:
                            if generation!=self.generation or self.requests.get(req) is not token:break
                        items.append((item,self.artwork(item,'XS')))
                    if len(items)!=len(key):continue
                    raw=pack_xs(generation,req,items)
                else:raw=self.artwork(key,tier)
            except (OSError,ValueError,TimeoutError):
                with self.lock:
                    if generation==self.generation and self.requests.get(req) is token:
                        self.scheduler.push(0,2,f'CK\t{generation}\t{req}\t0\tArtwork unavailable'.encode());self.requests.pop(req,None)
                continue
            with self.lock:
                if generation==self.generation and self.requests.get(req) is token:
                    if isinstance(key,tuple):self.scheduler.push(priority,3,raw,req,distance,bulk=True)
                    else:self.scheduler.add_art(req,key,tier,raw,offset,priority,distance)
                    self.requests.pop(req,None)
                    log.info('art ready gen=%d req=%d bytes=%d ms=%.1f',generation,req,len(raw),1000*(time.monotonic()-started))
    def poll(self,send):
        with self.lock:
            if not self.opened:return
            if self.state_dirty:self._state()
            library=self.cache.generation
            if library!=self.library_sent:
                self.scheduler.push(1,2,f'CG\t{self.generation}\t{library}'.encode());self.library_sent=library
            for _ in range(4):
                frame=self.scheduler.next_frame()
                if not frame:break
                if frame[0]==2 and frame[1].startswith(b'CP\t') and 'neighbors' not in self.capabilities:continue
                send(*frame)


def native_page_frame(generation,request,page):
    rows=[];size=0
    for item in page['items']:
        fields=[item.get(k,'') for k in ('id','title','subtitle','art','duration','count','kind','route','bg','year')]
        line='\t'.join(clean(v,380 if i==7 else 120) for i,v in enumerate(fields))+'\n'
        if size+len(line.encode())>15000:break
        rows.append(line);size+=len(line.encode())
    header=f"CL\t{generation}\t{request}\t{page['generation']}\t{page['offset']}\t{page['total']}\t{int(page['stale'])}\t{len(rows)}\n"
    return (header+''.join(rows)).encode()
