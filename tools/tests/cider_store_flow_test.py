"""Production worker store flow against the real store on a host directory:
USB-fetched covers are written, hits come back pinned, fills leave the arena,
inventory pages and acks follow the protocol."""
from pathlib import Path
import re,subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
s=(R/'apps-ipod/screens/playback/cider.c').read_text()
def fn(name):
 m=re.search(r'(?m)^(?:static )?[\w *]+\b'+name+r'\([^;{}]*\)\s*\{',s);assert m,name
 e=m.end();n=1
 while n:n+=(s[e]=='{')-(s[e]=='}');e+=1
 return s[m.start():e]
pre=r'''
#include <assert.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <stdbool.h>
#include <stdint.h>
#include <unistd.h>
#include <sys/stat.h>
#include "screens/playback/cider_model.h"
#include "screens/playback/cider_store.h"
#define HZ 100
#define ARRAYLEN(a) ((int)(sizeof(a)/sizeof((a)[0])))
#define TIME_AFTER(a,b) ((long)((a)-(b))>0)
#define TIME_BEFORE(a,b) ((long)((a)-(b))<0)
static int cider_mutex,disk_queue,posts;static bool tag_busy,queue_idle=true;
static struct cider_state state;
static struct cider_cache cache;
static struct cider_transfer transfer;
static struct cider_store store;
static bool disk_lookup,request_ready,disk_running=true;
static bool host_store,ls_due,want_ready,list_req,list_ready,pin_on;
static uint32_t cancel_request,list_cursor;
static unsigned int request_tries,list_len,nfills,noffers,nacks,art_revision,miss_next;
static char list_page[480],pin_key[65];
static struct { char key[65]; unsigned char tier; uint32_t crc; } acks[8];
static struct { uint32_t epoch,count,digest,total; } snap;
static struct { uint8_t key[32]; unsigned char tier; long until; } missed[8];
static struct cs_item list_items[16];
static unsigned char disk_data[CS_REC_MAX];
static long current_tick=1000,want_at;
static struct { char key[65]; unsigned char tier; } offers[8];
static void mutex_lock(int*p){(void)p;}static void mutex_unlock(int*p){(void)p;}
static bool tagcache_is_busy(void){return tag_busy;}
static bool queue_empty(int*q){(void)q;return queue_idle;}
static void yield(void){}
static void queue_post(int*q,int n,int v){(void)q;(void)n;(void)v;posts++;}
static int t_open(const char*p,int m){return open(p,m==CS_O_READ?O_RDONLY:O_RDWR|O_CREAT,0644);}
static int t_read(int fd,void*b,unsigned n){return read(fd,b,n);}
static int t_write(int fd,const void*b,unsigned n){return write(fd,b,n);}
static int t_seek(int fd,uint32_t o){return lseek(fd,o,SEEK_SET)<0?-1:0;}
static int t_sync(int fd){return fsync(fd);}
static int t_close(int fd){return close(fd);}
static int t_remove(const char*p){return unlink(p);}
static int t_fsize(const char*p){struct stat st;return stat(p,&st)==0?(int)st.st_size:-1;}
static void t_mkdir(const char*p){mkdir(p,0755);}
static const struct cs_io tio={t_open,t_read,t_write,t_seek,t_sync,t_close,t_remove,t_fsize,t_mkdir,NULL};
'''
post=r'''
static char A[65],B[65],C[65];
static unsigned char art[CIDER_ART_MAX];
static void key(char*k,char c){memset(k,c,64);k[64]=0;}
static unsigned int mk(const char*t,unsigned char fill){unsigned int dim=!strcmp(t,"L")?240:!strcmp(t,"M")?128:!strcmp(t,"S")?56:32;memset(art,fill,sizeof art);memset(art,0,12);art[0]=0x41;art[1]=0x54;art[2]=8;art[4]=dim;art[6]=dim;art[8]=!strcmp(t,"M");return 12+dim*dim*2;}
static void arena(const char*k,const char*t,unsigned char fill,bool dirty){assert(cider_cache_put(&cache,k,t,art,mk(t,fill),dirty));}
static int slots(void){int n=0;for(int i=0;i<CIDER_SLOTS;i++)n+=cache.slot[i].length!=0;return n;}
static void reload(unsigned int cap){if(store.io)cs_release(&store);cs_init(&store,&tio,"st-flow",cap);assert(cs_load(&store,disk_data,sizeof disk_data,77)==CS_OK);}
int main(int argc,char**argv){(void)argc;mkdir(argv[1],0755);assert(chdir(argv[1])==0);
 mkdir("st-flow",0755);for(int i=0;i<40;i++){char p[64];snprintf(p,sizeof p,"st-flow/p%02d.pak",i);unlink(p);}unlink("st-flow/index.cst");
 state.generation=7;key(A,'a');key(B,'b');key(C,'c');
 reload(CS_DEFAULT_CAP);host_store=true;
 /* 1. a cover that came over USB (dirty) is written to the store and acknowledged */
 mk("L",5);arena(A,"L",5,true);arena(B,"M",6,true);
 assert(!store_write_batch());
 assert(!cache.slot[0].dirty&&!cache.slot[1].dirty&&nacks==2&&acks[0].tier==CS_L&&!strcmp(acks[0].key,A));
 uint8_t kb[32];assert(cs_key_from_hex(A,kb)&&cs_maybe(&store,kb,CS_L)&&store.count==2);
 /* the ack carries the CRC the host diffed against */
 {extern uint32_t podlink_crc32(const unsigned char*,unsigned int);unsigned int n=mk("L",5);assert(acks[0].crc==podlink_crc32(art,n));}
 /* 2. next session: arena empty, pin wanted -> store hit lands pinned with no USB */
 memset(&cache,0,sizeof cache);reload(CS_DEFAULT_CAP);pin_on=true;strcpy(pin_key,A);
 assert(read_store(A,"L",false,0)&&cider_cache_find(&cache,A,"L")>=0);
 int at=-1;for(int i=0;i<CIDER_SLOTS;i++)if(cache.slot[i].length&&!strcmp(cache.slot[i].key,A))at=i;assert(at>=0&&cache.slot[at].pinned&&!cache.slot[at].dirty);
 /* 3. transfer lookup: hit ends the wait; the USB request is cancelled only if it had left */
 memset(&cache,0,sizeof cache);
 transfer.active=true;transfer.request=9;disk_lookup=true;request_tries=0;cancel_request=0;strcpy(transfer.key,B);strcpy(transfer.tier,"M");
 assert(read_store(B,"M",true,9)&&!transfer.active&&!cancel_request&&!disk_lookup);
 transfer.active=true;transfer.request=10;disk_lookup=true;request_tries=1;memset(&cache,0,sizeof cache);
 assert(read_store(B,"M",true,10)&&!transfer.active&&cancel_request==10);
 /* 4. a miss releases USB at once and is not retried for 30 s */
 transfer.active=true;transfer.request=11;disk_lookup=true;request_tries=0;request_ready=false;
 assert(!read_store(C,"M",true,11)&&transfer.active&&request_ready&&!disk_lookup);
 {uint8_t ck[32];int seen=0;cs_key_from_hex(C,ck);for(int i=0;i<8;i++)seen|=missed[i].until&&!memcmp(missed[i].key,ck,32);assert(seen);}
 /* 5. an idle fill is stored, acknowledged, and leaves the arena */
 memset(&cache,0,sizeof cache);nacks=0;
 arena(C,"XS",7,true);cider_cache_demote(&cache,C,"XS");assert(cache.slot[0].bg&&slots()==1);
 store_write_batch();assert(slots()==0&&nacks==1&&acks[0].tier==CS_XS);
 /* same bytes again: no second write, still acknowledged */
 uint32_t total=store.total;arena(C,"XS",7,true);store_write_batch();assert(store.total==total&&nacks==2);
 /* 6. the UI or the database wants the disk: keep the cover dirty */
 memset(&cache,0,sizeof cache);arena(A,"S",3,true);queue_idle=false;assert(!store_write_batch());assert(cache.slot[0].dirty);queue_idle=true;
 tag_busy=true;store_write_batch();assert(cache.slot[0].dirty);tag_busy=false;
 store_write_batch();assert(!cache.slot[0].dirty);
 /* 7. a full store starts an eviction and the cover waits for it */
 memset(&cache,0,sizeof cache);reload(80u<<10);arena(B,"M",1,true);arena(C,"M",2,true);arena(A,"M",3,true);
 bool more=false;for(int i=0;i<4&&!more;i++)more=store_write_batch();
 assert(more&&cs_busy(&store));int dirty=0;for(int i=0;i<CIDER_SLOTS;i++){dirty+=cache.slot[i].length&&cache.slot[i].dirty;}assert(dirty>=1);
 while(cs_busy(&store))cs_maintain(&store,disk_data,sizeof disk_data,false);
 for(int i=0;i<6&&store_write_batch();i++)while(cs_busy(&store))cs_maintain(&store,disk_data,sizeof disk_data,false);
 assert(store.total<=(80u<<10));
 /* 8. inventory pages: header, rows, final cursor, failure reply */
 memset(&cache,0,sizeof cache);reload(CS_DEFAULT_CAP);
 for(int i=0;i<50;i++){char k[65];memset(k,'0'+i%10,64);k[0]='a'+i/10;k[64]=0;arena(k,"XS",i,true);store_write_batch();}
 unsigned int cursor=0,rows=0,pages=0;
 for(;;){list_cursor=cursor;list_req=true;list_ready=false;build_list();assert(list_ready&&!list_req&&list_len<480);
  unsigned long gen,cur,next;unsigned int cnt;char*p=list_page;assert(!strncmp(p,"lv\t7\t",5));gen=strtoul(p+3,&p,10);cur=strtoul(p+1,&p,10);next=strtoul(p+1,&p,10);cnt=strtoul(p+1,&p,10);
  assert(gen==7&&cur==cursor&&*p=='\n');assert(list_len-(unsigned)(p+1-list_page)==cnt*26);rows+=cnt;pages++;
  if(next>=(CS_BUCKETS<<5)){break;}
  assert(next>cur);cursor=next;}
 assert(rows==store.count&&pages>=4);
 list_cursor=0;store.loaded=false;list_req=true;list_ready=false;build_list();assert(!strncmp(list_page,"lv\t7\t0\t0\t0\n",11));store.loaded=true;
 /* 9. protocol records: ls once, listing page, acks, want; nothing without CF */
 host_store=false;assert(store_record(list_page,sizeof list_page)==0);host_store=true;
 snap_store();char out[512];assert(ls_due&&store_record(out,sizeof out)&&!strncmp(out,"ls\t7\t",5)&&!ls_due);
 list_ready=true;list_len=snprintf(list_page,sizeof list_page,"lv\t7\t0\t0\t0\n");assert(store_record(out,sizeof out)==list_len&&!list_ready);
 nacks=1;strcpy(acks[0].key,A);acks[0].tier=CS_M;acks[0].crc=4660;assert(store_record(out,sizeof out)&&!strcmp(out,"la\t7\taaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\tM\t4660")&&!nacks);
 want_ready=true;noffers=1;assert(store_record(out,sizeof out)&&!strcmp(out,"lw\t7\t7")&&!want_ready);
 puts("Production store flow: USB cover written+acked, hit returns pinned without USB, miss races once, fill leaves arena, full store waits for eviction, inventory pages PASS");return 0;}
'''
body=''.join(fn(n) for n in ['pin_apply','tier_name','store_local','miss_note','hex8','store_record','snap_store','build_list','read_store','store_write_batch'])
(O/'flow.c').write_text(pre+body+post)
cmd=['cc','-std=gnu11','-Wall','-Wextra','-Wno-unused-variable','-Wno-unused-function','-Wno-unused-parameter','-Werror','-g','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(R/'apps-ipod'),'-I'+str(R/'firmware/export'),str(O/'flow.c'),str(R/'apps-ipod/screens/playback/cider_model.c'),str(R/'apps-ipod/screens/playback/cider_store.c'),str(R/'firmware/usbstack/podlink_wire.c'),'-o',str(O/'flow')]
subprocess.run(cmd,check=True);subprocess.run([str(O/'flow'),str(O/'scratch')],check=True)
