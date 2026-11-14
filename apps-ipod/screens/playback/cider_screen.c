/* Cider Now Playing: static native RGB565 art and remote controls. GPL v2+. */
#include "config.h"
#include "screens/playback/cider.h"
#include "input/action.h"
#include "input/agents_shortcut.h"
#include "system/activity.h"
#include "root_menu.h"
#include "lcd.h"
#include "font.h"
#include "kernel.h"
#include "button.h"
#include "draw/screen_access.h"
#include "usb.h"
#include "misc.h"
#include "backlight.h"
#include "iap/iap-core.h"
#include "draw/viewport.h"
#include <stdio.h>
#include <string.h>

static fb_data cover[240 * 240];

static void line(int y, const char *text)
{
    char fit[180];
    int width, height;
    snprintf(fit, sizeof fit, "%s", text);
    while (fit[0])
    {
        lcd_getstringsize(fit, &width, &height);
        if (width <= LCD_WIDTH - 16) break;
        unsigned int n = strlen(fit) - 1;
        while (n && ((unsigned char)fit[n] & 0xc0) == 0x80) n--;
        fit[n] = 0;
    }
    lcd_putsxy(8, y, fit);
}

static void draw(const struct cider_state *s, bool art, long seek)
{
    struct viewport vp = {0};
    viewport_set_fullscreen(&vp, SCREEN_MAIN);
    vp.font = FONT_UI;
    lcd_set_viewport(&vp);
    lcd_set_background(LCD_RGBPACK(18, 18, 18));
    lcd_clear_display();
    if (art) lcd_bitmap(cover, (LCD_WIDTH - 240) / 2, 0, 240, 240);
    else
    {
        lcd_set_foreground(LCD_RGBPACK((s->bg >> 16) & 255,
                            (s->bg >> 8) & 255, s->bg & 255));
        lcd_fillrect(40, 0, 240, 240);
    }
    lcd_set_foreground(LCD_RGBPACK(18, 18, 18));
    lcd_fillrect(0, LCD_HEIGHT - 90, LCD_WIDTH, 90);
    lcd_set_foreground(LCD_RGBPACK(244, 244, 244));
    line(LCD_HEIGHT - 86, s->title[0] ? s->title : "Cider");
    line(LCD_HEIGHT - 70, s->artist);
    line(LCD_HEIGHT - 54, s->album);
    uint32_t pos = seek >= 0 ? (uint32_t)seek :
                   cider_position(s, current_tick, HZ);
    lcd_set_foreground(LCD_RGBPACK(64, 64, 64));
    lcd_fillrect(8, LCD_HEIGHT - 35, LCD_WIDTH - 16, 3);
    lcd_set_foreground(LCD_RGBPACK(208, 208, 208));
    if (s->duration)
        lcd_fillrect(8, LCD_HEIGHT - 35,
                     (uint64_t)(LCD_WIDTH - 16) * pos / s->duration, 3);
    char text[96];
    snprintf(text, sizeof text, "%s  %lu:%02lu / %lu:%02lu  Vol %d",
             s->rate ? "Playing" : "Paused",
             (unsigned long)pos / 60000, (unsigned long)pos / 1000 % 60,
             (unsigned long)s->duration / 60000,
             (unsigned long)s->duration / 1000 % 60, s->volume);
    line(LCD_HEIGHT - 24, text);
    lcd_update();
    lcd_set_viewport(NULL);
}

int cider_screen(void)
{
    if (!cider_available()) return GO_TO_WPS;
    if (!agents_nav_enter(AGENTS_NAV_WPS)) return GO_TO_PREVIOUS;
    push_current_activity(ACTIVITY_WPS);
    cider_set_active(true);
    bool art = false;
    int result = GO_TO_PREVIOUS;
    char key[65] = "";
    struct cider_state s;
    long seek = -1;
    while (cider_available())
    {
        cider_snapshot(&s);
        if (strcmp(key, s.art))
        {
            snprintf(key, sizeof key, "%s", s.art);
            art = false;
            cider_request_art(key, "L");
        }
        if (!art) art = cider_copy_art(key, "L", cover, sizeof cover);
        draw(&s, art, seek);
        enum agents_nav_screen nav = agents_nav_pending();
        if (nav != AGENTS_NAV_NONE && nav != AGENTS_NAV_WPS)
        {
            result = nav == AGENTS_NAV_AGENTS ? GO_TO_AGENTS :
                     nav == AGENTS_NAV_COVERS ? GO_TO_ALBUM_COVERS :
                     GO_TO_PREVIOUS;
            break;
        }
        agents_nav_take(AGENTS_NAV_WPS);
        int action = get_action(CONTEXT_WPS, HZ / 10);
        switch (action)
        {
            case ACTION_WPS_PLAY:
                cider_control("toggle", 0);
                break;
            case ACTION_WPS_SKIPPREV:
                cider_control("previous", 0);
                break;
            case ACTION_WPS_SKIPNEXT:
                cider_control("next", 0);
                break;
            case ACTION_WPS_SEEKBACK:
            case ACTION_WPS_SEEKFWD:
                if (seek < 0) seek = cider_position(&s, current_tick, HZ);
                seek += action == ACTION_WPS_SEEKFWD ? 5000 : -5000;
                seek = MAX(0, MIN((long)s.duration, seek));
                break;
            case ACTION_WPS_STOPSEEK:
                if (seek >= 0) cider_control("seek", seek);
                seek = -1;
                break;
            case ACTION_WPS_VOLUP:
            case ACTION_WPS_VOLDOWN:
                s.volume = MAX(0, MIN(100, s.volume +
                           (action == ACTION_WPS_VOLUP ? 1 : -1)));
                cider_control("volume", s.volume);
                break;
            case ACTION_AGENTS_SWITCH:
            case ACTION_AGENTS_REMOTE_SWITCH:
                result = GO_TO_AGENTS;
                goto out;
            case ACTION_AGENTS_COVERS:
            case ACTION_AGENTS_REMOTE_COVERS:
                cover_nav_begin(GO_TO_CIDER);
                result = GO_TO_ALBUM_COVERS;
                goto out;
            case ACTION_WPS_MENU:
            case ACTION_STD_CANCEL:
                goto out;
            case ACTION_NONE:
            case ACTION_WPS_STOP:
                break;
            default:
                if (default_event_handler(action) == SYS_USB_CONNECTED)
                    goto out;
                break;
        }
    }
    if (!cider_available()) result = GO_TO_WPS;
out:
    cider_cancel_art();
    cider_set_active(false);
    pop_current_activity();
    agents_nav_leave(AGENTS_NAV_WPS);
    return result;
}
