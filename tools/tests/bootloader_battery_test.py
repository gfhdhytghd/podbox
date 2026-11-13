"""Execute the real bootloader battery trap with offline ADC/power ports."""
from pathlib import Path
import re
import subprocess
import sys

R = Path(__file__).resolve().parents[2]
O = Path(sys.argv[1]).resolve()
O.mkdir(parents=True, exist_ok=True)
S = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else R
source = (S / 'bootloader/ipod-s5l87xx.c').read_text()
m = re.search(r'(?m)^static void battery_trap\(void\)\s*\{', source)
end, depth = m.end(), 1
while depth:
    depth += (source[end] == '{') - (source[end] == '}')
    end += 1
limits = re.search(r'enum battery_trap_limits\s*\{[^}]+\};', source)
pre = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <setjmp.h>
#include <string.h>
#define HZ 100
#define POWER_INPUT_NONE 0
#define POWER_INPUT_USB 1
#define LCD_WHITE 1
#define LCD_RBYELLOW 2
#define LCD_REDORANGE 3
#define PCF5063X_REG_SVMCTL 10
#define USB_CHARGING_ENABLE 1
static bool verbose, plugged, charge_enabled, yield_charge;
static unsigned short battery_level_disksafe __attribute__((unused))=3500;
static int line, limit_ma, mode, nread, samples[8], nsamples;
static int charge_waits, countdown_waits, writes, value_written, failures;
static int attach_after, recover_after, drop_after, limits[64], nlimits;
static jmp_buf stop;
enum { BOOT, OFF, WAIT };
static void usb_charging_maxcurrent_change(int ma) {
    assert(nlimits<64);limits[nlimits++]=ma;limit_ma=ma;
    charge_enabled=plugged&&mode==USB_CHARGING_ENABLE&&ma>=100;
}
static void __attribute__((unused)) usb_charging_enable(int value) {
    mode=value;usb_charging_maxcurrent_change(100);
}
static int _battery_voltage(void) {
    if(nread>=40)longjmp(stop,WAIT);
    int i=nread++;return samples[i<nsamples?i:nsamples-1];
}
static unsigned power_input_status(void){return plugged?POWER_INPUT_USB:POWER_INPUT_NONE;}
static void lcd_set_foreground(int c){(void)c;}
static void sleep(int ticks) {
    if(ticks==HZ*3) {
        charge_waits++;
        if(drop_after&&charge_waits==drop_after)plugged=false;
        if(yield_charge)longjmp(stop,WAIT);
    } else {
        assert(ticks==HZ);countdown_waits++;
        if(attach_after&&countdown_waits==attach_after)plugged=true;
        if(recover_after&&countdown_waits==recover_after)samples[nsamples-1]=3400;
    }
}
static void pmu_write(int reg,int value){assert(reg==PCF5063X_REG_SVMCTL);writes++;value_written=value;}
static void power_off(void){longjmp(stop,OFF);}
static int quiet(const char*fmt,...){(void)fmt;return 0;}
#define printf quiet
'''
post = r'''
#undef printf
static void reset(int voltage,bool power) {
    verbose=false;plugged=power;charge_enabled=false;yield_charge=true;
    mode=limit_ma=nread=charge_waits=countdown_waits=writes=value_written=0;
    attach_after=recover_after=drop_after=nlimits=0;
    nsamples=1;samples[0]=voltage;
}
static int run(void){int result=setjmp(stop);if(result)return result;battery_trap();return BOOT;}
#define CHECK(c) do{if(!(c)){fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#c);failures++;}}while(0)
int main(void) {
    const int volts[]={3199,3200,3249,3250,3399,3400};
    for(int power=0;power<2;power++)for(unsigned i=0;i<sizeof volts/sizeof volts[0];i++) {
        int mv=volts[i];reset(mv,power);int result=run();
        printf("power=%d adc=%d result=%d limit=%d enabled=%d waits=%d/%d vsys=%d\n",
               power,mv,result,limit_ma,charge_enabled,charge_waits,countdown_waits,writes);
        if(mv>=3400){CHECK(result==BOOT);CHECK(!charge_waits&&!countdown_waits);CHECK(!verbose);}
        else if(power){CHECK(result==WAIT);CHECK(charge_waits==1);CHECK(limit_ma==(mv<3250?100:1000));CHECK(charge_enabled);}
        else{CHECK(result==OFF);CHECK(countdown_waits==10);CHECK(writes==(mv<3200));if(writes)CHECK(value_written==0xA);}
    }
    reset(3249,true);yield_charge=false;nsamples=3;samples[1]=3250;samples[2]=3400;
    CHECK(run()==BOOT);CHECK(charge_waits==2);CHECK(limits[nlimits-2]==100&&limits[nlimits-1]==1000);
    /* Literal ADC policy: 3200mV cell + 200mV charging uplift reads 3400.
     * This deliberately documents the lack of an unloaded confirmation. */
    reset(3200+200,true);CHECK(run()==BOOT);CHECK(!charge_waits);
    reset(3250,true);yield_charge=false;nsamples=3;samples[1]=3399;samples[2]=3400;
    CHECK(run()==BOOT);CHECK(charge_waits==2&&limit_ma==1000);
    reset(3250,false);yield_charge=false;attach_after=2;
    nsamples=5;samples[1]=3250;samples[2]=3250;samples[3]=3399;samples[4]=3400;
    CHECK(run()==BOOT);CHECK(countdown_waits==2&&charge_waits==1&&limit_ma==1000);
    reset(3399,true);yield_charge=false;drop_after=1;
    CHECK(run()==OFF);CHECK(charge_waits==1&&countdown_waits==10&&limit_ma==100);
    reset(3399,false);yield_charge=false;recover_after=2;
    CHECK(run()==BOOT);CHECK(countdown_waits==2);
    if(failures){fprintf(stderr,"%d battery-trap assertions failed\n",failures);return 1;}
    puts("bootloader ADC boundaries / charging loop / power attach-loss / countdown / Vsysok / charging offset PASS");
}
'''
(O / 'battery.c').write_text(pre + (limits[0] if limits else '') + source[m.start():end] + post)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g',
                '-fsanitize=address,undefined','-fno-sanitize-recover=all',
                '-fno-pie','-no-pie',str(O/'battery.c'),'-o',str(O/'battery')],check=True)
subprocess.run([str(O/'battery')],check=True)
