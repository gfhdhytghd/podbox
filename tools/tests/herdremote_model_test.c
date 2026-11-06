/* Host test for apps-ipod/screens/system/herdremote_model.c.
 *
 *   cc -std=gnu11 -Wall -Wextra -Werror -fsanitize=address,undefined \
 *      -Iapps-ipod/screens/system tools/tests/herdremote_model_test.c \
 *      apps-ipod/screens/system/herdremote_model.c -o /tmp/hrmodel &&
 *   /tmp/hrmodel
 *
 * With a file argument it instead applies each message in the file (a u16
 * little-endian length before each) and prints the model, for the Python
 * test that checks the two ends of the protocol agree. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "herdremote_model.h"

static struct hr_model m;

static int apply(const char *s)
{
    return hr_model_apply(&m, s, strlen(s));
}

static void dump(void)
{
    printf("hello=%d herdr_ok=%d age=%d\n", m.hello, m.herdr_ok, m.herdr_age);
    for (int i = 0; i < m.nws; i++)
        printf("W %s %d %u %u %u %u %c %c %s\n", m.ws[i].id,
               m.ws[i].focused, m.ws[i].working, m.ws[i].waiting,
               m.ws[i].done, m.ws[i].idle, m.ws[i].tab1, m.ws[i].other,
               m.ws[i].label);
    for (int i = 0; i < m.nagents; i++)
        printf("A %s %s %s %c %d %s|%s|%s\n", m.agents_ws, m.agents[i].pane,
               m.agents[i].kind, m.agents[i].status, m.agents[i].question,
               m.agents[i].name, m.agents[i].title, m.agents[i].last);
    for (int i = 0; i < m.nquestions; i++)
    {
        const struct hr_question *q = &m.questions[i];
        printf("Q %s %s %s %d %s\n", q->id, q->pane, q->workspace, q->multi,
               q->agent);
        for (int j = 0; j < q->nlines; j++)
            printf("? %s\n", q->lines[j]);
        for (int j = 0; j < q->noptions; j++)
            printf("o %d %s\n", q->options[j].number, q->options[j].label);
    }
    if (m.tabs_valid)
        for (int i = 0; i < m.ntabs; i++)
            printf("B %s %d %s %s %c\n", m.tabs_ws, i, m.tabs[i].id,
                   m.tabs[i].label, m.tabs[i].status);
    if (m.detail_valid)
        printf("D %s %d %s|%s|%s|%s|%s|%s|%s|%d\n", m.detail_pane,
               m.detail.age, m.detail.task, m.detail.now, m.detail.say,
               m.detail.step[0], m.detail.step[1], m.detail.step[2],
               m.detail.input, m.detail.cursor);
    if (m.result_new)
        printf("R %s %d %s\n", m.result_req, m.result_ok, m.result_text);
    if (m.usage.have)
    {
        const struct hr_usage *u = &m.usage;
        printf("U %d %d %u\n", u->age, u->ok, u->serial);
        for (int i = 0; i < HR_USAGE_SVCS; i++)
        {
            const struct hr_service *s = &u->svc[i];
            printf("P %s %c %d %d %d %d %lld %lld %s\n", s->name, s->state,
                   s->h5_pct, s->h5_reset, s->wk_pct, s->wk_reset,
                   s->tokens, s->usd_milli, s->note);
        }
        printf("N %lld %lld\n", u->tokens, u->usd_milli);
    }
    if (m.voice)
        printf("V %c\n", m.voice);
}

static int replay(const char *path)
{
    FILE *f = fopen(path, "rb");
    unsigned char len[2];
    static char buf[17000];
    if (!f)
        return 2;
    while (fread(len, 1, 2, f) == 2)
    {
        unsigned int n = len[0] | len[1] << 8;
        if (n > sizeof buf || fread(buf, 1, n, f) != n)
            return 2;
        if (!hr_model_apply(&m, buf, n))
        {
            /* Cut on a character boundary: the Python test decodes this */
            char head[24];
            buf[n] = '\0';
            hr_copy(head, buf, sizeof head);
            printf("rejected %s\n", head);
        }
    }
    fclose(f);
    dump();
    return 0;
}

