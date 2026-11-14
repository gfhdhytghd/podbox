"""Bounded real worker prefetch, Cider resource playback, and queue neighbors."""
import io
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import hrcider

class Prefetch(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        image=io.BytesIO();Image.new('RGB',(240,240),(30,80,140)).save(image,format='PNG')
        self.raw=image.getvalue();self.tmp=tempfile.TemporaryDirectory()
        self.cache=hrcider.Cache(self.tmp.name)
        self.client=Mock();self.client.request.return_value=[]
        self.bridge=hrcider.Bridge(client=self.client,cache=self.cache,volume=Mock())
        self.bridge.opened=True;self.bridge.generation=7
        self.bridge.capabilities=frozenset({'cn18','neighbors','warm','play-ack'})
    def tearDown(self):
        self.bridge.close();self.cache.close();self.tmp.cleanup()
    def workers(self):
        for _ in range(4):
            t=threading.Thread(target=self.bridge._prefetch_worker);t.start();self.bridge.threads.append(t)
    def test_visible_window_downloads_in_parallel_and_deduplicates_tiers(self):
        entered=[];lock=threading.Lock();release=threading.Event();four=threading.Event()
        def fetch(url,timeout):
            with lock:
                entered.append(url)
                if len(entered)==4:four.set()
            self.assertTrue(release.wait(2));return io.BytesIO(self.raw)
        self.bridge.fetch=fetch
        keys=[];buckets=set()
        for i in range(100):
            template=f'https://example.test/{i}/{{w}}x{{h}}bb.png';key=hrcider.art_id(template)
            bucket=int(key[:2],16)%32
            if bucket in buckets:continue
            buckets.add(bucket);keys.append(key);self.bridge.templates[key]=template
            if len(keys)==4:break
        self.workers();started=time.monotonic()
        try:
            self.assertTrue(self.bridge.command(('cw\t7\tM\t'+','.join(keys)).encode()))
            self.assertTrue(four.wait(2),'visible art fetches must overlap')
        finally:release.set()
        deadline=time.monotonic()+4
        while time.monotonic()<deadline and any(self.cache.get_art(k,'M') is None for k in keys):time.sleep(.01)
        self.assertTrue(all(self.cache.get_art(k,'M') for k in keys))
        for key in keys:self.bridge.artwork(key,'XS')
        self.assertTrue(all(self.cache.get_art(k,'XS') for k in keys));self.assertEqual(len(entered),4)
        for key in keys:self.bridge.artwork(key,'M')
        self.assertEqual(len(entered),4)
        print(f'prefetch: 4 concurrent cold originals, XS/M reuse, total_ms={(time.monotonic()-started)*1000:.1f}')
    def test_rapid_visible_windows_keep_queue_bounded(self):
        for i in range(2000):
            keys=[f'{(i*7+j):064x}' for j in range(7)]
            self.bridge.command(('cw\t7\tM\t'+','.join(keys)).encode())
            self.assertLessEqual(self.bridge.prefetch.qsize(),7)
            self.assertLessEqual(len(self.bridge.warm_jobs),7)
        before=self.bridge.warm_window.copy()
        self.bridge.command(('cw\t7\tM\t'+','.join(['a'*64]*8)).encode())
        self.assertEqual(self.bridge.warm_window,before)
    def test_queue_neighbors_follow_actual_queue_not_library_sort(self):
        self.bridge.state={'id':'catalog-b'}
        self.bridge.warm_window={('a'*64,'L')}
        def row(key):return {'id':key,'attributes':{'artwork':{'url':f'https://example.test/{key}/{{w}}x{{h}}bb.png'},'playParams':{'id':key}}}
        self.client.request.return_value=[row('previous'),row('catalog-b'),row('next')]
        t=threading.Thread(target=self.bridge._neighbors);t.start();self.bridge.threads.append(t)
        deadline=time.monotonic()+2
        while self.bridge.neighbors is None and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(self.bridge.neighbors,(7,'catalog-b',hrcider.art_id(row('previous')['attributes']['artwork']['url']),hrcider.art_id(row('next')['attributes']['artwork']['url'])))
        frame=self.bridge.scheduler.next_frame();self.assertEqual(frame[1],('CP\t'+'\t'.join(map(str,self.bridge.neighbors))).encode())
        self.assertEqual(len(self.bridge.warm_queue),2)
    def test_resource_play_uses_complete_href_instead_of_lightweight_id(self):
        client=hrcider.Client();client.request=Mock(side_effect=[{'data':[{'id':'i.song','attributes':{'playParams':{'id':'i.song','catalogId':'123'}}}]},{'status':'ok'}])
        result=client.control('play-item','library-songs:i.song')
        self.assertEqual(client.request.call_args_list[1].args,('/api/v1/playback/play-item-href',{'href':'/v1/me/library/songs/i.song'}))
        self.assertEqual(set(result['expected_ids']),{'i.song','123'})
    def test_unplayable_selection_never_sends_play_command(self):
        client=hrcider.Client();client.request=Mock(return_value={'data':[{'id':'bad','attributes':{}}]})
        with self.assertRaises(ValueError):client.control('play-item','library-songs:bad')
        self.assertEqual(client.request.call_count,1)

    def test_control_ack_waits_for_the_selected_song(self):
        self.bridge.available=True
        self.bridge.library.play=Mock(return_value={'expected_ids':['chosen']})
        self.client.request.side_effect=[{'info':{'playParams':{'id':'old'}}},{'info':{'playParams':{'catalogId':'chosen'}}}]
        self.bridge.controls.put((7,42,'native-play','play/library-songs/i.chosen'))
        t=threading.Thread(target=self.bridge._control);t.start();self.bridge.threads.append(t)
        deadline=time.monotonic()+2
        while not self.bridge.scheduler.heap and time.monotonic()<deadline:time.sleep(.005)
        frame=self.bridge.scheduler.next_frame()
        self.assertIsNotNone(frame);self.assertEqual(frame[1],b'CK\t7\t42\t1\tOK')
        self.assertEqual(self.client.request.call_count,2)

    def test_control_false_success_never_acknowledges_the_old_song(self):
        self.bridge.available=True
        self.bridge.library.play=Mock(return_value={'expected_ids':['chosen']})
        self.client.request.return_value={'info':{'playParams':{'id':'old'}}}
        self.bridge.controls.put((7,43,'native-play','play/library-songs/i.chosen'))
        t=threading.Thread(target=self.bridge._control);t.start();self.bridge.threads.append(t)
        deadline=time.monotonic()+4
        while not self.bridge.scheduler.heap and time.monotonic()<deadline:time.sleep(.01)
        frame=self.bridge.scheduler.next_frame()
        self.assertIsNotNone(frame);self.assertTrue(frame[1].startswith(b'CK\t7\t43\t0\t'))
        self.bridge.library.play.assert_called_once()

    def test_queue_template_and_now_playing_concrete_url_share_identity(self):
        template='https://example.test/image/{w}x{h}bb.{f}'
        current='https://example.test/image/600x600bb.png'
        self.assertEqual(hrcider.art_id(template),hrcider.art_id(current))
        self.assertEqual(hrcider.capped_url(template),hrcider.capped_url(current))
        self.assertEqual(hrcider.art_id(template.replace('{w}','%7Bw%7D').replace('{h}','%7Bh%7D')),hrcider.art_id(current))

    def test_schema3_migration_preserves_index_and_native_art_files(self):
        import hashlib,json,sqlite3,struct
        with tempfile.TemporaryDirectory() as root:
            cache=hrcider.Cache(root)
            template='https://example.test/old/{w}x{h}bb.png';old=hashlib.sha256(template.encode()).hexdigest();key=hrcider.art_id(template)
            cache.ingest('albums',[{'id':'a1','attributes':{'name':'Keep me','artwork':{'url':template}}}],1)
            image=hrcider.render(self.raw,'M');cache.put_art(old,'M',image)
            with cache.db:
                cache.db.execute('DELETE FROM templates')
                cache.db.execute('INSERT INTO templates VALUES (?,?)',(old,template))
                row=json.loads(cache.db.execute('SELECT value FROM items').fetchone()[0]);row['art']=old
                cache.db.execute('UPDATE items SET art=?,value=?',(old,json.dumps(row)))
                cache.db.execute('PRAGMA user_version=3')
            generation=cache.generation;cache.close()
            cache=hrcider.Cache(root)
            try:
                self.assertEqual(cache.get_art(key,'M'),image)
                self.assertTrue((Path(root)/(old+'-M.aat')).exists())
                self.assertEqual(cache.template(key),template)
                self.assertEqual(cache.page('albums')['items'][0]['title'],'Keep me')
                self.assertEqual(cache.page('albums')['items'][0]['art'],key)
                self.assertEqual(cache.generation,generation+1)
                self.assertEqual(cache.db.execute('PRAGMA user_version').fetchone()[0],4)
            finally:cache.close()

    def test_cancelled_xs_batch_yields_to_the_new_visible_cover(self):
        old=object();new=object();keys=['a'*64,'b'*64,'c'*64];chosen='d'*64
        calls=[];xs=hrcider.render(self.raw,'XS');medium=hrcider.render(self.raw,'M')
        self.bridge.requests[1]=old
        self.bridge.jobs.put((3,0,1,7,1,tuple(keys),'XS',0,old))
        def artwork(key,tier):
            calls.append(key)
            if key==keys[0]:
                self.bridge.requests.pop(1,None);self.bridge.requests[2]=new
                self.bridge.jobs.put((3,0,2,7,2,chosen,'M',0,new))
            return xs if tier=='XS' else medium
        self.bridge.artwork=artwork
        t=threading.Thread(target=self.bridge._work);t.start();self.bridge.threads.append(t)
        deadline=time.monotonic()+2
        while len(calls)<2 and time.monotonic()<deadline:time.sleep(.005)
        self.assertEqual(calls,[keys[0],chosen])
