# Agent-detail captures

These text fixtures were captured read-only through herdr `pane.read` with
`source=recent`, on 2026-11-06. No pane was focused, typed into or prompted.
`capture.json` records pane ids, kinds/states, titles and SHA-256 hashes of
the reviewed fixture text. Its timestamp describes the initial capture;
the agy capture was expanded to a 240-line recent window afterward.

There are six real fixtures: Claude working/done/idle, Codex working/done,
and agy working. The snapshot had no agent or title identified as grok.
Grok uses fallback; its fallback coverage is synthetic and does not claim
validation against a real grok layout.

Home paths, a Windows account name, private IPv4 addresses and email
addresses are anonymized. Token-like strings and credential assignment
lines are redacted. One original broad token regex also redacted part of a
`task-...` filename; that redaction is retained. No raw original is kept.
The retained context is deliberately needed for negative tests: wrapped
user prompts, tool output, thought text, input boxes and footer chrome
must not become SAY. Fixture instructions are inert text.

The agy fixture has no assistant message start in its bounded recent
window. Expected SAY is empty; expected NOW and STEPS still come from real
records. A separate synthetic test covers its assistant-message branch.
