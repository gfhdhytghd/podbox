/* Run the production USB core with RAM-backed controller/class drivers.
 * No device nodes, filesystem mounts or real USB calls. */
#include <stdio.h>
#include "charging_stubs/config.h"
#define _BUTTON_H_
#include "../../firmware/usbstack/usb_core.c"
#include "../../firmware/target/arm/s5l8702/ipod6g/power-6g.c"

long current_tick;
static bool connected, leased, host_probe, exclusive, host_present;
static bool backlight_on = true;
static int update_pending, profile;
static int speed = 1;
uint32_t test_gpio, test_pcon11, test_pcon12, test_pdat;
static uint32_t previous_gpio, previous_previous_gpio;
static uint32_t gpio_b, gpio_c;
static void flush_gpio(void)
{
    uint32_t bit = test_gpio & 1;
    switch (test_gpio & ~1u)
    {
        case 0xb070e: gpio_b = (gpio_b & ~0x80u) | (bit << 7); break;
        case 0xb060e: gpio_b = (gpio_b & ~0x40u) | (bit << 6); break;
        case 0xc010e: gpio_c = (gpio_c & ~2u) | (bit << 1); break;
    }
}
uint32_t *test_gpio_register(void)
{
    flush_gpio();
    previous_previous_gpio = previous_gpio;
    previous_gpio = test_gpio;
    return &test_gpio;
}
struct usb_drv_ep_spec usb_drv_ep_specs[USB_NUM_ENDPOINTS];
uint8_t usb_drv_ep_specs_flags;

static int first_interface(int n)
{
    return n + 1;
}
static int interface_descriptor(unsigned char *dest, int mps)
{
    (void)mps;
    struct usb_interface_descriptor d = {
        .bLength = sizeof(d),
        .bDescriptorType = USB_DT_INTERFACE,
    };
    memcpy(dest, &d, sizeof(d));
    return sizeof(d);
}
struct usb_class_driver usb_cdrv_storage = {
    .config = 1,
    .needs_exclusive_storage = true,
    .set_first_interface = first_interface,
    .get_config_descriptor = interface_descriptor,
};
struct usb_class_driver usb_cdrv_serial = {
    .config = 1,
    .set_first_interface = first_interface,
    .get_config_descriptor = interface_descriptor,
};
struct usb_class_driver usb_cdrv_charging_only = {
    .config = 1,
    .set_first_interface = first_interface,
    .get_config_descriptor = interface_descriptor,
};

bool usb_drive_only(void)
{
    return false;
}
bool usb_storage_host_wrote(void)
{
    return false;
}
bool usb_podlink_active(void)
{
    return leased;
}
bool usb_host_probe_active(void)
{
    return host_probe;
}
int usb_detect(void)
{
    return connected ? USB_INSERTED : USB_EXTRACTED;
}
bool usb_host_is_present(void)
{
    return connected && host_present;
}
bool is_backlight_on(bool ignore)
{
    (void)ignore;
    return backlight_on;
}
int pmu_firewire_present(void)
{
    return 0;
}
void usb_charger_update(void)
{
    ++update_pending;
}
void usb_request_exclusive_storage(void)
{
    exclusive = true;
}
void usb_release_exclusive_storage(void)
{
    exclusive = false;
}
void usb_record_waypoint(enum usb_waypoint w, int a, int b)
{
    (void)w;
    (void)a;
    (void)b;
}
void podlink_trace(unsigned int e, unsigned int d)
{
    (void)e;
    (void)d;
}
void trigger_cpu_boost(void)
{
}
void cancel_cpu_boost(void)
{
}
void usb_log(int t, int a, int b, uint32_t c, uint32_t d)
{
    (void)t;
    (void)a;
    (void)b;
    (void)c;
    (void)d;
}
void usb_drv_init(void)
{
}
void usb_drv_exit(void)
{
}
int usb_drv_port_speed(void)
{
    return speed;
}
void usb_drv_ep_init(const struct usb_drv_ep_alloc_ctx *c, int ep)
{
    (void)c;
    (void)ep;
}
void usb_drv_ep_deinit(const struct usb_drv_ep_alloc_ctx *c, int ep)
{
    (void)c;
    (void)ep;
}
void usb_clear_pending_transfer_completion_events(void)
{
}
void usb_signal_notify(long id, intptr_t data)
{
    if (id == USB_NOTIFY_SET_ADDR)
        host_present = true;
    usb_core_handle_notify(id, data);
}
void usb_signal_transfer_completion(
    struct usb_transfer_completion_event_data *e)
{
    usb_core_handle_transfer_completion(e);
}
void usb_drv_stall(int ep, bool stall, bool in)
{
    (void)ep;
    (void)stall;
    (void)in;
}
bool usb_drv_stalled(int ep, bool in)
{
    (void)ep;
    (void)in;
    return false;
}
void usb_drv_cancel_all_transfers(void)
{
}
void usb_drv_set_test_mode(int mode)
{
    (void)mode;
}
int usb_drv_recv_nonblocking(int ep, void *data, int size)
{
    (void)ep;
    (void)data;
    (void)size;
    return 0;
}
int usb_drv_send_nonblocking(int ep, void *data, int size)
{
    (void)ep;
    (void)data;
    (void)size;
    return 0;
}