int main(int argc, char **argv)
{
    if (argc == 2)
        return replay(argv[1]);

    assert(apply("HR\t1\therdremote-1") == 'H' && m.hello);
    assert(apply("HR\t2\tfuture") == 0);

    assert(apply("W\t2\nw1\tf\t1\t2\t3\t4\t项目\n"
                 "w2\t-\t0\t0\t0\t1\tother") == 'W');
    assert(m.nws == 2 && m.ws[0].focused && m.ws[0].waiting == 2);
    assert(!strcmp(m.ws[0].label, "项目") && !m.ws[1].focused);

    /* Without S, tab 1's state is what the counts say */
    assert(m.ws[0].tab1 == 'b' && m.ws[0].other == '-');
    assert(m.ws[1].tab1 == 'i');

    /* A bad record leaves the model as it was */
    assert(apply("W\t1\nw9\tf\t1") == 0);
    assert(m.nws == 2 && !strcmp(m.ws[1].id, "w2"));

    /* S by id; an unknown id and a bad letter are left out */
    assert(apply("S\t3\nw2\t-\tw\nw9\tb\tb\nw1\tz\tbb") == 'S');
    assert(m.ws[1].tab1 == '-' && m.ws[1].other == 'w');
    assert(m.ws[0].tab1 == '-' && m.ws[0].other == '-');
    assert(apply("S\t1\nw1\tw") == 0 && m.ws[0].tab1 == '-');
    assert(apply("S\t1\nw1\tw\tb") == 'S');
    assert(m.ws[0].tab1 == 'w' && m.ws[0].other == 'b');

    /* A W that only moves the focus, and the S repeated after it, change
     * nothing the list shows; the states S gave stay */
    assert(m.ws_shown_changed);
    assert(apply("W\t2\nw1\t-\t1\t2\t3\t4\t项目\n"
                 "w2\tf\t0\t0\t0\t1\tother") == 'W');
    assert(!m.ws_shown_changed && m.ws[1].focused && !m.ws[0].focused);
    assert(m.ws[0].tab1 == 'w' && m.ws[0].other == 'b');
    assert(apply("S\t2\nw1\tw\tb\nw2\t-\tw") == 'S' && !m.ws_shown_changed);
    assert(apply("S\t1\nw2\td\tw") == 'S' && m.ws_shown_changed);
    /* Changed counts, a label, the order or the set are shown */
    assert(apply("W\t2\nw1\t-\t2\t1\t3\t4\t项目\n"
                 "w2\tf\t0\t0\t0\t1\tother") == 'W');
    assert(m.ws_shown_changed && m.ws[0].tab1 == 'b' && m.ws[0].other == '-');
    assert(apply("W\t2\nw1\t-\t2\t1\t3\t4\t项目\n"
                 "w2\tf\t0\t0\t0\t1\tother2") == 'W' && m.ws_shown_changed);
    assert(apply("W\t2\nw2\tf\t0\t0\t0\t1\tother2\n"
                 "w1\t-\t2\t1\t3\t4\t项目") == 'W' && m.ws_shown_changed);
    assert(apply("W\t1\nw2\tf\t0\t0\t0\t1\tother2") == 'W' &&
           m.ws_shown_changed && m.nws == 1);
    assert(apply("W\t2\nw1\tf\t1\t2\t3\t4\t项目\n"
                 "w2\t-\t0\t0\t0\t1\tother") == 'W' && m.ws_shown_changed);
    assert(apply("S\t1\nw1\tw\tb") == 'S');

    assert(apply("A\tw1\t1\nw1:p1\tclaude\tb\t1\tasker\t标题\tlast line") == 'A');
    assert(m.agents_valid && m.nagents == 1 && m.agents[0].status == 'b');
    assert(m.agents[0].question && !strcmp(m.agents[0].name, "asker"));
    assert(!strcmp(m.agents_ws, "w1"));

    assert(apply("Q\tabcd1234\tw1:p1\tw1\t-\t项目 · claude\n?\tWhich?\n"
                 "o\t1\tRed\no\t3\tBlue") == 'Q');
    assert(m.nquestions == 1 && m.questions[0].options[1].number == 3);
    assert(hr_model_question_for(&m, "w1:p1") == &m.questions[0]);
    assert(!hr_model_question_for(&m, "w1:p2"));
    /* The same id again replaces rather than adds */
    assert(apply("Q\tabcd1234\tw1:p1\tw1\tm\tx\no\t1\t[x] Red") == 'Q');
    assert(m.nquestions == 1 && m.questions[0].multi);
    assert(apply("Q\tnooption\tw1:p1\tw1\t-\tx\n?\tq") == 0);
    assert(apply("X\tabcd1234") == 'X' && m.nquestions == 0);

    /* More questions than fit: the oldest goes */
    for (int i = 0; i < HR_MAX_QUESTIONS + 2; i++)
    {
        char msg[64];
        snprintf(msg, sizeof msg, "Q\tq%d\tp%d\tw\t-\ta\no\t1\tYes", i, i);
        assert(apply(msg) == 'Q');
    }
    assert(m.nquestions == HR_MAX_QUESTIONS);
    assert(!strcmp(m.questions[0].id, "q2"));

    assert(apply("R\t7\terr\tThe question has gone") == 'R');
    assert(m.result_new && !m.result_ok && !strcmp(m.result_req, "7"));
    assert(apply("T\t12\t0") == 'T' && m.herdr_age == 12 && !m.herdr_ok);

    /* U: one P per service, by name; '-' is -1 */
    assert(apply("U\t12\t1\n"
                 "P\tcodex\tok\t42\t7980\t61\t273600\t12300000\t41200\tPro\n"
                 "P\tclaude\ttok\t-\t-\t-\t-\t900\t5\t\n"
                 "P\tgrok\tnone\t-\t-\t-\t-\t0\t0\tnot used\n"
                 "P\tgemini\terr\t-\t-\t-\t-\t0\t0\ttimeout\n"
                 "N\t12300900\t41205") == 'U');
    const struct hr_usage *u = &m.usage;
    assert(u->have && u->ok && u->age == 12 && u->serial == 1);
    assert(u->svc[0].state == 'o' && u->svc[0].h5_pct == 42);
    assert(u->svc[0].h5_reset == 7980 && u->svc[0].wk_reset == 273600);
    assert(u->svc[0].tokens == 12300000 && u->svc[0].usd_milli == 41200);
    assert(!strcmp(u->svc[0].note, "Pro"));
    assert(u->svc[1].state == 't' && u->svc[1].h5_pct == -1);
    assert(u->svc[2].state == 'n' && u->svc[3].state == 'e');
    assert(!strcmp(u->svc[3].note, "timeout"));
    assert(u->tokens == 12300900 && u->usd_milli == 41205);
    /* Tokens past 32 bits */
    assert(apply("U\t0\t1\nP\tclaude\tok\t1\t2\t3\t4\t9876543210\t1\t\n"
                 "P\tsomeone\tok\t1\t2\t3\t4\t5\t6\t\n"
                 "N\t9876543210\t1") == 'U');
    assert(u->svc[1].tokens == 9876543210LL && u->serial == 2);
    assert(u->svc[0].state == 'n' && u->svc[0].tokens == -1);
    /* Every collector failed: the figures stay, marked not ok */
    assert(apply("U\t3\t0\nP\tclaude\terr\t-\t-\t-\t-\t0\t0\tx\n"
                 "N\t0\t0") == 'U');
    assert(!u->ok && u->serial == 2 && u->svc[1].tokens == 9876543210LL);
    /* Bad records leave the model as it was */
    assert(apply("U\t1\t1\nP\tcodex\tok\t101\t-\t-\t-\t0\t0\t\nN\t0\t0")
           == 0);
    assert(apply("U\t1\t1\nP\tcodex\tbad\t-\t-\t-\t-\t0\t0\t\nN\t0\t0")
           == 0);
    assert(apply("U\t1\t1\nP\tcodex\tok\t1\t-\t-\t-\t0\t0\t") == 0);
    assert(apply("U\t1\t1\nP\tcodex\tok\t1\t-\t-\t-\t1x\t0\t\nN\t0\t0")
           == 0);
    assert(apply("U\t-1\t1\nN\t0\t0") == 0);
    assert(u->serial == 2 && !u->ok);
    assert(apply("U\t1\t1\nN\t0\t0") == 'U' && u->ok && u->serial == 3);
    /* Past 10^18 a count is held there */
    assert(apply("U\t1\t1\nN\t123456789012345678901234\t0") == 'U');
    assert(u->tokens == 999999999999999999LL);
    /* The largest counts the computer sends; seconds are held at three
     * years */
    assert(apply("U\t999999999999999999\t1\n"
                 "P\tcodex\tok\t100\t999999999999999999\t0\t0\t"
                 "999999999999999999\t999999999999999999\t\n"
                 "N\t999999999999999999\t999999999999999999") == 'U');
    assert(u->age == 99999999 && u->svc[0].h5_reset == 99999999);
    assert(u->svc[0].tokens == 999999999999999999LL);

    /* V: the voice input's state; anything else leaves it */
    assert(m.voice == 0);
    assert(apply("V\ttalk") == 'V' && m.voice == 't');
    assert(apply("V\tidle") == 'V' && m.voice == 'i');
    assert(apply("V\tbusy") == 0 && m.voice == 'i');
    assert(apply("V") == 0 && apply("V\ttalk\tx") == 0 && m.voice == 'i');
    /* k's result: a bare ok, or err with its reason; a bare err is no R */
    assert(apply("R\t7\tok") == 'R' && m.result_ok &&
           !strcmp(m.result_req, "7") && !m.result_text[0]);
    assert(apply("R\t8\tok\t") == 'R' && m.result_ok && !m.result_text[0]);
    assert(apply("R\t9\terr") == 0 && !strcmp(m.result_req, "8"));
    assert(apply("R\t9\terr\tNo key tool") == 'R' && !m.result_ok &&
           !strcmp(m.result_text, "No key tool"));
    /* A voice request's result is an R like any other */
    assert(apply("R\t12\terr\tVoice input not running") == 'R');
    assert(!m.result_ok && !strcmp(m.result_req, "12") &&
           !strcmp(m.result_text, "Voice input not running"));

    /* Key runs: same key counts up, an opposite arrow takes one off, a
     * different key starts a run, and both caps drop the tap */
    struct hr_keys keys = { .nruns = 0 };
    struct hr_key_run run;
    assert(!hr_keys_take(&keys, &run));
    assert(hr_keys_add(&keys, "right") && hr_keys_add(&keys, "right") &&
           hr_keys_add(&keys, "right") && hr_keys_add(&keys, "left"));
    assert(keys.nruns == 1 && keys.runs[0].count == 2);
    assert(hr_keys_add(&keys, "left") && hr_keys_add(&keys, "left"));
    assert(keys.nruns == 0);
    assert(hr_keys_add(&keys, "left") && hr_keys_add(&keys, "bs") &&
           hr_keys_add(&keys, "enter") && hr_keys_add(&keys, "left"));
    assert(keys.nruns == 4 && !hr_keys_add(&keys, "bs"));
    assert(hr_keys_add(&keys, "right") && keys.nruns == 3);
    assert(hr_keys_take(&keys, &run) && !strcmp(run.key, "left") &&
           run.count == 1);
    assert(hr_keys_take(&keys, &run) && !strcmp(run.key, "bs"));
    assert(hr_keys_take(&keys, &run) && !strcmp(run.key, "enter"));
    assert(!hr_keys_take(&keys, &run));
    for (int i = 0; i < HR_KEY_COUNT; i++)
        assert(hr_keys_add(&keys, "bs"));
    assert(!hr_keys_add(&keys, "bs") && keys.runs[0].count == HR_KEY_COUNT);
    /* bs and enter never cancel each other */
    assert(hr_keys_add(&keys, "enter") && keys.nruns == 2);

    /* The wheel: a slow step is a character; fast steps in one direction
     * are words, the first and then every HR_WORD_STEPS-th */
    struct hr_wheel wh = { .dir = 0 };
    assert(!strcmp(hr_wheel_step(&wh, 100, 1, 5), "right"));
    assert(!strcmp(hr_wheel_step(&wh, 120, 1, 5), "right"));
    const char *flick[9];
    for (int i = 0; i < 9; i++)
        flick[i] = hr_wheel_step(&wh, 123 + i * 2, 1, 5);
    for (int i = 0; i < 9; i++)
        assert(i % HR_WORD_STEPS ? !flick[i] : !strcmp(flick[i], "wright"));
    /* Turning back is slow however quick, and so is a pause */
    assert(!strcmp(hr_wheel_step(&wh, 141, -1, 5), "left"));
    assert(!strcmp(hr_wheel_step(&wh, 143, -1, 5), "wleft"));
    assert(!strcmp(hr_wheel_step(&wh, 149, -1, 5), "left"));
    assert(!strcmp(hr_wheel_step(&wh, 154, -1, 5), "wleft"));
    /* Word runs: separate from character runs, opposite words cancel */
    struct hr_keys wk = { .nruns = 0 };
    assert(hr_keys_add(&wk, "right") && hr_keys_add(&wk, "wright") &&
           hr_keys_add(&wk, "wright") && hr_keys_add(&wk, "wleft"));
    assert(wk.nruns == 2 && !strcmp(wk.runs[1].key, "wright") &&
           wk.runs[1].count == 1);
    assert(hr_keys_add(&wk, "wleft") && wk.nruns == 1);
    assert(hr_keys_add(&wk, "left") && wk.nruns == 0);

    /* The seven-field T lists the subscribed workspace's tabs in herdr's
     * order; the three-field T is still the tick */
    assert(!m.tabs_valid);
    assert(apply("T\tw1\t3\t0\tw1:t1\tbuild\tw\n"
                 "T\tw1\t3\t1\tw1:t2\t测试\tb\n"
                 "T\tw1\t3\t2\tw1:t3\t\t-") == 'B');
    assert(m.tabs_valid && m.ntabs == 3 && !strcmp(m.tabs_ws, "w1"));
    assert(!strcmp(m.tabs[1].id, "w1:t2") && !strcmp(m.tabs[1].label, "测试"));
    assert(m.tabs[0].status == 'w' && m.tabs[1].status == 'b' &&
           m.tabs[2].status == '-' && !m.tabs[2].label[0]);
    assert(hr_model_tab_index(&m, "w1", "") == 0);
    assert(hr_model_tab_index(&m, "w1", "w1:t3") == 2);
    assert(hr_model_tab_index(&m, "w1", "w1:t9") == -1);
    assert(hr_model_tab_index(&m, "w2", "") == -1);
    assert(apply("T\t5\t1") == 'T' && m.herdr_age == 5 && m.ntabs == 3);
    /* Bad lists leave the tabs as they were: a count that does not match,
     * an index out of step, a record for another workspace, a letter that
     * is not a state */
    assert(apply("T\tw1\t2\t0\tw1:t1\ta\tw") == 0);
    assert(apply("T\tw1\t2\t0\tw1:t1\ta\tw\nT\tw1\t2\t0\tw1:t2\tb\tw")
           == 0);
    assert(apply("T\tw1\t2\t0\tw1:t1\ta\tw\nT\tw2\t2\t1\tw1:t2\tb\tw")
           == 0);
    assert(apply("T\tw1\t1\t0\tw1:t1\ta\tw\nT\tw1\t1\t1\tw1:t2\tb\tw")
           == 0);
    assert(apply("T\tw1\t1\t0\tw1:t1\ta\tz") == 'B' &&
           m.tabs[0].status == '-' && m.ntabs == 1);
    /* An empty or removed workspace: one record, no tabs */
    assert(apply("T\tw2\t0\t0\t-\t\t-") == 'B');
    assert(m.tabs_valid && m.ntabs == 0 && !strcmp(m.tabs_ws, "w2"));
    assert(hr_model_tab_index(&m, "w2", "") == 0);
    assert(apply("T\tw2\t0\t0\tw2:t1\t\t-") == 0 && m.ntabs == 0);

    /* D: eleven fields, the pane, its age, seven texts, any of them empty,
     * and the cursor */
    assert(!m.detail_valid);
    assert(apply("D\tw1:p1\t185\tTASK-HERDREMOTE.md execution\t"
                 "Compiling… 1m 12s\t进行中：跑栈测试。\t\t"
                 "Ran 7 shell commands\tRead 3 files\t\t0") == 'D');
    assert(m.detail_valid && !strcmp(m.detail_pane, "w1:p1"));
    assert(m.detail.age == 185);
    assert(!strcmp(m.detail.task, "TASK-HERDREMOTE.md execution"));
    assert(!strcmp(m.detail.now, "Compiling… 1m 12s"));
    assert(!strcmp(m.detail.say, "进行中：跑栈测试。"));
    assert(!m.detail.step[0][0] &&
           !strcmp(m.detail.step[1], "Ran 7 shell commands") &&
           !strcmp(m.detail.step[2], "Read 3 files"));
    assert(!m.detail.input[0] && m.detail.cursor == 0);
    /* An empty pane: every text empty, the age unknown */
    assert(apply("D\tw1:p2\t-1\t\t\t\t\t\t\t\t0") == 'D' &&
           !strcmp(m.detail_pane, "w1:p2") && m.detail.age == -1 &&
           !m.detail.task[0] && !m.detail.now[0] && !m.detail.say[0] &&
           !m.detail.step[2][0] && !m.detail.input[0]);
    /* Fallback, and the largest age */
    assert(apply("D\tw1:p1\t-2\t\t\tlast lines\t\t\t\t\t0") == 'D' &&
           m.detail.age == -2 && !strcmp(m.detail.say, "last lines"));
    assert(apply("D\tw1:p1\t2147483647\tt\tn\ts\t1\t2\t3\t\t0") == 'D' &&
           m.detail.age == 2147483647);
    /* The input: its escapes undone, the cursor in characters and held at
     * its end */
    assert(apply("D\tw1:p1\t0\tt\tn\ts\t\t\t\t"
                 "第一行\\n2nd \\\\ line\\x\t4") == 'D');
    assert(!strcmp(m.detail.input, "第一行\n2nd \\ line\\x"));
    assert(m.detail.cursor == 4);
    assert(apply("D\tw1:p1\t0\tt\tn\ts\t\t\t\t汉字\t99") == 'D' &&
           m.detail.cursor == 2);
    assert(apply("D\tw1:p1\t0\tt\tn\ts\t\t\t\t\t5") == 'D' &&
           m.detail.cursor == 0);
    /* Not eleven fields, a bad age or cursor, or a second record leave the
     * last D */
    assert(apply("D\tw1:p1\t0\tt\tn\ts\t1\t2\t3") == 0);
    assert(apply("D\tw1:p1\t0\tt\tn\ts\t1\t2\t3\t\t0\t") == 0);
    assert(apply("D\tw1:p1\tfirst\t\tthird") == 0);
    assert(apply("D\tw1:p1\t\tt\tn\ts\t1\t2\t3\t\t0") == 0);
    assert(apply("D\tw1:p1\t-3\tt\tn\ts\t1\t2\t3\t\t0") == 0);
    assert(apply("D\tw1:p1\t-\tt\tn\ts\t1\t2\t3\t\t0") == 0);
    assert(apply("D\tw1:p1\t+1\tt\tn\ts\t1\t2\t3\t\t0") == 0);
    assert(apply("D\tw1:p1\t1x\tt\tn\ts\t1\t2\t3\t\t0") == 0);
    assert(apply("D\tw1:p1\t2147483648\tt\tn\ts\t1\t2\t3\t\t0") == 0);
    assert(apply("D\tw1:p1\t0\tt\tn\ts\t1\t2\t3\t\t") == 0);
    assert(apply("D\tw1:p1\t0\tt\tn\ts\t1\t2\t3\t\t-1") == 0);
    assert(apply("D\tw1:p1\t0\tt\tn\ts\t1\t2\t3\t\t1a") == 0);
    assert(apply("D\t\t0\tt\tn\ts\t1\t2\t3\t\t0") == 0);
    assert(apply("D") == 0 && apply("D\t") == 0);
    assert(apply("D\tw1:p1\t0\tt\tn\ts\t1\t2\t3\t\t0\n"
                 "D\tw1:p1\t0\tt\tn\ts\t1\t2\t3\t\t0") == 0);
    assert(m.detail.age == 0 && !m.detail.input[0] && m.detail.cursor == 0);
    /* Texts are cut on a character boundary at the field's size */
    {
        static char big[8 + HR_DETAIL_SAY + 400];
        int n = snprintf(big, sizeof big, "D\tw1:p1\t0\t\t\t");
        for (int i = 0; i < (HR_DETAIL_SAY + 200) / 3; i++)
            n += snprintf(big + n, sizeof big - n, "汉");
        snprintf(big + n, sizeof big - n, "\t\t\t\t\t0");
        assert(apply(big) == 'D');
        assert(strlen(m.detail.say) == (HR_DETAIL_SAY - 1) / 3 * 3);
    }

    /* An input at its budget of 12000 bytes is kept whole */
    {
        static char big[12100];
        int n = snprintf(big, sizeof big, "D\tw1:p1\t0\tt\tn\ts\t\t\t\t");
        for (int i = 0; i < 4000; i++)
            n += snprintf(big + n, sizeof big - n, "汉");
        snprintf(big + n, sizeof big - n, "\t4000");
        assert(apply(big) == 'D' && strlen(m.detail.input) == 12000 &&
               m.detail.cursor == 4000);
    }

    /* Character offsets into UTF-8 */
    assert(hr_utf8_offset("", 0) == 0 && hr_utf8_offset("", 3) == 0);
    assert(hr_utf8_offset("ab汉c", 0) == 0 && hr_utf8_offset("ab汉c", 2) == 2);
    assert(hr_utf8_offset("ab汉c", 3) == 5 && hr_utf8_offset("ab汉c", 4) == 6);
    assert(hr_utf8_offset("ab汉c", 9) == 6);

    /* The input window follows the cursor's line, and only as far as that */
    assert(hr_input_window(0, 3, 5, 0) == 0);
    assert(hr_input_window(2, 5, 5, 0) == 0);
    assert(hr_input_window(4, 8, 5, 0) == 0);
    assert(hr_input_window(5, 8, 5, 0) == 1);
    assert(hr_input_window(7, 8, 5, 1) == 3);
    assert(hr_input_window(7, 8, 5, 3) == 3);
    assert(hr_input_window(3, 8, 5, 3) == 3);
    assert(hr_input_window(2, 8, 5, 3) == 2);
    assert(hr_input_window(0, 8, 5, 3) == 0);
    /* A shrunken input clamps the top; a shorter one than the window
     * starts at its first line */
    assert(hr_input_window(5, 6, 5, 3) == 1);
    assert(hr_input_window(1, 4, 5, 3) == 0);
    assert(hr_input_window(9, 10, 5, -2) == 5);

    /* Questions pop up once each, oldest first, until dismissed or gone */
    struct hr_popped popped;
    memset(&popped, 0, sizeof popped);
    m.nquestions = 0;
    assert(!hr_model_next_popup(&m, &popped));
    assert(apply("Q\tqa\tw1:p1\tw1\t-\ta\no\t1\tYes") == 'Q');
    assert(apply("Q\tqb\tw1:p2\tw1\t-\tb\no\t1\tYes") == 'Q');
    assert(hr_model_next_popup(&m, &popped) == &m.questions[0]);
    hr_popped_add(&popped, "qa");
    hr_popped_add(&popped, "qa");
    assert(hr_model_next_popup(&m, &popped) == &m.questions[1]);
    hr_popped_add(&popped, "qb");
    assert(!hr_model_next_popup(&m, &popped));
    /* A new qid for the same pane is a new question */
    assert(apply("Q\tqc\tw1:p1\tw1\tm\ta\no\t1\t[x] Yes") == 'Q');
    assert(!strcmp(hr_model_next_popup(&m, &popped)->id, "qc"));
    hr_popped_add(&popped, "qc");
    for (int i = 0; i < HR_POPPED; i++)
    {
        char id[12];
        snprintf(id, sizeof id, "x%d", i);
        hr_popped_add(&popped, id);
    }
    /* Forgotten once HR_POPPED newer ids have come */
    assert(!strcmp(hr_model_next_popup(&m, &popped)->id, "qa"));
    assert(apply("X\tqa") == 'X' && apply("X\tqb") == 'X' &&
           apply("X\tqc") == 'X');

    /* Cut on a character boundary: 汉 is three bytes */
    char small[5];
    hr_copy(small, "汉字", sizeof small);
    assert(!strcmp(small, "汉"));

    /* Rejects: unknown type, embedded NUL, oversize, empty */
    assert(apply("Z\t1") == 0);
    assert(hr_model_apply(&m, "W\0\t1", 4) == 0);
    static char big[5000];
    memset(big, 'a', sizeof big);
    assert(hr_model_apply(&m, big, sizeof big) == 0);
    assert(hr_model_apply(&m, "", 0) == 0);

    /* Junk never crashes and never overruns (run under ASan) */
    srand(1);
    for (int i = 0; i < 20000; i++)
    {
        char junk[256];
        int n = 1 + rand() % sizeof junk;
        static const char alphabet[] = "WSAQXRTUPNVHDo?\t\n1fbw-汉 ";
        for (int j = 0; j < n; j++)
            junk[j] = alphabet[rand() % (sizeof alphabet - 1)];
        hr_model_apply(&m, junk, n);
    }
    puts("herdremote_model_test: all passed");
    return 0;
}
