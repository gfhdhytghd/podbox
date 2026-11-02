/***************************************************************************
 * GNU General Public License (version 2+)
 *
 * System > Agents: the herdr agent dashboard and remote, over PodLink.
 *
 * The computer runs tools/herdremote, which sends HerdRemote v1 messages
 * (docs/podbox/herdremote-protocol.md) describing herdr's workspaces, the
 * agents in each workspace's first tab and any choice question one of them
 * is asking. This screen keeps that picture in herdremote_model.c and sends
 * back focus and answer commands. Three views share one list:
 *
 *   spaces    workspaces with their agent counts; SELECT focuses one on the
 *             computer and opens it
 *   agents    the first tab's agents, each with its last activity under it;
 *             SELECT focuses that pane, or opens its question
 *   question  the question and its options; SELECT answers
 *
 * A pending question puts an alert row at the top of the other two views.
 * MENU and LEFT go back one view; from the spaces view they leave.
 *
 * The link is polled from this loop between key reads, never waited on: a
 * send that finds the link busy stays in the outbox for the next pass.
 ****************************************************************************/

#include <stdio.h>
#include <string.h>
#include "config.h"
#include "kernel.h"
#include "backlight.h"
#include "font.h"
#include "podlink.h"
#include "input/action.h"
#include "draw/viewport.h"
#include "widgets/list.h"
#include "widgets/splash.h"
#include "widgets/dialog.h"
#include "system/shutdown.h"
#include "herdremote_model.h"
#include "agents_screen.h"

enum view { VIEW_SPACES, VIEW_AGENTS, VIEW_QUESTION };

enum row_kind {
    ROW_STATUS,     /* connection trouble; not selectable */
    ROW_ALERT,      /* a question is waiting */
    ROW_SPACE,
    ROW_AGENT,
    ROW_LAST,       /* the agent above's last activity; not selectable */
    ROW_TEXT,       /* a wrapped line of the question */
    ROW_OPTION,
    ROW_SUBMIT,     /* a multi-select question's Submit */
    ROW_EMPTY,
};

struct row {
    unsigned char kind;
    unsigned char index;
};

#define MAX_ROWS   64
#define TEXT_ROWS  24
#define OUTBOX     4
#define SILENCE    (6 * HZ)

static struct hr_model model;
static struct row rows[MAX_ROWS];
static int nrows;
static struct dialog_text_line text_rows[TEXT_ROWS];

static enum view view;
static char space_id[16];       /* the workspace the agents view shows */
static char question_pane[16];  /* the pane the question view answers */
static enum view question_from;

static char outbox[OUTBOX][96];
static int nout;
static unsigned int next_req = 1;
static char pending_req[8];     /* the answer or focus awaiting its result */
static bool pending_answer;
static long pending_until;
static long last_message;
static bool was_connected;

/* A free outbox slot, or NULL while the link is wedged (the user can press
 * again) */
static char *outbox_slot(void)
{
    return nout < OUTBOX ? outbox[nout++] : NULL;
}

/* Queue a command that the daemon answers with R: "<name>\t<req>\t<args>" */
static void queue_request(const char *name, const char *args)
{
    char *slot = outbox_slot();
    if (!slot)
        return;
    snprintf(pending_req, sizeof pending_req, "%u", next_req);
    next_req = next_req % 9999 + 1;
    /* An answer waits up to five seconds for the pane to change */
    pending_until = current_tick + 8 * HZ;
    snprintf(slot, sizeof outbox[0], "%s\t%s\t%s", name, pending_req, args);
}

static void subscribe(const char *ws)
{
    char *slot = outbox_slot();
    if (slot)
        snprintf(slot, sizeof outbox[0], "sub\t%s", ws[0] ? ws : "-");
}

static void flush_outbox(void)
{
    while (nout && podlink_state() == PODLINK_CONNECTED)
    {
        if (podlink_send(PODLINK_TEXT, outbox[0], strlen(outbox[0]))
                != PODLINK_SENT)
            return;
        memmove(outbox[0], outbox[1], (nout - 1) * sizeof outbox[0]);
        nout--;
    }
}

static const struct hr_question *current_question(void)
{
    return hr_model_question_for(&model, question_pane);
}

static const struct hr_workspace *find_space(const char *id)
{
    for (int i = 0; i < model.nws; i++)
        if (!strcmp(model.ws[i].id, id))
            return &model.ws[i];
    return NULL;
}

static const char *glyph(char status)
{
    switch (status)
    {
        case 'w': return "◐";
        case 'b': return "⚠";
        case 'd': return "●";
        case 'i': return "○";
        default:  return "·";
    }
}

