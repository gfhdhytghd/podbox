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
 * The theme draws the rows. The screen is %cs 40, and each row's %Lv is a
 * style letter, an icon letter and the text for the row's right-hand side:
 *   style  r  a row with an icon     q  the same, greyed: it cannot be
 *          h  a line of the question       chosen
 *          n  a note with no icon
 *   icon   w working  b asking  d done  i idle  u unknown  l no link
 *          1-8 an option's number  c x an unticked and a ticked box
 *          s Submit  - none
 * In the agents view %LD is an agent's kind and title over its last
 * activity. A theme that knows none of this shows each row's text alone.
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
#include "system/activity.h"
#include "speech/talk.h"
#include "herdremote_model.h"
#include "agents_screen.h"

enum view { VIEW_SPACES, VIEW_AGENTS, VIEW_QUESTION };

enum row_kind {
    ROW_STATUS,     /* connection trouble; not selectable */
    ROW_HINT,       /* what to do about it; not selectable */
    ROW_ALERT,      /* a question is waiting */
    ROW_SPACE,
    ROW_AGENT,
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
/* The question's lines, the terminal's wrapping undone, which text_rows
 * point into */
static char question_text[HR_MAX_LINES * 124 + HR_MAX_LINES];

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

static const char *status_word(char status)
{
    switch (status)
    {
        case 'w': return "working";
        case 'b': return "asking";
        case 'd': return "done";
        case 'i': return "idle";
        default:  return "";
    }
}

/* The most pressing state among a workspace's agents, as an icon letter,
 * and how many agents are in it */
static char space_state(const struct hr_workspace *w, char *buf, size_t size)
{
    static const char order[] = "bwdi";
    const unsigned char count[] = { w->waiting, w->working, w->done,
                                    w->idle };
    for (int i = 0; i < 4; i++)
        if (count[i])
        {
            snprintf(buf, size, "%u %s", count[i], status_word(order[i]));
            return order[i];
        }
    buf[0] = '\0';
    return 'i';
}

static char agent_state(const struct hr_agent *a)
{
    return a->question ? 'b' : a->status;
}

static const char *agent_name(const struct hr_agent *a)
{
    return a->name[0] ? a->name : a->title[0] ? a->title : a->kind;
}

/* An option's label without its tick box, and the detail after its " — ",
 * which is NULL when there is none */
static const char *option_parts(const struct hr_option *o, bool *ticked,
                                char *label, size_t size)
{
    const char *text = o->label;
    *ticked = !strncmp(text, "[x] ", 4);
    if (*ticked || !strncmp(text, "[ ] ", 4))
        text += 4;
    snprintf(label, size, "%s", text);
    char *dash = strstr(label, " — ");
    if (!dash)
        return NULL;
    *dash = '\0';
    return text + (dash - label) + strlen(" — ");
}

/* Scrim's cells for a row's text, with and without a right-hand side, and
 * for that side */
#define TEXT_CELL   250
#define SHORT_CELL  160
#define RIGHT_CELL  86

static int text_px(const char *text)
{
    int w, h;
    font_getstringsize((const unsigned char *)text, &w, &h, FONT_UI);
    return w;
}

/* A detail goes on the right of its row when it fits there, and otherwise
 * stays in the row's text, which scrolls when selected */
static bool fits_right(const char *text)
{
    return text_px(text) <= RIGHT_CELL;
}

/* Cut text to width on a character boundary and end it with an ellipsis,
 * when it is wider. The selected row is left whole: it scrolls. */
static const char *fit(char *buf, size_t size, const char *text, int width)
{
    if (text != buf)
        snprintf(buf, size, "%s", text);
    if (text_px(buf) <= width)
        return buf;
    size_t len = strlen(buf);
    while (len > 0)
    {
        do
            len--;
        while (len > 0 && (buf[len] & 0xc0) == 0x80);
        if (len + 4 > size)
            continue;
        strcpy(buf + len, "…");
        if (text_px(buf) <= width)
            break;
    }
    return buf;
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
        return "herdr is not running";
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

/* What to do about the trouble, where the computer has to do it */
static const char *trouble_hint(void)
{
    if (podlink_state() == PODLINK_OPENING)
        return "systemctl --user start herdremote";
    if (podlink_state() == PODLINK_CONNECTED && model.hello &&
        !model.herdr_ok)
        return "Start herdr on the computer";
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

/* A terminal wraps a long question across lines. A line that is long and
 * does not end a sentence runs on into the next; any other ends a
 * paragraph. */
static void join_question(const struct hr_question *q)
{
    size_t n = 0;
    question_text[0] = '\0';
    for (int i = 0; i < q->nlines; i++)
    {
        const char *prev = i ? q->lines[i - 1] : "";
        size_t len = strlen(prev);
        bool runs_on = len >= 60 && !strchr(".?!:", prev[len - 1]) &&
                       (len < 3 || (strcmp(prev + len - 3, "。") &&
                                    strcmp(prev + len - 3, "？") &&
                                    strcmp(prev + len - 3, "！") &&
                                    strcmp(prev + len - 3, "：")));
        n += snprintf(question_text + n, sizeof question_text - n, "%s%s",
                      !i ? "" : runs_on ? " " : "\n", q->lines[i]);
        if (n >= sizeof question_text)
            break;
    }
}

static void build_rows(struct gui_synclist *list)
{
    char buf[48];
    nrows = 0;
    if (view != VIEW_QUESTION)
    {
        if (trouble(buf, sizeof buf))
            add_row(ROW_STATUS, 0);
        if (trouble_hint())
            add_row(ROW_HINT, 0);
        if (model.nquestions)
            add_row(ROW_ALERT, 0);
    }
    if (view == VIEW_SPACES)
    {
        for (int i = 0; i < model.nws; i++)
            add_row(ROW_SPACE, i);
        if (!model.nws && model.hello && model.herdr_ok)
            add_row(ROW_EMPTY, 0);
    }
    else if (view == VIEW_AGENTS)
    {
        if (model.agents_valid && !strcmp(model.agents_ws, space_id))
            for (int i = 0; i < model.nagents; i++)
                add_row(ROW_AGENT, i);
        if (!model.agents_valid || strcmp(model.agents_ws, space_id) ||
            model.nagents == 0)
            add_row(ROW_EMPTY, 0);
    }
    else
    {
        const struct hr_question *q = current_question();
        if (!q)
            return;
        join_question(q);
        int n = dialog_wrap_text(question_text, text_width(list), FONT_UI,
                                 text_rows, TEXT_ROWS);
        for (int i = 0; i < n; i++)
            add_row(ROW_TEXT, i);
        for (int i = 0; i < q->noptions; i++)
            add_row(ROW_OPTION, i);
        if (q->multi)
            add_row(ROW_SUBMIT, 0);
    }
}

static const char *row_text(int item, char *buf, size_t size)
{
    if (item < 0 || item >= nrows)
        return "";
    const struct row *r = &rows[item];
    const struct hr_question *q;
    switch (r->kind)
    {
        case ROW_STATUS:
        {
            const char *t = trouble(buf, size);
            return t ? t : "";
        }
        case ROW_HINT:
        {
            const char *t = trouble_hint();
            return t ? t : "";
        }
        case ROW_ALERT:
            q = &model.questions[0];
            if (model.nquestions == 1)
                return q->nlines ? q->lines[q->nlines - 1] : q->agent;
            snprintf(buf, size, "%d questions waiting", model.nquestions);
            return buf;
        case ROW_SPACE:
            return model.ws[r->index].label;
        case ROW_AGENT:
            return agent_name(&model.agents[r->index]);
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
        {
            bool ticked;
            q = current_question();
            if (!q)
                return "";
            const char *detail = option_parts(&q->options[r->index], &ticked,
                                              buf, size);
            if (detail && !fits_right(detail))
            {
                size_t n = strlen(buf);
                snprintf(buf + n, size - n, " — %s", detail);
            }
            return buf;
        }
        case ROW_SUBMIT:
            return "Submit";
        default:
            if (view == VIEW_SPACES)
                return "No herdr workspaces";
            return model.hello ? "No agents in the first tab"
                               : "Waiting for data…";
    }
}

static const char *row_value(int item, void *data, char *buf, size_t size);
static bool selectable(int item);

/* A row's text, cut to its cell unless it is the selected row */
static const char *row_name(int item, void *data, char *buf, size_t size)
{
    struct gui_synclist *list = data;
    const char *text = row_text(item, buf, size);
    if (item < 0 || item >= nrows || rows[item].kind == ROW_TEXT ||
        (item == list->selected_item && selectable(item)))
        return text;
    char value[64];
    const char *v = row_value(item, data, value, sizeof value);
    return fit(buf, size, text, v[0] && v[1] && v[2] ? SHORT_CELL
                                                     : TEXT_CELL);
}

/* %Lv: see the file header */
static const char *row_value(int item, void *data, char *buf, size_t size)
{
    (void)data;
    if (item < 0 || item >= nrows || size < 3)
        return "";
    const struct row *r = &rows[item];
    const struct hr_question *q = current_question();
    char style = 'r', icon = '-';
    char right[48] = "";
    switch (r->kind)
    {
        case ROW_STATUS:
            style = 'q';
            icon = 'l';
            break;
        case ROW_ALERT:
        {
            icon = 'b';
            const struct hr_workspace *w =
                find_space(model.questions[0].workspace);
            if (model.nquestions == 1 && w)
                snprintf(right, sizeof right, "%s", w->label);
            break;
        }
        case ROW_SPACE:
            icon = space_state(&model.ws[r->index], right, sizeof right);
            break;
        case ROW_AGENT:
            icon = agent_state(&model.agents[r->index]);
            snprintf(right, sizeof right, "%s", status_word(icon));
            break;
        case ROW_TEXT:
            style = 'h';
            break;
        case ROW_OPTION:
        {
            bool ticked;
            char label[96];
            if (!q)
                break;
            const char *detail = option_parts(&q->options[r->index], &ticked,
                                              label, sizeof label);
            if (q->multi)
                icon = ticked ? 'x' : 'c';
            else if (r->index < 8)
                icon = '1' + r->index;
            if (detail && fits_right(detail))
                snprintf(right, sizeof right, "%s", detail);
            break;
        }
        case ROW_SUBMIT:
        {
            icon = 's';
            int n = 0;
            for (int i = 0; q && i < q->noptions; i++)
                n += !strncmp(q->options[i].label, "[x] ", 4);
            if (n)
                snprintf(right, sizeof right, "%d chosen", n);
            break;
        }
        default:
            style = 'n';
            break;
    }
    snprintf(buf, size, "%c%c%s", style, icon,
             fit(right, sizeof right, right, RIGHT_CELL));
    return buf;
}

/* %LD, in the agents view only, where every row that can be chosen has
 * one: an agent's kind and title over what it did last, or who asks the
 * waiting question */
static const char *row_description(int item, void *data, char *buf,
                                   size_t size)
{
    (void)data;
    if (view != VIEW_AGENTS || item < 0 || item >= nrows)
        return NULL;
    if (rows[item].kind == ROW_ALERT)
    {
        const struct hr_question *q = &model.questions[0];
        if (model.nquestions > 1)
            return "Select to answer the first";
        snprintf(buf, size, "%s asks", q->agent);
        return buf;
    }
    if (rows[item].kind != ROW_AGENT)
        return NULL;
    const struct hr_agent *a = &model.agents[rows[item].index];
    bool titled = a->title[0] && agent_name(a) != a->title;
    snprintf(buf, size, "%s%s%s%s%s", a->kind, titled ? " · " : "",
             titled ? a->title : "", a->last[0] ? "\n" : "", a->last);
    return buf;
}

/* Say the row's text and its right-hand side, not the letters for the
 * theme */
static int speak_row(int item, void *data)
{
    char buf[128];
    talk_text(row_text(item, buf, sizeof buf), false);
    const char *value = row_value(item, data, buf, sizeof buf);
    if (value[0] && value[1] && value[2])
        talk_text(value + 2, true);
    return 0;
}

static bool selectable(int item)
{
    if (item < 0 || item >= nrows)
        return false;
    int kind = rows[item].kind;
    return kind != ROW_STATUS && kind != ROW_HINT && kind != ROW_TEXT &&
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
    return "Agents";
}

/* Rebuild and draw, selecting the first row that can be chosen from select
 * on. The rows above it stay in view as far as they fit, so a question is
 * read from its start; top, when not -1, is the row to keep at the top
 * instead. */
static void show_at(struct gui_synclist *list, int select, int top)
{
    build_rows(list);
    gui_synclist_set_nb_items(list, nrows);
    gui_synclist_set_title(list, view_title(), Icon_NOICON);
    while (select < nrows - 1 && !selectable(select))
        select++;
    if (select >= nrows)
        select = 0;
    gui_synclist_select_item(list, select);
    if (top < 0)
        top = select - gui_synclist_get_nb_lines(list, SCREEN_MAIN) + 1;
    gui_synclist_set_top_item(list, top);
    gui_synclist_draw(list);
}

static void show(struct gui_synclist *list, int select)
{
    show_at(list, select, -1);
}

/* Rebuild the rows, keeping the selection on the same row when it is still
 * there: a status or alert row that comes or goes moves the rest. When the
 * row has gone the selection goes to the first row that can be chosen, never
 * to whatever took its place: on a question, that could be Cancel. */
static void show_same(struct gui_synclist *list)
{
    int sel = gui_synclist_get_sel_pos(list);
    if (sel < 0 || sel >= nrows || !selectable(sel))
    {
        show(list, 0);
        return;
    }
    struct row was = rows[sel];
    int top = list->start_item[SCREEN_MAIN];
    build_rows(list);
    int i = 0;
    while (i < nrows &&
           (rows[i].kind != was.kind || rows[i].index != was.index))
        i++;
    /* A row that has not moved keeps the view where the reader left it */
    if (i < nrows)
        show_at(list, i, i == sel ? top : -1);
    else
        show(list, 0);
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
        show_same(list);
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
        splashf(HZ, "%s", model.result_text);
    pending_answer = false;
    show_same(list);
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
    push_current_activity(ACTIVITY_AGENTS);
    gui_synclist_init(&list, row_name, &list, false, 1, NULL);
    gui_synclist_set_row_callbacks(&list, NULL, row_value, NULL);
    gui_synclist_set_description_callback(&list, row_description);
    gui_synclist_set_voice_callback(&list, speak_row);
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
            show_same(&list);
        }

        int before = gui_synclist_get_sel_pos(&list);
        int before_top = list.start_item[SCREEN_MAIN];
        if (list_do_action(CONTEXT_LIST, HZ / 10, &list, &action))
        {
            int sel = gui_synclist_get_sel_pos(&list);
            int lines = gui_synclist_get_nb_lines(&list, SCREEN_MAIN);
            if (sel > before && before >= before_top + lines)
            {
                /* The selection is below the rows in view, which were
                 * scrolled up to read: scroll back down towards it */
                gui_synclist_select_item(&list, before);
                gui_synclist_set_top_item(&list, before_top + 1);
                gui_synclist_draw(&list);
                continue;
            }
            /* Wheel moves skip the rows that cannot be chosen: on in the
             * direction of the move, else the other way */
            if (!selectable(sel))
            {
                int step = sel < before ? -1 : 1;
                int i = sel;
                while (i >= 0 && i < nrows && !selectable(i))
                    i += step;
                if (i < 0 && selectable(before))
                {
                    /* Nothing to choose above: the move scrolls the rows
                     * there into view, such as a long question's start */
                    gui_synclist_select_item(&list, before);
                    gui_synclist_set_top_item(&list, before_top - 1);
                    gui_synclist_draw(&list);
                    continue;
                }
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
                    show_same(&list);
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
    pop_current_activity();
    FOR_NB_SCREENS(i)
        viewportmanager_theme_undo(i, false);
    return ret;
}
