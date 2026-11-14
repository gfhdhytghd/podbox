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
    calls=[];fail=False;library_items=None
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
        elif self.path.endswith('repeat-mode') or self.path.endswith('shuffle-mode'):value={'value':0}
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
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.cache=hrcider.Cache(self.tmp.name,art_limit=150000);Handler.calls=[];Handler.fail=False;Handler.library_items=None
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