/* What is wrong with the link or the data, or NULL when nothing is */
static const char *trouble(char *buf, size_t size)
{
    switch (podlink_state())
    {
        case PODLINK_WAIT_USB:
            return "Connect USB to the computer";
        case PODLINK_OPENING:
            return "Start herdremote on the computer";
        case PODLINK_CONNECTED:
            break;
        default:
            return "Link closed";
    }
    if (!model.hello)
        return "Waiting for herdremote…";
    if (!model.herdr_ok)
        return "herdr is not running on the computer";
    if (TIME_AFTER(current_tick, last_message + SILENCE))
    {
        snprintf(buf, size, "No update for %ld s",
                 (current_tick - last_message) / HZ);
        return buf;
    }
    if (model.herdr_age > 5)
    {
        snprintf(buf, size, "herdr data %d s old", model.herdr_age);
        return buf;
    }
    return NULL;
}

static void add_row(int kind, int index)
{
    if (nrows < MAX_ROWS)
    {
        rows[nrows].kind = kind;
        rows[nrows].index = index;
        nrows++;
    }
}

static int text_width(struct gui_synclist *list)
{
    /* The skinned row's text is inset from the list viewport on both sides */
    return list->parent[SCREEN_MAIN]->width - 24;
}

static void build_rows(struct gui_synclist *list)
{
    char buf[48];
    nrows = 0;
    if (view != VIEW_QUESTION)
    {
        if (trouble(buf, sizeof buf))
            add_row(ROW_STATUS, 0);
        if (model.nquestions)
            add_row(ROW_ALERT, 0);
    }
    if (view == VIEW_SPACES)
    {
        for (int i = 0; i < model.nws; i++)
            add_row(ROW_SPACE, i);
        if (!model.nws && model.hello)
            add_row(ROW_EMPTY, 0);
    }
    else if (view == VIEW_AGENTS)
    {
        if (model.agents_valid && !strcmp(model.agents_ws, space_id))
            for (int i = 0; i < model.nagents; i++)
            {
                add_row(ROW_AGENT, i);
                if (model.agents[i].last[0])
                    add_row(ROW_LAST, i);
            }
        if (!model.agents_valid || strcmp(model.agents_ws, space_id) ||
            model.nagents == 0)
            add_row(ROW_EMPTY, 0);
    }
    else
    {
        const struct hr_question *q = current_question();
        if (!q)
            return;
        int n = 0;
        for (int i = 0; i < q->nlines && n < TEXT_ROWS; i++)
            n += dialog_wrap_text(q->lines[i], text_width(list), FONT_UI,
                                  &text_rows[n], TEXT_ROWS - n);
        for (int i = 0; i < n; i++)
            add_row(ROW_TEXT, i);
        for (int i = 0; i < q->noptions; i++)
            add_row(ROW_OPTION, i);
        if (q->multi)
            add_row(ROW_SUBMIT, 0);
    }
}

static const char *row_name(int item, void *data, char *buf, size_t size)
{
    (void)data;
    if (item < 0 || item >= nrows)
        return "";
    const struct row *r = &rows[item];
    const struct hr_question *q;
    switch (r->kind)
    {
        case ROW_STATUS:
        {
            char tmp[48];
            const char *t = trouble(tmp, sizeof tmp);
            snprintf(buf, size, "· %s", t ? t : "");
            return buf;
        }
        case ROW_ALERT:
            q = &model.questions[0];
            if (model.nquestions == 1)
                snprintf(buf, size, "⚠ %s asks: %s", q->agent,
                         q->nlines ? q->lines[q->nlines - 1] : "");
            else
                snprintf(buf, size, "⚠ %d questions waiting",
                         model.nquestions);
            return buf;
        case ROW_SPACE:
        {
            const struct hr_workspace *w = &model.ws[r->index];
            int n = snprintf(buf, size, "%s%s%s", w->waiting ? "⚠ " : "",
                             w->focused ? "▶ " : "", w->label);
            if (w->working && n < (int)size)
                n += snprintf(buf + n, size - n, "  ◐%u", w->working);
            if (w->waiting && n < (int)size)
                n += snprintf(buf + n, size - n, "  ⚠%u", w->waiting);
            if (w->done && n < (int)size)
                n += snprintf(buf + n, size - n, "  ●%u", w->done);
            if (w->idle && n < (int)size)
                snprintf(buf + n, size - n, "  ○%u", w->idle);
            return buf;
        }
        case ROW_AGENT:
        {
            const struct hr_agent *a = &model.agents[r->index];
            const char *who = a->name[0] ? a->name : a->kind;
            snprintf(buf, size, "%s %s · %s", glyph(a->status), who,
                     a->title[0] ? a->title : a->kind);
            return buf;
        }
        case ROW_LAST:
            snprintf(buf, size, "    %s", model.agents[r->index].last);
            return buf;
        case ROW_TEXT:
        {
            int len = text_rows[r->index].len;
            if (len >= (int)size)
                len = size - 1;
            memcpy(buf, text_rows[r->index].str, len);
            buf[len] = '\0';
            return buf;
        }
        case ROW_OPTION:
            q = current_question();
            if (!q)
                return "";
            snprintf(buf, size, "%d. %s", q->options[r->index].number,
                     q->options[r->index].label);
            return buf;
        case ROW_SUBMIT:
            return "Submit ▶";
        default:
            if (view == VIEW_SPACES)
                return "No herdr workspaces";
            return model.hello ? "No agents in tab 1" : "Waiting for data…";
    }
}

