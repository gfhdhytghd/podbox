import re
"""Cider control is exercised only against this loopback mock, never the user app."""
from pathlib import Path
import sys,unittest,tempfile,threading,json,time,io,zlib,struct
from unittest import mock
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import http.client,urllib.request,urllib.error,urllib.parse
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import hrcider

class Handler(BaseHTTPRequestHandler):
    calls=[];fail=False;library_items=None;queue={'ids':[],'pos':0};collections={};modes={'repeat':0,'shuffle':0}
    def log_message(self,*args):pass
    def do_GET(self): self.respond()
    def do_POST(self): self.respond()
    def do_PUT(self): self.respond()
    def respond(self):
        body=self.rfile.read(int(self.headers.get('Content-Length',0)))
        self.calls.append((self.command,self.path,json.loads(body) if body else None,self.headers.get('apptoken')))
        if self.fail:self.send_error(503);return
        if self.path.startswith('/art/'):
            from PIL import Image
            out=io.BytesIO();Image.new('RGB',(240,240),(40,60,180)).save(out,format='PNG')
            raw=out.getvalue();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
        if self.path.endswith('now-playing'):value={'info':{'name':'Title','artistName':'Artist','albumName':'Album','durationInMillis':100000,'playParams':{'id':'song-1'},'artwork':{'url':'https://example.test/{w}x{h}bb.jpg','bgColor':'123456'}}}
        elif self.path.endswith('is-playing'):value={'is_playing':True}
        elif self.path.endswith('/time'):value={'data':{'currentTime':12,'duration':100,'remaining':88}}
        elif self.path.endswith('/volume'):value={'volume':.5}
        elif self.path.startswith('/api/v2/queue') or self.path.endswith('play-collection') or self.path.endswith('play-item-href'):
            q=self.queue;data=json.loads(body) if body else {}
            track=lambda i:{'track':{'id':'cat-'+i,'attributes':{'playParams':{'id':i}}}}
            if self.path.endswith('play-collection') and (data['type'],data['id']) not in self.collections:self.send_error(404);return
            if self.path.endswith('play-collection'):q['ids']=list(self.collections[(data['type'],data['id'])]);q['pos']=0;value={'data':{}}
            elif self.path.endswith('play-item-href'):q['ids']=[self.path.rsplit('/',1)[1] if False else data['href'].rsplit('/',1)[1]];q['pos']=0;value={'status':'ok'}
            elif self.path.endswith('/queue/position'):value={'data':{'position':q['pos'],'total':len(q['ids'])}}
            elif self.path.endswith('/queue/jump'):q['pos']=data['index'];value={'data':{}}
            elif self.path.endswith('/queue/add-later'):q['ids'].append(data['id']);value={'data':{}}
            elif self.path.endswith('/queue/move'):
                cur=q['ids'][q['pos']];item=q['ids'].pop(data['from']);q['ids'].insert(data['to'],item);q['pos']=q['ids'].index(cur);value={'data':{}}
            else:
                qs=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query);off=int(qs['offset'][0]);lim=int(qs['limit'][0])
                value={'data':{'items':[track(i) for i in q['ids'][off:off+lim]],'position':q['pos']}}
            self.calls[-1]=self.calls[-1]
        elif self.path.startswith('/api/v2/playback/') and self.command=='PUT':
            data=json.loads(body)
            if self.path.endswith('/repeat'):self.modes['repeat']={'none':0,'one':1,'all':2}[data['mode']]
            if self.path.endswith('/shuffle'):self.modes['shuffle']=int(data['enabled'])
            value={'data':data}
        elif self.path.endswith('repeat-mode'):value={'value':self.modes['repeat']}
        elif self.path.endswith('shuffle-mode'):value={'value':self.modes['shuffle']}
        elif self.path.endswith('run-v3'):
            path=json.loads(body)['path'];query=urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
            offset=int(query.get('offset',['0'])[0]);limit=int(query.get('limit',['50'])[0])
            items=self.library_items if self.library_items is not None else [{'id':'a1','type':'library-albums','attributes':{'name':'Album','artistName':'Artist','dateAdded':'2026-01-01','trackCount':2}}]
            if re.search(r'/(?:songs|albums|playlists)/[^/?]+$',path):
                key=path.rsplit('/',1)[1];items=[{'id':key,'attributes':{'name':'Selected','playParams':{'id':key}}}]
            value={'data':items[offset:offset+limit],'meta':{'total':len(items)}}
            if offset+limit<len(items):value['next']=path
        else:value={'status':'ok'}
        raw=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)

