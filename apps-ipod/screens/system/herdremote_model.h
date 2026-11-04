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
#define HR_USAGE_SVCS    4

struct hr_workspace {
    char id[16];
    char label[44];
    bool focused;
    unsigned char working, waiting, done, idle;     /* agents in every tab */
    /* The best state of tab 1's agents and of the other tabs' agents, as
     * hr_agent.status letters, '-' with none. From S; a daemon that sends no
     * S leaves tab1 as W's counts say and other '-'. */
    char tab1, other;
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

/* One service's row of U. A '-' field is -1. */
struct hr_service {
    char name[8];           /* codex, claude, grok, gemini */
    char state;             /* o ok, t tok, n none, e err */
    int h5_pct, h5_reset;   /* used percent; seconds until reset */
    int wk_pct, wk_reset;
    long long tokens;
    long long usd_milli;
    char note[44];
};

/* The last U. A U whose ok is 0 after one that had data leaves the data,
 * clears ok and keeps serial: the screen shows the old figures as stale. */
struct hr_usage {
    bool have;
    bool ok;
    unsigned int serial;    /* counts the U messages that replaced the data */
    int age;                /* seconds old when sent */
    struct hr_service svc[HR_USAGE_SVCS];
    long long tokens, usd_milli;
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
    struct hr_usage usage;
    /* The voice input's state from V: 't' recording, 'i' not, 0 unknown */
    char voice;
};

/* Apply one message. Returns its type letter ('W', 'S', 'A', 'Q', 'X', 'R',
 * 'T', 'U', 'V', 'H' for the hello), or 0 if it was not a HerdRemote v1
 * message, in which case the model is unchanged. */
int hr_model_apply(struct hr_model *m, const char *data, unsigned int length);

void hr_model_reset(struct hr_model *m);

/* The question an agent pane is asking, or NULL */
const struct hr_question *hr_model_question_for(const struct hr_model *m,
                                                const char *pane);

/* Copy src into dst of size n, cut on a UTF-8 character boundary */
void hr_copy(char *dst, const char *src, unsigned int n);

/* Key taps waiting to be sent while one k is in flight, as runs of one key
 * and a count, oldest first. A tap of the newest run's key adds to its
 * count; an arrow against the newest run's opposite arrow takes one off. */
#define HR_KEY_RUNS  4
#define HR_KEY_COUNT 32     /* taps in one run, the most one k carries */
struct hr_key_run {
    char key[8];
    int count;
};
struct hr_keys {
    struct hr_key_run runs[HR_KEY_RUNS];
    int nruns;
};

/* Add one tap of key. Returns false when it was dropped: the runs are full,
 * or the newest run of that key is at HR_KEY_COUNT. */
bool hr_keys_add(struct hr_keys *k, const char *key);

/* Take the oldest run into out. Returns false when there is none. */
bool hr_keys_take(struct hr_keys *k, struct hr_key_run *out);

#endif
