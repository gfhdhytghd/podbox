"""Production request scheduling must not wait for the optional disk cache."""
from pathlib import Path
import re,subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
s=Path(sys.argv[2]).read_text() if len(sys.argv)>2 else (R/'apps-ipod/screens/playback/cider.c').read_text()
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
#define HZ 100
#define TIME_AFTER(a,b) ((long)((a)-(b))>0)
static int cider_mutex,disk_queue;
static struct cider_state state;
static struct cider_cache cache;
static struct cider_transfer transfer;
static bool disk_lookup,request_ready,disk_running=true,cancel_all,warm_ready;
static uint32_t next_request,grant,granted,cancel_request;
static unsigned int request_tries,ncommands,xs_count;
static char commands[12][384],xs_keys[5][65],warm_keys[455],warm_tier[3];
static long current_tick=1000,hello_at,request_at,art_used_at;
static void mutex_lock(int*p){(void)p;}static void mutex_unlock(int*p){(void)p;}
static bool storage_disk_is_active(void){return true;}
static void queue_post(int*q,int n,int v){(void)q;(void)n;(void)v;}
static void cider_log(const char*f,...){(void)f;}
static unsigned int cider_library_next(char*r,unsigned int n,uint32_t g){(void)r;(void)n;(void)g;return 0;}
'''
post=r'''
int main(void){state.generation=7;char key[65];memset(key,'a',64);key[64]=0;char out[512];
cider_request_art(key,"M");assert(transfer.active&&disk_lookup);
assert(next(out,sizeof out)&&!strncmp(out,"cg\t",3));
assert(next(out,sizeof out)&&!strncmp(out,"cr\t",3));assert(disk_lookup);
/* The request has left while the fake disk lookup remains blocked. */
puts("Production art request races disk lookup; network is not gated by disk cache PASS");}
'''
(O/'request.c').write_text(pre+fn('valid_key')+fn('next')+fn('cider_request_art')+post)
cmd=['cc','-std=gnu11','-Wall','-Wextra','-Wno-unused-variable','-Wno-unused-function','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(R/'apps-ipod'),str(O/'request.c'),str(R/'apps-ipod/screens/playback/cider_model.c'),str(R/'firmware/usbstack/podlink_wire.c'),'-I'+str(R/'firmware/export'),'-o',str(O/'request')]
subprocess.run(cmd,check=True);subprocess.run([str(O/'request')],check=True)
