"""Compare the host provider's port against the real native parser."""
from pathlib import Path
import ctypes,json,random,re,subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(R/'tools/herdremote'));from hrcider_featured import featured_guests
subprocess.run(['cc','-shared','-fPIC','-std=gnu11','-Wall','-Wextra','-Werror','-I'+str(R/'apps-ipod/database'),str(R/'apps-ipod/database/db_featured_parse.c'),'-o',str(O/'featured.so')],check=True)
lib=ctypes.CDLL(str(O/'featured.so'))
class Names(ctypes.Structure):_fields_=[('count',ctypes.c_int),('name',ctypes.c_void_p*8),('length',ctypes.c_int*8)]
known=['Nick Cave','Nick Cave & the Bad Seeds','Earth, Wind & Fire','AC/DC','Guest One','Guest Two','Florence and the Machine','Drake','Rihanna','Kylie Minogue','Jay-Z','Tyler, The Creator']
known_bytes={n.encode().lower() for n in known}
callback=ctypes.CFUNCTYPE(ctypes.c_bool,ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p)(lambda p,n,c:ctypes.string_at(p,n).strip(b' \t').lower() in known_bytes)
lib.db_featured_parse.argtypes=[ctypes.c_char_p,type(callback),ctypes.c_void_p,ctypes.POINTER(Names)]
src=(R/'tools/dbfeat/dbfeat.c').read_text();cases=[]
for match in re.finditer(r'\{\s*("(?:[^"\\]|\\.)*")\s*,\s*\{',src):
 try:cases.append(json.loads(match[1]))
 except ValueError:pass
rng=random.Random(23)
for i in range(1000):
 guests=rng.sample(known,rng.randint(1,5));cases.append('Song '+rng.choice(['(feat. ','[ft:',' featuring ',' w/',' f/',' duet with '])+rng.choice([' & ',', ',' / ',' x ',' + ',' and ']).join(guests)+rng.choice([')',' (Live)',' [Remix]','']))
for s in cases:
 raw=s.encode();names=Names();lib.db_featured_parse(raw,callback,None,ctypes.byref(names));native=[ctypes.string_at(names.name[i],names.length[i]).decode() for i in range(names.count)]
 assert featured_guests(s,known)==native,(s,featured_guests(s,known),native)
print(f'Host Featured Artists matches native C parser: {len(cases)} fixture/generated cases PASS')
