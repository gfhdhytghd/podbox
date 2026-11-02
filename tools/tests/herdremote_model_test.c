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
        printf("W %s %d %u %u %u %u %s\n", m.ws[i].id, m.ws[i].focused,
               m.ws[i].working, m.ws[i].waiting, m.ws[i].done, m.ws[i].idle,
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

    /* A bad record leaves the model as it was */
    assert(apply("W\t1\nw9\tf\t1") == 0);
    assert(m.nws == 2 && !strcmp(m.ws[1].id, "w2"));

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
        static const char alphabet[] = "WAQXRTHo?\t\n1f-汉 ";
        for (int j = 0; j < n; j++)
            junk[j] = alphabet[rand() % (sizeof alphabet - 1)];
        hr_model_apply(&m, junk, n);
    }
    puts("herdremote_model_test: all passed");
    return 0;
}
