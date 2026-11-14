/* Cider shuffle/repeat context page, in the native list style. GPL version 2+. */
#include "config.h"
#include "cider.h"
#include "cider_controls.h"
#include "input/action.h"
#include "lang.h"
#include "widgets/list.h"

const char *cider_mode_title(int mode)
{
    return str(mode == CIDER_MODE_REPEAT ? LANG_REPEAT : LANG_SHUFFLE);
}

const char *cider_mode_label(int mode, int value)
{
    if (mode == CIDER_MODE_REPEAT)
        return str(value == 1 ? LANG_ALL : value == 2 ? LANG_REPEAT_ONE :
                   value == 3 ? LANG_SHUFFLE :
                   value == 4 ? LANG_REPEAT_AB : LANG_OFF);
    return str(value ? LANG_ON : LANG_OFF);
}

static const char *context_name(int item, void *data, char *buf, size_t len)
{
    (void)data; (void)buf; (void)len;
    return cider_mode_title(item);
}

static const char *context_value(int item, void *data, char *buf, size_t len)
{
    (void)data; (void)buf; (void)len;
    return cider_mode_label(item, cider_mode_get(item));
}

static enum list_row_kind context_kind(int item, void *data)
{
    (void)data;
    return item == CIDER_MODE_SHUFFLE ? LIST_ROW_TOGGLE : LIST_ROW_SETTING;
}

/* SELECT cycles the chosen row; MENU leaves. A change the host reports
 * late (or refuses) shows up on the next tick. */
static int context_action(int action, struct gui_synclist *lists)
{
    static int shown[CIDER_MODE_COUNT];
    if (action == ACTION_STD_MENU)
        return ACTION_STD_CANCEL;
    if (action == ACTION_STD_OK)
    {
        int mode = gui_synclist_get_sel_pos(lists);
        if (mode >= 0 && mode < CIDER_MODE_COUNT)
            cider_mode_set(mode, cider_mode_step(mode, cider_mode_get(mode),
                                                 false));
        action = ACTION_REDRAW;
    }
    else if (action == ACTION_NONE)
    {
        for (int i = 0; i < CIDER_MODE_COUNT; i++)
        {
            int now = cider_mode_get(i);
            if (now != shown[i]) { shown[i] = now; action = ACTION_REDRAW; }
        }
        return action;
    }
    for (int i = 0; i < CIDER_MODE_COUNT; i++)
        shown[i] = cider_mode_get(i);
    return action;
}

bool cider_context_show(void)
{
    struct simplelist_info info;

    action_wait_for_release();
    simplelist_info_init(&info, str(LANG_NOW_PLAYING), CIDER_MODE_COUNT, NULL);
    info.get_name = context_name;
    info.get_value = context_value;
    info.get_kind = context_kind;
    info.action_callback = context_action;
    info.speak_onshow = false;
    return simplelist_show_list(&info);
}