static bool selectable(int item)
{
    if (item < 0 || item >= nrows)
        return false;
    int kind = rows[item].kind;
    return kind != ROW_STATUS && kind != ROW_TEXT && kind != ROW_LAST &&
           kind != ROW_EMPTY;
}

static const char *view_title(void)
{
    static char title[48];
    if (pending_req[0])
        return pending_answer ? "Answering…" : "Focusing…";
    if (view == VIEW_AGENTS)
    {
        const struct hr_workspace *w = find_space(space_id);
        snprintf(title, sizeof title, "%s", w ? w->label : "Agents");
        return title;
    }
    if (view == VIEW_QUESTION)
    {
        const struct hr_question *q = current_question();
        snprintf(title, sizeof title, "%s", q ? q->agent : "Question");
        return title;
    }
    if (model.nquestions)
    {
        snprintf(title, sizeof title, "Agents · ⚠%d", model.nquestions);
        return title;
    }
    return "Agents";
}

static void show(struct gui_synclist *list, int select)
{
    build_rows(list);
    gui_synclist_set_nb_items(list, nrows);
    gui_synclist_set_title(list, view_title(), Icon_NOICON);
    if (select >= 0)
    {
        while (select < nrows - 1 && !selectable(select))
            select++;
        gui_synclist_select_item(list, select < nrows ? select : 0);
    }
    gui_synclist_draw(list);
}

/* The selection, kept on the same kind of row after the rows changed. Call
 * before the rows are rebuilt: a status row that was selected may be gone,
 * so the selection then goes to the first row that can be chosen. */
static int reselect(struct gui_synclist *list)
{
    int sel = gui_synclist_get_sel_pos(list);
    if (sel < nrows && !selectable(sel))
        return 0;
    return sel >= nrows ? nrows - 1 : sel;
}

static void open_question(const char *pane)
{
    snprintf(question_pane, sizeof question_pane, "%s", pane);
    question_from = view;
    view = VIEW_QUESTION;
}

static void leave_question(void)
{
    view = question_from;
    question_pane[0] = '\0';
}

/* Act on SELECT. Returns true when the view changed. */
static bool activate(struct gui_synclist *list)
{
    int sel = gui_synclist_get_sel_pos(list);
    if (!selectable(sel) || pending_req[0])
        return false;
    const struct row *r = &rows[sel];
    const struct hr_question *q;
    switch (r->kind)
    {
        case ROW_ALERT:
            open_question(model.questions[0].pane);
            return true;
        case ROW_SPACE:
        {
            const struct hr_workspace *w = &model.ws[r->index];
            snprintf(space_id, sizeof space_id, "%s", w->id);
            queue_request("fw", space_id);
            subscribe(space_id);
            view = VIEW_AGENTS;
            return true;
        }
        case ROW_AGENT:
        {
            const struct hr_agent *a = &model.agents[r->index];
            if (a->question && hr_model_question_for(&model, a->pane))
            {
                open_question(a->pane);
                return true;
            }
            queue_request("fp", a->pane);
            return false;
        }
        case ROW_OPTION:
        case ROW_SUBMIT:
        {
            char args[24];
            q = current_question();
            if (!q)
                return false;
            snprintf(args, sizeof args, "%s\t%d", q->id,
                     r->kind == ROW_SUBMIT ? 0 : q->options[r->index].number);
            queue_request("a", args);
            pending_answer = !q->multi || r->kind == ROW_SUBMIT;
            return false;
        }
    }
    return false;
}

