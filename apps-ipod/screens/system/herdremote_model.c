/***************************************************************************
 * GNU General Public License (version 2+)
 *
 * HerdRemote v1 message parsing for the Agents screen; see
 * herdremote_model.h. A message is records split by '\n', fields by '\t'.
 * It is copied once into a scratch buffer and split in place there.
 ****************************************************************************/

#include <string.h>
#include <stdlib.h>
#include "herdremote_model.h"

#define HR_MESSAGE_MAX 4096
#define HR_MAX_FIELDS  10

/* Static rather than on the stack: the UI thread's stack is small, and a
 * message is parsed into these before it replaces anything in the model. */
static char scratch[HR_MESSAGE_MAX + 1];
static struct hr_workspace new_ws[HR_MAX_WS];
static struct hr_agent new_agents[HR_MAX_AGENTS];
static struct hr_question new_question;
static struct hr_usage new_usage;

void hr_copy(char *dst, const char *src, unsigned int n)
{
    unsigned int len = strlen(src);
    if (len >= n)
    {
        len = n - 1;
        /* Step back over continuation bytes to the start of the cut char */
        while (len > 0 && ((unsigned char)src[len] & 0xC0) == 0x80)
            len--;
    }
    memcpy(dst, src, len);
    dst[len] = '\0';
}

static int number(const char *s)
{
    return atoi(s);
}

/* Split one record into fields; returns how many */
static int split(char *record, char **fields)
{
    int n = 0;
    fields[n++] = record;
    for (char *p = record; *p && n < HR_MAX_FIELDS; p++)
    {
        if (*p == '\t')
        {
            *p = '\0';
            fields[n++] = p + 1;
        }
    }
    return n;
}

/* The next record, or NULL at the end */
static char *next_record(char **cursor)
{
    char *start = *cursor;
    if (!start)
        return NULL;
    char *end = strchr(start, '\n');
    if (end)
    {
        *end = '\0';
        *cursor = end + 1;
    }
    else
        *cursor = NULL;
    return start;
}

static int apply_workspaces(struct hr_model *m, char **cursor)
{
    char *f[HR_MAX_FIELDS], *record;
    int n = 0;
    while ((record = next_record(cursor)) && n < HR_MAX_WS)
    {
        if (split(record, f) != 7)
            return 0;
        struct hr_workspace *w = &new_ws[n++];
        hr_copy(w->id, f[0], sizeof w->id);
        w->focused = f[1][0] == 'f';
        w->working = number(f[2]);
        w->waiting = number(f[3]);
        w->done = number(f[4]);
        w->idle = number(f[5]);
        hr_copy(w->label, f[6], sizeof w->label);
        w->tab1 = w->waiting ? 'b' : w->working ? 'w' : w->done ? 'd' :
                  w->idle ? 'i' : '-';
        w->other = '-';
    }
    memcpy(m->ws, new_ws, n * sizeof new_ws[0]);
    m->nws = n;
    return 'W';
}

/* A state letter as S sends it, or '-' */
static char state(const char *s)
{
    return s[0] && strchr("bwdiu", s[0]) && !s[1] ? s[0] : '-';
}

/* S refers to W's workspaces by id; one W does not name is left out. The
 * whole message is checked before any of it is applied. */
static int apply_status(struct hr_model *m, char **cursor)
{
    char *f[HR_MAX_FIELDS], *record;
    int n = 0;
    while ((record = next_record(cursor)) && n < HR_MAX_WS)
    {
        if (split(record, f) != 3)
            return 0;
        struct hr_workspace *w = &new_ws[n++];
        hr_copy(w->id, f[0], sizeof w->id);
        w->tab1 = state(f[1]);
        w->other = state(f[2]);
    }
    for (int i = 0; i < n; i++)
        for (int j = 0; j < m->nws; j++)
            if (!strcmp(m->ws[j].id, new_ws[i].id))
            {
                m->ws[j].tab1 = new_ws[i].tab1;
                m->ws[j].other = new_ws[i].other;
            }
    return 'S';
}

