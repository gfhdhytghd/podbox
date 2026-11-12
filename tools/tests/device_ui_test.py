#!/usr/bin/env python3
"""Host regressions for idle-list battery updates; no display/device access."""
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(os.environ.get('ART_TEST_ROOT', Path(__file__).resolve().parents[2]))
OUT = Path(tempfile.mkdtemp(prefix='device-ui-tests-'))
os.environ['ART_TEST_ROOT'] = str(ROOT)
os.environ['ART_TEST_OUT'] = str(OUT)
sys.path.insert(0, str(Path(__file__).with_name('albumart')))
from support import build, function, run  # noqa: E402

code = r'''
struct gui_synclist { int unused; };
static bool inhibited;
static int dirty, flushes, action_result;
static int list_do_action_timeout(struct gui_synclist *l, int t)
{ (void)l; return t; }
static void gui_synclist_keyclick_callback(void) {}
static void keyclick_set_callback(void (*f)(void), void *d)
{ (void)f; (void)d; }
static bool skin_flush_inhibited(void) { return inhibited; }
static void skin_inhibit_flush(bool value) { inhibited = value; }
static int get_action(int c, int t)
{
    (void)c; (void)t;
    assert(inhibited);
    dirty++; /* ACTIONUPDATE painted the battery in the framebuffer. */
    return action_result;
}
static bool gui_synclist_do_button(struct gui_synclist *l, int *a)
{ (void)l; return *a != 0; }
static void skin_flush_dirty(void)
{ assert(!inhibited); if (dirty) { flushes++; dirty = 0; } }
'''
listing = (ROOT / 'apps-ipod/widgets/list.c').read_text()
start = listing.index('bool list_do_action(')
code += listing[start:listing.index('\n}', start) + 2]
code += r'''
enum { NO_CHARGER, CHARGER_UNPLUGGED, CHARGER_PLUGGED, CHARGER };
enum { DISCHARGING, CHARGING };
enum { SYS_CHARGER_CONNECTED, SYS_CHARGER_DISCONNECTED };
static int charger_input_state, charge_state, charge_done_count;
static int last_sent_battery_level, broadcasts;
static bool charge_finished, pin_charging;
#define POWER_INPUT_CHARGER 2
#define CHARGE_DONE_SAMPLES 4
static bool charging_state(void) { return pin_charging; }
static void queue_broadcast(int e, int d)
{ (void)e; (void)d; broadcasts++; }
'''
code += function('firmware/powermgmt.c', 'detect_charger')
power = (ROOT / 'firmware/powermgmt.c').read_text()
code = code.replace('#define CHARGE_DONE_SAMPLES 4',
                    re.search(r'^#define CHARGE_DONE_SAMPLES\s+\d+',
                              power, re.M).group())
start = power.index('static inline void charging_algorithm_step',
                    power.index('#elif CONFIG_CHARGING == CHARGING_MONITOR'))
code += power[start:power.index('static inline void charging_algorithm_close', start)]
code += r'''
int main(void)
{
    struct gui_synclist list = {0};
    int a = -1;
    action_result = 0;
    assert(!list_do_action(0, 10, &list, &a));
    assert(a == 0 && flushes == 1 && !dirty && !inhibited);
    action_result = 1;
    assert(list_do_action(0, 10, &list, &a));
    assert(flushes == 2 && !dirty);
    inhibited = true;
    action_result = 0;
    assert(!list_do_action(0, 10, &list, &a));
    assert(inhibited && dirty && flushes == 2);
    inhibited = false;
    skin_flush_dirty();
    assert(flushes == 3);
    /* A nearly full/full battery with !CHRG high still advertises the
     * charging source; only the percentage cap follows this pin. */
    for (int active = 0; active < 2; active++)
    {
        charger_input_state = NO_CHARGER;
        charge_finished = false;
        charge_done_count = broadcasts = 0;
        pin_charging = active != 0;
        assert(detect_charger(POWER_INPUT_CHARGER));
        charging_algorithm_step();
        assert(charge_state == CHARGING);
        assert(detect_charger(POWER_INPUT_CHARGER));
        assert(charger_input_state == CHARGER && broadcasts == 1);
        for (int i = 0; i < CHARGE_DONE_SAMPLES; i++)
            charging_algorithm_step();
        assert(charge_state == CHARGING);
        assert(charge_finished == !pin_charging);
        assert(detect_charger(0));
        charging_algorithm_step();
        assert(charge_state == DISCHARGING && !charge_finished);
    }
    puts("PASS: idle status flush, handled action, outer inhibition, "
         "charging-source transitions and full-battery termination");
}
'''
exe = build('device-ui', code)
print(run([exe], 'device-ui'), end='')

# Both cells are in the shared Chrome layer. The busy condition cannot select
# the battery away. Verify the actual shipped theme, including link/time space.
skin = (ROOT / 'themes/scrim/.rockbox/wps/scrim.sbs').read_text()
blocks = re.split(r'(?m)^%Vl\(', skin)
battery = next(b for b in blocks if b.startswith('Chrome,289,5,24,24,2)'))
busy = next(b for b in blocks if b.startswith('Chrome,263,5,24,24,2)'))
assert '%?bc<' in battery and '%?bl<' in battery
assert '%lh' not in battery and '%lb' not in battery and '%lw' not in battery
assert '%?or(%lh,%lb,%lw)<' in busy and '%?la<' in busy
for label, title_end, link_x, time_x, time_w in [
    ('G', 150, 150, 184, 70), ('N', 102, 102, 136, 118)
]:
    assert link_x + 24 <= time_x
    assert time_x + time_w <= 263
    assert f'%Vl(Chrome{label}L,{link_x},5,24,24,2)' in skin
    assert f'%Vl(Chrome{label},{time_x},8,{time_w},24,1)' in skin
assert 263 + 24 <= 289 and 289 + 24 <= 320
print('PASS: Scrim battery and busy cells coexist with time and PodLink')
print('Logs:', OUT)
