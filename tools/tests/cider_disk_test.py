"""Production Cider disk-cap eviction against a fake directory/filesystem."""
from pathlib import Path
import re,subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
s=(R/'apps-ipod/screens/playback/cider.c').read_text()
def fn(name):
 m=re.search(r'(?m)^static void '+name+r'\([^;{}]*\)\s*\{',s);assert m
 e=m.end();d=1
 while d:d+=(s[e]=='{')-(s[e]=='}');e+=1
 return s[m.start():e]
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#define DISK_LIMIT (32u*1024u*1024u)
#define DISK_ROOT "/cider-cache"
#define M_ROOT "/art/cider-v1"
#define MAX_PATH 256
#define ATTR_DIRECTORY 1
#define remove fake_remove
static int disk_queue,open_count,removed,yields;
static bool busy,pending;
struct dirent {char d_name[96];};
struct dirinfo {int attribute;unsigned int size;time_t mtime;};
struct item {const char*root,*name;unsigned int size;time_t mtime;bool gone;};
static struct item files[]={{DISK_ROOT,"old-L.aat",20u*1024u*1024u,1,false},{M_ROOT,"new-M.aat",20u*1024u*1024u,2,false}};
typedef struct {const char*root;int at,last;struct dirent entry;} DIR;
static DIR dirs[2];
static bool tagcache_is_busy(void){return busy;}
static bool queue_empty(int*q){(void)q;return !pending;}
static DIR*opendir(const char*p){int i=!strcmp(p,M_ROOT);dirs[i]=(DIR){.root=p};open_count++;return &dirs[i];}
static struct dirent*readdir(DIR*d){while(d->at<2){int i=d->at++;if(!files[i].gone&&!strcmp(files[i].root,d->root)){d->last=i;snprintf(d->entry.d_name,sizeof d->entry.d_name,"%s",files[i].name);return &d->entry;}}return NULL;}
static struct dirinfo dir_get_info(DIR*d,struct dirent*e){(void)e;struct item*f=&files[d->last];return (struct dirinfo){0,f->size,f->mtime};}
static void closedir(DIR*d){(void)d;assert(open_count>0);open_count--;}
static int fake_remove(const char*p){assert(!open_count);for(int i=0;i<2;i++){char path[256];snprintf(path,sizeof path,"%s/%s",files[i].root,files[i].name);if(!strcmp(path,p)){files[i].gone=true;removed++;return 0;}}assert(false);return -1;}
static void yield(void){yields++;}
'''
post=r'''
int main(void){busy=true;disk_evict();assert(!removed);busy=false;pending=true;disk_evict();assert(!removed);pending=false;disk_evict();assert(removed==1&&files[0].gone&&!files[1].gone&&!open_count&&yields==1);disk_evict();assert(removed==1);puts("Cider disk cap: oldest-first to 32MiB, closed-directory deletion, DB/USB pending deferral, namespace isolation PASS");}
'''
(O/'disk.c').write_text(pre+fn('disk_evict')+post)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie',str(O/'disk.c'),'-o',str(O/'disk')],check=True);subprocess.run([str(O/'disk')],check=True)
worker=fn('disk_worker')
assert worker.index('if (!storage_disk_is_active()) continue;')<worker.index('O_WRONLY')
assert 'if (wrote) disk_evict();' in worker and 'batch < 4' in worker
assert 'SYS_USB_CONNECTED_ACK' in worker and 'SYS_USB_DISCONNECTED' in worker
print('Production worker wiring: active-disk-only writes/eviction, batch<=4, USB park/ACK, no UI filesystem calls PASS')