class LocalOpener:
    def __init__(self,base):self.base=base
    def open(self,request,timeout=1):
        url=request.full_url if hasattr(request,'full_url') else request
        assert url.startswith(self.base+'/'), 'Only the mock Cider server is allowed'
        parsed=urllib.parse.urlsplit(url)
        connection=http.client.HTTPConnection('127.0.0.1',parsed.port,timeout=timeout)
        data=getattr(request,'data',None);headers=dict(getattr(request,'headers',{}))
        connection.request(request.get_method() if hasattr(request,'get_method') else 'GET',parsed.path+('?' + parsed.query if parsed.query else ''),data,headers)
        response=connection.getresponse()
        if response.status>=400:
            response.close();connection.close()
            raise urllib.error.HTTPError(url,response.status,'mock failure',{},None)
        return response

class Cider(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start();cls.url='http://127.0.0.1:'+str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.cache=hrcider.Cache(self.tmp.name,art_limit=150000);Handler.calls=[];Handler.fail=False;Handler.library_items=None;Handler.queue={'ids':[],'pos':0};Handler.collections={};Handler.modes={'repeat':0,'shuffle':0}
    def tearDown(self):self.cache.close();self.tmp.cleanup()
    def client(self,**kwargs):return hrcider.Client(self.url,opener=LocalOpener(self.url),**kwargs)
    def fetch(self,url,timeout):return LocalOpener(self.url).open(url,timeout)
    def test_routes_auth_poll_and_no_auto_controls(self):
        c=self.client(token='mock-token');state=c.snapshot()
        self.assertEqual((state['id'],state['position'],state['volume']),('song-1',12000,0))
        self.assertFalse(any(m=='POST' for m,*_ in Handler.calls))
        for cmd,value in [('toggle',0),('next',0),('previous',0),('seek',12000),('play-item','songs:song-1')]:c.control(cmd,value)
        posts=[v for v in Handler.calls if v[0]=='POST']
        self.assertEqual([p[1] for p in posts],['/api/v1/playback/'+x for x in ['playpause','next','previous','seek']]+['/api/v1/amapi/run-v3','/api/v1/playback/play-item-href'])
        self.assertEqual(posts[3][2],{'position':12.0});self.assertEqual(posts[-1][2],{'href':'/v1/catalog/$STOREFRONT/songs/song-1'})
        with self.assertRaises(ValueError):c.control('volume',55)
        self.assertFalse(any(path.endswith('/volume') for _,path,*_ in Handler.calls))
        self.assertTrue(all(p[3]=='mock-token' for p in Handler.calls))
    def test_shuffle_repeat_set_explicit_values(self):
        c=self.client(token='t')
        for cmd,value in [('shuffle',1),('shuffle','0'),('repeat',0),('repeat','1'),('repeat',2)]:c.control(cmd,value)
        calls=[v[:3] for v in Handler.calls]
        self.assertEqual(calls,[('PUT','/api/v2/playback/shuffle',{'enabled':True}),('PUT','/api/v2/playback/shuffle',{'enabled':False}),
            ('PUT','/api/v2/playback/repeat',{'mode':'none'}),('PUT','/api/v2/playback/repeat',{'mode':'one'}),('PUT','/api/v2/playback/repeat',{'mode':'all'})])
        n=len(Handler.calls)
        for cmd,value in [('shuffle',2),('shuffle',-1),('repeat',3),('repeat','x'),('repeat','')]:
            with self.assertRaises(ValueError):c.control(cmd,value)
        self.assertEqual(len(Handler.calls),n)
        Handler.fail=True
        with self.assertRaises(OSError):c.control('repeat',2)
    def lib(self):
        from hrcider_library import Library
        return Library(self.cache,self.client())
    def posts(self):return [(v[1].split('/api/')[1],v[2]) for v in Handler.calls if v[0]=='POST' and 'amapi' not in v[1]]
    def test_song_in_album_plays_the_whole_album_from_that_song(self):
        Handler.collections[('library-albums','al1')]=['t1','t2','t3','t4','t5']
        lib=self.lib();lib.set_context('2|album/al1');r=lib.play('play/library-songs/t3')
        self.assertEqual(Handler.queue,{'ids':['t1','t2','t3','t4','t5'],'pos':2})
        self.assertEqual(self.posts(),[('v2/playback/play-collection',{'type':'library-albums','id':'al1'}),('v2/queue/jump',{'index':2})])
        self.assertIn('t3',r['expected_ids'])
        # first row: no jump; catalog album and playlist routes pick the catalog/library collection types
        Handler.calls=[];Handler.collections[('albums','9')]=['a','b'];lib.set_context('0|album/catalog-us/9');lib.play('play/songs/a')
        self.assertEqual(self.posts(),[('v2/playback/play-collection',{'type':'albums','id':'9'})]);self.assertEqual(Handler.queue['pos'],0)
        Handler.calls=[];Handler.collections[('library-playlists','p.1')]=['x','y','z'];lib.set_context('1|playlist/p.1');lib.play('play/library-songs/y')
        self.assertEqual((Handler.queue['ids'],Handler.queue['pos']),(['x','y','z'],1))
        # Cider dropped an unplayable track: the song is found by id, not by row number
        Handler.collections[('library-albums','al2')]=['q1','q3','q4'];lib.set_context('2|album/al2');lib.play('play/library-songs/q4')
        self.assertEqual(Handler.queue['pos'],2);lib.set_context('3|album/al2');lib.play('play/library-songs/q3')
        self.assertEqual(Handler.queue['pos'],1)
    def test_song_in_plain_list_queues_a_window_around_it(self):
        lib=self.lib();rows=[dict(id='s%d'%i,route='play/library-songs/s%d'%i) for i in range(8)]
        calls=[]
        def page(route,offset,limit):calls.append((route,offset,limit));return dict(items=rows[offset:offset+limit])
        lib.page=page
        lib.set_context('3|songs');r=lib.play('play/library-songs/s3')
        self.assertEqual(calls,[('songs',0,26)]);self.assertEqual(Handler.queue,{'ids':['s3'],'pos':0})
        self.assertTrue(r['fill'](threading.Event()))
        self.assertEqual(Handler.queue,{'ids':['s0','s1','s2','s3','s4','s5','s6','s7'],'pos':3})
        # first song of a list: nothing before; last of a one-row list: plain play
        lib.set_context('0|songs');lib.play('play/library-songs/s0').get('fill')(threading.Event())
        self.assertEqual((Handler.queue['ids'],Handler.queue['pos']),(['s0','s1','s2','s3','s4','s5','s6','s7'],0))
        lib.page=lambda route,offset,limit:dict(items=rows[:1]);lib.set_context('0|songs');self.assertNotIn('fill',lib.play('play/library-songs/s0'))
        # a cancelled fill adds nothing more
        lib.page=page;lib.set_context('3|songs');r=lib.play('play/library-songs/s3');stop=threading.Event();stop.set()
        self.assertFalse(r['fill'](stop));self.assertEqual(Handler.queue['ids'],['s3'])
    def test_context_is_optional_stale_or_bad_and_never_blocks_the_song(self):
        lib=self.lib()
        lib.play('play/library-songs/solo');self.assertEqual(Handler.queue['ids'],['solo']);self.assertNotIn('v2/playback/play-collection',[p for p,_ in self.posts()])
        for bad in ('x|album/a','-1|album/a','2|','2','99999999|songs'):
            lib.set_context(bad);self.assertIsNone(lib.context)
        lib.set_context('1|album/zzz');lib.play('play/library-songs/lone')   # unknown collection: the song still plays
        self.assertEqual(Handler.queue['ids'],['lone'])
        lib.set_context('1|album/zzz');lib.context=(1,'album/zzz',time.monotonic()-60);Handler.calls=[];lib.play('play/library-songs/old')
        self.assertFalse(any('play-collection' in v[1] for v in Handler.calls))
        # context is single-use
        Handler.collections[('library-albums','al1')]=['t1','t2'];lib.set_context('1|album/al1');lib.play('play/library-songs/t2');Handler.calls=[];lib.play('play/library-songs/t1')
        self.assertFalse(any('play-collection' in v[1] for v in Handler.calls))
    def test_bridge_native_context_then_play_queues_the_album(self):
        Handler.collections[('library-albums','al1')]=['t1','t2','song-1','t4']
        b=hrcider.Bridge(client=self.client(),cache=self.cache,fetch=self.fetch,volume=mock.Mock(read=mock.Mock(return_value=(42,False))))
        b.start()
        try:
            end=time.monotonic()+4
            while not b.available and time.monotonic()<end:time.sleep(.01)
            b.command(b'ci\t1\t131072');gen=b.generation
            b.command(f'cc\t{gen}\t7\tnative-context\t2|album/al1'.encode());b.command(f'cc\t{gen}\t8\tnative-play\tplay/library-songs/song-1'.encode())
            frames=[];end=time.monotonic()+5
            while sum(1 for k,p in frames if p.startswith(b'CK'))<2 and time.monotonic()<end:
                b.poll(lambda *v:frames.append(v));time.sleep(.01)
            acks={p.split(b'\t')[2]:p.split(b'\t')[3] for k,p in frames if p.startswith(b'CK')}
            self.assertEqual(acks,{b'7':b'1',b'8':b'1'});self.assertEqual(Handler.queue,{'ids':['t1','t2','song-1','t4'],'pos':2})
        finally:
            b.close();self.cache=hrcider.Cache(self.tmp.name)
    def test_repeat5_native_modes_map_to_cider(self):
        c=self.client()
        puts=lambda:[v[1].rsplit('/',1)[1]+':'+json.dumps(v[2],sort_keys=True) for v in Handler.calls if v[0]=='PUT']
        # Rockbox order 0 off, 1 all, 2 one; Cider's own numbers differ (1 one, 2 all).
        for n,mode,real in [(0,'none',0),(1,'all',2),(2,'one',1)]:
            Handler.calls=[];c.control('repeat5',n);self.assertEqual(puts(),['repeat:{"mode": "%s"}'%mode])
            s=c.snapshot();self.assertEqual((s['repeat5'],s['repeat'],s['shuffle']),(n,real,0))
        for bad in (5,-1,'x',''):
            with self.assertRaises(ValueError):c.control('repeat5',bad)
        # Shuffle mode: repeat all + shuffle on, reported as 3, legacy field stays real
        Handler.calls=[];c.control('repeat5',3)
        self.assertEqual(puts(),['repeat:{"mode": "all"}','shuffle:{"enabled": true}'])
        s=c.snapshot();self.assertEqual((s['repeat5'],s['repeat'],s['shuffle']),(3,2,1))
        c.control('repeat5',0);Handler.calls=[]
        s=c.snapshot();self.assertEqual((s['repeat5'],s['shuffle']),(0,0),'leaving restores the earlier shuffle')
        # The user changes Cider itself: the virtual mode collapses to the real one
        c.control('repeat5',3);Handler.modes['shuffle']=0
        self.assertEqual(c.snapshot()['repeat5'],1)
        c.control('repeat5',3);Handler.modes['repeat']=1
        self.assertEqual(c.snapshot()['repeat5'],2)
        # A failed RPC leaves no virtual mode behind
        Handler.fail=True
        with self.assertRaises(OSError):c.control('repeat5',3)
        Handler.fail=False;self.assertEqual(c.snapshot()['repeat5'],2)
    def test_ab_repeat_markers_seek_and_clear(self):
        c=self.client();st=lambda pos,id_='song-1':{'id':id_,'position':pos,'rate':1000}
        with self.assertRaises(ValueError):c.control('ab-mark',1000)
        c.control('repeat5',4);self.assertEqual(c.snapshot()['repeat5'],4)
        self.assertFalse(c.ab_active);self.assertIsNone(c.ab_tick(st(99999)))
        c.control('ab-mark',10000)
        with self.assertRaises(ValueError):c.control('ab-mark',10000)
        Handler.calls=[];c.control('ab-mark',20000)
        self.assertEqual([(v[1],v[2]) for v in Handler.calls],[('/api/v1/playback/seek',{'position':10.0})])
        self.assertTrue(c.ab_active)
        self.assertIsNone(c.ab_tick(st(19999)));self.assertEqual(c.ab_tick(st(20000)),10000)
        self.assertIsNone(c.ab_tick({'id':'song-1','position':25000,'rate':0}))
        # a new track clears the markers; the mode stays
        c.snapshot();self.assertTrue(c.ab_active)
        self.assertEqual(c._mode5(*c.real,'other'),4)
        self.assertFalse(c.ab_active)
        c.control('ab-mark',1000);c.control('ab-mark',3000);c.control('ab-mark',0);self.assertFalse(c.ab_active)
        # leaving A-B, or the user touching Cider, drops it
        c.control('ab-mark',1000);c.control('repeat5',1);self.assertEqual(c.snapshot()['repeat5'],1)
        with self.assertRaises(ValueError):c.control('ab-mark',1000)
        c.control('repeat5',4);Handler.modes['shuffle']=1;self.assertEqual(c.snapshot()['repeat5'],1)
    def test_cn_carries_repeat5_only_when_advertised(self):
        for caps,count in ((b'cn18,modes5',19),(b'cn18',18)):
            b=hrcider.Bridge(client=self.client(),cache=self.cache,fetch=self.fetch,volume=mock.Mock(read=mock.Mock(return_value=(42,False))))
            b.start()
            try:
                end=time.monotonic()+4
                while not b.available and time.monotonic()<end:time.sleep(.01)
                b.command(b'ci\t1\t131072\t'+caps)
                frames=[];end=time.monotonic()+4
                while not any(p.startswith(b'CN\t') for k,p in frames) and time.monotonic()<end:
                    b.poll(lambda *v:frames.append(v));time.sleep(.01)
                fields=next(p for k,p in frames if p.startswith(b'CN\t')).split(b'\t')
                self.assertEqual(len(fields),count)
                if count==19:self.assertEqual(fields[18],b'0')
            finally:
                b.close();self.cache=hrcider.Cache(self.tmp.name)
    def test_bridge_routes_shuffle_repeat_and_acks(self):
        b=hrcider.Bridge(client=self.client(),cache=self.cache,fetch=self.fetch,volume=mock.Mock(read=mock.Mock(return_value=(42,False))))
        b.start()
        try:
            end=time.monotonic()+4
            while not b.available and time.monotonic()<end:time.sleep(.01)
            b.command(b'ci\t1\t131072');gen=b.generation
            b.command(f'cc\t{gen}\t4\tshuffle\t1'.encode());b.command(f'cc\t{gen}\t5\trepeat\t2'.encode());b.command(f'cc\t{gen}\t6\trepeat\t9'.encode())
            frames=[];end=time.monotonic()+4
            while sum(1 for k,p in frames if p.startswith(b'CK'))<3 and time.monotonic()<end:
                b.poll(lambda *v:frames.append(v));time.sleep(.01)
            acks={p.split(b'\t')[2]:p.split(b'\t')[3] for k,p in frames if p.startswith(b'CK')}
            self.assertEqual(acks,{b'4':b'1',b'5':b'1',b'6':b'0'})
            puts=[v[1:3] for v in Handler.calls if v[0]=='PUT']
            self.assertIn(('/api/v2/playback/shuffle',{'enabled':True}),puts);self.assertIn(('/api/v2/playback/repeat',{'mode':'all'}),puts)
        finally:
            b.close();self.cache=hrcider.Cache(self.tmp.name)
    def test_single_item_kind_passes_through_to_rpc(self):
        self.client().control('play-item','song:song-2')
        self.assertEqual(Handler.calls[-1][2],{'href':'/v1/catalog/$STOREFRONT/songs/song-2'})
    def test_native_columns_with_nonuniform_pixels(self):
        from PIL import Image
        image=Image.new('RGB',(240,240),(255,0,0))
        image.paste((0,0,255),(120,0,240,240))
        out=io.BytesIO();image.save(out,format='PNG')
        data=hrcider.render(out.getvalue(),'M')
        self.assertEqual(struct.unpack_from('<H',data,12+2*(5*128+100))[0],0xf800)
        self.assertEqual(struct.unpack_from('<H',data,12+2*(100*128+5))[0],0x001f)

    def test_empty_queue_never_retains_old_progress(self):
        c=self.client();c.request=mock.Mock(side_effect=[{'info':None},{'is_playing':False},{'data':{'currentTime':174.5}},{'value':0},{'value':0}])
        state=c.snapshot()
        self.assertEqual((state['duration'],state['position'],state['rate']),(0,0,0))

    def test_real_v2_current_time_field(self):
        state=self.client().snapshot()
        self.assertEqual(state['position'],12000)

    def test_credit_priorities_cancel_generation_resume_crc(self):
        s=hrcider.Scheduler();s.reset(1,24576)
        raw=b'x'*32780;s.add_art(1,'a'*64,'M',raw,0,3,4)
        self.assertIsNone(s.next_frame())
        s.grant(24576);s.push(0,2,b'CK\t1\t7\tok');self.assertEqual(s.next_frame(),(2,b'CK\t1\t7\tok'))
        kind,payload=s.next_frame();frame=hrcider.decode_chunk(payload);self.assertEqual(frame['offset'],0);self.assertEqual(zlib.crc32(frame['data']),frame['chunk_crc'])
        self.assertLessEqual(s.sent_bytes,24576)
        s.cancel(1);self.assertIsNone(s.next_frame())
        offset=frame['offset']+len(frame['data']);s.add_art(2,'a'*64,'M',raw,offset,3,0);s.grant(24576)
        frame2=hrcider.decode_chunk(s.next_frame()[1]);self.assertEqual(frame2['offset'],offset)
        s.reset(2,131072);self.assertIsNone(s.next_frame());self.assertEqual(s.credit,0)
    def test_cap_native_aat_and_xs_batch(self):
        self.assertEqual(hrcider.capped_url('https://example.test/{w}x{h}bb.jpg'),'https://example.test/240x240bb.jpg')
        self.assertEqual(hrcider.capped_url('https://example.test/4000x4000bb.jpg'),'https://example.test/240x240bb.jpg')
        from PIL import Image
        out=io.BytesIO();Image.new('RGB',(240,240),(255,0,0)).save(out,format='PNG');raw=out.getvalue()
        for tier,dim in hrcider.SIZES.items():
            art=hrcider.render(raw,tier);self.assertEqual(len(art),12+dim*dim*2)
            self.assertEqual(struct.unpack('<6H',art[:12]),(0x5441,8,dim,dim,int(tier=='M'),0))
            self.assertEqual(art[12:14],b'\x00\xf8')
        xs=hrcider.pack_xs(1,2,[('a'*64,hrcider.render(raw,'XS'))]*4)
        self.assertLessEqual(len(xs),12288)
        self.assertEqual(len(hrcider.unpack_xs(xs)[2]),4)
    def test_cache_index_stale_eviction_schema_concurrent(self):
        c=self.client();index=hrcider.Index(self.cache,c,interval=0)
        index.step();gen=self.cache.generation
        page=self.cache.page('albums',0,50,stale=True);self.assertTrue(page['stale']);self.assertEqual(page['items'][0]['id'],'a1')
        index.step();self.assertGreaterEqual(self.cache.generation,gen)
        self.cache.art_limit=50000
        native=struct.pack('<6H',0x5441,8,128,128,1,0)+b'a'*32768
        self.cache.put_art('a'*64,'M',native);self.cache.put_art('b'*64,'M',native)
        self.assertIsNone(self.cache.get_art('a'*64,'M'));self.assertEqual(len(self.cache.get_art('b'*64,'M')),32780)
        errors=[]
        def worker():
            try:
                for _ in range(30):self.cache.page('albums',0,50)
            except Exception as e:errors.append(e)
        ts=[threading.Thread(target=worker) for _ in range(4)]
        for t in ts:t.start()
        for t in ts:t.join()
        self.assertEqual(errors,[])
    def test_index_incremental_change_delete_and_on_demand(self):
        index=hrcider.Index(self.cache,self.client(),interval=0)
        for now in range(4):index.step(now)
        self.assertFalse(index.initial);gen=self.cache.generation
        Handler.library_items=[{'id':'a1','attributes':{'name':'Changed','dateAdded':'2026-11-13','trackCount':4}}]
        index.step(200);self.assertGreater(self.cache.generation,gen)
        self.assertEqual(self.cache.page('albums')['items'][0]['title'],'Changed')
        for now in range(201,204):index.step(now)
        Handler.library_items=[];index.step(400)
        self.assertEqual(self.cache.page('albums')['total'],0)
        index.request();index.step(401);self.assertTrue(index.force)
    def test_oversize_original_never_persists(self):
        from PIL import Image
        out=io.BytesIO();Image.new('RGB',(241,240)).save(out,format='PNG')
        with self.assertRaises(ValueError):self.cache.put_art('a'*64,'original',out.getvalue())
        self.assertIsNone(self.cache.get_art('a'*64,'original'))
    def test_state_coalescing_and_library_generation(self):
        b=hrcider.Bridge(client=self.client(),cache=self.cache,fetch=self.fetch,volume=mock.Mock(read=mock.Mock(return_value=(42,False))))
        b.opened=True;b.generation=42;b.scheduler.reset(42)
        b.state=self.client().snapshot();now=[100.]
        with mock.patch.object(hrcider.time,'monotonic',side_effect=lambda:now[0]):
            b.available=False;b._state(True);sent=[];b.poll(lambda *v:sent.append(v))
            now[0]=100.01;b.available=True;b._state();self.assertTrue(b.state_dirty)
            now[0]=100.3;b.poll(lambda *v:sent.append(v));self.assertFalse(b.state_dirty)
        self.assertEqual(sum(p.startswith(b'CN\t') for _,p in sent),1)
        self.assertTrue(any(p.startswith(b'CG\t') for _,p in sent))
    def test_compact_library_page_and_request_bounds(self):
        self.cache.ingest('albums',[{'id':str(i),'attributes':{'name':'中'*200,'artistName':'Artist'}} for i in range(50)],1)
        payload=hrcider.page_frame(1,2,self.cache.page('albums'))
        self.assertLessEqual(len(payload),12288)
        self.assertEqual(int(payload.split(b'\n')[0].split(b'\t')[-1]),len(payload.split(b'\n'))-1)
        b=hrcider.Bridge(client=self.client(),cache=self.cache,fetch=self.fetch,volume=mock.Mock(read=mock.Mock(return_value=(42,False))));b.command(b'ci\t1\t24576')
        command=f'cr\t{b.generation}\t-1\t'+('a'*64)+'\tM\t0\t3\t0'
        self.assertTrue(b.command(command.encode()));self.assertFalse(b.requests)

    def test_art_cache_hit_stale_and_capped_download_no_token(self):
        b=hrcider.Bridge(client=self.client(token='mock-token'),cache=self.cache,fetch=self.fetch,volume=mock.Mock(read=mock.Mock(return_value=(42,False))))
        template=self.url+'/art/{w}x{h}bb.png';key=hrcider.art_id(template);b.templates[key]=template
        first=b.artwork(key,'M');self.assertEqual(len(first),32780)
        self.assertEqual(Handler.calls[-1],('GET','/art/240x240bb.png',None,None))
        calls=len(Handler.calls);Handler.fail=True
        self.assertEqual(b.artwork(key,'M'),first)
        self.assertEqual(len(b.artwork(key,'XS')),2060)
        self.assertEqual(len(Handler.calls),calls)
        for p in Path(self.tmp.name).iterdir():
            if p.is_file():self.assertNotIn(b'mock-token',p.read_bytes())
    def test_schema_rebuild_and_mixed_read_write_concurrency(self):
        self.cache.ingest('albums',[{'id':'x','attributes':{'name':'Old'}}],1)
        errors=[]
        def writer():
            try:
                for i in range(40):self.cache.ingest('albums',[{'id':'x','attributes':{'name':str(i)}}],2)
            except Exception as e:errors.append(e)
        t=threading.Thread(target=writer);t.start()
        for _ in range(80):self.cache.page('albums')
        t.join();self.assertEqual(errors,[])
        with self.cache.db:self.cache.db.execute('PRAGMA user_version=999')
        self.cache.close();self.cache=hrcider.Cache(self.tmp.name)
        self.assertEqual(self.cache.page('albums')['total'],0)
    def test_control_error_is_not_retried(self):
        Handler.fail=True;c=self.client()
        with self.assertRaises(OSError):c.control('toggle')
        self.assertEqual(len(Handler.calls),1)
    def test_bridge_workers_full_cover_xs_controls_and_cancel(self):
        b=hrcider.Bridge(client=self.client(),cache=self.cache,fetch=self.fetch,volume=mock.Mock(read=mock.Mock(return_value=(42,False))))
        b.start()
        try:
            end=time.monotonic()+4
            while not b.available and time.monotonic()<end:time.sleep(.01)
            self.assertTrue(b.available)
            b.command(b'ci\t1\t131072');gen=b.generation
            b.command(f'cg\t{gen}\t131072'.encode())
            from PIL import Image
            out=io.BytesIO();Image.new('RGB',(240,240),(20,40,60)).save(out,format='PNG')
            key=b.state['art'];self.cache.put_art(key,'original',out.getvalue())
            b.command(f'cr\t{gen}\t1\t{key}\tM\t0\t3\t0'.encode())
            frames=[];end=time.monotonic()+4
            while not any(k==3 for k,p in frames) and time.monotonic()<end:
                b.poll(lambda *v:frames.append(v));time.sleep(.005)
            b.poll(lambda *v:frames.append(v))
            chunks=[hrcider.decode_chunk(p) for k,p in frames if k==3]
            whole=b''.join(v['data'] for v in chunks)
            self.assertEqual(len(whole),32780);self.assertEqual(zlib.crc32(whole),chunks[0]['crc'])
            self.assertLessEqual(sum(len(p) for k,p in frames if k==3),131072)
            b.command(f'cc\t{gen}\t9\tvolume-step\t1'.encode())
            end=time.monotonic()+3
            while not b.volume.step.called and time.monotonic()<end:time.sleep(.01)
            b.volume.step.assert_called_with(1)
            self.assertFalse(any(path.endswith('/volume') for _,path,*_ in Handler.calls))
            b.command(f'cb\t{gen}\t2\t{key},{key}'.encode());frames=[];end=time.monotonic()+3
            while not any(k==3 and p.startswith(b'CXS1') for k,p in frames) and time.monotonic()<end:
                b.poll(lambda *v:frames.append(v));time.sleep(.005)
            self.assertEqual(len(hrcider.unpack_xs(next(p for k,p in frames if k==3 and p.startswith(b'CXS1')))[2]),2)
            b.command(f'cx\t{gen}\t0'.encode());self.assertFalse(b.requests)
            b.disconnect();self.assertIsNone(b.scheduler.next_frame())
        finally:
            b.close()
            # close owns its cache; teardown closes a fresh handle.
            self.cache=hrcider.Cache(self.tmp.name)

    def test_window_contiguous_center_and_cancel(self):
        w=hrcider.Window();self.assertEqual(w.update(10,0),[10,9,11,8,12,7,13,6,14]);self.assertEqual(w.tier(20),'XS');self.assertEqual(w.tier(0),'M')
        self.assertFalse(set(w.update(30,20))&set(range(6,15)))

if __name__=='__main__':unittest.main()
