/***************************************************************************
 * GNU General Public License (version 2+)
 *
 * PodLink's USB end, which a simulator does not have: the CDC byte stream
 * is a pseudo-terminal instead. firmware/usbstack/podlink.c runs unchanged
 * on top of it, so System > PodLink and System > Agents work in the sim.
 *
 * The first usb_podlink_open() creates the pty and prints its path, and
 * writes it to the file $PODLINK_SIM_PATH names when that is set. Point the
 * computer side at it, for example
 *   python3 tools/herdremote/herdremote.py --port /dev/pts/7
 * The pty lives as long as the sim. Closing the computer's end reads as an
 * unplug; opening it again reads as a plug. Each of those, and each open of
 * the link, starts a new stream generation. An unplug also drops what the
 * pty still holds, as a USB reset empties the device's buffers; a plug does
 * not, as the computer may already have written its OPEN.
 *
 * Like shim-dircache.c this implements rather than stubs (see README.md):
 * a stub that never connects would leave both screens with nothing to show.
 * A Windows sim has no pty and stays unplugged.
 ****************************************************************************/
#include "config.h"

#if defined(SIMULATOR) && defined(HAVE_USB_PODLINK)

#include <stdbool.h>
#include <stdint.h>
#include "kernel.h"
#include "usb.h"
#include "usbstack/usb_serial.h"

#ifndef _WIN32
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <termios.h>
#include <unistd.h>

static int pty = -1;

static void pty_create(void)
{
    int fd = posix_openpt(O_RDWR | O_NOCTTY | O_NONBLOCK);
    if (fd < 0 || grantpt(fd) || unlockpt(fd))
        goto fail;
    /* Raw on the computer's side, or the line discipline eats the frames */
    int slave = open(ptsname(fd), O_RDWR | O_NOCTTY);
    if (slave < 0)
        goto fail;
    struct termios t;
    tcgetattr(slave, &t);
    cfmakeraw(&t);
    tcsetattr(slave, TCSANOW, &t);
    close(slave);
    pty = fd;
    printf("PodLink: %s\n", ptsname(fd));
    fflush(stdout);
    const char *out = getenv("PODLINK_SIM_PATH");
    FILE *f = out ? fopen(out, "w") : NULL;
    if (f)
    {
        fprintf(f, "%s\n", ptsname(fd));
        fclose(f);
    }
    return;
fail:
    if (fd >= 0)
        close(fd);
}
#endif /* !_WIN32 */

static bool active;
static uint32_t generation;

bool usb_podlink_open(void)
{
    if (active)
        return false;
#ifndef _WIN32
    if (pty < 0)
        pty_create();
#endif
    ++generation;
    active = true;
    return true;
}

void usb_podlink_close(void)
{
    active = false;
}

bool usb_podlink_active(void)
{
    return active;
}

/* A pty master reads POLLHUP while no one holds the other end open. The
 * computer's end opening or closing is a new stream, as a plug or unplug
 * is; one noticed only by this call still counts. */
bool usb_serial_get_state(uint32_t *gen)
{
#ifndef _WIN32
    static bool peer;
    struct pollfd p = { .fd = pty, .events = POLLIN };
    bool now = false;
    if (pty >= 0 && active)
    {
        poll(&p, 1, 0);
        now = !(p.revents & POLLHUP);
    }
    if (now != peer)
    {
        peer = now;
        ++generation;
        if (!now && pty >= 0)
            tcflush(pty, TCIOFLUSH);
    }
    *gen = generation;
    return now;
#else
    *gen = generation;
    return false;
#endif
}

bool usb_serial_connected(void)
{
    uint32_t gen;
    return usb_serial_get_state(&gen);
}

/* The firmware sleeps on a semaphore the receive interrupt posts. A sim has
 * no interrupt, so it looks once and otherwise sleeps the whole wait. */
void usb_serial_wait(int ticks)
{
#ifndef _WIN32
    struct pollfd p = { .fd = pty, .events = POLLIN };
    if (pty >= 0 && poll(&p, 1, 0) > 0 && (p.revents & POLLIN))
        return;
#endif
    sleep(ticks);
}

int usb_serial_read(unsigned char *data, int length)
{
#ifndef _WIN32
    int n = read(pty, data, length);
    return n > 0 ? n : 0;
#else
    (void)data;
    (void)length;
    return 0;
#endif
}

int usb_serial_write(const unsigned char *data, int length)
{
#ifndef _WIN32
    int n = write(pty, data, length);
    return n > 0 ? n : 0;
#else
    (void)data;
    (void)length;
    return 0;
#endif
}

#endif /* SIMULATOR && HAVE_USB_PODLINK */
