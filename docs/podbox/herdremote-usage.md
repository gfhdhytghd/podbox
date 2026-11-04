# HerdRemote subscription usage (computer side)

Verified on this machine on 2026-11-04. The implementation is
`tools/herdremote/hrusage.py`; the wire contract is in
[herdremote-protocol.md](herdremote-protocol.md). No model is invoked.

## Sources on this machine

| Service | Read-only source | Fields and interpretation |
| --- | --- | --- |
| Codex | `~/.codex/sessions/**/*.jsonl` | `turn_context.payload.model`; `token_usage_record.payload.usage` per response, deduplicated by response ID. Older logs use `event_msg.payload.type=token_count`, with differences of `info.total_token_usage`. Repeated totals are not usage. |
| Codex limits | Same logs | Most recent `rate_limits` for `limit_id=codex`. Inspect **both** `primary` and `secondary`: `window_minutes=300` means 5 hours; `10080` means weekly. This machine currently has a weekly **primary**, with a null secondary. `used_percent` is used percent. `resets_at` is Unix seconds; `resets_in_seconds` is relative to the event timestamp, not read time. `plan_type` is the note when no caveat takes precedence. |
| Claude Code | `~/.claude/projects/**/*.jsonl`, including `subagents/` | Assistant `message.model` and `message.usage`: `input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`. Cache-creation detail separates `ephemeral_5m_input_tokens` and `ephemeral_1h_input_tokens`. Streaming chunks sharing message ID/request ID merge component maxima; copied logs deduplicate globally. |
| Claude limits | Existing `~/.claude/.credentials.json`, `claudeAiOauth.accessToken`; **GET** `https://api.anthropic.com/api/oauth/usage` | `five_hour.utilization/resets_at`, `seven_day.utilization/resets_at`; reset is an ISO timestamp. The installed `/usr/bin/claude` wrapper starts `/opt/claude-code/bin/claude`. That binary contains this usage route and beta header `oauth-2025-04-20`, used by its usage UI. A real read-only GET succeeded during this task. Only the OAuth access token is used; no API key, refresh token, login, credential write, or refresh request. Expired login returns `err`. Redirects are refused. |
| Grok native | `/usr/bin/grok`; `~/.grok/sessions/<encoded-project>/<session>/usage.json` | `turns[].endedAt`, `turnNumber`, `modelUsage[model]`, with `inputTokens`, `cachedReadTokens`, `cacheCreationTokens`, `outputTokens`, `reasoningTokens`. Sum turn/model counters only. Do not sum the duplicate `session` totals or use `costUsdTicks` as API list price. `updates.jsonl` also has `params.update.usage`, but duplicates these totals and is not read. |
| Grok launch configs | `~/.codex/grok.config.toml`, `grok-build.config.toml` | Existing configs identify `grok-4.6` / `grok-build-0.1` and `lais_gateway`. They are not quota sources. The collector reads native Grok usage files; it does not call that gateway or attribute other Codex-wrapper sessions to Grok. |
| Gemini / Antigravity | `~/.local/bin/agy`; `~/.gemini/antigravity-cli/brain/<conversation>/.system_generated/logs/transcript.jsonl` | `PLANNER_RESPONSE` records have `created_at`, `step_index`, `input_tokens`, `cache_read_tokens`, `output_tokens`. Deduplicate by conversation and step. Never read `transcript_full.jsonl` or `chunks/`: they mirror records. The inspected canonical records have **no model ID** and no separate numeric thinking counter; unknown model is retained, with zero cost and an unpriced note. Do not infer a model from a current setting or from thinking text. This includes all models used through agy, under its Gemini service row. |
| Standard Gemini CLI | `~/.gemini/tmp/**/session-*.json` | `messages[].id/timestamp/model/tokens`. `tokens.input` includes `cached`; `output` and `thoughts` are disjoint. `total` is a cross-check, never added again; `tool` is not an extra billed token class. Snapshot JSON is read only when inode/size/mtime changes. |

Grok's installed usage documentation (`~/.grok/docs/user-guide/24-monitoring-usage.md`)
explains token telemetry. Its `04-slash-commands.md` describes `/usage` as an
interactive account-allowance modal, and `17-sessions.md` describes the local
`grok usage <session-id>` JSON envelope. The inspected native stores do not supply a 5h or
weekly subscription quota. Antigravity's canonical logs also do not supply those
limits. No verified read-only quota endpoint was found for those two installed
clients. Their limit/reset fields are `-`; token data returns `tok`, or `none`
when no events are in the window. Neither client is launched to obtain data.
Grok's OAuth file and Antigravity's OAuth file are not opened by the collectors.

## Windows, token classes, and accuracy

- A valid future weekly reset selects `[reset - 7 days, reset)`, excluding
  future records. Without a current weekly reset, use the trailing 7 days.
  An expired weekly sample falls back to that trailing window and says
  `weekly sample expired`; it never invents a future reset.