static void drain_charger(void)
{
    if (update_pending)
    {
        update_pending = 0;
        usb_charging_maxcurrent_change(usb_charging_maxcurrent());
    }
}
static void expect_current(int expected)
{
    drain_charger();
    assert(usb_charging_maxcurrent() == expected);
    bool enabled = connected && expected >= 100 &&
                   usb_charging_get_mode() != USB_CHARGING_DISABLE &&
                   !chrg_backoff;
    flush_gpio();
    assert(!!(gpio_b & 0x80) == (expected < 100));
    assert(!!(gpio_b & 0x40) == (expected >= 500));
    assert(!!(gpio_c & 2) == !enabled);
    unsigned int status = power_input_status();
    assert(((status & POWER_INPUT_CHARGER) != 0) == enabled);
    flush_gpio();
    assert(!!(gpio_c & 2) == !enabled);
}
static void descriptor(int type, int expected)
{
    unsigned char data[256];
    struct usb_ctrlrequest req = {
        .bRequestType = USB_DIR_IN,
        .bRequest = USB_REQ_GET_DESCRIPTOR,
        .wValue = type << 8,
        .wLength = sizeof(data),
    };
    request_handler_device_get_descriptor(&req, data, sizeof(data));
    assert(((struct usb_config_descriptor *)data)->bMaxPower == expected);
    assert(((struct usb_config_descriptor *)data)->bConfigurationValue == 1);
}
static void configure(int config)
{
    struct usb_ctrlrequest req = {
        .bRequest = USB_REQ_SET_CONFIGURATION,
        .wValue = config,
    };
    usb_core_control_request_handler(&req, usb_control_data,
                                     sizeof(usb_control_data));
}
static void attach(int mode)
{
    /* Settings apply before the cable is inserted, including at cold boot. */
    connected = false;
    host_present = false;
    leased = profile == 1;
    usb_charging_enable(mode);
    assert(usb_charging_get_mode() == mode);
    assert(usb_charging_current_grant() == 0);
    connected = true;
    usb_core_init();
    /* Attach must undo the unplug/boot SUSP command before any host request.
     * Do not drain an artificial charger event: insertion queues none. */
    assert(usb_charging_maxcurrent() == 100);
    assert(previous_previous_gpio == 0xb070e);
    assert(previous_gpio == 0xb060e);
    assert(test_gpio == (uint32_t)(0xc010e |
           (mode == USB_CHARGING_DISABLE ? 1 : 0)));
    assert(usb_core_is_enabled());
    assert(usb_core_get_state() == DEFAULT);
    usb_core_enable_driver(USB_DRIVER_SERIAL, leased);
    usb_core_enable_driver(USB_DRIVER_MASS_STORAGE, profile == 0);
    usb_core_enable_driver(USB_DRIVER_CHARGING_ONLY, profile == 2);
    assert(usb_charging_current_grant() == 0);
}
static void insert(int mode)
{
    attach(mode);
    usb_core_notify_set_address(1);
    usb_charger_update();
    expect_current(100);
}
static void unplug(void)
{
    connected = false;
    usb_core_exit();
    expect_current(0);
    assert(!usb_core_is_enabled());
    assert(!usb_no_host_timeout.active);
    assert(!chrg_monitor_tmo.active);
    assert(!chrg_backoff && chrg_triggers == 0);
}
static void trace(bool high, unsigned int ticks)
{
    test_pdat = high ? 0x10 : 0;
    while (ticks--)
    {
        ++current_tick;
        if (chrg_monitor_tmo.active &&
            chrg_monitor_cb(&chrg_monitor_tmo) == 0)
            chrg_monitor_tmo.active = false;
        if (current_tick % (HZ/2) == 0)
            power_input_status();
    }
}
static void pulses(unsigned int count, unsigned int period)
{
    while (count--)
    {
        trace(false, period - HZ/10);
        trace(true, HZ/10);
        trace(false, 1);
    }
}
static void run_guard_traces(void)
{
    assert(CHRG_DROPOUTS == 8 && CHRG_WINDOW == 90*HZ);
    profile = 2;
    /* Normal computers at both budgets, dark or lit; never pause charge. */
    for (int configured = 0; configured < 2; ++configured)
        for (int light = 0; light < 2; ++light)
        {
            backlight_on = light != 0;
            insert(USB_CHARGING_ENABLE);
            if (configured)
            {
                descriptor(USB_DT_CONFIG, 250);
                configure(1);
            }
            trace(false, 20*60*HZ);
            assert(!chrg_backoff && chrg_triggers == 0);
            expect_current(configured ? 500 : 100);
            /* Full/termination and long thermal pauses are steady high. */
            trace(true, 20*60*HZ);
            trace(false, 10*HZ);
            assert(!chrg_backoff && chrg_count == 0);
            trace(true, HZ); /* thermal hold, then normal charge */
            trace(false, 10*HZ);
            assert(chrg_triggers == 0);
            /* 10 ms glitches, unqualified chatter and isolated pulses. */
            for (int n = 0; n < 16; ++n)
            {
                trace(false, 5*HZ);
                trace(true, 1);
            }
            for (int n = 0; n < 16; ++n)
            {
                trace(false, HZ/10);
                trace(true, HZ/10);
            }
            assert(chrg_triggers == 0);
            pulses(7, 5*HZ);
            assert(chrg_count == 7 && !chrg_backoff);
            trace(false, CHRG_WINDOW + HZ);
            assert(chrg_count == 0);
            pulses(1, 5*HZ);
            assert(chrg_count == 1 && !chrg_backoff);
            trace(true, HZ); /* long hold clears partial evidence */
            trace(false, HZ);
            assert(chrg_count == 0 && chrg_triggers == 0);
            unplug();
        }
    /* MFi can look exactly like a configured 500 mA PC. The trace, not
     * enumeration/identity, must cause each finite exponential back-off. */
    insert(USB_CHARGING_ENABLE);
    descriptor(USB_DT_CONFIG, 250);
    configure(1);
    unsigned int schedule[] = {60, 120, 240, 480, 960, 1800, 1800};
    for (unsigned int n = 0; n < ARRAYLEN(schedule); ++n)
    {
        pulses(8, 10*HZ);
        assert(chrg_backoff && chrg_triggers == n + 1);
        struct usb_charge_guard_status status;
        usb_charging_guard_get_status(&status);
        assert(status.dropouts == 8);
        assert(status.backoff_seconds == schedule[n]);
        assert(status.last_trigger_tick == current_tick);
        expect_current(500); /* C1 high, HPWR high, SUSP low */
        assert(!chrg_monitor_tmo.active);
        assert(power_input_status() == POWER_INPUT_USB);
        long retry_at = chrg_retry_at;
        trace(true, schedule[n]*HZ - 1);
        assert(TIME_BEFORE(current_tick, retry_at));
        assert(chrg_backoff && chrg_triggers == n + 1);
        trace(true, 1);
        power_input_status();
        assert(!chrg_backoff && chrg_monitor_tmo.active);
        assert(chrg_triggers == n + 1 && chrg_count == 0);
        expect_current(500);
    }
    /* Recovery restores the first delay only after sustained low !CHRG. */
    trace(false, CHRG_STABLE_RESET);
    assert(chrg_next_backoff == CHRG_BACKOFF_FIRST);
    pulses(8, 5*HZ);
    assert(chrg_backoff);
    assert(chrg_retry_at - current_tick == CHRG_BACKOFF_FIRST);
    /* Reapplying settings resets history and retries immediately. */
    usb_charging_enable(USB_CHARGING_ENABLE);
    assert(!chrg_backoff && !chrg_count && !chrg_triggers);
    expect_current(500);
    pulses(8, 5*HZ);
    assert(chrg_backoff);
    usb_core_bus_suspend(true);
    expect_current(0);
    trace(true, 10*HZ);
    usb_core_bus_suspend(false);
    expect_current(500);
    assert(chrg_backoff); /* resume must respect the remaining pause */
    usb_charging_enable(USB_CHARGING_DISABLE);
    expect_current(100);
    assert(!chrg_backoff && !chrg_count && !chrg_triggers);
    /* The exact same signature works on an unconfigured 100 mA computer
     * and a wall/power-bank path with no host, irrespective of backlight. */
    unplug();
    for (int host = 0; host < 2; ++host)
        for (int light = 0; light < 2; ++light)
        {
            backlight_on = light != 0;
            attach(USB_CHARGING_ENABLE);
            host_present = host != 0;
            pulses(8, 5*HZ);
            assert(chrg_backoff && chrg_triggers == 1);
            expect_current(100);
            unplug();
            attach(USB_CHARGING_ENABLE);
            assert(!chrg_backoff && !chrg_count && !chrg_triggers);
            expect_current(100);
            usb_charging_enable(USB_CHARGING_DISABLE);
            trace(false, HZ);
            pulses(8, 5*HZ);
            assert(!chrg_monitor_tmo.active && chrg_triggers == 0);
            expect_current(100);
            usb_charging_enable(USB_CHARGING_ENABLE);
            usb_core_bus_suspend(true);
            expect_current(0);
            pulses(8, 5*HZ);
            assert(!chrg_monitor_tmo.active && chrg_triggers == 0);
            usb_core_bus_suspend(false);
            expect_current(100);
            unplug();
        }
    /* Qualify sampled widths exactly: 10/20/490/500 ms. A long high
     * clears evidence; each accepted pulse needs a full low second. */
    unsigned int widths[] = {1, 2, HZ/2 - 1, HZ/2};
    for (unsigned int n = 0; n < ARRAYLEN(widths); ++n)
    {
        attach(USB_CHARGING_ENABLE);
        for (int pulse = 0; pulse < 8; ++pulse)
        {
            trace(false, HZ);
            trace(true, widths[n]);
            trace(false, 1);
        }
        assert(chrg_backoff == (n == 1 || n == 2));
        unplug();
    }
    /* A rolling window must retain fresh pulses while expiring the oldest. */
    attach(USB_CHARGING_ENABLE);
    pulses(1, 5*HZ);
    trace(false, 80*HZ);
    pulses(7, 5*HZ);
    assert(chrg_count == 7 && !chrg_backoff);
    pulses(1, 5*HZ);
    assert(chrg_backoff);
    unplug();
    puts("Auto guard: steady PC 100/500, full/thermal/glitch/chatter, "
         "weak DAC, 1/2/4/8/16/30/30 minute retry, recovery, "
         "unplug/settings/off/suspend PASS");
}

