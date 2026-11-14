"""Host mock request/chunk timings and native .aat one-read reference; no USB."""
from pathlib import Path
import json,os,statistics,sys,tempfile,threading,time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'herdremote/tests'))
from test_cider import Handler,LocalOpener
import hrcider
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import podlink
from http.server import ThreadingHTTPServer
O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
base='http://127.0.0.1:'+str(server.server_port)
with tempfile.TemporaryDirectory() as tmp:
 cache=hrcider.Cache(tmp)
 opener=LocalOpener(base)
 bridge=hrcider.Bridge(client=hrcider.Client(base,opener=opener),cache=cache,fetch=lambda url,timeout:opener.open(url,timeout))
 key=hrcider.art_id(base+'/art/{w}x{h}bb.png');bridge.templates[key]=base+'/art/{w}x{h}bb.png'
 cold_start=time.perf_counter();native=bridge.artwork(key,'M');cold_ms=(time.perf_counter()-cold_start)*1000
 for tier in hrcider.SIZES:bridge.artwork(key,tier)
 worker=threading.Thread(target=bridge._work,daemon=True);worker.start();bridge.threads=[worker]
 bridge.command(b'ci\t1\t131072');generation=bridge.generation
 bridge.command(f'cg\t{generation}\t131072'.encode())
 measurements={}
 for tier in ('M','L'):
  times=[];totals=[];frames=[]
  for req in range(1,17):
   decoder=podlink.Decoder();got=bytearray();charged=chunks=0
   def receive(kind,payload):
    global charged,chunks
    for frame in decoder.feed(podlink.encode(kind,1,payload)):
     if frame.type!=podlink.BINARY:continue
     part=hrcider.decode_chunk(frame.payload)
     assert part['request']==req and part['offset']==len(got)
     got.extend(part['data']);charged+=len(payload);chunks+=1
   begin=time.perf_counter()
   bridge.command(f'cr\t{generation}\t{req}\t{key}\t{tier}\t0\t3\t0'.encode())
   expected=12+hrcider.SIZES[tier]**2*2
   while len(got)<expected and time.perf_counter()-begin<5:
    bridge.poll(receive)
    if len(got)<expected:time.sleep(.02)
   assert got==bridge.artwork(key,tier)
   elapsed=time.perf_counter()-begin
   times.append(elapsed*1000);totals.append(expected/elapsed);frames.append(chunks)
   bridge.command(f'cg\t{generation}\t{charged}'.encode())
  measurements[tier]={'samples':len(times),'median_request_to_complete_ms':statistics.median(times),'min_ms':min(times),'max_ms':max(times),'median_payload_bytes_per_second':statistics.median(totals),'chunks_per_cover':frames[0]}
 path=Path(tmp)/'reference.aat';path.write_bytes(native);disk=[]
 for _ in range(100):
  begin=time.perf_counter_ns();fd=os.open(path,os.O_RDONLY);data=os.read(fd,len(native));os.close(fd);disk.append((time.perf_counter_ns()-begin)/1e6);assert data==native
 result={'transport':'mock callback, real PodLink encode/decode and CRC, 20ms host pump; no device/USB','art_cold_mock_http_render_M_ms':cold_ms,'art':measurements,'host_warm_one_open_one_read_aat_ms':statistics.median(disk),'native_device_disk_dma':'UNMEASURED - device access forbidden','byte_sizes':{tier:12+dim*dim*2 for tier,dim in hrcider.SIZES.items()},'max_credit':131072,'one_request_per_cover':True}
 (O/'benchmark.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
 bridge.close()
server.shutdown();server.server_close();t.join()
