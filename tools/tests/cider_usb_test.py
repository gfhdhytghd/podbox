"""USB arrival inside real store operations and the production worker ACK path.

All files live below the supplied output directory. An optional source root
allows the same runtime assertions to run against the unmodified baseline.
"""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(sys.argv[1]).resolve()
SOURCE = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else ROOT
OUT.mkdir(parents=True, exist_ok=True)
header = (SOURCE / "apps-ipod/screens/playback/cider_store.h").read_text()
pre = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
#include "screens/playback/cider_store.h"
static struct cider_store store;
static uint8_t buf[CS_REC_MAX],payload[CS_REC_MAX],key[32];
static bool armed,pending;
static unsigned int reads,writes,steps;
static int t_open(const char*p,int mode){return open(p,mode==CS_O_READ?O_RDONLY:O_RDWR|O_CREAT,0644);}
static int t_read(int fd,void*p,unsigned n){reads++;int r=read(fd,p,n);if(armed)pending=true;return r;}
static int t_write(int fd,const void*p,unsigned n){writes++;int r=write(fd,p,n);if(armed)pending=true;return r;}
static int t_seek(int fd,uint32_t off){return lseek(fd,off,SEEK_SET)<0?-1:0;}
static int t_sync(int fd){return fsync(fd);}
static int t_close(int fd){return close(fd);}
static int t_remove(const char*p){return unlink(p);}
static int t_size(const char*p){struct stat s;return stat(p,&s)==0?(int)s.st_size:-1;}
static void t_mkdir(const char*p){mkdir(p,0755);}
static void t_yield(void){steps++;}
static bool __attribute__((unused)) cancelled(void){return pending;}
static const struct cs_io io={t_open,t_read,t_write,t_seek,t_sync,t_close,t_remove,t_size,t_mkdir,t_yield};
static void init(const char*dir){cs_init(&store,&io,dir,CS_DEFAULT_CAP);@CANCEL@}
static void arm(void){pending=false;reads=writes=steps=0;armed=true;}
static void disarm(void){armed=pending=false;}
static void put(int n){memset(key,n,32);memset(payload,n,sizeof payload);assert(cs_put(&store,key,CS_XS,payload,cs_payload_size(CS_XS),buf,sizeof buf,NULL)==CS_OK);}
int main(int argc,char**argv)
{
    assert(argc==2);int stage=argv[1][0]-'0';
    char dir[24];snprintf(dir,sizeof dir,"stage-%d",stage);init(dir);
    int r=0;
    if(stage==0){arm();r=cs_load(&store,buf,sizeof buf,99);}
    else {
        assert(cs_load(&store,buf,sizeof buf,99)==CS_OK);
        if(stage>=3)for(int n=1;n<=12;n++)put(n);
        cs_release(&store);
        if(stage==1){arm();r=cs_load(&store,buf,sizeof buf,99);}
        if(stage==2){struct cs_item items[16];unsigned int count;arm();r=cs_list(&store,0,items,16,&count,buf,sizeof buf);}
        if(stage==3){
            char path[64];snprintf(path,sizeof path,"%s/index.cst",dir);assert(unlink(path)==0);
            assert(cs_load(&store,buf,sizeof buf,99)==CS_OK&&cs_busy(&store));
            arm();cs_maintain(&store,buf,sizeof buf,false);
        }
        if(stage==4){store.compact=0;store.compact_off=0;store.cur=-1;arm();cs_maintain(&store,buf,sizeof buf,true);}
    }
    printf("USB during stage=%d: reads=%u writes=%u checkpoints=%u result=%d\n",stage,reads,writes,steps,r);fflush(stdout);
    assert(pending);
    if(stage==0)assert(writes<=1);       /* no remaining 2 MiB table creation */
    if(stage==1)assert(reads<=1);        /* no remaining table validation */
    if(stage==2)assert(reads<=1);        /* no remaining sparse inventory */
    if(stage>=3)assert(reads<=8);        /* finish at most the current record */
    cs_unload(&store);assert(store.ifd==-1&&store.pfd==-1&&!store.loaded);
    disarm();assert(cs_load(&store,buf,sizeof buf,100)==CS_OK);
    while(cs_busy(&store))cs_maintain(&store,buf,sizeof buf,false);
    if(stage>=3){const uint8_t*p;unsigned int n;for(int i=1;i<=12;i++){
        memset(key,i,32);assert(cs_get(&store,key,CS_XS,buf,sizeof buf,&p,&n)==CS_OK);
        assert(n==cs_payload_size(CS_XS));for(unsigned j=0;j<n;j++)assert(p[j]==i);
    }}
    cs_unload(&store);puts("USB interruption + close + reload/rebuild/data integrity PASS");
}
'''.replace("@CANCEL@", "store.cancelled=cancelled;" if "(*cancelled)" in header else "")

def fn(text, name):
    m = re.search(r"(?m)^[A-Za-z_][\w \t*]*\b" + name +
                  r"\([^;{}]*\)\s*\{", text)
    assert m, name
    end, depth = m.end(), 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[m.start():end]


worker = fn((SOURCE / "apps-ipod/screens/playback/cider.c").read_text(), "disk_worker")
worker_ports = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <setjmp.h>
#define HZ 100
#define CS_OK 0
#define SYS_USB_CONNECTED 100
#define SYS_USB_DISCONNECTED 101
#define SYS_USB_CONNECTED_ACK 102
#define TIME_AFTER(a,b) ((a)>(b))
#define TIME_BEFORE(a,b) ((a)<(b))
#define QPEEK_REMOVE_EVENTS 1
#define QPEEK_FILTER1(x) (&(const long[2]){x,x})
struct queue_event {int id;intptr_t data;};
static jmp_buf done;
static int disk_queue,cider_mutex,locks,closed,acks,waits,dequeued;
static long current_tick;
static bool list_req,list_ready,disk_lookup;
static unsigned nfills;
static unsigned char disk_data[8];
static struct {bool loaded;}store;
static struct {bool active;uint32_t request;char key[65],tier[3];}transfer;
static long art_used_at;
static bool disk_usb_pending(void){return true;}
static bool queue_peek_ex(int*q,struct queue_event*ev,unsigned flags,const long(*f)[2])
{(void)q;(void)f;assert(flags==QPEEK_REMOVE_EVENTS);if(ev)*ev=(struct queue_event){SYS_USB_CONNECTED,42};return true;}
static void queue_wait_w_tmo(int*q,struct queue_event*ev,int ticks)
{(void)q;(void)ticks;dequeued++;
 /* An art wakeup and an old disconnect must stay ahead of the new connect. */
 *ev=(struct queue_event){dequeued==1?1:dequeued==2?SYS_USB_DISCONNECTED:SYS_USB_CONNECTED,42};}
static void queue_wait(int*q,struct queue_event*ev)
{(void)q;(void)ev;waits++;assert(acks==1);longjmp(done,1);}
static void mutex_lock(int*q){(void)q;locks++;}
static void mutex_unlock(int*q){(void)q;}
static void cs_unload(void*p){(void)p;closed++;store.loaded=false;}
static void usb_acknowledge(int id,intptr_t seq)
{assert(id==SYS_USB_CONNECTED_ACK&&seq==42&&closed==1);assert(locks==0&&dequeued==3);acks++;}
static bool tagcache_is_busy(void){return false;}
static bool storage_disk_is_active(void){return false;}
static int cs_load(void*p,void*b,unsigned n,uint32_t seed){(void)p;(void)b;(void)n;(void)seed;assert(false);return 0;}
static void snap_store(void){assert(false);}
static bool read_store(const char*k,const char*t,bool job,uint32_t req){(void)k;(void)t;(void)job;(void)req;assert(false);return false;}
static struct {char key[65];int tier;}fills[8];
static const char*tier_name(int t){(void)t;return "L";}
static void build_list(void){assert(false);}
static bool store_write_batch(void){assert(false);return false;}
static bool cs_maintain(void*p,void*b,unsigned n,bool idle){(void)p;(void)b;(void)n;(void)idle;assert(false);return false;}
static bool cs_busy(void*p){(void)p;return false;}
static bool queue_empty(int*q){(void)q;return false;}
static void cs_release(void*p){(void)p;assert(false);}
'''
worker_post = r'''
int main(void){store.loaded=true;list_req=list_ready=true;
 if(!setjmp(done))disk_worker();
 assert(acks==1&&waits==1&&closed==1&&!store.loaded);
 puts("production worker USB: FIFO across old disconnect/new connect, unload before ACK, zero UI/service mutex waits, parked with no disk access PASS");}
'''
flags = ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-g",
         "-fsanitize=address,undefined", "-fno-pie", "-no-pie"]
(OUT / "store-usb.c").write_text(pre)
subprocess.run(flags + ["-I" + str(SOURCE / "apps-ipod"),
                       "-I" + str(ROOT / "firmware/export"),
                       str(OUT / "store-usb.c"),
                       str(SOURCE / "apps-ipod/screens/playback/cider_store.c"),
                       str(ROOT / "firmware/usbstack/podlink_wire.c"),
                       "-o", str(OUT / "store-usb")], check=True)
(OUT / "worker-usb.c").write_text(worker_ports + worker + worker_post)
subprocess.run(flags + ["-Wno-unused-function", str(OUT / "worker-usb.c"),
                       "-o", str(OUT / "worker-usb")], check=True)
failures = []
for stage in range(5):
    scratch = OUT / ("scratch-" + str(stage))
    scratch.mkdir(exist_ok=True)
    r = subprocess.run([str(OUT / "store-usb"), str(stage)], cwd=scratch)
    if r.returncode:
        failures.append((stage, r.returncode))
r = subprocess.run([str(OUT / "worker-usb")])
if r.returncode:
    failures.append(("worker", r.returncode))
assert not failures, failures