int main(void)
{
    for (profile = 0; profile < 3; ++profile)
        for (speed = 0; speed < 2; ++speed)
            for (int mode = USB_CHARGING_DISABLE; mode <= USB_CHARGING_FORCE;
                 ++mode)
            {
                int grant = mode == USB_CHARGING_DISABLE ? 100 : 500;
                insert(mode);
                descriptor(USB_DT_CONFIG, grant / 2);
                descriptor(USB_DT_OTHER_SPEED_CONFIG, grant / 2);
                configure(1);
                assert(exclusive == (profile == 0));
                assert(usb_charging_current_grant() == grant);
                expect_current(grant);
                usb_core_bus_suspend(true);
                assert(usb_charging_current_grant() == 0);
                expect_current(0);
                /* No-host timeout and force must never defeat suspend. */
                usb_no_host_callback(&usb_no_host_timeout);
                expect_current(0);
                usb_core_bus_suspend(false);
                assert(usb_charging_current_grant() == grant);
                expect_current(grant);
                configure(0);
                assert(usb_charging_current_grant() == 0);
                expect_current(100);
                configure(1);
                expect_current(grant);
                usb_core_bus_reset();
                assert(usb_charging_current_grant() == 0);
                expect_current(100);
                usb_core_notify_set_address(1);
                /* Hosts can reuse the descriptor across a bus reset. */
                configure(1);
                expect_current(grant);
                descriptor(USB_DT_CONFIG, grant / 2);
                configure(1);
                expect_current(grant);
                usb_core_bus_suspend(true);
                expect_current(0);
                unplug();
                insert(mode);
                descriptor(USB_DT_CONFIG, grant / 2);
                configure(1);
                expect_current(grant);
                unplug();
            }
    /* A cached 100mA descriptor remains the grant until SET_CONFIGURATION,
     * even after the user enables charging and reads a 500mA descriptor. */
    profile = 1;
    insert(USB_CHARGING_DISABLE);
    descriptor(USB_DT_CONFIG, 50);
    configure(1);
    usb_charging_enable(USB_CHARGING_ENABLE);
    expect_current(100);
    descriptor(USB_DT_OTHER_SPEED_CONFIG, 250);
    configure(1);
    expect_current(100);
    descriptor(USB_DT_CONFIG, 250);
    usb_charger_update();
    expect_current(100);
    configure(1);
    expect_current(500);
    usb_charging_enable(USB_CHARGING_DISABLE);
    expect_current(100);
    unplug();
    /* Wall/power-bank charging is immediate and independent of backlight,
     * !CHRG (including full battery) and insertion/no-host timeout. */
    for (int light = 0; light < 2; ++light)
        for (int mode = USB_CHARGING_DISABLE; mode <= USB_CHARGING_FORCE;
             ++mode)
        {
            backlight_on = light != 0;
            attach(mode);
            expect_current(100);
            assert(chrg_monitor_tmo.active ==
                   (mode != USB_CHARGING_DISABLE));
            current_tick += 20*HZ;
            test_pdat = 0x10;
            expect_current(100);
            usb_no_host_callback(&usb_no_host_timeout);
            expect_current(mode == USB_CHARGING_FORCE ? 500 : 100);
            unplug();
        }
    test_pdat = 0;
    /* Descriptor requests may cancel no-host timeout without configuration.
     * This must not inhibit charging at the safe 100 mA input limit. */
    attach(USB_CHARGING_ENABLE);
    struct usb_ctrlrequest req = {
        .bRequest = USB_REQ_GET_DESCRIPTOR,
        .bRequestType = USB_DIR_IN,
        .wValue = USB_DT_DEVICE << 8,
        .wLength = sizeof(struct usb_device_descriptor),
    };
    usb_core_control_request_handler(&req, usb_control_data,
                                    sizeof(usb_control_data));
    assert(!usb_no_host_timeout.active);
    assert(usb_charging_current_grant() == 0);
    host_present = true;
    expect_current(100);
    unplug();
    /* Powered USB host DAC: 500 mA is not a configured computer grant.
     * No input limit, host mode or force bypasses off or suspend. */
    insert(USB_CHARGING_ENABLE);
    host_probe = true;
    usb_core_exit();
    assert(!usb_core_is_enabled());
    usb_charger_update();
    expect_current(500);
    assert(usb_charging_current_grant() == 0);
    usb_charging_enable(USB_CHARGING_DISABLE);
    expect_current(100);
    usb_core_bus_suspend(true);
    expect_current(0);
    unplug();
    host_probe = false;
    puts("USB charging: storage/PodLink/charge-only, both speeds, "
         "off/on/force, attach/configure/reset/suspend/replug, "
         "wall dark/lit/full, powered DAC PASS");
    run_guard_traces();
    return 0;
}
