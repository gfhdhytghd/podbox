/***************************************************************************
 * GNU General Public License (version 2+)
 *
 * System > Agents: the herdr agent dashboard and remote, over PodLink.
 *
 * The computer runs tools/herdremote, which sends HerdRemote v1 messages
 * (docs/podbox/herdremote-protocol.md) describing herdr's workspaces, the
 * tabs and agents of the one the player subscribes to, the last lines of
 * one pane, and any choice question an agent is asking. This screen keeps
 * that picture in herdremote_model.c and sends back focus, key and answer
 * commands. Two views are the top level, spaces and usage; two more lie
 * under spaces; and a question opens over any of them:
 *
 *   spaces    workspaces with their agent counts. The wheel moves and the
 *             computer's focus follows it (fw); SELECT enters the
 *             workspace, focusing it; LEFT and RIGHT go to usage
 *   usage     each AI service's limits, tokens and cost, drawn by this file
 *             over an empty list; RIGHT goes back to spaces. The other keys
 *             type on the computer: LEFT Backspace (once per press and per
 *             repeat), the wheel the Left and Right arrows (Ctrl with
 *             them, a word at a time, when it turns fast), SELECT Enter.
 *             Taps made while one is on its way go together, with a count
 *   agents    one tab's agents, each with its last activity under it; the
 *             title names the tab. The focus follows the wheel (fp);
 *             SELECT opens the agent's detail; LEFT and RIGHT go to the tab
 *             before or after, focusing it (ft); MENU goes back to spaces
 *   detail    the agent's work, as the computer reads it from the pane
 *             (det, D) and this file draws it: its name and state with the
 *             time in it, what it is on (the terminal title, scrolled when
 *             long), what is typed in its input box with the cursor, in a
 *             window of five lines that follows the cursor, its live
 *             working line, its latest message to the user, wrapped, and
 *             its last tool steps, which give way when the message needs
 *             the room. DETAIL-DESIGN.md (beside the task cards) is the
 *             page's specification. The keys type as in usage, once the
 *             pane is focused (fp); MENU goes back
 *   question  the question and its options; SELECT answers; MENU dismisses
 *   confirm   "Leave Agents?", from MENU in spaces or usage: SELECT leaves,
 *             MENU goes back; the other keys do nothing
 *
 * A question pops up as it arrives, over whichever view is open, once Play
 * is up and its result in, and once the confirm view has closed; answering
 * or dismissing it goes back there. A dismissed question does not pop up
 * again, but the alert row at the top of the spaces and agents views still
 * opens it. Only SELECT in the confirm view leaves Agents. Focus sent for
 * the wheel, a tab, a workspace or the detail says nothing when it works
 * and its reason in the band when it fails.
 *
 * The list is drawn again only for what changes it: a wheel step draws it
 * as it moves, and a message does when it alters a row, the title or the
 * usage or detail view. A focus result, a tick, a voice state or a W that
 * only moved the computer's focus draws nothing.
 *
 * Play held talks to the computer, in every view: its press sends vd with
 * the selected workspace or pane ("-" in usage), its release vu, and the
 * computer's voice input types what is said. A band at the foot of the list
 * shows the recording and then its result. Held Play does not power off
 * here.
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
#include "button.h"
#include "font.h"
#include "rbunicode.h"
#include "diacritic.h"
#include "podlink.h"
#include "usb.h"
#include "input/action.h"
#include "input/agents_shortcut.h"
#ifdef IPOD_ACCESSORY_PROTOCOL
#include "iap/agents_volume.h"
#endif
#include "draw/viewport.h"
#include "draw/screen_access.h"
#include "draw/text_wrap.h"
#include "widgets/list.h"
#include "skin/statusbar_skinned.h"
#include "widgets/splash.h"
#include "widgets/dialog.h"
#include "system/shutdown.h"
#include "system/activity.h"
#include "speech/talk.h"
#include "herdremote_model.h"
#include "detail_layout.h"
#include "agents_screen.h"

enum view { VIEW_SPACES, VIEW_AGENTS, VIEW_DETAIL, VIEW_QUESTION, VIEW_USAGE,
            VIEW_CONFIRM };

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

/* id is the workspace or pane a row stands for, copied when the rows are
 * built: the computer reorders the lists, so an index alone does not say
 * which item the selection was on once the model has changed */
