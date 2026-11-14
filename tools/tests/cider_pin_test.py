"""Album track list L pin and the idle-fill / store-hold scheduling in the
production cider.c: pin priority, USB hold on a store hit, fill gating."""
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
#include <string.h>
#include <stdio.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdarg.h>
#include "screens/playback/cider_model.h"
#include "screens/playback/cider_store.h"
#define HZ 100
#define ARRAYLEN(a) ((int)(sizeof(a)/sizeof((a)[0])))
#define TIME_AFTER(a,b) ((long)((a)-(b))>0)
#define TIME_BEFORE(a,b) ((long)((a)-(b))<0)
#define HOLD_ACTIVE (HZ)
#define HOLD_ASLEEP (5*HZ/2)
static int cider_mutex,disk_queue,posts;
static struct cider_state state;
static struct cider_cache cache;
static struct cider_transfer transfer;
static struct cider_store store;
static bool disk_lookup,request_ready,disk_running=true,cancel_all,warm_ready,disk_active=true;
static bool host_store,ls_due,want_ready,list_req,list_ready,pin_on;
static uint32_t next_request,grant,granted,cancel_request,list_cursor;
static unsigned int request_tries,ncommands,xs_count,list_len,nfills,noffers,nacks;
static char commands[12][384],xs_keys[5][65],warm_keys[455],warm_tier[3],list_page[480],pin_key[65];
static struct { char key[65]; unsigned char tier; } fills[8], offers[8];
static struct { char key[65]; unsigned char tier; uint32_t crc; } acks[8];
static struct { uint32_t epoch,count,digest,total; } snap;
static struct { uint8_t key[32]; unsigned char tier; long until; } missed[8];
static unsigned int miss_next;
static long current_tick=1000,hello_at,request_at,art_used_at,hold_until,want_at,pin_at;
static void mutex_lock(int*p){(void)p;}static void mutex_unlock(int*p){(void)p;}
static bool storage_disk_is_active(void){return disk_active;}
static void queue_post(int*q,int n,int v){(void)q;(void)n;(void)v;posts++;}
static void cider_log(const char*f,...){(void)f;}
static unsigned int cider_library_next(char*r,unsigned int n,uint32_t g){(void)r;(void)n;(void)g;return 0;}
static unsigned int art_revision;
'''
post=r'''
static char out[2048],A[65],B[65],C[65];
static void key(char*k,char c){memset(k,c,64);k[64]=0;}
static void settle_tick(long t){current_tick=t;}
static void send_all(char*first){/* drain next() until quiet */ first[0]=0;char r[512];unsigned int n;while((n=next(r,sizeof r))){strcat(first,r);strcat(first,"|");}}
static unsigned char art[CIDER_ART_MAX];
static void fill_art(const char*k,const char*t){unsigned int dim=!strcmp(t,"L")?240:!strcmp(t,"M")?128:!strcmp(t,"S")?56:32;memset(art,0,sizeof art);art[0]=0x41;art[1]=0x54;art[2]=8;art[4]=dim;art[6]=dim;art[8]=!strcmp(t,"M");assert(cider_cache_put(&cache,k,t,art,12+dim*dim*2,false));}
int main(void){
 state.generation=7;key(A,'a');key(B,'b');key(C,'c');
 store.loaded=true;store.cap=CS_DEFAULT_CAP;store.evict=store.compact=store.cur=-1;
 /* --- store hit holds the USB request; the miss goes out at once --- */
 memset(store.bloom,0,sizeof store.bloom);
 cider_request_art(A,"M");assert(transfer.active&&!disk_lookup&&request_ready);
 assert(next(out,sizeof out)&&!strncmp(out,"cg\t",3));assert(next(out,sizeof out)&&!strncmp(out,"cr\t",3)&&strstr(out,"\t3\t0"));
 memset(store.bloom,0xff,sizeof store.bloom);transfer.active=false;granted=0;
 cider_request_art(B,"M");assert(transfer.active&&disk_lookup&&!request_ready&&hold_until==current_tick+HOLD_ACTIVE);
 assert(next(out,sizeof out)&&!strncmp(out,"cg\t",3));assert(!next(out,sizeof out));        /* held */
 settle_tick(current_tick+HOLD_ACTIVE-1);assert(!next(out,sizeof out));
 settle_tick(current_tick+1);assert(next(out,sizeof out)&&!strncmp(out,"cr\t",3));          /* released: race */
 transfer.active=false;disk_active=false;
 cider_request_art(C,"M");assert(hold_until==current_tick+HOLD_ASLEEP);                     /* spin-up allowance */
 disk_active=true;transfer.active=false;memset(store.bloom,0,sizeof store.bloom);
 /* --- album pin: asks for L at once, below visible covers, above idle fill --- */
 memset(&cache,0,sizeof cache);transfer.active=false;ncommands=0;granted=grant=0;
 cider_pin_art(A);assert(pin_on&&!strcmp(pin_key,A));
 cider_pin_art("-");assert(!pin_on);cider_pin_art(A);
 settle_tick(current_tick+10);
 assert(next(out,sizeof out));assert(transfer.active&&!strcmp(transfer.key,A)&&!strcmp(transfer.tier,"L")&&!transfer.fill);
 send_all(out);assert(strstr(out,"cr\t7\t")&&strstr(out,A)&&strstr(out,"\tL\t0\t3\t0"));
 /* A visible list cover replaces the pin transfer first ... */
 cider_request_art(B,"S");assert(transfer.active&&!strcmp(transfer.key,B));
 /* ... and the pin does not come back until that request is done and 5 s passed */
 transfer.active=false;settle_tick(current_tick+100);assert(next(out,sizeof out)==0||!strstr(out,"\tL\t"));assert(!transfer.active||strcmp(transfer.key,A));
 settle_tick(current_tick+5*HZ);next(out,sizeof out);assert(transfer.active&&!strcmp(transfer.key,A)&&!strcmp(transfer.tier,"L"));
 /* L arrives: pinned; scrolling or filling never evicts it */
 transfer.active=false;fill_art(A,"L");pin_apply();
 int at=-1;for(int i=0;i<CIDER_SLOTS;i++)if(cache.slot[i].length&&!strcmp(cache.slot[i].key,A))at=i;assert(at>=0&&cache.slot[at].pinned);
 char k[65];for(int i=0;i<40;i++){memset(k,'0'+(i%10),64);k[64]=0;k[0]='a'+(i/10);fill_art(k,"M");}
 assert(cider_cache_find(&cache,A,"L")>=0);
 /* Now Playing wants the same art-id: served from the pinned copy, no transfer */
 transfer.active=false;cider_request_art(A,"L");assert(!transfer.active);
 /* nothing to re-request while it is resident */
 settle_tick(current_tick+20*HZ);send_all(out);assert(!transfer.active);
 /* unpinned: now evictable */
 cider_unpin_art();assert(!pin_on&&!cache.slot[at].pinned);
 for(int i=0;i<40;i++){memset(k,'0'+(i%10),64);k[64]=0;k[0]='A'+(i/10);fill_art(k,"M");}
 assert(cider_cache_find(&cache,A,"L")<0);
 /* a new pin replaces the old one */
 cider_pin_art(B);cider_pin_art(C);assert(!strcmp(pin_key,C));
 cider_unpin_art();
 /* --- idle fill: only after CF, with a spinning disk and an idle UI --- */
 memset(&cache,0,sizeof cache);transfer.active=false;ncommands=0;noffers=0;cancel_request=0;cancel_all=false;
 char rec[400];snprintf(rec,sizeof rec,"CO\t7\tM\t%s",A);
 store_text((const unsigned char*)rec,strlen(rec));assert(noffers==0);                      /* not negotiated */
 store_text((const unsigned char*)"CF\t7\tlocalstore",15);assert(host_store&&ls_due);
 store_text((const unsigned char*)"CF\t6\tlocalstore",15);
 store_text((const unsigned char*)rec,strlen(rec));assert(noffers==1&&offers[0].tier==CS_M);
 store_text((const unsigned char*)rec,strlen(rec));assert(noffers==1);                       /* duplicate */
 snprintf(rec,sizeof rec,"CO\t6\tM\t%s",B);store_text((const unsigned char*)rec,strlen(rec));assert(noffers==1);   /* stale gen */
 art_used_at=current_tick;disk_active=true;background_start();assert(!transfer.active);       /* UI busy */
 settle_tick(current_tick+3*HZ+1);disk_active=false;background_start();assert(!transfer.active); /* disk asleep: never wake it */
 disk_active=true;store.loaded=false;background_start();assert(!transfer.active);store.loaded=true;
 background_start();assert(transfer.active&&transfer.fill&&!strcmp(transfer.tier,"M")&&!disk_lookup&&request_ready&&noffers==0);
 send_all(out);assert(strstr(out,"\tM\t0\t4\t0"));                                          /* idle priority */
 /* a visible request preempts the fill */
 cider_request_art(B,"S");assert(!transfer.fill&&!strcmp(transfer.key,B));
 /* a written fill is not followed by the next one until it left the arena */
 transfer.active=false;cancel_request=0;cancel_all=false;settle_tick(current_tick+4*HZ);noffers=1;strcpy(offers[0].key,C);offers[0].tier=CS_XS;
 fill_art(A,"M");cache.slot[0].dirty=true;cache.slot[0].bg=true;background_start();assert(!transfer.active);
 cache.slot[0].dirty=false;background_start();assert(transfer.active&&transfer.fill&&!strcmp(transfer.tier,"XS"));
 /* no offers: ask for more, at most every 4 s */
 transfer.active=false;noffers=0;want_ready=false;settle_tick(current_tick+5*HZ);background_start();assert(want_ready);
  puts("Production album pin + idle fill + store hold: pinned L survives scrolling, shared with Now Playing, visible covers preempt, fills gated PASS");}
'''
(O/'pin.c').write_text(pre+fn('valid_key')+fn('pin_apply')+fn('cider_unpin_art')+fn('cider_pin_art')+fn('tier_name')+fn('store_local')+fn('miss_note')+fn('fill_add')+fn('store_text')+fn('begin_transfer')+fn('background_start')+fn('hex8')+fn('store_record')+fn('next')+fn('cider_request_art')+post)
cmd=['cc','-std=gnu11','-Wall','-Wextra','-Wno-unused-variable','-Wno-unused-function','-Wno-unused-parameter','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(R/'apps-ipod'),'-I'+str(R/'firmware/export'),str(O/'pin.c'),str(R/'apps-ipod/screens/playback/cider_model.c'),str(R/'apps-ipod/screens/playback/cider_store.c'),str(R/'firmware/usbstack/podlink_wire.c'),'-o',str(O/'pin')]
subprocess.run(cmd,check=True);subprocess.run([str(O/'pin')],check=True)
