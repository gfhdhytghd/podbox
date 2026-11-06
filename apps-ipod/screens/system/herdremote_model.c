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
#define HR_MAX_FIELDS  16

/* Static rather than on the stack: the UI thread's stack is small, and a
 * message is parsed into these before it replaces anything in the model. */
static char scratch[HR_MESSAGE_MAX + 1];
static struct hr_workspace new_ws[HR_MAX_WS];
static struct hr_agent new_agents[HR_MAX_AGENTS];
static struct hr_tab new_tabs[HR_MAX_TABS];
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

static const struct hr_workspace *find_ws(const struct hr_model *m,
                                          const char *id)
{
    for (int i = 0; i < m->nws; i++)
        if (!strcmp(m->ws[i].id, id))
            return &m->ws[i];
    return NULL;
}

/* A workspace whose counts have not changed keeps the states its last S
 * gave it, so the S the computer repeats after every W changes nothing */
static int apply_workspaces(struct hr_model *m, char **cursor)
{
    char *f[HR_MAX_FIELDS], *record;
    int n = 0;
    bool same = true;
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
        const struct hr_workspace *old = find_ws(m, w->id);
        if (old && old->working == w->working &&
            old->waiting == w->waiting && old->done == w->done &&
            old->idle == w->idle)
        {
            w->tab1 = old->tab1;
            w->other = old->other;
            if (old != &m->ws[n - 1] || strcmp(old->label, w->label))
                same = false;
        }
        else
        {
            w->tab1 = w->waiting ? 'b' : w->working ? 'w' : w->done ? 'd' :
                      w->idle ? 'i' : '-';
            w->other = '-';
            same = false;
        }
    }
    m->ws_shown_changed = !same || n != m->nws;
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
    m->ws_shown_changed = false;
    for (int i = 0; i < n; i++)
        for (int j = 0; j < m->nws; j++)
            if (!strcmp(m->ws[j].id, new_ws[i].id))
            {
                if (m->ws[j].tab1 != new_ws[i].tab1 ||
                    m->ws[j].other != new_ws[i].other)
                    m->ws_shown_changed = true;
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

/* The seven-field T: every record names the workspace and the count, and
 * the records are numbered from 0. An empty workspace is one record with a
 * count of 0. The whole message is checked before any of it is applied. */
static int apply_tabs(struct hr_model *m, char **f, int nf, char **cursor)
{
    char *fields[HR_MAX_FIELDS];
    char ws[16];
    int n = 0, total = number(f[2]);
    if (total < 0 || total > HR_MAX_TABS)
        return 0;
    hr_copy(ws, f[1], sizeof ws);
    for (;;)
    {
        if (nf != 7 || strcmp(f[0], "T") || strcmp(f[1], ws) ||
            number(f[2]) != total)
            return 0;
        if (total == 0)
        {
            if (n || strcmp(f[4], "-"))
                return 0;
        }
        else
        {
            if (n >= total || number(f[3]) != n || !f[4][0])
                return 0;
            struct hr_tab *t = &new_tabs[n++];
            hr_copy(t->id, f[4], sizeof t->id);
            hr_copy(t->label, f[5], sizeof t->label);
            t->status = state(f[6]);
        }
        char *record = next_record(cursor);
        if (!record)
            break;
        nf = split(record, fields);
        f = fields;
    }
    if (n != total)
        return 0;
    memcpy(m->tabs, new_tabs, n * sizeof new_tabs[0]);
    hr_copy(m->tabs_ws, ws, sizeof m->tabs_ws);
    m->ntabs = n;
    m->tabs_valid = true;
    return 'B';
}

/* D: one record, the pane and its last lines */
static int apply_detail(struct hr_model *m, char **f, int nf)
{
    if (nf < 2 || nf > 2 + HR_DETAIL_LINES || !f[1][0])
        return 0;
    hr_copy(m->detail_pane, f[1], sizeof m->detail_pane);
    m->ndetail = nf - 2;
    for (int i = 0; i < m->ndetail; i++)
        hr_copy(m->detail[i], f[2 + i], sizeof m->detail[0]);
    m->detail_valid = true;
    return 'D';
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
    /* A result with no text is a success that says nothing */
    if (!strcmp(type, "R") && (n == 4 || (n == 3 && !strcmp(f[2], "ok"))))
    {
        hr_copy(m->result_req, f[1], sizeof m->result_req);
        m->result_ok = !strcmp(f[2], "ok");
        hr_copy(m->result_text, n == 4 ? f[3] : "", sizeof m->result_text);
        m->result_new = true;
        return 'R';
    }
    if (!strcmp(type, "U"))
        return apply_usage(m, f, n, &cursor);
    if (!strcmp(type, "V") && n == 2 &&
        (!strcmp(f[1], "talk") || !strcmp(f[1], "idle")))
    {
        m->voice = f[1][0];
        return 'V';
    }
    if (!strcmp(type, "T") && n == 3)
    {
        m->herdr_age = number(f[1]);
        m->herdr_ok = f[2][0] == '1';
        return 'T';
    }
    if (!strcmp(type, "T") && n == 7)
        return apply_tabs(m, f, n, &cursor);
    if (!strcmp(type, "D") && (!cursor || !*cursor))
        return apply_detail(m, f, n);
    return 0;
}

int hr_model_tab_index(const struct hr_model *m, const char *ws,
                       const char *id)
{
    if (!m->tabs_valid || strcmp(m->tabs_ws, ws))
        return -1;
    if (!id[0])
        return 0;
    for (int i = 0; i < m->ntabs; i++)
        if (!strcmp(m->tabs[i].id, id))
            return i;
    return -1;
}

static bool popped_has(const struct hr_popped *p, const char *id)
{
    for (int i = 0; i < HR_POPPED; i++)
        if (!strcmp(p->id[i], id))
            return true;
    return false;
}

void hr_popped_add(struct hr_popped *p, const char *id)
{
    if (!id[0] || popped_has(p, id))
        return;
    hr_copy(p->id[p->next], id, sizeof p->id[0]);
    p->next = (p->next + 1) % HR_POPPED;
}

const struct hr_question *hr_model_next_popup(const struct hr_model *m,
                                              const struct hr_popped *p)
{
    for (int i = 0; i < m->nquestions; i++)
        if (!popped_has(p, m->questions[i].id))
            return &m->questions[i];
    return NULL;
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

static bool hr_keys_opposite(const char *a, const char *b)
{
    return (!strcmp(a, "left") && !strcmp(b, "right")) ||
           (!strcmp(a, "right") && !strcmp(b, "left")) ||
           (!strcmp(a, "wleft") && !strcmp(b, "wright")) ||
           (!strcmp(a, "wright") && !strcmp(b, "wleft"));
}

bool hr_keys_add(struct hr_keys *k, const char *key)
{
    struct hr_key_run *last = k->nruns ? &k->runs[k->nruns - 1] : NULL;
    if (last && !strcmp(last->key, key))
    {
        if (last->count >= HR_KEY_COUNT)
            return false;
        last->count++;
        return true;
    }
    if (last && hr_keys_opposite(last->key, key))
    {
        if (--last->count == 0)
            k->nruns--;
        return true;
    }
    if (k->nruns == HR_KEY_RUNS)
        return false;
    last = &k->runs[k->nruns++];
    hr_copy(last->key, key, sizeof last->key);
    last->count = 1;
    return true;
}

bool hr_keys_take(struct hr_keys *k, struct hr_key_run *out)
{
    if (!k->nruns)
        return false;
    *out = k->runs[0];
    k->nruns--;
    memmove(&k->runs[0], &k->runs[1], k->nruns * sizeof k->runs[0]);
    return true;
}

const char *hr_wheel_step(struct hr_wheel *w, long now, int dir, long fast)
{
    bool is_fast = w->dir == dir && now - w->last <= fast;
    w->last = now;
    w->dir = dir;
    if (!is_fast)
    {
        w->fast = 0;
        return dir > 0 ? "right" : "left";
    }
    if (w->fast++ % HR_WORD_STEPS)
        return NULL;
    return dir > 0 ? "wright" : "wleft";
}