/* Take whatever the link has. Returns true if anything on screen changed. */
static bool poll_link(void)
{
    bool changed = false;
    bool connected = podlink_state() == PODLINK_CONNECTED;
    if (connected != was_connected)
    {
        was_connected = connected;
        model.hello = false;
        nout = 0;
        pending_req[0] = '\0';
        if (connected)
        {
            char *slot = outbox_slot();
            if (slot)
                strcpy(slot, "hr\t1");
            if (view != VIEW_SPACES)
                subscribe(space_id);
        }
        changed = true;
    }
    for (int i = 0; i < 16; i++)
    {
        struct podlink_message m;
        if (!podlink_poll(&m))
            break;
        if (m.type != PODLINK_TEXT)
            continue;
        int before = model.nquestions;
        bool had = question_pane[0] && current_question();
        int type = hr_model_apply(&model, (const char *)m.data, m.length);
        if (!type)
            continue;
        last_message = current_tick;
        changed = true;
        if (type == 'Q' && model.nquestions > before && !had)
            backlight_on();
    }
    flush_outbox();
    return changed;
}

/* A command's result has come back: say so, and move on after an answer */
static void take_result(struct gui_synclist *list)
{
    if (pending_req[0] && TIME_AFTER(current_tick, pending_until))
    {
        pending_req[0] = '\0';
        pending_answer = false;
        splash(HZ * 2, "No reply from the computer");
        show(list, reselect(list));
    }
    if (!model.result_new)
        return;
    model.result_new = false;
    if (strcmp(model.result_req, pending_req))
        return;
    pending_req[0] = '\0';
    if (!model.result_ok)
        splashf(HZ * 2, "%s", model.result_text);
    else if (pending_answer)
        splashf(HZ, "✓ %s", model.result_text);
    pending_answer = false;
    show(list, reselect(list));
}

int agents_screen(void)
{
    struct gui_synclist list;
    int action, ret = 0;
    char buf[48];

    if (!podlink_open())
    {
        splash(HZ * 3, "Unplug USB, then open Agents");
        return 0;
    }
    hr_model_reset(&model);
    view = VIEW_SPACES;
    space_id[0] = question_pane[0] = pending_req[0] = '\0';
    pending_answer = was_connected = false;
    nout = 0;
    last_message = current_tick;

    FOR_NB_SCREENS(i)
        viewportmanager_theme_enable(i, true, NULL);
    gui_synclist_init(&list, row_name, NULL, false, 1, NULL);
    show(&list, 0);

    long redraw_at = current_tick + HZ;
    const char *shown_trouble = NULL;
    bool done = false;
    while (!done)
    {
        bool changed = poll_link();
        take_result(&list);
        if (view == VIEW_QUESTION && !current_question())
        {
            /* Answered, or answered on the computer */
            leave_question();
            show(&list, 0);
        }
        /* Status rows count seconds, so they redraw once a second */
        const char *t = trouble(buf, sizeof buf);
        if (changed || t != shown_trouble ||
            (t && TIME_AFTER(current_tick, redraw_at)))
        {
            shown_trouble = t;
            redraw_at = current_tick + HZ;
            show(&list, reselect(&list));
        }

        int before = gui_synclist_get_sel_pos(&list);
        if (list_do_action(CONTEXT_LIST, HZ / 10, &list, &action))
        {
            /* Wheel moves skip the rows that cannot be chosen: on in the
             * direction of the move, else the other way */
            int sel = gui_synclist_get_sel_pos(&list);
            if (!selectable(sel))
            {
                int step = sel < before ? -1 : 1;
                int i = sel;
                while (i >= 0 && i < nrows && !selectable(i))
                    i += step;
                if (i < 0 || i >= nrows)
                    for (i = sel; i >= 0 && i < nrows && !selectable(i); )
                        i -= step;
                if (i >= 0 && i < nrows)
                {
                    gui_synclist_select_item(&list, i);
                    gui_synclist_draw(&list);
                }
            }
            continue;
        }
        switch (action)
        {
            case ACTION_STD_OK:
                if (activate(&list))
                    show(&list, 0);
                else
                    show(&list, reselect(&list));
                break;
            case ACTION_STD_CANCEL:
            case ACTION_STD_MENU:
                if (view == VIEW_QUESTION)
                    leave_question();
                else if (view == VIEW_AGENTS)
                {
                    view = VIEW_SPACES;
                    space_id[0] = '\0';
                    subscribe("");
                }
                else
                {
                    done = true;
                    break;
                }
                pending_req[0] = '\0';
                show(&list, 0);
                break;
            case ACTION_NONE:
                break;
            default:
                if (default_event_handler(action) == SYS_USB_CONNECTED)
                {
                    ret = 1;
                    done = true;
                }
                break;
        }
    }

    /* Close cleanly: the transport finishes its CLOSE only while polled */
    podlink_close();
    long deadline = current_tick + 2 * HZ;
    while (podlink_state() != PODLINK_CLOSED &&
           TIME_BEFORE(current_tick, deadline))
    {
        struct podlink_message m;
        podlink_poll(&m);
        sleep(1);
    }
    FOR_NB_SCREENS(i)
        viewportmanager_theme_undo(i, false);
    return ret;
}
