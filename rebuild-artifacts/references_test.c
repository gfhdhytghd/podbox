
#define _GNU_SOURCE
#include <assert.h>
#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <dirent.h>
#include <errno.h>
#include <limits.h>
#define MAX_PATH 512
#define MIN(a,b) ((a)<(b)?(a):(b))
#define MAX(a,b) ((a)>(b)?(a):(b))
#define ARRAYLEN(a) (sizeof(a)/sizeof((a)[0]))
#define logf(...) ((void)0)
#define debug_log(...) ((void)0)
#define yield() ((void)0)
#define HZ 100
#define TIME_BEFORE(a,b) ((long)((a)-(b))<0)

struct mutex {int n;};static struct mutex llist_mutex;
static void mutex_lock(struct mutex*m){(void)m;}static void mutex_unlock(struct mutex*m){(void)m;}
enum {TYPE_BITMAP=1};
struct memory_handle {int id,type,fd;unsigned references;int pinned;};
static struct memory_handle current;static bool alive;static int freed;
static struct memory_handle *find_handle(int id){return alive&&current.id==id?&current:NULL;}
static void close_fd(int*fd){*fd=-1;}
static void unlink_handle(struct memory_handle*h){assert(h->references==1);alive=false;freed++;}

#line 1628 "apps-ipod/audio/buffering.c"
bool bufretain(int handle_id)
{
    mutex_lock(&llist_mutex);
    struct memory_handle *h = find_handle(handle_id);
    bool ok = h && h->type == TYPE_BITMAP && h->references < UINT_MAX;
    if (ok) h->references++;
    mutex_unlock(&llist_mutex);
    return ok;
}

#line 1638 "apps-ipod/audio/buffering.c"
bool buf_pin_handle(int handle_id, bool pin)
{
    struct memory_handle *h = find_handle(handle_id);
    if (!h)
        return false;

    if (pin) {
        h->pinned++;
    } else if (h->pinned > 0) {
        h->pinned--;
    }

    return true;
}

#line 862 "apps-ipod/audio/buffering.c"
static bool close_handle(int handle_id)
{
    mutex_lock(&llist_mutex);
    struct memory_handle *h = find_handle(handle_id);

    /* If the handle is not found, it is closed */
    if (h) {
        if (h->references > 1)
            h->references--;
        else
        {
            close_fd(&h->fd);
            unlink_handle(h);
        }
    }

    mutex_unlock(&llist_mutex);
    return true;
}

int main(void){
 for(int i=0;i<1000;i++){
   current=(struct memory_handle){i+1,TYPE_BITMAP,-1,1,0};alive=true;
   assert(bufretain(i+1));assert(buf_pin_handle(i+1,true));
   assert(close_handle(i+1));assert(alive&&current.references==1);
   assert(buf_pin_handle(i+1,false));assert(close_handle(i+1));assert(!alive);
   assert(!bufretain(i+1));
 }
 assert(freed==1000);puts("1000 track releases preserve retained bitmap readers until their release");return 0;
}
