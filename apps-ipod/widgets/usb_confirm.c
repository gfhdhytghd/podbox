/* PodBox USB consent popup. GNU General Public License, version 2+.
 * Uses the shared popup frame over the current screen; playback stays live. */
#include "config.h"
#include "usb.h"
#ifdef HAVE_USB_CONFIRM
#include "widgets/usb_confirm.h"
#include "widgets/dialog.h"
#include "input/action.h"
#include "system/shutdown.h"
#include "screens/playback/wps.h"
#include "audio.h"
#include "backlight.h"

static const char *const lines[] = {
    "Computer connected",
    "SELECT  Connect",
    "MENU    Charge only",
};

static void confirm_measure(struct dialog *d, struct screen *s,
                            struct viewport *box, void *data)
{
    (void)data;
    struct dialog_insets in;
    int width = 0;
    dialog_get_insets(&d->style, &in);
    for (unsigned i = 0; i < ARRAYLEN(lines); ++i)
    {
        int w;
        s->getstringsize(lines[i], &w, NULL);
        width = MAX(width, w);
    }
    box->width = MIN(width + in.left + in.right, s->lcdwidth - 24);
    box->height = ARRAYLEN(lines) * s->getcharheight() + in.top + in.bottom;
    box->x = (s->lcdwidth - box->width) / 2;
    box->y = (s->lcdheight - box->height) / 2;
}

static void confirm_draw(struct dialog *d, struct screen *s,
                         struct viewport *content, void *data)
{
    (void)d;
    (void)data;
    content->flags |= VP_FLAG_ALIGN_CENTER;
    s->set_viewport(content);
    s->set_drawmode(DRMODE_FG);
    for (unsigned i = 0; i < ARRAYLEN(lines); ++i)
        s->putsxy(0, i * s->getcharheight(), lines[i]);
}

static int confirm_action(struct dialog *d, int action, void *data)
{
    uint32_t epoch = *(uint32_t *)data;
    if (!usb_confirm_pending(epoch))
        return DIALOG_ABORT;
    switch (action)
    {
        case ACTION_STD_OK:
        case ACTION_STD_CANCEL:
            if (!d->backlight_on)
                break;
            usb_confirm_reply(epoch, action == ACTION_STD_OK);
            return DIALOG_ACCEPT;
        case ACTION_WPS_PLAY:
        case BUTTON_MULTIMEDIA_PLAYPAUSE:
            if (audio_status() & AUDIO_STATUS_PLAY)
            {
                if (audio_status() & AUDIO_STATUS_PAUSE)
                    unpause_action(true);
                else
                    pause_action(true);
            }
            break;
        default:
            if (action & SYS_EVENT)
                default_event_handler(action);
            break;
    }
    return DIALOG_CONTINUE;
}

static const struct button_mapping *confirm_keymap(int context)
{
    (void)context;
    static const struct button_mapping keys[] = {
        { ACTION_STD_OK, BUTTON_SELECT, BUTTON_NONE },
        { ACTION_STD_CANCEL, BUTTON_MENU, BUTTON_NONE },
        { ACTION_WPS_PLAY, BUTTON_PLAY | BUTTON_REL, BUTTON_PLAY },
        LAST_ITEM_IN_LIST
    };
    return keys;
}

void gui_usb_confirm_run(uint32_t epoch)
{
    static const struct dialog_callbacks cb = {
        .measure = confirm_measure,
        .draw = confirm_draw,
        .on_action = confirm_action,
    };
    if (!usb_confirm_pending(epoch))
        return;
    backlight_on();
    struct dialog d;
    dialog_init(&d, CONTEXT_STD, NULL, NULL, &cb, &epoch);
    d.keymap = confirm_keymap;
    dialog_run(&d, HZ / 10);
    action_wait_for_release();
}
#endif
