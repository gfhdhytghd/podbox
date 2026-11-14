"""Native 50-row page latency over mock PodLink; no Cider or device control."""
from pathlib import Path
import sys,tempfile,time,threading,json,statistics
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'herdremote'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hrcider import Bridge
from hrcider_cache import Cache
import podlink
O=Path(sys.argv[1]);O.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory() as d:
 c=Cache(d)
 c.ingest('songs',[dict(id=f's{i:06}',attributes=dict(name=f'Title {i:06}',durationInMillis=123000)) for i in range(10000)],1)
 b=Bridge(client=Mock(),cache=c,volume=Mock());b.index.step=lambda:None
 b.command(b'ci\t1\t131072');b.command(f'cg\t{b.generation}\t131072'.encode())
 t=threading.Thread(target=b._pages);t.start()
 results=[];payloads=[];rows=[]
 for i in range(32):
  decoder=podlink.Decoder();reply=[];request=100+i
  def receive(kind,payload):
   for frame in decoder.feed(podlink.encode(kind,1,payload)):
    if frame.payload.startswith(f'CL\t{b.generation}\t{request}\t'.encode()):reply.append(frame.payload)
  start=time.perf_counter();b.command(f'cq\t{b.generation}\t{request}\t{i*50}\t50\tsongs'.encode())
  while not reply and time.perf_counter()-start<3:
   b.poll(receive)
   if not reply:time.sleep(.02)
  assert reply
  elapsed=(time.perf_counter()-start)*1000
  fields=reply[0].split(b'\n',1)[0].split(b'\t');assert int(fields[5])==10000
  results.append(elapsed);payloads.append(len(reply[0]));rows.append(int(fields[7]))
  b.command(f'cg\t{b.generation}\t{len(reply[0])}'.encode())
 b.stop.set();t.join(3);c.close()
 result=dict(transport='mock callback + real framing/CRC; 20-ms pump, dedicated visible-page worker',library_songs=10000,samples=len(results),median_request_to_complete_ms=statistics.median(results),min_ms=min(results),max_ms=max(results),rows_per_frame=rows[0],payload_bytes=payloads[0],physical_usb='UNMEASURED')
 (O/'page-benchmark.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
