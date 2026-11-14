"""Real RTC-record decoder/UI consumer with fake mailbox/RTC, ASan+UBSan."""
from pathlib import Path
import re,subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
s=(R/'apps-ipod/system/podlink_time.c').read_text();s=re.sub(r'^#include "[^\n]+\n','',s,flags=re.M)
pre=r'''
#define _GNU_SOURCE
#include <assert.h>
#include <stdlib.h>
#include <time.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#define HZ 100
#define PODLINK_TIME_RECORD 64
#define REMOTE_LOG_DATA 192
#define REMOTE_LOG_TIME 11
static unsigned long current_tick,received;
static bool pending,ui=true,rtc_fail;
static struct {bool sync_time_from_computer;}global_settings={true};
static struct {int debug_log_since;}global_status;
static struct tm rtc;
static int writes;
static char wire[64],logline[192];
static bool podlink_take_time(char*r,unsigned long*t){assert(ui);if(!pending)return false;memcpy(r,wire,64);*t=received;pending=false;return true;}
static int rtc_read_datetime(struct tm*t){assert(ui);*t=rtc;return 0;}
static int set_time(const struct tm*t){assert(ui);if(rtc_fail)return -1;rtc=*t;writes++;return 0;}
static void remote_log_event(unsigned k,unsigned long tick,unsigned long a,unsigned long b,const void*d,unsigned len){assert(k==REMOTE_LOG_TIME);(void)tick;(void)a;(void)b;assert(len<=sizeof(logline));memcpy(logline,d,len);}
'''
post=r'''
static void receive(const char*s){snprintf(wire,64,"%s",s);pending=true;received=current_tick;}
int main(void){
 setenv("TZ","UTC",1);tzset();struct tm t;int offset;
 const char*good="TS\t1\t2026-11-13T19:02:03\t-18000";
 assert(podlink_time_decode(good,&t,&offset)&&offset==-18000);
 assert(t.tm_hour==19&&t.tm_wday==5&&t.tm_yday==316);
 const char*bad[]={"", "TS\t2\t2026-11-13T19:02:03\t+0", "TS\t1\t2026-02-29T19:02:03\t+0", "TS\t1\t2000-02-30T00:00:00\t+0", "TS\t1\t2038-01-01T00:00:00\t+0", "TS\t1\t1999-01-01T00:00:00\t+0", "TS\t1\t2026-11-13T24:02:03\t+0", "TS\t1\t2026-11-13T19:60:03\t+0", "TS\t1\t2026-11-13T19:02:60\t+0", "TS\t1\t2026-11-13T19:02:03\t+50401", "TS\t1\t2026-11-13T19:02:03\t-50401", "TS\t1\t2026-11-13T19:02:03\t0", "TS\t1\t2026-11-13T19:02:03\t+0\n", "TS\t1\t2026-11-13T19:02:03\t+0\tjunk", "TS\t1\t2026-11-13T19:02:03\t+000000"};
 for(unsigned i=0;i<sizeof(bad)/sizeof(*bad);i++)assert(!podlink_time_decode(bad[i],&t,&offset));
 assert(podlink_time_decode("TS\t1\t2000-02-29T00:00:00\t+19800",&t,&offset));
 assert(podlink_time_decode("TS\t1\t2037-12-31T23:59:59\t+50400",&t,&offset));
 for(int d=-3;d<=3;d++){
  assert(podlink_time_decode(good,&rtc,&offset));time_t ts=mktime(&rtc)+d;gmtime_r(&ts,&rtc);
  int before=writes;receive(good);podlink_time_sync();
  assert(writes==before+(abs(d)>2));assert(strstr(logline,abs(d)>2?"applied":"ignored-drift"));
 }
 global_settings.sync_time_from_computer=false;receive(good);int n=writes;podlink_time_sync();assert(writes==n&&strstr(logline,"ignored-off"));
 global_settings.sync_time_from_computer=true;receive(bad[1]);podlink_time_sync();assert(strstr(logline,"ignored-invalid"));
 receive(good);current_tick+=501;podlink_time_sync();assert(writes==n&&strstr(logline,"ignored-stale"));
 receive(good);current_tick+=300;rtc.tm_year=0;global_status.debug_log_since=123;podlink_time_sync();assert(rtc.tm_sec==6&&writes==n+1);assert(global_status.debug_log_since==mktime(&rtc));n=writes;
 /* An exact civil-hour DST fall-back changes RTC, never ticks or durations. */
 assert(podlink_time_decode(good,&rtc,&offset));time_t before=mktime(&rtc);global_status.debug_log_since=before-60;
 receive("TS\t1\t2026-11-13T18:02:03\t-18000");unsigned long tick=current_tick;
 podlink_time_sync();assert(current_tick==tick&&writes==n+1);assert(mktime(&rtc)-global_status.debug_log_since==60);
 receive(good);rtc_fail=true;n=writes;podlink_time_sync();assert(writes==n&&strstr(logline,"ignored-rtc-error"));
 puts("clock decode/range/leap/DST/drift +/-2s/off/stale/age/RTC-error/UI-only/log-expiry PASS");
}
'''
def fn(t,n):
 m=re.search(r'(?m)^(?:static[ \t]+)?(?:int|void)[ \t*]+'+n+r'\([^;{}]*\)\s*\{',t);assert m,n
 end=m.end();depth=1
 while depth:depth+=(t[end]=='{')-(t[end]=='}');end+=1
 return t[m.start():end]
power=(R/'firmware/powermgmt.c').read_text()
sleep='static bool sleeptimer_active;static unsigned long sleeptimer_endtick;static int sleeptimer_duration;\n'+fn(power,'set_sleep_timer')+fn(power,'get_sleep_timer')
post=post.replace('setenv("TZ","UTC",1);','set_sleep_timer(600);setenv("TZ","UTC",1);').replace('int before=writes;receive(good);podlink_time_sync();','int before=writes;int seconds=get_sleep_timer();receive(good);podlink_time_sync();assert(get_sleep_timer()==seconds);')
(O/'clock.c').write_text(pre+sleep+s+post)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie',str(O/'clock.c'),'-o',str(O/'clock')],check=True)
subprocess.run([str(O/'clock')],check=True)
a=(R/'apps-ipod/input/action.c').read_text();service=(R/'firmware/usbstack/podlink_service.c').read_text()
assert 'set_time(' not in service and 'get_time(' not in service
assert 'button_queue_post(BUTTON_REDRAW, 0)' in service
assert a.count('podlink_time_sync();')==5
assert 'rtc_tm = *tm;' in (R/'firmware/common/timefuncs.c').read_text()
assert 'current_tick + seconds * HZ' in (R/'firmware/powermgmt.c').read_text()
print('Transport mailbox/wakeup; UI hooks; RTC cache; monotonic sleep timer wiring PASS')
