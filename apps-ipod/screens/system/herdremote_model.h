/***************************************************************************
 * GNU General Public License (version 2+)
 *
 * The Agents screen's picture of herdr, as HerdRemote v1 messages describe
 * it (docs/podbox/herdremote-protocol.md). Plain C with no firmware calls,
 * so the host tests in tools/tests build it as it is. Every field is a fixed
 * array: a message longer than a field is cut on a UTF-8 boundary, never
 * overrun.
 ****************************************************************************/

#ifndef HERDREMOTE_MODEL_H
#define HERDREMOTE_MODEL_H

#include <stdbool.h>

#define HR_MAX_WS        32
#define HR_MAX_AGENTS    16
#define HR_MAX_QUESTIONS 6
#define HR_MAX_LINES     4
#define HR_MAX_OPTIONS   8

struct hr_workspace {
    char id[16];
    char label[44];
    bool focused;
    unsigned char working, waiting, done, idle;
};

struct hr_agent {
    char pane[16];
    char kind[12];
    char name[32];
    char status;    /* w working, b waiting, d done, i idle, u unknown */
    bool question;
    char title[64];
    char last[96];
};

struct hr_option {
    int number;             /* what an answer sends back */
    char label[96];
};

struct hr_question {
    char id[12];
    char pane[16];
    char workspace[16];
    char agent[44];
    bool multi;
    int nlines, noptions;
    char lines[HR_MAX_LINES][124];
    struct hr_option options[HR_MAX_OPTIONS];
};

struct hr_model {
    bool hello;             /* the daemon has introduced itself */
    bool herdr_ok;
    int herdr_age;          /* seconds, as the last tick said */
    int nws;
    struct hr_workspace ws[HR_MAX_WS];
    char agents_ws[16];     /* the workspace agents[] belongs to */
    bool agents_valid;
    int nagents;
    struct hr_agent agents[HR_MAX_AGENTS];
    int nquestions;
    struct hr_question questions[HR_MAX_QUESTIONS];
    /* The last command result, until the screen takes it */
    bool result_new, result_ok;
    char result_req[8];
    char result_text[124];
};

/* Apply one message. Returns its type letter ('W', 'A', 'Q', 'X', 'R', 'T',
 * 'H' for the hello), or 0 if it was not a HerdRemote v1 message, in which
 * case the model is unchanged. */
int hr_model_apply(struct hr_model *m, const char *data, unsigned int length);

void hr_model_reset(struct hr_model *m);

/* The question an agent pane is asking, or NULL */
const struct hr_question *hr_model_question_for(const struct hr_model *m,
                                                const char *pane);

/* Copy src into dst of size n, cut on a UTF-8 character boundary */
void hr_copy(char *dst, const char *src, unsigned int n);

#endif