struct row {
    unsigned char kind;
    unsigned char index;
    char id[16];
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
static char space_tab[16];      /* its tab; "" is the first */
static char detail_pane[16];    /* the pane the detail view shows */
static struct hr_agent detail_agent;    /* that agent, as last listed */
static bool detail_ready;       /* its fp has succeeded, so keys may go */
static long detail_at;          /* when the last D came: its age counts on */
static int detail_shown_age;    /* the minutes the header shows, -1 none */
/* The detail's task line scrolls when it is wider than the view: it rests
 * at either end, then moves TASK_STEP pixels every ANIM_TICKS */
#define TASK_REST   (3 * HZ / 2)
#define TASK_STEP   2
static char task_text[HR_DETAIL_TEXT];  /* the task line as drawn */
static int task_w;              /* its width in pixels */
static int task_x;              /* its scroll, pixels from the left */
static int task_y;              /* its line's top in the view */
static long task_next;          /* when it moves next */
static bool task_at_end;        /* it rests at its end, to go back */
static char question_pane[16];  /* the pane the question view answers */
static enum view question_from; /* the view to go back to, on which row */
static struct row question_from_row;
static struct hr_popped popped; /* the questions that have popped up */
static enum view confirm_from;  /* the view under the confirm, which row */
static struct row confirm_from_row;

static char outbox[OUTBOX][96];
static int nout;
static unsigned int next_req = 1;
static char pending_req[8];     /* the answer awaiting its result */
static bool pending_answer;
static long pending_until;
/* Focus follows the wheel: the selected workspace (fw) or pane (fp) is
 * focused on the computer as soon as the wheel moves. One fw, fp or ft is
 * out at a time, and says nothing unless it fails; the wheel moving
 * meanwhile leaves only its latest row wanted, sent when the result comes.
 * A row the last request already focused is not sent again. */
#define FOCUS_REPLY (8 * HZ)
static char focus_want[16];     /* the row to focus next; "" for none */
static char focus_done[16];     /* the row the last fw or fp was for */
static char focus_req[8];       /* the fw, fp or ft awaiting its result */
static long focus_until;
static bool focus_for_detail;   /* that request is the detail view's fp */
static long last_message;
static bool was_connected;
_Static_assert(sizeof(struct hr_model) <= 32 * 1024, "Agents model cache budget");
static bool cache_valid;
static uint32_t cache_generation;
static struct row cache_row;
static int cache_top;


/* The usage view asks again this often while it is open */
#define USAGE_EVERY (20 * HZ)
static long usage_next;         /* when to ask next */
static long usage_at;           /* when the shown figures came */
static unsigned int usage_serial;
#ifdef IPOD_ACCESSORY_PROTOCOL
static bool volume_paging;
#endif
static bool native_play_pending;
static unsigned long native_play_at;
_Static_assert(sizeof(struct detail_layout) <= 6400, "Detail layout cache budget");
static struct detail_layout detail_layout;
static int detail_body_y, detail_body_avail;
static int usage_scroll, detail_scroll, detail_scroll_max, detail_page_lines;
static struct row usage_from;   /* the spaces row to go back to */

/* Hold to talk: the band at the foot of the list, while it is wanted */
#define TALK_REPLY  (8 * HZ)    /* how long vd and vu wait for their R */
#define TALK_BAND   30          /* the band's height in pixels */
static bool talk_remote, talk_discarded;
static bool talk_held;          /* vd sent, with vu/vc still to come */
static long talk_since;         /* when the recording began, for its count */
static char talk_req[8];        /* the vd/vu/vc awaiting its result */
static bool talk_req_up;        /* that request is the vu */
static long talk_until;         /* when the request or the note expires */
static char talk_note[64];      /* the last result */
static bool talk_failed;        /* the note is an error */
static bool talk_shown;         /* the band is on screen */
/* One k is in flight at a time; the taps that come meanwhile wait as runs
 * with a count, and the next run goes when its R comes */
#define KEY_LOST    (2 * HZ)    /* a k unanswered this long is forgotten */
static char key_req[8];         /* the k in flight */
static long key_sent;           /* when it went */
static struct hr_keys key_runs; /* the taps waiting */
/* Wheel steps this close together are fast and jump words */
#define WHEEL_FAST  (HZ / 20)
static struct hr_wheel wheel;

static void talk_result(const char *text, bool failed);

static void background_link(const struct podlink_message *m)
{
    if (m->type == PODLINK_CLOSE)
    {
        cache_valid = false;
        return;
    }
    if (!cache_valid || m->type != PODLINK_TEXT || !m->length ||
        m->data[0] == 'D')
        return;
    int type = hr_model_apply(&model, (const char *)m->data, m->length);
    if (type) last_message = current_tick;
    if (type == 'U') usage_at = current_tick;
    if (type == 'V' && talk_discarded && !talk_failed)
        model.voice = 0;
    if (type == 'R')
    {
        if (talk_req[0] && !strcmp(model.result_req, talk_req))
        {
            talk_req[0] = '\0';
            if (!model.result_ok || talk_req_up)
                talk_result(model.result_text, !model.result_ok);
        }
        else if (focus_req[0] && !strcmp(model.result_req, focus_req))
        {
            focus_req[0] = '\0';
            if (focus_for_detail) detail_ready = model.result_ok;
            focus_for_detail = false;
            if (!model.result_ok) talk_result(model.result_text, true);
        }
        else if (key_req[0] && !strcmp(model.result_req, key_req))
            key_req[0] = '\0';
        else if (pending_req[0] && !strcmp(model.result_req, pending_req))
        {
            pending_req[0] = '\0';
            pending_answer = false;
            if (!model.result_ok) talk_result(model.result_text, true);
        }
        model.result_new = false;
    }
}


/* A free outbox slot, or NULL while the link is wedged (the user can press
 * again) */
static char *outbox_slot(void)
{
    return nout < OUTBOX - (talk_held ? 1 : 0) ? outbox[nout++] : NULL;
}

static void take_req(char *req, size_t size)
{
    snprintf(req, size, "%u", next_req);
    next_req = next_req % 9999 + 1;
}

/* Queue a command that the daemon answers with R: "<name>\t<req>\t<args>" */
static void queue_request(const char *name, const char *args)
{
    char *slot = outbox_slot();
    if (!slot)
        return;
    take_req(pending_req, sizeof pending_req);
    /* An answer waits up to five seconds for the pane to change */
    pending_until = current_tick + 8 * HZ;
    snprintf(slot, sizeof outbox[0], "%s\t%s\t%s", name, pending_req, args);
}

/* Queue fw, fp or ft, whose success is silent */
static void queue_focus(const char *name, const char *args, bool for_detail)
{
    char *slot = outbox_slot();
    if (!slot)
        return;
    take_req(focus_req, sizeof focus_req);
    focus_until = current_tick + FOCUS_REPLY;
    focus_for_detail = for_detail;
    snprintf(slot, sizeof outbox[0], "%s\t%s\t%s", name, focus_req, args);
}

static void ask_usage(void)
{
    char *slot = outbox_slot();
    if (slot)
        strcpy(slot, "us");
    usage_next = current_tick + USAGE_EVERY;
}

/* Ask for the tabs and agents of one workspace tab; "" for the first tab,
 * and an empty ws for none */
static void subscribe(const char *ws, const char *tab)
{
    char *slot = outbox_slot();
    if (slot)
        snprintf(slot, sizeof outbox[0], "sub\t%s%s%s", ws[0] ? ws : "-",
                 tab[0] ? "\t" : "", tab);
}

/* Ask for one pane's last lines; "" for none */
static void subscribe_detail(const char *pane)
{
    char *slot = outbox_slot();
    if (slot)
        snprintf(slot, sizeof outbox[0], "det\t%s", pane[0] ? pane : "-");
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

static const struct hr_agent *find_agent(const char *pane)
{
    if (!model.agents_valid)
        return NULL;
    for (int i = 0; i < model.nagents; i++)
        if (!strcmp(model.agents[i].pane, pane))
            return &model.agents[i];
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

/* The status dot a theme's %Ls shows. Asking and working in tab 1 are
 * the two that move; the rest are herdr's own dots and colours. */
enum dot {
    DOT_NONE,
    DOT_ASKING,         /* red, blinking */
    DOT_WORKING,        /* green, breathing */
    DOT_ASKING_ELSEWHERE,   /* in another tab: red, a … badge */
    DOT_WORKING_ELSEWHERE,  /* in another tab: green, a … badge */
    DOT_DONE,           /* herdr's teal dot */
    DOT_IDLE,           /* herdr's green ring */
    DOT_UNKNOWN,        /* herdr's small grey dot */
};

/* What is left after asking and working, in herdr's order: done, idle,
 * unknown */
static enum dot rest_dot(char a, char b)
{
    if (a == 'd' || b == 'd')
        return DOT_DONE;
    if (a == 'i' || b == 'i')
        return DOT_IDLE;
    return DOT_UNKNOWN;
}

static enum dot row_dot(int item)
{
    if (item < 0 || item >= nrows)
        return DOT_NONE;
    const struct row *r = &rows[item];
    if (r->kind == ROW_SPACE)
    {
        const struct hr_workspace *w = &model.ws[r->index];
        if (w->tab1 == 'b')
            return DOT_ASKING;
        if (w->tab1 == 'w')
            return DOT_WORKING;
        if (w->other == 'b')
            return DOT_ASKING_ELSEWHERE;
        if (w->other == 'w')
            return DOT_WORKING_ELSEWHERE;
        return rest_dot(w->tab1, w->other);
    }
    if (r->kind == ROW_AGENT)
    {
        char state = agent_state(&model.agents[r->index]);
        return state == 'b' ? DOT_ASKING : state == 'w' ? DOT_WORKING :
               rest_dot(state, state);
    }
    return DOT_NONE;
}

/* catppuccin-latte, the palette herdr is set to on the computer */
#define DOT_RED     LCD_RGBPACK(210, 15, 57)
#define DOT_GREEN   LCD_RGBPACK(64, 160, 43)
#define DOT_TEAL    LCD_RGBPACK(23, 146, 153)
#define DOT_GREY    LCD_RGBPACK(156, 160, 176)
#define DOT_HALO    LCD_RGBPACK(255, 255, 255)

/* One frame in ANIM_STEPS of the breathing: (1 - cos) / 2 from 40% up */
#define ANIM_STEPS 24
#define ANIM_TICKS (HZ / 10)
static const unsigned char breath[ANIM_STEPS] = {
    102, 105, 112, 124, 140, 159, 178, 198, 217, 233, 245, 252,
    255, 252, 245, 233, 217, 198, 178, 159, 140, 124, 112, 105,
};

/* How strongly to draw a dot now, 0 to 255 */
static int dot_strength(enum dot dot)
{
    long frame = current_tick / ANIM_TICKS;
    if (dot == DOT_WORKING)
        return breath[frame % ANIM_STEPS];
    if (dot == DOT_ASKING)
        return frame % 10 < 6 ? 255 : 0;     /* 0.6 s on, 0.4 s off */
    return 255;
}

/* Each dot is two coverage masks in the format alpha_bitmap_part() reads:
 * 4 bits a pixel, two to a byte, 0 opaque and 15 clear. The halo is a light
 * edge round the shape, so a dot stands out on any cover; the fill is the
 * shape in the state's colour. Both are worked out once per cell size from
 * 8x8 samples a pixel. Lengths are sixteenths of a pixel at a 24-pixel cell
 * and scale with it. */
#define DOT_MAX 32
#define DOT_BYTES ((DOT_MAX * DOT_MAX + 1) / 2)
enum { SHAPE_DISC, SHAPE_RING, SHAPE_SMALL, SHAPE_BADGE, SHAPES };
static unsigned char dot_masks[SHAPES][2][DOT_BYTES];
static unsigned char dot_faded[DOT_BYTES];
static int dot_masks_size;

/* Whether the point (x, y) from the cell's centre is inside one layer */
static bool dot_inside(int shape, bool halo, int x, int y, int size)
{
#define L(sixteenths) ((sixteenths) * size / 24)
#define IN(cx, cy, r) (((x) - (cx)) * ((x) - (cx)) + \
                       ((y) - (cy)) * ((y) - (cy)) <= (r) * (r))
    switch (shape)
    {
        case SHAPE_DISC:
            return halo ? IN(0, 0, L(108)) && !IN(0, 0, L(88))
                        : IN(0, 0, L(88));
        case SHAPE_RING:
            return halo ? IN(0, 0, L(108)) && !IN(0, 0, L(88))
                        : IN(0, 0, L(88)) && !IN(0, 0, L(56));
        case SHAPE_SMALL:
            return halo ? IN(0, 0, L(64)) && !IN(0, 0, L(44))
                        : IN(0, 0, L(44));
        default:
        {
            /* A disc up and left, and below right of it a light badge
             * holding three dots in the disc's colour: "busy, elsewhere" */
            int d = L(-28), by = L(104), bx = L(80);
            int run = L(56), br = L(44);
            int ex = x < bx - run ? bx - run : x > bx + run ? bx + run : x;
            bool badge = (x - ex) * (x - ex) + (y - by) * (y - by) <=
                         br * br;
            if (halo)
                return badge || (IN(d, d, L(108)) && !IN(d, d, L(88)));
            if (badge)
                return IN(bx - L(48), by, L(16)) || IN(bx, by, L(16)) ||
                       IN(bx + L(48), by, L(16));
            return IN(d, d, L(88));
        }
    }
#undef IN
#undef L
}

static void dot_build(int size)
{
    for (int shape = 0; shape < SHAPES; shape++)
        for (int halo = 0; halo < 2; halo++)
        {
            unsigned char *m = dot_masks[shape][halo];
            memset(m, 0xff, DOT_BYTES);
            for (int y = 0; y < size; y++)
                for (int x = 0; x < size; x++)
                {
                    int n = 0;
                    for (int sy = 0; sy < 8; sy++)
                        for (int sx = 0; sx < 8; sx++)
                            n += dot_inside(shape, halo,
                                            x * 16 + sx * 2 + 1 - size * 8,
                                            y * 16 + sy * 2 + 1 - size * 8,
                                            size);
                    int i = y * size + x;
                    unsigned a = 15 - (n * 15 + 32) / 64;
                    m[i / 2] = i & 1 ? (m[i / 2] & 0x0f) | (a << 4)
                                     : (m[i / 2] & 0xf0) | a;
                }
        }
    dot_masks_size = size;
}

static int dot_shape(enum dot dot)
{
    switch (dot)
    {
        case DOT_IDLE:              return SHAPE_RING;
        case DOT_UNKNOWN:           return SHAPE_SMALL;
        case DOT_ASKING_ELSEWHERE:
        case DOT_WORKING_ELSEWHERE: return SHAPE_BADGE;
        default:                    return SHAPE_DISC;
    }
}

static unsigned dot_colour(enum dot dot)
{
    switch (dot)
    {
        case DOT_ASKING:
        case DOT_ASKING_ELSEWHERE:  return DOT_RED;
        case DOT_DONE:              return DOT_TEAL;
        case DOT_UNKNOWN:           return DOT_GREY;
        default:                    return DOT_GREEN;
    }
}

/* The %Ls callback. The halo is drawn solid, which blends it with the
 * backdrop itself and so also wipes the cell's last frame; the fill is
 * then blended over it. */
static bool draw_dot(int item, void *data, struct screen *display,
                     struct viewport *vp)
{
    (void)data;
    enum dot dot = row_dot(item);
    if (dot == DOT_NONE)
        return false;
    int size = MIN(MIN(vp->width, vp->height), DOT_MAX);
    int strength = dot_strength(dot);
    if (!strength)
        return true;
    if (size != dot_masks_size)
        dot_build(size);
    int shape = dot_shape(dot);
    const unsigned char *fill = dot_masks[shape][0];
    if (strength < 255)
    {
        /* A breath: the fill alone fades, towards the backdrop */
        for (int i = 0; i < (size * size + 1) / 2; i++)
        {
            int lo = 15 - (15 - (fill[i] & 15)) * strength / 255;
            int hi = 15 - (15 - (fill[i] >> 4)) * strength / 255;
            dot_faded[i] = lo | hi << 4;
        }
        fill = dot_faded;
    }
    int ox = (vp->width - size) / 2, oy = (vp->height - size) / 2;
    display->set_drawmode(DRMODE_SOLID);
    display->set_foreground(DOT_HALO);
    display->alpha_bitmap_part(dot_masks[shape][1], 0, 0, size,
                               ox, oy, size, size);
    display->set_drawmode(DRMODE_FG);
    display->set_foreground(dot_colour(dot));
    display->alpha_bitmap_part(fill, 0, 0, size, ox, oy, size, size);
    display->set_drawmode(DRMODE_SOLID);
    return true;
}

/* Whether a dot in view moves, so the screen has frames to draw */
static bool animating(struct gui_synclist *list)
{
    int top = list->start_item[SCREEN_MAIN];
    int lines = gui_synclist_get_nb_lines(list, SCREEN_MAIN);
    for (int i = top; i < top + lines && i < nrows; i++)
    {
        enum dot dot = row_dot(i);
        if (dot == DOT_ASKING || dot == DOT_WORKING)
            return true;
    }
    return false;
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
/* The title bar's cell while the link is held, 140 px, less what its bold
 * font adds to a width measured in the list's font: a title cut to this
 * does not scroll. The numeric battery display leaves a narrower cell. */
#define TITLE_CELL  124

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
        const char *id = kind == ROW_SPACE ? model.ws[index].id :
                         kind == ROW_AGENT ? model.agents[index].pane : "";
        rows[nrows].kind = kind;
        rows[nrows].index = index;
        snprintf(rows[nrows].id, sizeof rows[nrows].id, "%s", id);
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
    if (view == VIEW_USAGE || view == VIEW_DETAIL || view == VIEW_CONFIRM)
        return;
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
            return model.hello ? "No agents in this tab"
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
            icon = 'a';
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
    static char title[64];
    if (pending_req[0])
        return "Answering…";
    if (view == VIEW_AGENTS)
    {
        /* The tab, with an arrow for each neighbour; the workspace alone
         * when it has one tab, or from a computer that lists no tabs */
        const struct hr_workspace *w = find_space(space_id);
        int i = hr_model_tab_index(&model, space_id, space_tab);
        if (i >= 0 && model.ntabs > 1)
        {
            const struct hr_tab *t = &model.tabs[i];
            snprintf(title, sizeof title, "%s%d/%d %s%s", i ? "‹ " : "",
                     i + 1, model.ntabs, t->label[0] ? t->label : t->id,
                     i < model.ntabs - 1 ? " ›" : "");
        }
        else
            snprintf(title, sizeof title, "%s", w ? w->label : "Agents");
        return title;
    }
    if (view == VIEW_DETAIL)
    {
        /* The agent's name, or its kind when it has none: never the task,
         * which the page's TASK line carries. Cut rather than scrolled. */
        const struct hr_agent *a = &detail_agent;
        return fit(title, sizeof title, a->name[0] ? a->name : a->kind,
                   TITLE_CELL);
    }
    if (view == VIEW_QUESTION)
    {
        const struct hr_question *q = current_question();
        snprintf(title, sizeof title, "%s", q ? q->agent : "Question");
        return title;
    }
    if (view == VIEW_USAGE || (view == VIEW_CONFIRM &&
                               confirm_from == VIEW_USAGE))
        return "Usage";
    return "Agents";
}

/* The usage view's colours. The grey is Scrim's unselected row text, the
 * darkest that stays legible on a blue cover. */
#define USE_TEXT    LCD_RGBPACK(255, 255, 255)
#define USE_SOFT    LCD_RGBPACK(216, 216, 216)
#define USE_DIM     LCD_RGBPACK(189, 189, 189)
#define USE_WARN    LCD_RGBPACK(255, 183, 77)
#define USE_PACE    LCD_RGBPACK(255, 214, 0)
#define USE_FULL    LCD_RGBPACK(255, 110, 110)
#define USE_WEEK    (7 * 86400)
#define USE_LINE    21      /* pixels from one text line to the next */
#define USE_BLOCK   40      /* pixels a service takes */

/* Seconds since the shown figures were sent */
static int usage_age(void)
{
    return model.usage.age + (current_tick - usage_at) / HZ;
}

/* A reset time sent seconds ago, counted down: "3d4h", "2h13m", "13m" */
static const char *fmt_reset(char *buf, size_t size, int secs)
{
    if (secs < 0)
        return "—";
    secs -= (current_tick - usage_at) / HZ;
    if (secs < 60)
        return "<1m";
    if (secs >= 86400)
        snprintf(buf, size, "%dd%dh", secs / 86400, secs % 86400 / 3600);
    else if (secs >= 3600)
        snprintf(buf, size, "%dh%dm", secs / 3600, secs % 3600 / 60);
    else
        snprintf(buf, size, "%dm", secs / 60);
    return buf;
}

/* "950", "12.3k", "123M", "4.5B" */
static const char *fmt_tokens(char *buf, size_t size, long long n)
{
    static const char units[] = "kMBT";
    if (n < 0)
        return "—";
    if (n < 1000)
    {
        snprintf(buf, size, "%d", (int)n);
        return buf;
    }
    int i = 0;
    long long u = 1000;
    for (; i < 3 && n / u >= 1000; i++)
        u *= 1000;
    int tenths = n / (u / 10);
    if (tenths < 1000)
        snprintf(buf, size, "%d.%d%c", tenths / 10, tenths % 10, units[i]);
    else
        snprintf(buf, size, "%d%c", tenths / 10, units[i]);
    return buf;
}

/* "$41.20", or whole dollars from $1000 */
static const char *fmt_usd(char *buf, size_t size, long long milli)
{
    if (milli < 0)
        return "—";
    long long cents = (milli + 5) / 10;
    if (cents < 100000)
        snprintf(buf, size, "$%d.%02d", (int)(cents / 100),
                 (int)(cents % 100));
    else
        snprintf(buf, size, "$%ld", (long)MIN(cents / 100, 999999999LL));
    return buf;
}

static int text_w(struct screen *d, const char *text)
{
    int w, h;
    d->getstringsize((const unsigned char *)text, &w, &h);
    return w;
}

static void text_at(struct screen *d, int x, int y, unsigned colour,
                    const char *text)
{
    d->set_foreground(colour);
    d->putsxy(x, y, (const unsigned char *)text);
}

/* One limit in a cell w wide: its label, a bar, and the used percent and
 * reset time at the cell's right. A bar with no room is left out. */
static void draw_limit(struct screen *d, int x, int y, int w,
                       const char *label, int pct, int reset, int period,
                       bool dim)
{
    char r[16], text[24];
    if (pct < 0)
        snprintf(text, sizeof text, "—");
    else
        snprintf(text, sizeof text, "%d%% %s", pct,
                 fmt_reset(r, sizeof r, reset));
    unsigned colour = dim ? USE_DIM : USE_TEXT;
    int lw = text_w(d, label), tw = text_w(d, text);
    text_at(d, x, y, USE_DIM, label);
    text_at(d, x + w - tw, y, colour, text);
    int bx = x + lw + 5, bw = w - lw - tw - 11;
    if (bw < 12)
        return;
    int by = y + 6, bh = 8;
    d->set_foreground(dim ? USE_DIM : USE_SOFT);
    d->drawrect(bx, by, bw, bh);
    if (pct > 0)
    {
        /* Stale only greys a bar that is neither full nor ahead of its
         * period; the shown reset time is counted down like fmt_reset */
        int left = reset < 0 ? -1
                   : MAX(0, reset - (current_tick - usage_at) / HZ);
        d->set_foreground(pct >= 95 ? USE_FULL :
                          hr_usage_over_pace(pct, left, period) ? USE_PACE :
                          dim ? USE_DIM : USE_TEXT);
        d->fillrect(bx + 1, by + 1, MAX(1, (bw - 2) * pct / 100), bh - 2);
    }
}

/* A service: its name, tokens and cost, then its limits. A service
 * with nothing to show, or whose figures could not be read, is greyed and
 * gives its note in place of the limits. */
static void draw_service(struct screen *d, int y, int w,
                         const struct hr_service *s, bool stale)
{
    static const char *const names[HR_USAGE_SVCS] =
        { "Codex", "Claude", "Grok", "Gemini" };
    char t[16], c[16], right[40];
    bool grey = s->state == 'n' || s->state == 'e';
    bool dim = stale || grey;
    int i = s - model.usage.svc;
    text_at(d, 0, y, dim ? USE_DIM : USE_TEXT,
            names[i]);
    if (s->state == 'e' || (s->state == 'n' && s->tokens <= 0))
        snprintf(right, sizeof right, "—");
    else
        snprintf(right, sizeof right, "%s  %s",
                 fmt_tokens(t, sizeof t, s->tokens),
                 fmt_usd(c, sizeof c, s->usd_milli));
    text_at(d, w - text_w(d, right), y, dim ? USE_DIM : USE_SOFT, right);
    y += USE_LINE - 2;
    if (grey)
    {
        char note[48];
        text_at(d, 0, y, USE_DIM, fit(note, sizeof note,
                s->note[0] ? s->note : s->state == 'e' ? "Cannot read"
                                                       : "Not used", w));
        return;
    }
    /* A service with no 5h limit has one bar, the week's, the row wide */
    if (s->h5_pct < 0 && s->h5_reset < 0)
    {
        draw_limit(d, 0, y, w, "Week", s->wk_pct, s->wk_reset,
                   USE_WEEK, dim);
        return;
    }
    int half = (w - 12) / 2;
    draw_limit(d, 0, y, half, "5h", s->h5_pct, s->h5_reset, 5 * 3600,
               dim);
    draw_limit(d, w - half, y, half, "wk", s->wk_pct, s->wk_reset, USE_WEEK,
               dim);
}

/* Draw the usage view over the empty list, which has cleared its area */
static void draw_usage(struct gui_synclist *list)
{
    struct screen *d = &screens[SCREEN_MAIN];
    struct viewport *vp = list->parent[SCREEN_MAIN];
    const struct hr_usage *u = &model.usage;
    char buf[48], t[16], c[16];
    d->set_viewport(vp);
    unsigned fg = vp->fg_pattern;
    d->clear_viewport();
    const char *t_trouble = trouble(buf, sizeof buf);
    if (!u->have)
    {
        const char *msg = t_trouble ? t_trouble : "Waiting for usage…";
        text_at(d, (vp->width - text_w(d, msg)) / 2,
                (vp->height - USE_LINE) / 2, USE_DIM, msg);
    }
    else
    {
        int age = usage_age();
        bool stale = t_trouble || !u->ok || age > 3 * USAGE_EVERY / HZ;
        for (int i = 0; i < HR_USAGE_SVCS; i++)
            draw_service(d, i * USE_BLOCK - usage_scroll, vp->width,
                         &u->svc[i], stale);
        int y = HR_USAGE_SVCS * USE_BLOCK - usage_scroll;
        d->set_foreground(USE_DIM);
        d->hline(0, vp->width - 1, y);
        y += 3;
        snprintf(buf, sizeof buf, "%s  %s", fmt_tokens(t, sizeof t, u->tokens),
                 fmt_usd(c, sizeof c, u->usd_milli));
        text_at(d, 0, y, stale ? USE_DIM : USE_TEXT, "Total");
        text_at(d, text_w(d, "Total") + 10, y, stale ? USE_DIM : USE_TEXT,
                buf);
        if (stale)
            text_at(d, vp->width - text_w(d, "stale"), y, USE_WARN, "stale");
    }
    d->set_foreground(fg);
    d->update_viewport();
    d->set_viewport(NULL);
}

/* The detail view's colours beyond the usage view's: the working line, and
 * the dot by the state */
#define DET_ACCENT  LCD_RGBPACK(140, 220, 150)
#define DET_GAP     4       /* pixels between the view's parts */
#define DET_DOT     9       /* the dot's diameter */

/* Seconds in the state now: the age the last D gave, counted on since it
 * came; -1 unknown, -2 fallback */
static int detail_age(void)
{
    int age = model.detail.age;
    if (age < 0)
        return age;
    long on = (current_tick - detail_at) / HZ;
    return on > 2147483647 - age ? 2147483647 : age + (int)on;
}

/* "<1m", "29m", "1h05m", "2d3h" */
static const char *fmt_age(char *buf, size_t size, int secs)
{
    if (secs < 60)
        return "<1m";
    if (secs < 3600)
        snprintf(buf, size, "%dm", secs / 60);
    else if (secs < 86400)
        snprintf(buf, size, "%dh%02dm", secs / 3600, secs % 3600 / 60);
    else
        snprintf(buf, size, "%dd%dh", secs / 86400, secs % 86400 / 3600);
    return buf;
}

/* A disc DET_DOT across, its top left at x, y */
static void draw_disc(struct screen *d, int x, int y, unsigned colour)
{
    static const unsigned char inset[DET_DOT] = { 3, 1, 1, 0, 0, 0, 1, 1, 3 };
    d->set_foreground(colour);
    for (int i = 0; i < DET_DOT; i++)
        d->hline(x + inset[i], x + DET_DOT - 1 - inset[i], y + i);
}

/* Draw the task line at its scroll, in a strip of its own so the ticker can
 * draw it alone */
static void draw_task(struct screen *d, struct viewport *vp)
{
    struct viewport strip = *vp;
    strip.y += task_y;
    strip.height = USE_LINE;
    d->set_viewport(&strip);
    d->clear_viewport();
    text_at(d, -task_x, 0, USE_DIM, task_text);
    d->update_viewport();
    d->set_viewport(vp);
}

/* A frame of the task line's scroll, while the detail view is open */
static void tick_task(struct gui_synclist *list)
{
    struct viewport *vp = list->parent[SCREEN_MAIN];
    int over = task_w - vp->width;
    if (!task_text[0] || over <= 0 || TIME_BEFORE(current_tick, task_next))
        return;
    if (task_at_end)
    {
        task_x = 0;
        task_at_end = false;
        task_next = current_tick + TASK_REST;
    }
    else
    {
        task_x += TASK_STEP;
        if (task_x >= over)
        {
            task_x = over;
            task_at_end = true;
            task_next = current_tick + TASK_REST;
        }
    }
    draw_task(&screens[SCREEN_MAIN], vp);
    screens[SCREEN_MAIN].set_viewport(NULL);
}

/* The input block: what is typed in the agent's input box, in a frame, up
 * to INPUT_LINES lines of it in a window that follows the cursor's line */
#define INPUT_LINES 5
#define INPUT_PAD   3       /* between the frame and the text */
static int input_top;       /* the window's first line */

/* The line of the wrapped input the cursor is on, and its byte position:
 * the first line whose text or the spaces after it hold the cursor; after
 * a line break, the next line. Returns the number of lines. */
static int input_lines(const char *text, int width, struct font *pf,
                       const char *cursor_at, int *cursor_line)
{
    const char *end;
    int n = 0;
    *cursor_line = -1;
    for (const char *p = text; *p; n++)
    {
        const char *next = text_wrap_next_punct(p, width, pf, &end);
        if (*cursor_line < 0 && cursor_at >= p &&
            (cursor_at <= end || cursor_at < next || !*next))
            *cursor_line = n;
        p = next;
    }
    if (n == 0 || text[strlen(text) - 1] == '\n')
    {
        /* The cursor sits on the empty line after a final line break */
        if (*cursor_line < 0)
            *cursor_line = n;
        n++;
    }
    return n;
}

static int draw_input(struct screen *d, struct viewport *vp, int y,
                      bool dim)
{
    const struct hr_detail *det = &model.detail;
    struct font *pf = font_get(vp->font);
    int width = vp->width - 2 * (INPUT_PAD + 1), x0 = INPUT_PAD + 1;
    int h = pf->height, cursor_line;
    const char *text = det->input, *end;
    const char *cursor_at = text + hr_utf8_offset(text, det->cursor);
    char line[200];
    font_lock(vp->font, true);
    int nlines = input_lines(text, width, pf, cursor_at, &cursor_line);
    input_top = hr_input_window(cursor_line, nlines, INPUT_LINES, input_top);
    int shown = MIN(nlines - input_top, INPUT_LINES);
    d->set_foreground(USE_DIM);
    d->drawrect(0, y, vp->width, shown * USE_LINE + 2 * INPUT_PAD);
    int ly = y + INPUT_PAD;
    const char *p = text;
    for (int i = 0; i < input_top + shown; i++)
    {
        const char *next = *p ? text_wrap_next_punct(p, width, pf, &end)
                              : p + (end = p, 0);
        if (i >= input_top)
        {
            snprintf(line, sizeof line, "%.*s",
                     (int)MIN(end - p, (long)sizeof line - 1), p);
            text_at(d, x0, ly, dim ? USE_DIM : USE_TEXT, line);
            if (i == cursor_line)
            {
                /* The bar after the character before the cursor */
                const char *at = cursor_at < end ? cursor_at : end;
                snprintf(line, sizeof line, "%.*s",
                         (int)MIN(at - p, (long)sizeof line - 1), p);
                int cx = x0 + (at > p ? text_w(d, line) : 0);
                d->set_foreground(dim ? USE_DIM : USE_TEXT);
                d->vline(MIN(cx, vp->width - 2), ly + 1, ly + h - 2);
            }
            ly += USE_LINE;
        }
        p = next;
    }
    font_lock(vp->font, false);
    return y + shown * USE_LINE + 2 * INPUT_PAD + DET_GAP;
}

static int detail_glyph(const char *text, int *bytes, void *ctx)
{
    ucschar_t ch;
    const unsigned char *start = (const unsigned char *)text;
    const unsigned char *next = utf8decode(start, &ch);
    *bytes = next - start;
    return ch == '\n' || IS_DIACRITIC(ch) ? 0 : font_get_width(ctx, ch);
}

/* Draw the detail view over the empty list. The agent's name and kind head
 * it, with a dot, its state and the time in it on the right; below a rule
 * come the task, the working line (or the state when done, idle or
 * asking), the message, wrapped to as many lines as it needs, and the last
 * steps, the oldest dropped first when the message needs their lines. A
 * fallback detail is the pane's last lines in grey, with no time. */
static void draw_detail(struct gui_synclist *list)
{
    struct screen *d = &screens[SCREEN_MAIN];
    struct viewport *vp = list->parent[SCREEN_MAIN];
    const struct hr_detail *det = &model.detail;
    char buf[128], age[16], line[200];
    d->set_viewport(vp);
    unsigned fg = vp->fg_pattern;
    d->clear_viewport();
    const struct hr_agent *a = find_agent(detail_pane);
    if (a)
        detail_agent = *a;
    a = &detail_agent;
    bool have = model.detail_valid && !strcmp(model.detail_pane, detail_pane);
    bool fallback = have && det->age == -2;
    int secs = have ? detail_age() : -1;
    const char *t_trouble = trouble(buf, sizeof buf);

    /* The header: name · kind, and the dot, state and time on the right */
    char state = agent_state(a);
    snprintf(buf, sizeof buf, "%s%s", status_word(state),
             secs >= 0 ? " " : "");
    if (secs >= 0)
    {
        size_t n = strlen(buf);
        snprintf(buf + n, sizeof buf - n, "%s", fmt_age(age, sizeof age, secs));
    }
    detail_shown_age = secs >= 0 ? secs / 60 : -1;
    int sw = text_w(d, buf);
    int h = font_get(vp->font)->height;
    text_at(d, vp->width - sw, 0, state == 'b' ? USE_WARN : USE_SOFT, buf);
    draw_disc(d, vp->width - sw - DET_DOT - 5, (h - DET_DOT) / 2,
              state == 'b' ? DOT_RED : state == 'w' ? DOT_GREEN :
              state == 'd' ? DOT_TEAL : DOT_GREY);
    snprintf(buf, sizeof buf, "%s%s%s", a->name[0] ? a->name : "",
             a->name[0] ? " · " : "", a->kind);
    text_at(d, 0, 0, USE_DIM,
            fit(buf, sizeof buf, buf, vp->width - sw - DET_DOT - 13));
    int y = USE_LINE + 2;
    d->set_foreground(USE_DIM);
    d->hline(0, vp->width - 1, y);
    y += DET_GAP;

    bool empty = have && !det->task[0] && !det->now[0] && !det->say[0] &&
                 !det->step[0][0] && !det->step[1][0] && !det->step[2][0];
    if (!have || empty)
    {
        task_text[0] = '\0';
        const char *msg = t_trouble ? t_trouble :
                          have ? "Nothing in the pane" : "Waiting for the pane…";
        text_at(d, (vp->width - text_w(d, msg)) / 2,
                y + (vp->height - y - USE_LINE) / 2, USE_DIM, msg);
        d->set_foreground(fg);
        d->update_viewport();
        d->set_viewport(NULL);
        return;
    }

    /* The task, scrolled when wide: a new one starts at its left */
    if (strcmp(task_text, det->task))
    {
        snprintf(task_text, sizeof task_text, "%s", det->task);
        task_w = text_w(d, task_text);
        task_x = 0;
        task_at_end = false;
        task_next = current_tick + TASK_REST;
    }
    if (task_text[0])
    {
        task_y = y;
        draw_task(d, vp);
        y += USE_LINE;
    }

    /* What is typed, first: it is what the wheel's keys are editing */
    if (det->input[0])
        y = draw_input(d, vp, y, t_trouble != NULL);

    /* The working line, or the state */
    if (det->now[0])
    {
        unsigned colour = DET_ACCENT;
        const char *now = det->now;
        if (!strcmp(now, "done") || !strcmp(now, "idle"))
        {
            snprintf(line, sizeof line, "%s%s%s", now,
                     secs >= 0 ? " · " : "",
                     secs >= 0 ? fmt_age(age, sizeof age, secs) : "");
            now = line;
            colour = USE_SOFT;
        }
        else if (now[0] == '?')
            colour = USE_WARN;
        text_at(d, 0, y, t_trouble ? USE_DIM : colour,
                fit(line, sizeof line, now, vp->width));
        y += USE_LINE;
    }
    y += DET_GAP;

    /* Retain closed lines during streaming. A reader above the bottom
     * keeps the accepted message, steps and body geometry until returning. */
    bool frozen = detail_scroll < detail_scroll_max;
    struct font *pf = font_get(vp->font);
    char identity[9] = "";
    if (!strcmp(a->kind, "codex") && strlen(model.detail_revision) == 16)
        snprintf(identity, sizeof identity, "%.8s", model.detail_revision);
    font_lock(vp->font, true);
    detail_layout_update(&detail_layout, detail_pane, identity, det->say,
                         det, vp->width, frozen, detail_glyph, pf);
    font_lock(vp->font, false);
    if (!frozen || !detail_body_avail)
    {
        detail_body_y = y;
        detail_body_avail = MAX(0, (vp->height - y) / USE_LINE);
    }
    y = detail_body_y;
    int avail = detail_body_avail, need = detail_layout.count;
    detail_page_lines = MAX(1, avail);
    /* Tool rows consume spare space; starting/finishing a tool must never
     * push a message line off screen or change the scroll anchor. */
    int maximum = MAX(0, need - avail);
    detail_scroll = hr_detail_scroll(detail_scroll, detail_scroll_max, maximum);
    detail_scroll_max = maximum;
    unsigned colour = t_trouble || fallback ? USE_DIM : USE_TEXT;
    for (int i = detail_scroll; i < need && i < detail_scroll + avail; i++)
    {
        int start = detail_layout.start[i], end = detail_layout.end[i];
        snprintf(line, sizeof line, "%.*s", MIN(end - start, (int)sizeof line - 1),
                 detail_layout.text + start);
        text_at(d, 0, y + (i - detail_scroll) * USE_LINE, colour, line);
    }
    int row = need - detail_scroll;
    for (int i = 0; i < HR_DETAIL_STEPS && row < avail; i++)
    {
        if (!detail_layout.steps[i][0]) continue;
        snprintf(line, sizeof line, "› %s", detail_layout.steps[i]);
        text_at(d, 0, y + row++ * USE_LINE, USE_DIM,
                fit(line, sizeof line, line, vp->width));
    }
    d->set_foreground(fg);
    d->update_viewport();
    d->set_viewport(NULL);
}

/* Draw the confirm view over the empty list: the question, and what the
 * two keys do */
static void draw_confirm(struct gui_synclist *list)
{
    struct screen *d = &screens[SCREEN_MAIN];
    struct viewport *vp = list->parent[SCREEN_MAIN];
    const char *title = "Leave Agents?", *hint = "SELECT leave · MENU stay";
    d->set_viewport(vp);
    unsigned fg = vp->fg_pattern;
    d->clear_viewport();
    int y = (vp->height - 2 * USE_LINE - DET_GAP) / 2;
    text_at(d, (vp->width - text_w(d, title)) / 2, y, USE_TEXT, title);
    text_at(d, (vp->width - text_w(d, hint)) / 2, y + USE_LINE + DET_GAP,
            USE_DIM, hint);
    d->set_foreground(fg);
    d->update_viewport();
    d->set_viewport(NULL);
}

/* A microphone 11 pixels wide and 17 tall, its top left at x, y */
static void draw_mic(struct screen *d, int x, int y, unsigned colour)
{
    d->set_foreground(colour);
    d->hline(x + 4, x + 6, y);
    d->fillrect(x + 3, y + 1, 5, 9);
    d->hline(x + 4, x + 6, y + 10);
    d->vline(x, y + 5, y + 9);
    d->vline(x + 10, y + 5, y + 9);
    d->drawpixel(x + 1, y + 10);
    d->drawpixel(x + 9, y + 10);
    d->drawpixel(x + 2, y + 11);
    d->drawpixel(x + 8, y + 11);
    d->hline(x + 3, x + 7, y + 12);
    d->vline(x + 5, y + 13, y + 15);
    d->hline(x + 2, x + 8, y + 16);
}

/* Whether the band has anything to say: a hold, a recording the computer
 * reports, a request awaiting its result, or a result still shown */
static bool talk_is_short(void);

static bool talk_wanted(void)
{
    if ((talk_held && talk_is_short()) ||
        (native_play_pending && (unsigned long)current_tick - native_play_at <
                                (3 * HZ + 19) / 20))
        return false;
    if (talk_discarded && !talk_failed)
        return false;
    return talk_held || model.voice == 't' || talk_req[0] ||
           (talk_note[0] && TIME_BEFORE(current_tick, talk_until));
}

/* The band over the foot of the list: the microphone, what the voice input
 * is doing and, while it records, for how long */
static void draw_talk(struct gui_synclist *list)
{
    struct screen *d = &screens[SCREEN_MAIN];
    struct viewport *vp = list->parent[SCREEN_MAIN];
    char secs[12] = "";
    const char *text = talk_note;
    unsigned colour = USE_TEXT, mic = USE_DIM;
    /* Not held, a recording is shown only while V says one runs */
    bool recording = talk_held ? !talk_failed && model.voice != 'i' :
                     !talk_req[0] && (!talk_note[0] ||
                                      TIME_AFTER(current_tick, talk_until));
    if (recording)
    {
        text = "Talking…";
        mic = USE_FULL;
        snprintf(secs, sizeof secs, "%lds", (current_tick - talk_since) / HZ);
    }
    else if (talk_held && !talk_failed)
        text = "Stopped on the computer";
    else if (!talk_held && talk_req[0])
    {
        text = "Sending…";
        colour = USE_DIM;
    }
    else if (talk_failed)
        colour = USE_FULL;
    d->set_viewport(vp);
    unsigned fg = vp->fg_pattern;
    int y = vp->height - TALK_BAND;
    int h = font_get(vp->font)->height;
    d->set_foreground(LCD_RGBPACK(28, 28, 28));
    d->fillrect(0, y, vp->width, TALK_BAND);
    d->set_drawmode(DRMODE_FG);
    d->set_foreground(mic);
    d->hline(0, vp->width - 1, y);
    draw_mic(d, 8, y + (TALK_BAND - 17) / 2, mic);
    int sw = secs[0] ? text_w(d, secs) : 0;
    char fitted[64];
    text_at(d, 28, y + (TALK_BAND - h) / 2, colour,
            fit(fitted, sizeof fitted, text, vp->width - 28 - sw - 16));
    if (secs[0])
        text_at(d, vp->width - 8 - sw, y + (TALK_BAND - h) / 2, USE_SOFT,
                secs);
    d->set_drawmode(DRMODE_SOLID);
    d->set_foreground(fg);
    d->update_viewport_rect(0, y, vp->width, TALK_BAND);
    d->set_viewport(NULL);
    talk_shown = true;
}

/* What vd is about: the selected workspace or agent, or the question's
 * pane; "-" for none */
static void talk_target(struct gui_synclist *list, char *buf, size_t size)
{
    int sel = gui_synclist_get_sel_pos(list);
    const struct row *r = sel >= 0 && sel < nrows ? &rows[sel] : NULL;
    if (view == VIEW_QUESTION)
        snprintf(buf, size, "%s", question_pane);
    else if (view == VIEW_DETAIL)
        snprintf(buf, size, "%s", detail_pane);
    else if (r && r->kind == ROW_SPACE)
        snprintf(buf, size, "w:%s", r->id);
    else if (r && r->kind == ROW_AGENT)
        snprintf(buf, size, "%s", r->id);
    else if (r && r->kind == ROW_ALERT && model.nquestions)
        snprintf(buf, size, "%s", model.questions[0].pane);
    else
        snprintf(buf, size, "-");
}

static void talk_result(const char *text, bool failed)
{
    snprintf(talk_note, sizeof talk_note, "%s", text);
    talk_failed = failed;
    talk_until = current_tick + (failed ? 3 * HZ : 2 * HZ);
}

/* Play pressed: start recording on the computer */
static void start_talk(struct gui_synclist *list, bool remote)
{
    char target[24];
    if (talk_held)
        return;
    if (podlink_state() != PODLINK_CONNECTED || !model.hello)
    {
        talk_result("No link to the computer", true);
        return;
    }
    if (nout >= OUTBOX - 1)
        return; /* Keep one slot for vu/vc while recording. */
    char *slot = outbox_slot();
    if (!slot)
        return;
    talk_target(list, target, sizeof target);
    take_req(talk_req, sizeof talk_req);
    snprintf(slot, sizeof outbox[0], "vd\t%s\t%s", talk_req, target);
    talk_req_up = false;
    talk_held = true;
    talk_remote = remote;
    talk_discarded = false;
    agents_shortcut_talking(true);
    talk_since = current_tick;
    talk_until = current_tick + TALK_REPLY;
    talk_note[0] = '\0';
    talk_failed = false;
    model.voice = 0;
}

/* Discard clears the band immediately; a normal stop submits the recording. */
static void finish_talk(bool discard)
{
    if (!talk_held)
        return;
    talk_held = false;
    talk_discarded = discard;
    if (discard)
    {
        model.voice = 0;
        talk_note[0] = '\0';
        talk_failed = false;
    }
    agents_shortcut_talking(false);
    char *slot = podlink_state() == PODLINK_CONNECTED ? outbox_slot() : NULL;
    if (!slot)
    {
        talk_req[0] = '\0';
        return;
    }
    take_req(talk_req, sizeof talk_req);
    snprintf(slot, sizeof outbox[0], "%s\t%s", discard ? "vc" : "vu", talk_req);
    talk_req_up = !discard;
    talk_until = current_tick + TALK_REPLY;
}

static bool talk_is_short(void)
{
    unsigned long limit = talk_remote ? (3 * HZ + 9) / 10 :
                                       (3 * HZ + 19) / 20;
    return (unsigned long)current_tick - (unsigned long)talk_since < limit;
}

static void finish_native_play(void)
{
    bool tap = native_play_pending &&
               (unsigned long)current_tick - native_play_at < (3 * HZ + 19) / 20;
    native_play_pending = false;
    finish_talk(talk_is_short());
    if (tap)
    {
        talk_note[0] = '\0';
        talk_failed = false;
        default_event_handler(BUTTON_MULTIMEDIA_PLAYPAUSE);
    }
}

static void forget_keys(void)
{
    key_req[0] = '\0';
    key_runs.nruns = 0;
    wheel.dir = 0;
}

/* Send the oldest waiting run when no k is in flight. A run waits on while
 * the outbox is full, and in the detail view until its pane is focused. */
static void pump_keys(void)
{
    if (key_req[0] && TIME_AFTER(current_tick, key_sent + KEY_LOST))
        key_req[0] = '\0';
    if (key_req[0] || !key_runs.nruns)
        return;
    if (view == VIEW_DETAIL && !detail_ready)
        return;
    char *slot = outbox_slot();
    struct hr_key_run run;
    if (!slot || !hr_keys_take(&key_runs, &run))
        return;
    take_req(key_req, sizeof key_req);
    if (run.count == 1)
        snprintf(slot, sizeof outbox[0], "k\t%s\t%s", key_req, run.key);
    else
        snprintf(slot, sizeof outbox[0], "k\t%s\t%s\t%d",
                 key_req, run.key, run.count);
    key_sent = current_tick;
}

/* One key tap on the computer, from the usage or detail view: bs, left,
 * right or enter. A tap past the runs' caps is dropped. */
static void send_key(const char *key)
{
    if (podlink_state() != PODLINK_CONNECTED || !model.hello)
    {
        talk_result("No link to the computer", true);
        return;
    }
    hr_keys_add(&key_runs, key);
    pump_keys();
}

/* An R for k: silent when it worked, its reason in the band when not. The
 * computer's busy and rate refusals are not failures and stay silent.
 * Returns false for any other request's R. */
static bool take_key_result(void)
{
    if (!key_req[0] || strcmp(model.result_req, key_req))
        return false;
    model.result_new = false;
    key_req[0] = '\0';
    if (!model.result_ok && strcmp(model.result_text, "Key busy") &&
        strcmp(model.result_text, "Key rate limit"))
        talk_result(model.result_text, true);
    pump_keys();
    return true;
}

/* An R for fw, fp or ft: silent when it worked, its reason in the band
 * when not. The detail view's fp lets the waiting keys go, or on failure
 * drops them. Returns false for any other request's R. */
static bool take_focus_result(void)
{
    if (!focus_req[0] || strcmp(model.result_req, focus_req))
        return false;
    model.result_new = false;
    focus_req[0] = '\0';
    if (!model.result_ok)
        talk_result(model.result_text, true);
    if (focus_for_detail)
    {
        detail_ready = model.result_ok;
        if (detail_ready)
            pump_keys();
        else
            key_runs.nruns = 0;
    }
    return true;
}

/* The wheel moved in the spaces or agents view: the selected workspace or
 * pane is the one to focus; any other row wants none */
static void want_focus(struct gui_synclist *list)
{
    int sel = gui_synclist_get_sel_pos(list);
    const struct row *r = sel >= 0 && sel < nrows ? &rows[sel] : NULL;
    bool is = r && ((view == VIEW_SPACES && r->kind == ROW_SPACE) ||
                    (view == VIEW_AGENTS && r->kind == ROW_AGENT));
    snprintf(focus_want, sizeof focus_want, "%s", is ? r->id : "");
}

/* Forget the wanted and the done focus: the view has changed */
static void reset_focus(void)
{
    focus_want[0] = focus_done[0] = '\0';
}

/* Send the wanted focus when no request is out and Play is up with its
 * result in */
static void send_wanted_focus(void)
{
    if (!focus_want[0] || focus_req[0] || talk_held || talk_req[0])
        return;
    if (strcmp(focus_want, focus_done))
    {
        queue_focus(view == VIEW_SPACES ? "fw" : "fp", focus_want, false);
        snprintf(focus_done, sizeof focus_done, "%s", focus_want);
    }
    focus_want[0] = '\0';
}

/* An R for vd or vu. Returns false for any other request's R. */
static bool take_talk_result(void)
{
    if (!talk_req[0] || strcmp(model.result_req, talk_req))
        return false;
    model.result_new = false;
    talk_req[0] = '\0';
    if (!model.result_ok || talk_req_up)
        talk_result(model.result_text, !model.result_ok);
    return true;
}

/* Scrim gives the agents view a shorter list, with the selected row's
 * description (%LD) in a band under it, by switching the UI viewport on
 * the bar's own render, which reads %LD from the list's last draw; and the
 * list takes a switched viewport through a theme toggle, whose render finds
 * no list drawn and so switches back, until the next render and draw. A
 * list drawn once, before the bar has chosen, has rows under the band, and
 * their dots are drawn over its text at every frame. So the draw that is
 * seen is the third: two unseen draws and two renders bring the bar and
 * the list to agree, the way a wheel step after the band appears does. */
static void settle_viewport(struct gui_synclist *list)
{
    gui_synclist_inhibit_flush(true);
    gui_synclist_draw(list);
    sb_skin_update(SCREEN_MAIN, true);
    gui_synclist_draw(list);
    sb_skin_update(SCREEN_MAIN, true);
    gui_synclist_inhibit_flush(false);
    gui_synclist_layout(list);
}

static void update_volume_mode(void)
{
#ifdef IPOD_ACCESSORY_PROTOCOL
    bool want = view != VIEW_USAGE &&
                !(view == VIEW_CONFIRM && confirm_from == VIEW_USAGE);
    if (want != volume_paging)
    {
        iap_agents_volume_enable(want);
        volume_paging = want;
    }
#endif
}

/* Rebuild and draw, selecting the first row that can be chosen from select
 * on. In the question view the rows above it stay in view as far as they
 * fit, so a question is read from its start; in a list the selection goes
 * to the middle. top, when not -1, is the row to keep at the top instead. */
static void show_at(struct gui_synclist *list, int select, int top)
{
    update_volume_mode();
    build_rows(list);
    gui_synclist_set_nb_items(list, nrows);
    gui_synclist_set_title(list, view_title(), Icon_NOICON);
    while (select < nrows - 1 && !selectable(select))
        select++;
    if (select >= nrows)
        select = 0;
    gui_synclist_select_item(list, select);
    settle_viewport(list);
    int lines = gui_synclist_get_nb_lines(list, SCREEN_MAIN);
    if (top < 0)
        top = view == VIEW_QUESTION ? select - lines + 1 : select - lines / 2;
    gui_synclist_set_top_item(list, top);
    gui_synclist_draw(list);
    if (view == VIEW_USAGE)
        draw_usage(list);
    else if (view == VIEW_DETAIL)
        draw_detail(list);
    else if (view == VIEW_CONFIRM)
        draw_confirm(list);
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
    while (i < nrows && (rows[i].kind != was.kind ||
                         (was.id[0] ? strcmp(rows[i].id, was.id) :
                                      rows[i].index != was.index)))
        i++;
    /* A row that has not moved keeps the view where the reader left it */
    if (i < nrows)
        show_at(list, i, i == sel ? top : -1);
    else
        show(list, 0);
}

/* Rebuild and draw, selecting the row that stands for r when it is still
 * there, else the first */
static void show_row(struct gui_synclist *list, const struct row *r)
{
    build_rows(list);
    int i = 0;
    while (i < nrows && (rows[i].kind != r->kind ||
                         strcmp(rows[i].id, r->id)))
        i++;
    show(list, i < nrows ? i : 0);
}

/* The question view over the current view, which it goes back to, on the
 * row it was on */
static void open_question(struct gui_synclist *list, const char *pane)
{
    int sel = gui_synclist_get_sel_pos(list);
    question_from = view;
    question_from_row = sel >= 0 && sel < nrows ? rows[sel] : (struct row){0};
    snprintf(question_pane, sizeof question_pane, "%s", pane);
    const struct hr_question *q = current_question();
    if (q)
        hr_popped_add(&popped, q->id);
    view = VIEW_QUESTION;
    focus_want[0] = '\0';
    show(list, 0);
}

static void leave_question(struct gui_synclist *list)
{
    view = question_from;
    question_pane[0] = '\0';
    pending_req[0] = '\0';
    pending_answer = false;
    show_row(list, &question_from_row);
}

/* A question that has not popped up yet does so now, the oldest first and
 * one at a time, unless Play is held or its result is still to come, or
 * the confirm view is open */
static void pop_question(struct gui_synclist *list)
{
    if (view == VIEW_QUESTION || view == VIEW_CONFIRM || talk_held ||
        talk_req[0])
        return;
    const struct hr_question *q = hr_model_next_popup(&model, &popped);
    if (!q)
        return;
    hr_popped_add(&popped, q->id);
    backlight_on();
    open_question(list, q->pane);
}

static void open_usage(struct gui_synclist *list)
{
    usage_scroll = 0;
    int sel = gui_synclist_get_sel_pos(list);
    usage_from = sel >= 0 && sel < nrows ? rows[sel] : (struct row){0};
    view = VIEW_USAGE;
    reset_focus();
    ask_usage();
    show(list, 0);
}

/* Back to the spaces view, on the row the usage view was opened from */
static void close_usage(struct gui_synclist *list)
{
    view = VIEW_SPACES;
    key_runs.nruns = 0;
    show_row(list, &usage_from);
}

/* Into a workspace: its first tab's agents, and the computer shows it */
static void enter_space(struct gui_synclist *list, const char *ws)
{
    snprintf(space_id, sizeof space_id, "%s", ws);
    space_tab[0] = '\0';
    subscribe(space_id, space_tab);
    queue_focus("fw", space_id, false);
    view = VIEW_AGENTS;
    reset_focus();
    show(list, 0);
}

/* Back to the spaces view, on the workspace's row */
static void close_space(struct gui_synclist *list)
{
    struct row r = { .kind = ROW_SPACE };
    snprintf(r.id, sizeof r.id, "%s", space_id);
    view = VIEW_SPACES;
    space_id[0] = space_tab[0] = '\0';
    subscribe("", "");
    reset_focus();
    show_row(list, &r);
}

/* LEFT or RIGHT in the agents view: the tab before or after, which the
 * computer shows at once; its agents come with the next A, and the
 * selection goes to the first of them */
static void switch_tab(struct gui_synclist *list, int dir)
{
    char args[40];
    int i = hr_model_tab_index(&model, space_id, space_tab);
    if (i < 0 || i + dir < 0 || i + dir >= model.ntabs)
        return;
    snprintf(space_tab, sizeof space_tab, "%s", model.tabs[i + dir].id);
    snprintf(args, sizeof args, "%s\t%s", space_id, space_tab);
    queue_focus("ft", args, false);
    subscribe(space_id, space_tab);
    model.agents_valid = false;
    reset_focus();
    show(list, 0);
}

/* Into an agent's detail: its pane's lines come with D, and its keys go
 * once the computer has shown the pane */
static void open_detail(struct gui_synclist *list, const struct hr_agent *a)
{
    detail_scroll = detail_scroll_max = 0;
    memset(&detail_layout, 0, sizeof detail_layout);
    detail_body_avail = 0;
    snprintf(detail_pane, sizeof detail_pane, "%s", a->pane);
    detail_agent = *a;
    detail_ready = false;
    task_text[0] = '\0';
    detail_shown_age = -1;
    input_top = 0;
    forget_keys();
    model.detail_valid = false;
    subscribe_detail(detail_pane);
    queue_focus("fp", detail_pane, true);
    view = VIEW_DETAIL;
    reset_focus();
    show(list, 0);
}

/* Back to the agents view, on the agent's row */
static void close_detail(struct gui_synclist *list)
{
    struct row r = { .kind = ROW_AGENT };
    snprintf(r.id, sizeof r.id, "%s", detail_pane);
    subscribe_detail("");
    forget_keys();
    detail_ready = false;
    detail_pane[0] = '\0';
    view = VIEW_AGENTS;
    reset_focus();
    show_row(list, &r);
}

/* MENU at the top level: ask before leaving, over the view it came from */
static void open_confirm(struct gui_synclist *list)
{
    int sel = gui_synclist_get_sel_pos(list);
    confirm_from = view;
    confirm_from_row = sel >= 0 && sel < nrows ? rows[sel] : (struct row){0};
    view = VIEW_CONFIRM;
    reset_focus();
    show(list, 0);
}

/* MENU in the confirm view: back to the view and row it came from */
static void close_confirm(struct gui_synclist *list)
{
    view = confirm_from;
    if (view == VIEW_USAGE)
        show(list, 0);
    else
        show_row(list, &confirm_from_row);
}

/* Act on SELECT: enter the workspace, open the agent's detail or the
 * waiting question, or answer */
static void activate(struct gui_synclist *list)
{
    int sel = gui_synclist_get_sel_pos(list);
    if (!selectable(sel) || pending_req[0])
        return;
    const struct row *r = &rows[sel];
    const struct hr_question *q;
    switch (r->kind)
    {
        case ROW_ALERT:
            open_question(list, model.questions[0].pane);
            return;
        case ROW_SPACE:
            enter_space(list, model.ws[r->index].id);
            return;
        case ROW_AGENT:
            open_detail(list, &model.agents[r->index]);
            return;
        case ROW_OPTION:
        case ROW_SUBMIT:
        {
            char args[24];
            q = current_question();
            if (!q)
                return;
            snprintf(args, sizeof args, "%s\t%d", q->id,
                     r->kind == ROW_SUBMIT ? 0 : q->options[r->index].number);
            queue_request("a", args);
            pending_answer = !q->multi || r->kind == ROW_SUBMIT;
            show_same(list);
            return;
        }
    }
}

/* Take whatever the link has. Returns true if anything on screen changed. */
static bool poll_link(void)
{
    bool changed = false;
    bool connected = podlink_state() == PODLINK_CONNECTED;
    uint32_t generation = podlink_generation();
    if (generation != cache_generation)
    {
        cache_valid = false;
        was_connected = false;
        cache_generation = generation;
    }
    if (connected != was_connected)
    {
        was_connected = connected;
        model.hello = false;
        model.voice = 0;
        nout = 0;
        pending_req[0] = '\0';
        focus_req[0] = '\0';
        reset_focus();
        /* The computer stops recording when the link goes */
        if (talk_held || talk_req[0])
            talk_result("Link lost", true);
        talk_held = false;
        agents_shortcut_talking(false);
        talk_req[0] = '\0';
        forget_keys();
        if (connected)
        {
            char *slot = outbox_slot();
            if (slot)
                strcpy(slot, "hr\t1");
            if (space_id[0])
                subscribe(space_id, space_tab);
            if (detail_pane[0])
            {
                detail_ready = false;
                subscribe_detail(detail_pane);
                queue_focus("fp", detail_pane, true);
            }
            if (view == VIEW_USAGE)
                ask_usage();
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
        /* Only what alters a row, the title or a drawn view is a change */
        if (type == 'W' || type == 'S')
            changed |= model.ws_shown_changed;
        else if (type != 'T' && type != 'R' && type != 'V' && type != 'H')
            changed = true;
        if (type == 'Q' && model.nquestions > before && !had)
            backlight_on();
        if (type == 'U' && model.usage.serial != usage_serial)
        {
            usage_serial = model.usage.serial;
            usage_at = current_tick;
        }
        if (type == 'V' && model.voice == 't' && !talk_held)
            talk_since = current_tick;
        if (type == 'D')
            detail_at = current_tick;
        /* The tab shown has gone: back to the first */
        if (type == 'B' && space_tab[0] && !strcmp(model.tabs_ws, space_id)
            && hr_model_tab_index(&model, space_id, space_tab) < 0)
        {
            space_tab[0] = '\0';
            subscribe(space_id, space_tab);
            model.agents_valid = false;
        }
        /* One result at a time: take_result reads it before the next */
        if (type == 'R' && !take_talk_result() && !take_key_result() &&
            !take_focus_result())
            break;
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

static bool agents_play_down(void)
{
    int mask = BUTTON_PLAY;
#ifdef IPOD_ACCESSORY_PROTOCOL
    mask |= BUTTON_RC_PLAY;
#endif
    return (button_status() & mask) != 0;
}

int agents_screen(void)
{
    struct gui_synclist list;
    int action, ret = 0;
    char buf[48];

    if (!podlink_open_agents(background_link))
    {
        splash(HZ * 3, "Unplug USB, then open Agents");
        return 0;
    }
    bool resume = cache_valid && cache_generation == podlink_generation() &&
                  podlink_state() == PODLINK_CONNECTED;
    if (!resume)
    {
    hr_model_reset(&model);
    view = VIEW_SPACES;
    space_id[0] = space_tab[0] = detail_pane[0] = '\0';
    question_pane[0] = pending_req[0] = focus_req[0] = '\0';
    pending_answer = was_connected = detail_ready = false;
    detail_shown_age = -1;
    task_text[0] = '\0';
    reset_focus();
    talk_held = talk_shown = talk_discarded = false;
    agents_shortcut_talking(false);
    talk_req[0] = talk_note[0] = '\0';
    forget_keys();
    nout = 0;
    last_message = current_tick;
    usage_serial = model.usage.serial;
    usage_scroll = detail_scroll = detail_scroll_max = 0;
    memset(&detail_layout, 0, sizeof detail_layout);
    detail_body_avail = 0;
    detail_page_lines = 1;
    }
    cache_generation = podlink_generation();

    FOR_NB_SCREENS(i)
        viewportmanager_theme_enable(i, true, NULL);
    native_play_pending = false;
#ifdef IPOD_ACCESSORY_PROTOCOL
    volume_paging = false;
    update_volume_mode();
#endif
    push_current_activity(ACTIVITY_AGENTS);
    /* Play is held to talk here, and held long enough it would power off */
    bool poweroff = button_get_sw_poweroff_state();
    button_set_sw_poweroff_state(false);
    gui_synclist_init(&list, row_name, &list, false, 1, NULL);
    gui_synclist_set_row_callbacks(&list, NULL, row_value, NULL);
    gui_synclist_set_description_callback(&list, row_description);
    gui_synclist_set_voice_callback(&list, speak_row);
    gui_synclist_set_status_callback(&list, draw_dot);
    if (resume)
    {
        show_row(&list, &cache_row);
        gui_synclist_set_top_item(&list, cache_top);
        if (view == VIEW_USAGE) draw_usage(&list);
        else if (view == VIEW_DETAIL) draw_detail(&list);
        else gui_synclist_draw(&list);
    }
    else
        show(&list, 0);

    long frame_at = current_tick;
    long redraw_at = current_tick + HZ;
    const char *shown_trouble = NULL;
    bool done = false;
    while (!done)
    {
        bool changed = poll_link();
        pump_keys();
        take_result(&list);
        if (view == VIEW_QUESTION)
        {
            const struct hr_question *q = current_question();
            /* Answered, or answered on the computer */
            if (!q)
                leave_question(&list);
            /* A box ticked gives the question a new id, already in view */
            else
                hr_popped_add(&popped, q->id);
        }
        pop_question(&list);
        /* Status rows count seconds, so they redraw once a second */
        const char *t = trouble(buf, sizeof buf);
        if (view == VIEW_USAGE)
        {
            /* Its times count down once a second, and it goes stale with age */
            if (TIME_AFTER(current_tick, usage_next))
                ask_usage();
            if (changed || t != shown_trouble ||
                TIME_AFTER(current_tick, redraw_at))
            {
                shown_trouble = t;
                redraw_at = current_tick + HZ;
                draw_usage(&list);
            }
        }
        else if (changed || t != shown_trouble ||
                 (t && TIME_AFTER(current_tick, redraw_at)) ||
                 (view == VIEW_DETAIL && detail_shown_age >= 0 &&
                  detail_age() / 60 != detail_shown_age))
        {
            /* The detail's time in state is drawn again each minute */
            shown_trouble = t;
            redraw_at = current_tick + HZ;
            if (view == VIEW_DETAIL)
                draw_detail(&list);
            else if (view == VIEW_CONFIRM)
                draw_confirm(&list);
            else
                show_same(&list);
        }

        /* Only the dots, and the detail's task line, are drawn again for a
         * frame, and only while one in view moves and the screen can be
         * seen */
        if (TIME_AFTER(current_tick, frame_at))
        {
            frame_at = current_tick + ANIM_TICKS - 1;
            if (is_backlight_on(true) && view == VIEW_DETAIL)
                tick_task(&list);
            else if (is_backlight_on(true) && animating(&list))
                skinlist_redraw_status(&screens[SCREEN_MAIN], &list);
        }

        /* A release the keymap did not see still ends the hold. A failure
         * stays in view while Play is held, and for a moment after. */
        bool play_down = agents_play_down();
        if ((talk_held || native_play_pending) && !play_down)
            finish_native_play();
        if (talk_failed && play_down && TIME_BEFORE(current_tick, talk_until))
            talk_until = current_tick + 3 * HZ;
        if (talk_req[0] && TIME_AFTER(current_tick, talk_until))
        {
            talk_req[0] = '\0';
            talk_result("No reply from the computer", true);
        }
        if (focus_req[0] && TIME_AFTER(current_tick, focus_until))
        {
            focus_req[0] = '\0';
            talk_result("No reply from the computer", true);
            if (focus_for_detail)
                key_runs.nruns = 0;
        }
        send_wanted_focus();
        /* The band is drawn over whatever the list drew last; once it has
         * gone the list is drawn again beneath */
        if (talk_wanted())
            draw_talk(&list);
        else if (talk_shown)
        {
            talk_shown = false;
            if (view == VIEW_USAGE)
                draw_usage(&list);
            else if (view == VIEW_DETAIL)
                draw_detail(&list);
            else if (view == VIEW_CONFIRM)
                draw_confirm(&list);
            else
                show_same(&list);
        }

        int before = gui_synclist_get_sel_pos(&list);
        int before_top = list.start_item[SCREEN_MAIN];
        if (view == VIEW_USAGE || view == VIEW_DETAIL || view == VIEW_CONFIRM)
        {
            /* No list to scroll: the wheel is the arrow keys, or in the
             * confirm view nothing */
            action = get_action(CONTEXT_AGENTS, ANIM_TICKS);
            int dir = 0;
            if (view == VIEW_CONFIRM)
                ;
            else if (action == ACTION_STD_PREV ||
                     action == ACTION_STD_PREVREPEAT)
                dir = -1;
            else if (action == ACTION_STD_NEXT ||
                     action == ACTION_STD_NEXTREPEAT)
                dir = 1;
            if (action == ACTION_LISTTREE_PGUP ||
                action == ACTION_LISTTREE_PGDOWN)
            {
#ifdef IPOD_ACCESSORY_PROTOCOL
                int raw_button;
                get_action_statuscode(&raw_button);
                if (view == VIEW_USAGE &&
                    (raw_button & (BUTTON_RC_VOL_UP | BUTTON_RC_VOL_DOWN)))
                {
                    default_event_handler(action == ACTION_LISTTREE_PGUP ?
                        BUTTON_MULTIMEDIA_VOLUME_UP : BUTTON_MULTIMEDIA_VOLUME_DOWN);
                }
                else
#endif
                {
                    int direction = action == ACTION_LISTTREE_PGUP ? -1 : 1;
                    if (view == VIEW_USAGE)
                    {
                        int height = list.parent[SCREEN_MAIN]->height;
                        int maximum = MAX(0, HR_USAGE_SVCS * USE_BLOCK +
                                             USE_LINE + 3 - height);
                        usage_scroll = MAX(0, MIN(maximum,
                                          usage_scroll + direction * height));
                        draw_usage(&list);
                    }
                    else if (view == VIEW_DETAIL)
                    {
                        detail_scroll = MAX(0, MIN(detail_scroll_max,
                            detail_scroll + direction * detail_page_lines));
                        draw_detail(&list);
                    }
                }
                action = ACTION_NONE;
            }
            const char *key = dir ? hr_wheel_step(&wheel, current_tick, dir,
                                                  WHEEL_FAST) : NULL;
            if (key)
                send_key(key);
        }
        else if (list_do_action(CONTEXT_AGENTS, ANIM_TICKS, &list, &action))
        {
            int sel = gui_synclist_get_sel_pos(&list);
            int lines = gui_synclist_get_nb_lines(&list, SCREEN_MAIN);
            if (sel != before && view != VIEW_QUESTION)
                want_focus(&list);
            if (view == VIEW_QUESTION && sel > before &&
                before >= before_top + lines)
            {
                /* The selection is below the rows in view, which were
                 * scrolled up to read: scroll back down towards it. Only
                 * the question scrolls so; in the agents view a selection
                 * out of view is the band's doing, and the move itself
                 * brings it back. */
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
            {
                /* The list keymap gives SELECT and RIGHT the same action;
                 * RIGHT sends it on the press only, so a held RIGHT goes
                 * one view or tab */
                int button;
                get_action_statuscode(&button);
                bool right = button & BUTTON_RIGHT;
                if (view == VIEW_CONFIRM)
                {
                    if (!right)
                        done = true;
                }
                else if (view == VIEW_USAGE)
                {
                    if (right)
                        close_usage(&list);
                    else
                        send_key("enter");
                }
                else if (view == VIEW_DETAIL)
                {
                    if (!right)
                        send_key("enter");
                }
                else if (view == VIEW_SPACES && right)
                    open_usage(&list);
                else if (view == VIEW_AGENTS && right)
                    switch_tab(&list, 1);
                else if (!right)
                    activate(&list);
                break;
            }
            case ACTION_STD_CANCEL:
            case ACTION_STD_MENU:
            {
                /* LEFT goes to usage, the tab before or Backspace; MENU
                 * goes back a view, and from spaces and usage asks before
                 * leaving Agents. LEFT acts on the press alone, but for
                 * Backspace, which its repeats send too. */
                int button;
                int status = get_action_statuscode(&button);
                bool left = action == ACTION_STD_CANCEL &&
                            (button & BUTTON_LEFT);
                if (view == VIEW_CONFIRM)
                {
                    if (!left)
                        close_confirm(&list);
                    break;
                }
                if (view == VIEW_USAGE || view == VIEW_DETAIL)
                {
                    if (left)
                        send_key("bs");
                    else if (view == VIEW_USAGE)
                        open_confirm(&list);
                    else
                        close_detail(&list);
                    break;
                }
                if (left && (status & ACTION_REPEAT))
                    break;
                if (view == VIEW_SPACES)
                {
                    if (left)
                        open_usage(&list);
                    else
                        open_confirm(&list);
                }
                else if (view == VIEW_AGENTS)
                {
                    if (left)
                        switch_tab(&list, -1);
                    else
                        close_space(&list);
                }
                else if (!left)
                    leave_question(&list);
                break;
            }
            case ACTION_AGENTS_TALK:
                native_play_pending = true;
                native_play_at = current_tick;
                if (view != VIEW_CONFIRM)
                {
                    start_talk(&list, false);
                }
                break;
            case ACTION_AGENTS_REMOTE_TALK:
                if (view != VIEW_CONFIRM)
                    start_talk(&list, true);
                break;
            case ACTION_AGENTS_DISCARD:
                finish_talk(true);
                break;
            case ACTION_AGENTS_SWITCH:
                native_play_pending = false;
                finish_talk(true);
                ret = AGENTS_SWITCH_WPS;
                done = true;
                break;
            case ACTION_AGENTS_TALK_END:
                finish_native_play();
                break;
            case ACTION_STD_CONTEXT:
                /* SELECT held is still one Enter in usage and detail: the
                 * first repeat sends it, the rest and the release nothing */
                if ((view == VIEW_USAGE || view == VIEW_DETAIL) &&
                    !(get_action_statuscode(NULL) & ACTION_REPEAT))
                    send_key("enter");
                break;
            case ACTION_AGENTS_LEFT_REPEAT:
                if (view == VIEW_USAGE || view == VIEW_DETAIL)
                    send_key("bs");
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

    /* A hold ends with the screen */
    finish_talk(false);
    flush_outbox();
    /* The resident service owns the connection; exit keeps the cached view. */
    long deadline = current_tick + 2 * HZ;
    while (nout && podlink_state() == PODLINK_CONNECTED &&
           TIME_BEFORE(current_tick, deadline))
    {
        poll_link();
        flush_outbox();
        sleep(1);
    }
    if (view == VIEW_CONFIRM)
    {
        view = confirm_from;
        show_row(&list, &confirm_from_row);
    }
    forget_keys();
    focus_want[0] = '\0';
    int selected = gui_synclist_get_sel_pos(&list);
    cache_row = selected >= 0 && selected < nrows ? rows[selected] :
                                                   (struct row){0};
    cache_top = list.start_item[SCREEN_MAIN];
    cache_valid = was_connected && model.hello && !nout;
    cache_generation = podlink_generation();
    podlink_close();
    /* Play still held would power off at once: wait for its release */
    while (ret != AGENTS_SWITCH_WPS && agents_play_down())
        sleep(HZ / 20);
    button_set_sw_poweroff_state(poweroff);
    pop_current_activity();
#ifdef IPOD_ACCESSORY_PROTOCOL
    iap_agents_volume_enable(false);
    volume_paging = false;
#endif
    native_play_pending = false;
    FOR_NB_SCREENS(i)
        viewportmanager_theme_undo(i, false);
    return ret;
}
