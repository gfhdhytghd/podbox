"""Real USB core/target driver, GPIO latch model and Debug renderer, offline."""
from pathlib import Path
import subprocess
import sys

R=Path(__file__).resolve().parents[2]
O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
cc=['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined',
    '-fno-pie','-no-pie','-ffunction-sections','-fdata-sections','-Wl,--gc-sections',
    '-I'+str(R/'tools/tests'),'-I'+str(R/'tools/tests/charging_stubs'),'-I'+str(R/'firmware/export'),'-I'+str(R/'firmware/usbstack')]
source=r'''
#define main previous_charging_main
INTEGRATION
#undef main
static void physical(int ma,bool inhibit) {
    flush_gpio();
    printf("physical expected=%d B=%02x C=%02x inhibit=%d\n",ma,gpio_b,gpio_c,inhibit);fflush(stdout);
    assert(!!(gpio_b&0x80)==(ma==0));
    assert(!!(gpio_b&0x40)==(ma>=500));
    assert(!!(gpio_c&1)==(ma==1000));
    assert(!!(gpio_c&2)==inhibit);
    assert(gpio_c&4); /* C2 remains untouched. */
}
static void high_request(void) {
    usb_charging_maxcurrent_change(500);
}
int main(void) {
    profile=2;insert(USB_CHARGING_ENABLE);gpio_c|=4;
    usb_charging_maxcurrent_change(100);physical(100,false);
    usb_charging_maxcurrent_change(0);physical(0,true);
    descriptor(USB_DT_CONFIG,250);configure(1);drain_charger();high_request();physical(500,false);
    /* Even a stale/oversized caller cannot assert C0 in normal firmware. */
    usb_charging_maxcurrent_change(1000);physical(500,false);
    usb_charging_maxcurrent_change(2000);physical(500,false);
    /* Restore the pre-M4 first-trigger pause, with no high-tier pre-stage. */
    pulses(8,5*HZ);assert(chrg_triggers==1&&chrg_backoff);physical(500,true);
    assert(chrg_retry_at-current_tick==60*HZ);
    trace(true,60*HZ);power_input_status();physical(500,false);
    pulses(8,5*HZ);assert(chrg_triggers==2&&chrg_backoff);
    assert(chrg_retry_at-current_tick==120*HZ);physical(500,true);
    usb_core_bus_suspend(true);drain_charger();physical(0,true);
    usb_core_bus_suspend(false);drain_charger();physical(500,true);
    trace(false,120*HZ);power_input_status();physical(500,false);
    trace(false,CHRG_STABLE_RESET);assert(chrg_next_backoff==60*HZ);physical(500,false);
    usb_charging_enable(USB_CHARGING_ENABLE);high_request();physical(500,false);
    usb_charging_enable(USB_CHARGING_DISABLE);physical(100,true);
    unplug();insert(USB_CHARGING_ENABLE);gpio_c|=4;descriptor(USB_DT_CONFIG,250);configure(1);drain_charger();high_request();physical(500,false);
    usb_charging_maxcurrent_change(100);physical(100,false);
    unplug();physical(0,true);
    puts("Normal input hard-cap 500 / C0 always low / C1-C2 / first-trigger pause-retry / reset PASS");
}
'''
integration=(R/'tools/tests/usb_charging_test.c').read_text().replace('../../firmware/',str(R/'firmware')+'/')
include='#include "'+str(R/'firmware/target/arm/s5l8702/ipod6g/power-6g.c')+'"'
integration=integration.replace(include, '#ifdef TEST_BOOTLOADER_POWER\n#define BOOTLOADER\n#endif\n'+include+'\n#ifdef TEST_BOOTLOADER_POWER\n#undef BOOTLOADER\n#endif')
source=source.replace('INTEGRATION',integration)
(O/'input.c').write_text(source)
for mode in ['normal']:
    out=O/('input-'+mode)
    subprocess.run(cc+(['-DTEST_BOOTLOADER_POWER'] if mode=='bootloader' else [])+[str(O/'input.c'),'-o',str(out)],check=True)
    subprocess.run([str(out)],check=True)
# Exercise the actual existing Debug USB/Battery row with output latches.
s=(R/'apps-ipod/screens/system/debug_menu.c').read_text()
a=s.index('                lcd_putsf(0, 9,',s.index('                unsigned int gpio_b = PDAT(11)'));b=s.index(';',a)+1
start=s.rfind('                int input_limit =',0,a)
body=s[start:b] if start>=0 and a-start<400 else s[a:b]
preamble=r'''
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
static char line[128];
static void lcd_putsf(int x,int y,const char *format,...) {
    (void)x;(void)y;va_list a;va_start(a,format);vsnprintf(line,sizeof line,format,a);va_end(a);
}
static void render(unsigned gpio_b,unsigned gpio_c){(void)gpio_b;(void)gpio_c;
'''
post=r'''
}
int main(void){
    render(0,0);assert(strstr(line,"100mA")&&strstr(line,"C0:0"));
    render(0x40,0);assert(strstr(line,"500mA")&&strstr(line,"C0:0"));
    render(0x40,1);assert(strstr(line,"1000mA")&&strstr(line,"C0:1"));
    render(0x80,0);assert(strstr(line,"0mA")&&strstr(line,"C0:0"));
    puts("Debug USB/Battery real row: 100/500/1000/suspend and C0 latch PASS");
}
'''
(O/'debug.c').write_text(preamble+body+post)
subprocess.run(cc+[str(O/'debug.c'),'-o',str(O/'debug')],check=True)
subprocess.run([str(O/'debug')],check=True)
