/* Run the production USB core with RAM-backed controller/class drivers.
 * No device nodes, filesystem mounts or real USB calls. */
#include <stdio.h>
#include "charging_stubs/config.h"
#define _BUTTON_H_
#include "../../firmware/usbstack/usb_core.c"
#include "../../firmware/target/arm/s5l8702/ipod6g/power-6g.c"

long current_tick;
static bool connected, leased, host_probe, exclusive, host_present;
static int applied_ma, update_pending, profile;
static int speed = 1;
uint32_t test_gpio, test_pcon11, test_pcon12, test_pdat;
static uint32_t previous_gpio, previous_previous_gpio;
uint32_t *test_gpio_register(void)
{
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
    return true;
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
    applied_ma = usb_committed_ma;
    assert(applied_ma == expected);
    /* The actual target callback's final GPIO command controls C1. */
    assert(previous_previous_gpio ==
           (uint32_t)(0xb070e | (expected < 100 ? 1 : 0)));
    assert(previous_gpio == (uint32_t)(0xb060e | (expected >= 500 ? 1 : 0)));
    assert(test_gpio == (uint32_t)(0xc010e | (expected >= 500 ? 0 : 1)));
    unsigned int status = power_input_status();
    assert(((status & POWER_INPUT_CHARGER) != 0) == (expected >= 500));
    if (connected)
        assert(test_gpio == (uint32_t)(0xc010e | (expected >= 500 ? 0 : 1)));
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
static void insert(int mode)
{
    connected = true;
    host_present = false;
    leased = profile != 0;
    usb_charging_enable(mode);
    usb_core_init();
    usb_core_enable_driver(USB_DRIVER_SERIAL, leased);
    usb_core_enable_driver(USB_DRIVER_MASS_STORAGE, !leased);
    usb_core_notify_set_address(1);
    usb_charger_update();
    expect_current(100);
}
static void unplug(void)
{
    connected = false;
    usb_core_exit();
    expect_current(0);
    assert(!usb_no_host_timeout.active);
}
int main(void)
{
    for (profile = 0; profile < 2; ++profile)
        for (speed = 0; speed < 2; ++speed)
            for (int mode = USB_CHARGING_DISABLE; mode <= USB_CHARGING_FORCE;
                 ++mode)
            {
                int grant = mode == USB_CHARGING_DISABLE ? 100 : 500;
                insert(mode);
                descriptor(USB_DT_CONFIG, grant / 2);
                descriptor(USB_DT_OTHER_SPEED_CONFIG, grant / 2);
                configure(1);
                assert(exclusive == !leased);
                expect_current(grant);
                usb_core_bus_suspend(true);
                expect_current(0);
                /* No-host timeout and force must never defeat suspend. */
                usb_no_host_callback(&usb_no_host_timeout);
                expect_current(0);
                usb_core_bus_suspend(false);
                expect_current(grant);
                configure(0);
                expect_current(100);
                configure(1);
                expect_current(grant);
                usb_core_bus_reset();
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
    /* Force/no-host and the DAC probe retain their existing power policy,
     * but neither can override unplug, off or suspend. */
    insert(USB_CHARGING_FORCE);
    usb_no_host_callback(&usb_no_host_timeout);
    expect_current(500);
    usb_core_bus_suspend(true);
    expect_current(0);
    usb_core_bus_suspend(false);
    expect_current(500);
    host_probe = true;
    usb_charging_enable(USB_CHARGING_ENABLE);
    expect_current(500);
    usb_charging_enable(USB_CHARGING_DISABLE);
    expect_current(100);
    usb_core_bus_suspend(true);
    expect_current(0);
    unplug();
    host_probe = false;
    /* A prior wall-charger detection must not defeat a new host limit. */
    insert(USB_CHARGING_ENABLE);
    usb_charger_detected = true;
    usb_charging_enable(USB_CHARGING_DISABLE);
    expect_current(100);
    usb_charger_detected = true;
    usb_core_bus_suspend(true);
    expect_current(0);
    unplug();
    puts("USB charging: storage/PodLink, both speeds, off/on/force PASS");
    return 0;
}
