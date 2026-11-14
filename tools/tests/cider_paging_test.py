"""Actual bounded page parser/requester, fragmentation, stale generation, eviction."""
from pathlib import Path
import subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
(O/'config.h').write_text('')
(O/'kernel.h').write_text('#define HZ 100\n#define TIME_BEFORE(a,b) ((long)((a)-(b))<0)\nextern long current_tick;\n')
(O/'paging.c').write_text(r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "screens/playback/cider_library.h"
long current_tick;
static char wire[16000],request[512];
static int respond(unsigned id,unsigned lib,int offset,int count){
int n=snprintf(wire,sizeof wire,"CL\t7\t%u\t%u\t%d\t200\t0\t%d\n",id,lib,offset,count);
for(int i=0;i<count;i++)n+=snprintf(wire+n,sizeof wire-n,"s%d\tSong %d\tArtist\t-\t123000\t0\tsong\tplay/library-songs/s%d\t181818\t2020\n",offset+i,offset+i,offset+i);
return n;}
int main(void){
cider_library_open("songs");assert(cider_library_total()==-1);unsigned gen,id,off;
assert(cider_library_next(request,sizeof request,7)>0);assert(sscanf(request,"cq\t%u\t%u\t%u",&gen,&id,&off)==3&&gen==7&&off==0);
assert(!cider_library_next(request,sizeof request,7));
int n=respond(id,2,0,25);assert(cider_library_receive(wire,n,7,2));struct cider_row row;
assert(cider_library_total()==200&&cider_library_row(24,&row)&&!strcmp(row.id,"s24"));assert(!cider_library_row(25,&row));
assert(cider_library_next(request,sizeof request,7)>0);sscanf(request,"cq\t%u\t%u\t%u",&gen,&id,&off);assert(off==25);
n=respond(id,1,25,25);assert(!cider_library_receive(wire,n,7,2));n=respond(id,2,25,25);assert(!cider_library_receive(wire,n-1,7,2));assert(cider_library_receive(wire,n,7,2));assert(cider_library_row(49,&row));
for(int i=50;i<175;i+=25){assert(!cider_library_row(i,&row));assert(cider_library_next(request,sizeof request,7)>0);sscanf(request,"cq\t%u\t%u\t%u",&gen,&id,&off);n=respond(id,2,off,25);assert(cider_library_receive(wire,n,7,2));}
cider_library_invalidate(3);assert(cider_library_total()==-1);cider_library_open("albums");assert(cider_library_total()==-1&&!cider_library_receive(wire,n,7,2));puts("Production native paging, partial frames, generations, 3-page LRU PASS");}
''')
cmd=['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(O),'-I'+str(R/'apps-ipod'),str(O/'paging.c'),str(R/'apps-ipod/screens/playback/cider_library.c'),'-o',str(O/'paging')]
subprocess.run(cmd,check=True);subprocess.run([str(O/'paging')],check=True)
