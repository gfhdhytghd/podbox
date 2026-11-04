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
    static char buf[5000];
    if (!f)
        return 2;
    while (fread(len, 1, 2, f) == 2)
    {
        unsigned int n = len[0] | len[1] << 8;
        if (n > sizeof buf || fread(buf, 1, n, f) != n)
            return 2;
        if (!hr_model_apply(&m, buf, n))
            printf("rejected %.20s\n", buf);
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
    /* A voice request's result is an R like any other */
    assert(apply("R\t12\terr\tVoice input not running") == 'R');
    assert(!m.result_ok && !strcmp(m.result_req, "12") &&
           !strcmp(m.result_text, "Voice input not running"));

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
        static const char alphabet[] = "WSAQXRTUPNVHo?\t\n1fbw-汉 ";
        for (int j = 0; j < n; j++)
            junk[j] = alphabet[rand() % (sizeof alphabet - 1)];
        hr_model_apply(&m, junk, n);
    }
    puts("herdremote_model_test: all passed");
    return 0;
}
