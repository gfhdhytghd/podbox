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
from hrcider_cache import Cache, Index, art_id

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
    if '{w}' in template and '{h}' in template:
        url=template.replace('{w}','240').replace('{h}','240')
    else:
        url,n=re.subn(r'\d+x\d+(?=(?:bb|cc|sr)?[.])','240x240',template)
        if not n:raise ValueError('Artwork URL has no capped size variant')
    parts=urllib.parse.urlsplit(url)
    if parts.scheme!='https' and not (parts.scheme=='http' and parts.hostname in ('127.0.0.1','localhost','::1')):
        raise ValueError('Artwork needs HTTPS')
    return url


def render(raw,tier):
    from PIL import Image,ImageOps
    dim=SIZES[tier]
    with Image.open(io.BytesIO(raw)) as source:
        if source.width>240 or source.height>240:raise ValueError('CDN exceeded 240px cap')
        image=ImageOps.fit(source.convert('RGB'),(dim,dim),method=Image.Resampling.LANCZOS)
        pixels=bytearray(dim*dim*2)
        # A tiny ordered dither preserves smooth gradients without a device decoder.
        bayer=((0,8,2,10),(12,4,14,6),(3,11,1,9),(15,7,13,5))
        for y in range(dim):
            for x in range(dim):
                r,g,b=image.getpixel((x,y));d=bayer[y&3][x&3]-8
                r=max(0,min(255,r+d//2));g=max(0,min(255,g+d//4));b=max(0,min(255,b+d//2))
                value=(r>>3)<<11|(g>>2)<<5|b>>3
                struct.pack_into('<H',pixels,2*(x*dim+y if tier=='M' else y*dim+x),value)
    return AAT.pack(0x5441,8,dim,dim,int(tier=='M'),0)+pixels


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
        # Environment HTTP proxies must not forward local credentials.
        self.opener=opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(self,path,body=None,timeout=None):
        headers={'Accept':'application/json'}
        if self.token:headers['apptoken']=self.token
        data=None if body is None else json.dumps(body).encode()
        if data is not None:headers['Content-Type']='application/json'
        req=urllib.request.Request(self.base+path,data=data,headers=headers)
        try:
            with self.opener.open(req,timeout=self.timeout if timeout is None else timeout) as r:
                raw=r.read(2*1024*1024+1)
                if len(raw)>2*1024*1024:raise ValueError('Cider reply too large')
                value=json.loads(raw)
        except urllib.error.HTTPError as e:
            code=e.code;e.close()
            raise OSError('Cider HTTP '+str(code)) from None
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
        volume=self.request('/api/v1/playback/volume').get('volume',0)
        repeat=self.request('/api/v1/playback/repeat-mode').get('value',0)
        shuffle=self.request('/api/v1/playback/shuffle-mode').get('value',0)
        artwork=info.get('artwork') or {};params=info.get('playParams') or {}
        duration=max(0,int(info.get('durationInMillis') or 0))
        position=min(duration,max(0,int(float(timing.get('currentTime',timing.get('currentPlaybackTime',0)))*1000)))
        return dict(id=str(params.get('id') or info.get('id') or ''),title=info.get('name',''),artist=info.get('artistName',''),
                    album=info.get('albumName',''),duration=duration,
                    position=position,rate=1000 if playing else 0,
                    volume=max(0,min(100,round(float(volume)*100))),repeat=int(repeat or 0),shuffle=int(shuffle or 0),
                    art=art_id(artwork.get('url','')),template=artwork.get('url',''),bg=str(artwork.get('bgColor','181818'))[:6])
    def control(self,command,value=0):
        if command in ('seek','volume') and not math.isfinite(float(value)):
            raise ValueError('Nonfinite Cider value')
        routes={'toggle':('playpause',{}),'next':('next',{}),'previous':('previous',{}),
                'seek':('seek',{}),'volume':('volume',{})}
        if command=='play-item':
            kind,key=str(value).split(':',1)
            if not re.fullmatch(r'[a-z][a-z0-9-]{0,31}',kind) or not re.fullmatch(r'[\w.:-]{1,128}',key):raise ValueError('Invalid play item')
            route,body='play-item',dict(type=kind,id=key)
        else:
            if command not in routes:raise ValueError('Unknown Cider control')
            route,body=routes[command]
            if command=='seek':body={'position':max(0,float(value))/1000}
            if command=='volume':body={'volume':max(0,min(100,float(value)))/100}
        # A control timeout is never retried: toggle/skip are not idempotent.
        return self.request('/api/v1/playback/'+route,body)
    def library(self,kind,offset,limit):
        return self.request('/api/v1/amapi/run-v3',{'path':f'/v1/me/library/{kind}?limit={limit}&offset={offset}'},timeout=3)


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
    def __init__(self,client=None,cache=None,fetch=None):
        self.client=client or Client(os.environ.get('CIDER_API_URL','http://127.0.0.1:10767'),os.environ.get('CIDER_API_TOKEN'))
        self.fetch=fetch or urllib.request.urlopen
        self.cache=cache or Cache();self.scheduler=Scheduler();self.lock=threading.RLock()
        self.stop=threading.Event();self.requests={};self.jobs=queue.PriorityQueue(32);self.controls=queue.Queue(16)
        self.state=None;self.available=False;self.reason='Starting';self.state_seq=0;self.last_state=0
        self.opened=False;self.state_dirty=False;self.library_sent=0;self.wake=threading.Event();self.failures=0;self.templates={};self.generation=0;self.threads=[]
        self.index=Index(self.cache,self.client)
    def start(self):
        for target,name in [(self._poll,'cider-state'),(self._work,'cider-art'),(self._control,'cider-control'),(self._index,'cider-index')]:
            t=threading.Thread(target=target,name=name,daemon=True);t.start();self.threads.append(t)
    def close(self):
        self.stop.set()
        for t in self.threads:t.join(timeout=3)
        if not any(t.is_alive() for t in self.threads):self.cache.close()
    def disconnect(self):
        with self.lock:
            self.opened=False;self.generation=0;self.requests.clear();self._drop_jobs();self.scheduler.reset(0)
    def command(self,payload):
        try:
            fields=payload.decode().split('\t');name=fields[0]
            if name not in ('ci','cg','cr','cx','cc','cp','ct','cb'):return False
            with self.lock:
                if name=='ci':
                    if len(fields)!=3 or fields[1]!='1':raise ValueError('Cider protocol')
                    window=int(fields[2]);self.generation=secrets.randbelow(0xfffffffe)+1;self.scheduler.reset(self.generation,window)
                    self.requests.clear();self._drop_jobs();self.opened=True;self.library_sent=0;self.last_state=0;self._state(True)
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
                    req=int(fields[2]);self.controls.put_nowait((self.generation,req,fields[3],fields[4]))
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
                elif name=='ct':self.index.request()
            return True
        except (ValueError,UnicodeError,queue.Full):
            log.warning('Invalid or full Cider request')
            return True
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
                with self.lock:
                    changed=not self.available or published is None or any(state[k]!=published[k] for k in state if k not in ('position','template'))
                    predicted=published['position']+(now-anchor)*published['rate'] if published else 0
                    changed |= abs(state['position']-predicted)>2000 or now-self.last_state>=15
                    self.available=True;self.reason='';self.state=state
                    if state['template']:
                        self.templates[state['art']]=state['template']
                        if len(self.templates)>128:self.templates.pop(next(iter(self.templates)))
                    if changed:published=dict(state);anchor=now;self._state()
                self.failures=0;next_poll=now+1
            except (OSError,ValueError,TimeoutError) as error:
                with self.lock:
                    if self.available or self.reason=='Starting':self.available=False;self.reason=clean(str(error),80) or 'Cider unavailable';self._state()
                self.failures=min(8,self.failures+1);next_poll=now+min(30,2**(self.failures-1))
    def _index(self):
        while not self.stop.is_set():self.index.step();self.stop.wait(.25)
    def _control(self):
        while not self.stop.is_set():
            try:generation,req,command,value=self.controls.get(timeout=.2)
            except queue.Empty:continue
            with self.lock:
                if generation!=self.generation or not self.opened:continue
            try:
                if not self.available:raise OSError('Cider unavailable')
                self.client.control(command,value);ok=True;text='OK';self.wake.set()
            except (OSError,ValueError,TimeoutError):ok=False;text='Control failed; not retried'
            with self.lock:
                if generation==self.generation and self.opened:self.scheduler.push(0,2,f'CK\t{generation}\t{req}\t{int(ok)}\t{text}'.encode())
    def artwork(self,key,tier):
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
    def _work(self):
        while not self.stop.is_set():
            try:priority,distance,order,generation,req,key,tier,offset,token=self.jobs.get(timeout=.2)
            except queue.Empty:continue
            started=time.monotonic()
            with self.lock:
                if generation!=self.generation or self.requests.get(req) is not token:continue
            try:
                raw=pack_xs(generation,req,[(k,self.artwork(k,'XS')) for k in key]) if isinstance(key,tuple) else self.artwork(key,tier)
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
                send(*frame)
