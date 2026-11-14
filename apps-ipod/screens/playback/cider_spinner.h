/* Integer spinner geometry shared by the skin and host tests. GPL version 2+. */
#ifndef CIDER_SPINNER_H
#define CIDER_SPINNER_H
#include <stdint.h>
#include <stdbool.h>
struct cider_dot { int x, y, diameter, quarter; };
static inline unsigned cider_spinner_frame(uint32_t tick, unsigned hz)
{
    /* HZ=100: alternate 12/13 ticks, with exactly eight frames per second. */
    return hz ? (tick % hz) * 8 / hz : 0;
}
static inline unsigned cider_spinner_wait(uint32_t tick, unsigned hz)
{
    unsigned frame = cider_spinner_frame(tick, hz);
    return hz ? ((frame + 1) * hz + 7) / 8 - tick % hz : 1;
}
static inline int cider_spinner_dots(int w, int h, unsigned frame,
                                    struct cider_dot dots[4])
{
    if (w < 1 || h < 1) return 0;
    int size = w < h ? w : h;
    int d = (size + 2) / 5;
    if (d < 1) d = 1;
    int span = size - d;
    /* Clockwise from twelve o'clock; 181/256 approximates sqrt(1/2). */
    static const int8_t dx[8] = {0, 90, 127, 90, 0, -90, -127, -90};
    static const int8_t dy[8] = {-127, -90, 0, 90, 127, 90, 0, -90};
    for (int i = 0; i < 4; i++)
    {
        unsigned p = (frame + 8 - i) % (size < 7 ? 4 : 8);
        int x, y;
        if (size < 7)
        {
            x = (p == 1 || p == 2) ? span : 0;
            y = p >= 2 ? span : 0;
        }
        else
        {
            x = (span * (127 + dx[p]) + 127) / 254;
            y = (span * (127 + dy[p]) + 127) / 254;
        }
        dots[i] = (struct cider_dot){(w-size)/2+x,(h-size)/2+y,d,4-i};
    }
    return 4;
}
static inline bool cider_spinner_show(bool active, bool loading, int playmode)
{
    return active && loading && playmode <= 2; /* FF/RW have priority. */
}
#endif
