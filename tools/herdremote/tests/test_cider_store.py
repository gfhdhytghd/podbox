"""Host side of the iPod-local cover store: ledger, diff, offers, negotiation."""
import struct,sys,tempfile,time,unittest,zlib
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import hrcider
from hrcider_cache import Cache,art_id
from hrcider_store import Ledger,Planner,digest_of,item_hash,offer_records,parse_rows,LIST_END

def native(tier,fill):
    dim={'L':240,'M':128,'S':56,'XS':32}[tier]
    return struct.pack('<6H',0x5441,8,dim,dim,int(tier=='M'),0)+bytes([fill])*(2*dim*dim)
def album(n):
    return dict(id=f'a{n}',type='library-albums',attributes=dict(name=f'A{n}',artistName='X',artwork=dict(url=f'https://x/{n}/{{w}}x{{h}}bb.jpg')))
def rows_text(items):
    return ''.join(f'{k.hex()}{t}{c:08x}\n' for k,t,c in items)

class Base(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.cache=Cache(self.tmp.name)
        self.ids=[art_id(f'https://x/{n}/{{w}}x{{h}}bb.jpg') for n in range(12)]
        self.cache.ingest('albums',[album(n) for n in range(12)],1)
    def tearDown(self):self.cache.close();self.tmp.cleanup()

class Wire(unittest.TestCase):
    def test_item_hash_matches_firmware(self):
        # Value computed by the production cs_item_hash() in cider_store.c.
        self.assertEqual(item_hash(bytes(range(1,9)),2,0xdeadbeef),1241701291)
        self.assertEqual(digest_of([(bytes(range(1,9)),2,0xdeadbeef)]*2),(2*1241701291)&0xffffffff)
    def test_rows_roundtrip_and_reject_garbage(self):
        items=[(bytes(range(8)),1,0xabcdef01),(b'\xff'*8,3,0)]
        self.assertEqual(parse_rows(rows_text(items)),items)
        for bad in ('short\n','0'*16+'9'+'0'*8+'\n','z'*16+'1'+'0'*8+'\n'):
            with self.assertRaises(ValueError):parse_rows(bad)
    def test_offer_records_group_by_tier_and_cap_five_keys(self):
        pairs=[(f'{i:064x}','M') for i in range(7)]+[('f'*64,'XS')]
        recs=offer_records(9,pairs);self.assertEqual(len(recs),3)
        self.assertEqual([r.split(b'\t')[2] for r in recs],[b'M',b'M',b'XS'])
        self.assertEqual(len(recs[0].split(b'\t')[3].split(b',')),5)
        self.assertTrue(all(len(r)<400 for r in recs))

class LedgerTests(Base):
    def test_match_skips_listing_then_mismatch_pages_and_replaces(self):
        led=Ledger(self.cache);k=bytes.fromhex(self.ids[0][:16])
        led.ack(self.ids[0],'M',77)
        epoch,count,digest=led.summary();self.assertEqual(count,1)
        self.assertEqual(led.status(epoch,count,digest),True)
        # The player lost it and holds two others: list them in two pages.
        held=[(bytes.fromhex(self.ids[1][:16]),3,5),(bytes.fromhex(self.ids[2][:16]),2,6)]
        self.assertFalse(led.status(42,2,digest_of(held)))
        self.assertEqual(led.page(0,100,[held[0]]),100)
        self.assertIsNone(led.page(100,LIST_END,[held[1]]))
        self.assertTrue(led.synced);self.assertEqual(led.summary(),(42,2,digest_of(held)))
        self.assertIsNone(led.get(self.ids[0],'M'));self.assertEqual(led.get(self.ids[1],'XS'),5)
    def test_inconsistent_or_stale_listing_keeps_old_ledger(self):
        led=Ledger(self.cache);led.ack(self.ids[0],'M',77);before=led.summary()
        held=[(bytes.fromhex(self.ids[1][:16]),3,5)]
        led.status(1,2,digest_of(held))                       # claims 2, lists 1
        self.assertEqual(led.page(0,LIST_END,held),None);self.assertEqual(led.summary(),before)
        led.status(1,1,digest_of(held))
        self.assertIsNone(led.page(5,10,held))                # wrong cursor aborts
        led.status(1,1,digest_of(held))
        self.assertIsNone(led.page(0,0,held));self.assertEqual(led.summary(),before)   # no progress: player failed

class PlannerTests(Base):
    def planner(self,cache=None):
        led=Ledger(cache or self.cache);return led,Planner(cache or self.cache,led)
    def test_offers_missing_xs_then_m_then_s_and_cools_down(self):
        led,pl=self.planner()
        a=pl.next(5,now=0);self.assertTrue(all(t=='XS' for _,t in a));self.assertEqual(len(a),5)
        b=pl.next(10,now=1);self.assertEqual([t for _,t in b].count('XS'),7);self.assertEqual([t for _,t in b].count('M'),3)
        self.assertEqual(len({k for k,t in a+b if t=='XS'}),12)           # nothing offered twice
    def test_ledger_hits_are_skipped_and_changed_renders_return(self):
        led,pl=self.planner()
        for i,key in enumerate(self.ids):
            self.cache.put_art(key,'M',native('M',i));led.ack(key,'M',self.cache.art_crc(key,'M'))
            led.ack(key,'XS',1)
        self.assertEqual(pl.next(40,now=0),[(k,'S') for k in sorted(self.ids)])   # only S is missing
        self.cache.put_art(self.ids[3],'M',native('M',99))                # renderer output changed
        pl.reset();got=pl.next(40,now=0);self.assertIn((self.ids[3],'M'),got)
        self.assertNotIn((self.ids[4],'M'),got)
    def test_unrendered_held_cover_is_not_downloaded_for_a_crc_check(self):
        led,pl=self.planner()
        for key in self.ids:led.ack(key,'XS',1);led.ack(key,'M',2);led.ack(key,'S',3)
        self.assertEqual(pl.next(10,now=0),[])                            # held, nothing to compare
    def test_now_playing_l_goes_first_and_finished_pass_rescans_later(self):
        led,pl=self.planner()
        first=pl.next(3,priority=[self.ids[7],'-'],now=0);self.assertEqual(first[0],(self.ids[7],'L'))
        self.assertNotIn((self.ids[7],'L'),pl.next(3,priority=[self.ids[7]],now=1))   # cooling down
        for key in self.ids:
            for t in ('XS','M','S'):led.ack(key,t,1)
        self.assertEqual(pl.next(5,now=2),[])
        led.cache.db.execute('DELETE FROM ipod_art');                     # player lost everything
        self.assertEqual(pl.next(5,now=3),[]);self.assertTrue(pl.next(5,now=200))

class BridgeTests(Base):
    def setUp(self):
        super().setUp();self.client=Mock();self.client.request.return_value=[]
        self.bridge=hrcider.Bridge(client=self.client,cache=self.cache,volume=Mock())
        self.bridge.state=dict(duration=1000,position=0,rate=0,volume=20,shuffle=0,repeat=0,id='song',art=self.ids[5],bg='181818',title='T',artist='A',album='B',muted=False,composer='')
        self.bridge.available=True
    def tearDown(self):self.bridge.close();super().tearDown()
    def frames(self):
        out=[];self.bridge.poll(lambda *f:out.append(f));return [p for _,p in out]
    def connect(self,caps=b'cn18,neighbors,warm,play-ack,localstore'):
        self.bridge.command(b'ci\t1\t131072\t'+caps);self.gen=self.bridge.generation;return self.frames()
    def test_negotiation_old_firmware_and_old_host_paths(self):
        frames=self.connect();self.assertTrue(any(f==f'CF\t{self.gen}\tlocalstore'.encode() for f in frames))
        frames=self.connect(b'cn18,neighbors,warm,play-ack');self.assertFalse(any(f.startswith(b'CF\t') for f in frames))
        gen=self.bridge.generation
        self.bridge.command(f'ls\t{gen}\t1\t0\t0\t0\t1048576'.encode());self.assertFalse(any(f.startswith(b'CQ\t') for f in self.frames()))
        self.bridge.command(f'lw\t{gen}\t5'.encode());self.assertFalse(any(f.startswith(b'CO\t') for f in self.frames()))
    def test_fu27_store_and_modes5_negotiate_together(self):
        caps=b'cn18,neighbors,warm,play-ack,localstore,modes5'
        self.bridge.state['repeat5']=4
        frames=self.connect(caps)
        self.assertEqual(self.bridge.capabilities,frozenset(caps.decode().split(',')))
        self.assertIn(f'CF\t{self.gen}\tlocalstore'.encode(),frames)
        cn=next(f for f in frames if f.startswith(b'CN\t')).split(b'\t')
        self.assertEqual(len(cn),19);self.assertEqual(cn[-1],b'4')
        # Renegotiation removes each feature independently; store never changes CN width.
        frames=self.connect(b'cn18,localstore')
        self.assertTrue(any(f.startswith(b'CF\t') for f in frames))
        self.assertEqual(len(next(f for f in frames if f.startswith(b'CN\t')).split(b'\t')),18)
        frames=self.connect(b'cn18,modes5')
        self.assertFalse(any(f.startswith(b'CF\t') for f in frames))
        self.assertEqual(len(next(f for f in frames if f.startswith(b'CN\t')).split(b'\t')),19)
        frames=self.connect(b'localstore,modes5')
        self.assertTrue(any(f.startswith(b'CF\t') for f in frames))
        self.assertEqual(len(next(f for f in frames if f.startswith(b'CN\t')).split(b'\t')),16)
    def test_status_mismatch_lists_then_offers_only_after_verified_sync(self):
        self.connect()
        held=[(bytes.fromhex(self.ids[0][:16]),0,9)]
        self.bridge.command(f'lw\t{self.gen}\t5'.encode());self.assertFalse(any(f.startswith(b'CO\t') for f in self.frames()))  # unverified
        self.bridge.command(f'ls\t{self.gen}\t5\t1\t{digest_of(held)}\t300\t1048576'.encode())
        self.assertIn(f'CQ\t{self.gen}\t0'.encode(),self.frames())
        body=rows_text(held)
        self.bridge.command(f'lv\t{self.gen}\t0\t{LIST_END}\t1\n{body}'.encode().replace(b'\n',b'\n',1))
        self.assertTrue(self.bridge.ledger.synced)
        # Offers: now playing L first, then XS of the library; prefetch queued for them.
        self.bridge.command(f'lw\t{self.gen}\t5'.encode())
        offers=[f for f in self.frames() if f.startswith(b'CO\t')]
        self.assertEqual(offers[0].split(b'\t')[2],b'L');self.assertIn(self.ids[5].encode(),offers[0])
        self.assertTrue(self.bridge.offer_pairs);self.assertTrue(any(p[1]=='L' for p in self.bridge.offer_pairs))
    def test_ack_updates_ledger_and_foreign_generation_is_ignored(self):
        self.connect();key=self.ids[2]
        self.bridge.command(f'la\t{self.gen}\t{key}\tM\t1234'.encode());self.assertEqual(self.bridge.ledger.get(key,'M'),1234)
        self.bridge.command(f'la\t{self.gen+1}\t{key}\tS\t1'.encode());self.assertIsNone(self.bridge.ledger.get(key,'S'))
        before=self.bridge.ledger.summary();self.bridge.command(f'la\t{self.gen}\t{key}\tQQ\t1'.encode());self.assertEqual(self.bridge.ledger.summary(),before)
    def test_offered_cover_is_prepared_and_served_at_idle_priority(self):
        self.connect();self.bridge.ledger.synced=True
        key=self.ids[1];self.cache.put_art(key,'M',native('M',4))
        req=f'cr\t{self.gen}\t5\t{key}\tM\t0\t4\t0'.encode()
        self.bridge.command(req);self.assertEqual(len(self.bridge.jobs.queue),1);self.assertEqual(self.bridge.jobs.queue[0][0],4)

if __name__=='__main__':unittest.main()