static int apply_agents(struct hr_model *m, const char *ws, char **cursor)
{
    char *f[HR_MAX_FIELDS], *record;
    int n = 0;
    while ((record = next_record(cursor)) && n < HR_MAX_AGENTS)
    {
        if (split(record, f) != 7)
            return 0;
        struct hr_agent *a = &new_agents[n++];
        hr_copy(a->pane, f[0], sizeof a->pane);
        hr_copy(a->kind, f[1], sizeof a->kind);
        a->status = f[2][0] ? f[2][0] : 'u';
        a->question = f[3][0] == '1';
        hr_copy(a->name, f[4], sizeof a->name);
        hr_copy(a->title, f[5], sizeof a->title);
        hr_copy(a->last, f[6], sizeof a->last);
    }
    memcpy(m->agents, new_agents, n * sizeof new_agents[0]);
    hr_copy(m->agents_ws, ws, sizeof m->agents_ws);
    m->nagents = n;
    m->agents_valid = true;
    return 'A';
}

static int find_question(const struct hr_model *m, const char *id)
{
    for (int i = 0; i < m->nquestions; i++)
        if (!strcmp(m->questions[i].id, id))
            return i;
    return -1;
}

static void remove_question(struct hr_model *m, int i)
{
    memmove(&m->questions[i], &m->questions[i + 1],
            (m->nquestions - i - 1) * sizeof m->questions[0]);
    m->nquestions--;
}

static int apply_question(struct hr_model *m, char **head, int nhead,
                          char **cursor)
{
    char *f[HR_MAX_FIELDS], *record;
    struct hr_question *q = &new_question;
    if (nhead != 6)
        return 0;
    memset(q, 0, sizeof *q);
    hr_copy(q->id, head[1], sizeof q->id);
    hr_copy(q->pane, head[2], sizeof q->pane);
    hr_copy(q->workspace, head[3], sizeof q->workspace);
    q->multi = head[4][0] == 'm';
    hr_copy(q->agent, head[5], sizeof q->agent);
    while ((record = next_record(cursor)))
    {
        int n = split(record, f);
        if (n == 2 && !strcmp(f[0], "?"))
        {
            if (q->nlines < HR_MAX_LINES)
                hr_copy(q->lines[q->nlines++], f[1], sizeof q->lines[0]);
        }
        else if (n == 3 && !strcmp(f[0], "o"))
        {
            if (q->noptions < HR_MAX_OPTIONS)
            {
                struct hr_option *o = &q->options[q->noptions++];
                o->number = number(f[1]);
                hr_copy(o->label, f[2], sizeof o->label);
            }
        }
        else
            return 0;
    }
    if (q->noptions == 0)
        return 0;
    int i = find_question(m, q->id);
    if (i < 0)
    {
        /* Full: the oldest question goes, as the daemon would resend it */
        if (m->nquestions == HR_MAX_QUESTIONS)
            remove_question(m, 0);
        i = m->nquestions++;
    }
    m->questions[i] = *q;
    return 'Q';
}

/* A count, held at max, or '-', which is -1. Returns false for anything
 * else. */
static bool count(const char *s, long long max, long long *out)
{
    if (!strcmp(s, "-"))
    {
        *out = -1;
        return true;
    }
    if (!*s)
        return false;
    long long v = 0;
    for (; *s; s++)
    {
        if (*s < '0' || *s > '9')
            return false;
        v = v > (max - (*s - '0')) / 10 ? max : v * 10 + (*s - '0');
    }
    *out = v;
    return true;
}

/* The computer sends counts below 10^18; anything larger is held there */
#define HR_BIG 999999999999999999LL

/* A count kept as an int: a percent, which must be no more than max, or a
 * number of seconds, held at max */
static bool small_count(const char *s, int max, bool clamp, int *out)
{
    long long v;
    if (!count(s, HR_BIG, &v) || (!clamp && v > max))
        return false;
    *out = v > max ? max : v;
    return true;
}

#define HR_SECONDS 99999999     /* over three years */

/* U, then one P per service and N. A P for a service this screen does not
 * know is left out. */