- Reset fields in the cache are absolute. Encoding subtracts the current
  computer time, floors at zero, and emits integer seconds. The player does
  not need a correct wall clock. The oldest service observation determines
  `U.age`. Codex uses the timestamp of its latest recorded limit sample, so
  an old local limit is visibly old even after the file is read again.
- Codex and native Grok input counters include cached input; their output
  counters include reasoning. Subtract the cached subset before pricing
  fresh input; do not add reasoning again. Codex cache writes are also split
  out of input. Claude's four top-level classes are disjoint; its thinking
  detail is already in output. Gemini CLI thoughts are added to output.
- Counts cover local records only, not activity on other computers, deleted
  logs, web UI usage, or missing telemetry. Local tokens and account-wide
  subscription percentages have different coverage. These totals are not an
  account invoice. Native Grok `usage.json` supplies completed turns; a turn
  still in progress can appear only after it ends.
- Modern Codex response records take precedence over legacy cumulative
  counters for the same session. Response IDs deduplicate copied/forked
  history. Legacy fallback deduplicates identical timestamp/model/counter
  checkpoints and uses counter deltas. A restored legacy log that starts
  mid-session cannot establish when its initial cumulative usage occurred.
- agy logs without a model cannot establish API-equivalent price. Records
  without a timestamp or usage are excluded, rather than guessed.

## Price table

`tools/herdremote/usage_prices.json` stores USD per million tokens, source URL,
verification date, and `confirmed` for every entry. Exact known IDs and dated
Claude snapshot suffixes are supported. Unknown or unconfirmed IDs count tokens
but cost zero; the note says `unpriced model; cost partial`. No family substitution.
The checked standard global list prices come from:

- [OpenAI API prices](https://developers.openai.com/api/docs/pricing) and
  [GPT-6 Sol](https://developers.openai.com/api/docs/models/gpt-6-sol).
- [Claude model and cache prices](https://platform.claude.com/docs/en/about-claude/pricing).
- [Gemini API prices](https://ai.google.dev/gemini-api/docs/pricing).
- [Grok 4.7 API prices](https://docs.x.ai/developers/models/grok-4.7).

The table explicitly marks `grok-4.6`, `grok-4.6-build`, and `grok-build-0.1`
unconfirmed; those installed model IDs could not be confirmed from the current
public official list. agy missing IDs are also unpriced. The table uses standard
API prices, not subscription credits, service-tier premiums, regional premiums,
tool fees, or cache storage rent. Gemini 3.1 Pro's published >200k input tier is
handled, as are OpenAI's >272k tier and Haiku 5.5's >100k tier. OpenAI's
[pricing Markdown](https://developers.openai.com/api/docs/pricing.md) exposes
both context tiers in the table. Codex cache-write TTL is absent from the
inspected counter: the short cache-write rate is the estimate. OpenAI's optional
extended rate is stored as `cache_write_12h`; Claude uses the explicit
1h split. Integer milli-USD is rounded once per service with Decimal half-up.

## Cache and failure isolation

`us` immediately sends the current `U` cache, then requests work if the last
request was at least 20 seconds ago. Collecting never runs on the serial/herdr
loop. It does not require herdr to be reachable. Four persistent spawned worker
processes keep counters and file offsets in memory, supervised by one daemon
thread. The supervisor kills any collector after 5 seconds, retains its previous
numeric fields/sample time, and changes its state/note to `err/collector timeout`.
Other services complete independently. Worker exceptions have fixed sanitized
notes; exceptions and credentials are never logged. Children do not inherit open
serial/herdr descriptors. Closing the cache kills/reaps children.

The first index scans eligible logs (mtime within 7 days), newest first, in
3-second slices. An unfinished index returns `err` with `indexing local logs;
partial totals` and continues at its saved byte offset on the next request.
It never advertises partial counts as a completed sample. Unchanged logs are
not parsed again. Appends resume at the last complete line; a partial final
line is retried. Truncation/inode replacement discards that file's old counters.
Expired/deleted files leave the index. JSON snapshots use inode/size/mtime.
There is no persistent credential or prompt cache, and no writes to source logs.

## Read-only diagnostic

```
python3 tools/herdremote/herdremote.py --usage-dump
python3 tools/herdremote/herdremote.py --usage-dump --usage-wait 90
```

The second form permits multiple 20-second refresh rounds for a large cold
index. Both print one final `U/P/N` frame and exit. Neither instantiates the
herdr daemon, probes serial ports, focuses panes, nor sends keys. Only the
existing Claude OAuth usage GET can use the network. Real output and validation
are recorded in `/home/wilf/data/PodJS/ipod/AGENTS-USAGE-BACKEND.md`.

Tests use `tests/samples/usage/` with scrubbed real-shaped schema fixtures.
All usage network calls in tests are mocked. The pty integration uses the
existing private fake herdr and fake iPod only.
