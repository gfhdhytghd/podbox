/* Real producers/ring/formatter; no hardware. GPL version 2+. */
#include "button.h"
#include "kernel.h"
#include "remote_log.h"
#include "iap/remote_capture.h"
#include "input/agents_remote.h"
#include "button_usb_remote.h"
#include "usb_ch9.h"
#include <limits.h>
#include <stdio.h>
#include <string.h>

long current_tick;
static char line[REMOTE_LOG_LINE];
static void expect(const char *needle)
{
    unsigned int n = remote_log_read(line, sizeof(line));
    assert(n && n == strlen(line) && n < REMOTE_LOG_LINE);
    assert(!memcmp(line, "LOG ", 4) && !strchr(line, '\n'));
    if (!strstr(line, needle)) {puts(line); assert(!"missing log text");}
}
static void raw(unsigned int length, unsigned int flags)
{
    remote_capture_begin(&iap_remote_capture, length);
    for (unsigned int i = 0; i < length; i++)
        remote_capture_byte(&iap_remote_capture, i == 0 ? 2 :
                            i == 2 ? 1 : 0);
    remote_capture_end(&iap_remote_capture, flags, current_tick);
}
static void gesture(struct agents_remote_state *s, int context, int button,
                    int held, unsigned long now)
{
    agents_remote_button(s, context, button, held, now);
    while (s->queued) agents_remote_button(s, context, 0, held, now);
}
int main(void)
{
    assert(!remote_log_enabled());
    unsigned int epoch = remote_log_epoch();
    raw(4, 0);
    remote_volume_log(1, 3, 6, 1, 'P');
    assert(!remote_log_read(line, sizeof(line)));
    remote_log_enable(true, remote_log_format);
    assert(remote_log_enabled() && remote_log_epoch() != epoch);
    current_tick = 12; raw(4, 0);
    expect("2 tick=12 +0.12s flags=0 L2/00 len=4 rx=4 stored=4 02 00 01 00  PLAY");
    current_tick = 24; raw(200, REMOTE_BAD_CHECK);
    expect("BAD:10 L2/00 len=200 rx=200 stored=128 TRUNC");
    assert(strlen(line) < REMOTE_LOG_LINE);
    remote_volume_log(30, 3, 6, 1, 'P');
    expect("VOL 2 tick=30 previous=03 target=06 dir=1 decision=P");
    remote_log_event(REMOTE_LOG_HELD, 31, BUTTON_RC_MENU, 0, NULL, 0);
    expect("HELD tick=31 MENU");
    struct usb_ctrlrequest req = {.bRequestType=0x40, .bRequest=0x5b,
        .wValue=1, .wIndex=1};
    current_tick=32; assert(button_usb_remote_request(&req));
    expect("EP0 tick=32 MENU down");
    req.wValue=2; req.wIndex=0;
    assert(button_usb_remote_request(&req)); expect("SELECT up");
    req.wValue=6; assert(!button_usb_remote_request(&req));
    assert(!remote_log_read(line, sizeof(line)));
    for (int c=REMOTE_OTHER; c<=REMOTE_COVERS; c++)
    {
        struct agents_remote_state s = {0};
        gesture(&s,c,BUTTON_RC_PLAY,BUTTON_RC_PLAY,100);
        gesture(&s,c,BUTTON_RC_PLAY|BUTTON_REL,0,110);
        gesture(&s,c,0,0,131); expect("PLAY tick=131 tap");
        gesture(&s,c,BUTTON_RC_PLAY,BUTTON_RC_PLAY,200);
        gesture(&s,c,BUTTON_RC_PLAY|BUTTON_REL,0,210);
        gesture(&s,c,BUTTON_RC_PLAY,BUTTON_RC_PLAY,220);
        gesture(&s,c,BUTTON_RC_PLAY|BUTTON_REL,0,225);
        gesture(&s,c,0,0,246);
        expect("PLAY tick=246 double");
        gesture(&s,c,BUTTON_RC_PLAY,BUTTON_RC_PLAY,300);
        gesture(&s,c,0,BUTTON_RC_PLAY,360); expect("PLAY tick=360 hold");
        gesture(&s,c,BUTTON_RC_PLAY|BUTTON_REL,0,361);
        gesture(&s,c,BUTTON_RC_PLAY,BUTTON_RC_PLAY,500);
        gesture(&s,c,BUTTON_RC_PLAY|BUTTON_REL,0,510);
        gesture(&s,c,BUTTON_RC_PLAY,BUTTON_RC_PLAY,520);
        gesture(&s,c,BUTTON_RC_PLAY|BUTTON_REL,0,530);
        gesture(&s,c,BUTTON_RC_PLAY,BUTTON_RC_PLAY,540);
        gesture(&s,c,BUTTON_RC_PLAY|BUTTON_REL,0,550);
        expect("PLAY tick=550 triple");
    }
    struct agents_remote_state s = {0};
    gesture(&s,REMOTE_AGENTS,BUTTON_RC_VOL_UP|BUTTON_REL|BUTTON_REPEAT,0,400);
    remote_log_event(REMOTE_LOG_PAGE,400,3,0,NULL,0);
    expect("PAGE tick=400 up");
    gesture(&s,REMOTE_AGENTS,BUTTON_RC_VOL_DOWN,0,401);
    remote_log_event(REMOTE_LOG_PAGE,401,0,0,NULL,0);
    expect("PAGE tick=401 down");
    gesture(&s,REMOTE_WPS,BUTTON_RC_VOL_UP,0,402);
    assert(!remote_log_read(line, sizeof(line)));
    for (int i=0; i<REMOTE_LOG_RECORDS+11; i++)
        remote_log_event(REMOTE_LOG_TAP,i,0,0,NULL,0);
    expect("LOST 11");
    for (int i=0; i<REMOTE_LOG_RECORDS; i++) expect("PLAY");
    assert(!remote_log_read(line, sizeof(line)));
    /* Continuous overflow cannot starve queued records behind LOST notices. */
    for (int i=0; i<REMOTE_LOG_RECORDS+1; i++)
        remote_log_event(REMOTE_LOG_TAP,i,0,0,NULL,0);
    expect("LOST 1");
    remote_log_event(REMOTE_LOG_TAP,1,0,0,NULL,0);
    expect("PLAY"); expect("LOST 1");
    epoch=remote_log_epoch();
    remote_log_enable(false, remote_log_format);
    assert(remote_log_epoch()!=epoch);
    raw(4,0); remote_volume_log(500,1,2,1,'P');
    assert(!remote_log_read(line,sizeof(line)));
    remote_log_enable(true, remote_log_format);
    assert(!remote_log_read(line,sizeof(line)));
    puts("remote stream: exact RAW/BAD/TRUNC/VOL, held, Play in 4 contexts, EP0 validation, page, bounded overflow/LOST fairness, off/reset PASS");
}