static int apply_usage(struct hr_model *m, char **head, int nhead,
                       char **cursor)
{
    static const char *const names[HR_USAGE_SVCS] =
        { "codex", "claude", "grok", "gemini" };
    char *f[HR_MAX_FIELDS], *record;
    struct hr_usage *u = &new_usage;
    bool total = false;
    if (nhead != 3 || !small_count(head[1], HR_SECONDS, true, &u->age) ||
        u->age < 0 || (strcmp(head[2], "0") && strcmp(head[2], "1")))
        return 0;
    memset(u->svc, 0, sizeof u->svc);
    for (int i = 0; i < HR_USAGE_SVCS; i++)
    {
        hr_copy(u->svc[i].name, names[i], sizeof u->svc[i].name);
        u->svc[i].state = 'n';
        u->svc[i].h5_pct = u->svc[i].h5_reset = -1;
        u->svc[i].wk_pct = u->svc[i].wk_reset = -1;
        u->svc[i].tokens = u->svc[i].usd_milli = -1;
    }
    u->ok = head[2][0] == '1';
    u->tokens = u->usd_milli = -1;
    while ((record = next_record(cursor)))
    {
        int n = split(record, f);
        if (n == 3 && !strcmp(f[0], "N"))
        {
            if (!count(f[1], HR_BIG, &u->tokens) ||
                !count(f[2], HR_BIG, &u->usd_milli))
                return 0;
            total = true;
            continue;
        }
        if (n != 10 || strcmp(f[0], "P"))
            return 0;
        struct hr_service s;
        static const char states[][5] = { "ok", "tok", "none", "err" };
        s.state = 0;
        for (unsigned int i = 0; i < sizeof states / sizeof states[0]; i++)
            if (!strcmp(f[2], states[i]))
                s.state = states[i][0];
        if (!s.state || !small_count(f[3], 100, false, &s.h5_pct) ||
            !small_count(f[4], HR_SECONDS, true, &s.h5_reset) ||
            !small_count(f[5], 100, false, &s.wk_pct) ||
            !small_count(f[6], HR_SECONDS, true, &s.wk_reset) ||
            !count(f[7], HR_BIG, &s.tokens) ||
            !count(f[8], HR_BIG, &s.usd_milli))
            return 0;
        hr_copy(s.note, f[9], sizeof s.note);
        for (int i = 0; i < HR_USAGE_SVCS; i++)
            if (!strcmp(f[1], names[i]))
            {
                hr_copy(s.name, names[i], sizeof s.name);
                u->svc[i] = s;
            }
    }
    if (!total)
        return 0;
    if (!u->ok && m->usage.have)
    {
        m->usage.ok = false;
        return 'U';
    }
    u->have = true;
    u->serial = m->usage.serial + 1;
    m->usage = *u;
    return 'U';
}

int hr_model_apply(struct hr_model *m, const char *data, unsigned int length)
{
    char *f[HR_MAX_FIELDS];
    if (length == 0 || length > HR_MESSAGE_MAX)
        return 0;
    memcpy(scratch, data, length);
    scratch[length] = '\0';
    if (strlen(scratch) != length)
        return 0;
    char *cursor = scratch;
    char *record = next_record(&cursor);
    int n = split(record, f);
    const char *type = f[0];

    if (!strcmp(type, "HR") && n >= 2)
    {
        if (number(f[1]) != 1)
            return 0;
        m->hello = true;
        return 'H';
    }
    if (!strcmp(type, "W") && n == 2)
        return apply_workspaces(m, &cursor);
    if (!strcmp(type, "S") && n == 2)
        return apply_status(m, &cursor);
    if (!strcmp(type, "A") && n == 3)
    {
        char ws[16];
        hr_copy(ws, f[1], sizeof ws);
        return apply_agents(m, ws, &cursor);
    }
    if (!strcmp(type, "Q"))
        return apply_question(m, f, n, &cursor);
    if (!strcmp(type, "X") && n == 2)
    {
        int i = find_question(m, f[1]);
        if (i >= 0)
            remove_question(m, i);
        return 'X';
    }
    if (!strcmp(type, "R") && n == 4)
    {
        hr_copy(m->result_req, f[1], sizeof m->result_req);
        m->result_ok = !strcmp(f[2], "ok");
        hr_copy(m->result_text, f[3], sizeof m->result_text);
        m->result_new = true;
        return 'R';
    }
    if (!strcmp(type, "U"))
        return apply_usage(m, f, n, &cursor);
    if (!strcmp(type, "T") && n == 3)
    {
        m->herdr_age = number(f[1]);
        m->herdr_ok = f[2][0] == '1';
        return 'T';
    }
    return 0;
}

void hr_model_reset(struct hr_model *m)
{
    memset(m, 0, sizeof *m);
}

const struct hr_question *hr_model_question_for(const struct hr_model *m,
                                                const char *pane)
{
    for (int i = 0; i < m->nquestions; i++)
        if (!strcmp(m->questions[i].pane, pane))
            return &m->questions[i];
    return NULL;
}
