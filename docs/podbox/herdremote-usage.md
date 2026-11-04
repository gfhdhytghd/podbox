# HerdRemote subscription usage (computer side)

Updated for Amendments 1–5 on 2026-11-04. The implementation is
`tools/herdremote/hrusage.py`; the wire shape remains `us` → `U/P/N` in
[herdremote-protocol.md](herdremote-protocol.md). No model/prompt request is made.

## Omarchy reference and chosen interfaces

The supplied reference is **origin/quattro**, commit `cc9976409`, from
`/home/wilf/data/PodJS/ipod/evidence/omarchy-usage-ref/`. The four
`bin/omarchy-agent-usage-{codex,claude,grok,agy}` scripts were read, together
with `docs/agent-usage-collectors.md` and `shell/plugins/agents/collectors.py`.
`Main.qml:241` builds `omarchy-agent-usage-update` commands; `refreshLimits()`
uses `--limits-only` and reloads the resulting records. The panel consumes their JSON; the shared module supplies scan/limit caching,
not another live usage API. HerdRemote adopts these quota sources without a
runtime dependency on Omarchy or its other providers.

| Provider | Omarchy interface | HerdRemote choice and difference |
| --- | --- | --- |
| Codex | `codex app-server` stdio JSON-RPC: `initialize`, `initialized`, `account/rateLimits/read`; local rollouts for token classes | Same RPC. Try the existing `~/.codex/app-server-control/app-server-control.sock` through `codex app-server proxy --sock …` first. On this machine the control path closes without an RPC reply, so use a private, short-lived read-only stdio process. Total RPC deadline 4.1s within the collector's 5s hard limit. No thread/turn creation, login, reset-credit consumption, or model call. |
| Claude Code | GET `https://api.anthropic.com/api/oauth/usage`, beta `oauth-2025-04-20`, existing `claudeAiOauth` login | Same endpoint and beta header. The CLI's JSON/SDK prompt output describes a model turn, not a read-only account quota command; no prompt is sent to obtain quotas. |
| Grok | GET `https://cli-chat-proxy.grok.com/v1/billing?format=credits`; `/v1/billing` for a monthly unified quota; `~/.grok/auth.json` OAuth login; plan from `settings_cache.json` | Same weekly credits endpoint. The current machine has no usable settings-cache plan, so use the installed CLI's read-only `/v1/user?include=subscription` to get `subscriptionTier`. Never put a monthly meter in the weekly field. No 5h meter. |
| agy / Antigravity | Secret Service lookup `service=gemini username=antigravity`, then `antigravity-oauth-token` file fallback. POST `https://daily-cloudcode-pa.googleapis.com/v1internal:loadCodeAssist` and `:retrieveUserQuotaSummary` | Same quota source, with the installed view's client metadata and full versioned User-Agent (see below). The first POST sends `{"metadata":{"ideType":"ANTIGRAVITY"}}`. The second sends the returned `cloudaicompanionProject` string/id as `project`. Only quota/plan reads; neither POST invokes a model. Omit Omarchy's `omp usage` refresh fallback because this task forbids credential rotation. |

The installed Codex schema was generated read-only with:

```
codex app-server generate-json-schema --out /tmp/herdremote-codex-schema
```

`GetAccountRateLimitsResponse` contains `rateLimitsByLimitId` and `rateLimits`,
with `usedPercent`, `windowDurationMins`, `resetsAt`, and `planType`. Read
**both** windows by duration: 300 minutes is 5h, 10080 is weekly; the weekly
window on this account is currently **primary**, with a null secondary.
The generated `GetAccountTokenUsageResponse`, exposed as `account/usage/read`,
has account-wide daily token buckets/lifetime summary and optional per-thread
breakdowns. It does not provide exact reset-aligned account totals by model
and token class. Those daily totals cannot supply this protocol's window and
API-equivalent price, so the local incremental index remains for token classes.
See [official app-server documentation](https://learn.chatgpt.com/docs/app-server).

OAuth credentials are read only. Expired Codex file JWTs, Claude, Grok, or agy
credentials return an explicit expiry error rather than triggering a refresh.
Grok API-key auth is rejected; an API-key Codex login has no subscription
limits and is not probed. Keyring lookup never uses `store`/`clear`. Redirects
are refused. Errors use fixed messages, not response bodies or exception text.
Private RPC processes are terminated after each check; hard worker termination
also kills their process group. No deployed daemon, real agent focus, or device
is touched by these checks.

## Grok and Gemini mappings, including absent values

Grok `config.currentPeriod.type=USAGE_PERIOD_TYPE_WEEKLY` identifies its weekly
window; `currentPeriod.end` is the reset. `creditUsagePercent` can be a number
or `{val: number}`. As Omarchy documents, protobuf JSON can omit a zero, so an
unexpired current weekly period with no percentage is **zero used**, including
unified accounts, as required by Amendment 5 and the CLI view. An expired
period is an error, never a new zero sample. Plan `XPremiumPlus` is rendered
as `x_premium_plus`. 5h is always `-` for this billing source.
The stripped installed binary contains a zero-default numeric branch in its
billing renderer; an original source line for `creditUsagePercent` is not
recoverable from that package. See `amendment5/cli-inspection.md` and
`grok-weekly-view.disasm` in the evidence directory for bounded disassembly
and the explicit source-line limitation. The zero mapping also follows the
user's observed CLI output and the explicit Amendment 5 contract.

Gemini selects the group named `Gemini models`, with `weekly` and `5h` buckets.
Convert remaining fraction to integer used percent: `(1 - remaining) * 100`.
Reset timestamps remain absolute until wire encoding. The note always includes
other supplied groups, even at zero: `C/G <week>/<5h> A <week>/<5h>`. `A` is
Gemini 4 Argon. Disabled buckets are unavailable. A missing fraction with a
reset is protobuf zero remaining; a positive `remainingAmount` without a
denominator is not converted to percent. Do not substitute the first group or
an Argon group for the Gemini group. These are explicit differences from the
reference's fallback-to-first-group and touched-only secondary meters.

### Installed agy view request (Amendment 5)

The installed Go binary's pclntab source map and disassembly identify:

- `third_party/jetski/cli/store/quota_manager.go:29`, `Manager.doRefreshQuota`:
  the view calls quota summary; its force-refresh flag only clears a local cache.
- `third_party/jetski/language_server/code_assist_client/user_management.go:150`,
  `fetchLoadCodeAssistResponse`: consumer metadata has `ideType=ANTIGRAVITY`
  (protobuf enum 9).
- The same file at line 266, `getCAICProject`: use the consumer
  `loadCodeAssist.cloudaicompanionProject`, not a model id or a guessed project.
- The same file at line 786, `fetchRetrieveUserQuotaSummary`: the request has
  only `project`; there is no model/group/tier filter to add.
- `auth_provider.go:1141`, `CLIAuthProvider.UpdateEndpointURL`: consumer auth
  uses `daily-cloudcode-pa.googleapis.com`; the GCP/business branch uses prod.
  This collector supports the existing consumer agy login.
- `http_helpers.go:261`, `UserAgent`: the consumer CLI uses name/kind/version
  with client attributes. The current value is
  `antigravity/cli/1.5.0 (aidev_client; os_type=linux; arch=amd64)`.

The previous short `User-Agent: antigravity` returned only C/G even after the
metadata fix. With the full CLI User-Agent, the same endpoint/project returned
**Gemini Models**, **Claude and GPT models**, and **Gemini 4 Argon**. Main week
and 5h are 0% used; C/G is 0/0; Argon is 100% weekly with no 5h bucket. No prompt
request or token refresh was made. Runtime reads the installed CLI's version
from the first 16KiB of `~/.gemini/antigravity-cli/cli.log` startup header; if
unavailable it reports `agy client version unavailable`, preserving a valid
last good sample. No version is silently hardcoded.

These original Go paths are compiled source-map entries, not source files
installed on disk. Reproducible extraction, binary hashes, disassembly, safe
quota response and real dump are under
`ipod/evidence/agents-ui/usage/backend-amendments/amendment5/`.

### Shared quota budget and last good limits (Amendment 5)

Opening Usage or requesting `--usage-dump` does not bypass request budgets.
Quota requests reserve a timestamp **before** I/O, under a stable `flock`, in
`~/.cache/podbox-herdremote/usage/requests/<service>.json`. This directory
ignores `HERDREMOTE_USAGE_CACHE_DIR`, so cold-index diagnostics still share the
same account budget. Cache JSON is atomic and mode 0600, without credentials.
Concurrent instances and restarts reuse both requests and last good answers.

Omarchy `Main.qml:190` defaults to 900s; near a limit, line 214 caps the refresh
at 180s. Its shared `collectors.py` guards probes for 15s and keeps last-good
answers; the inspected Claude script retains 429 fallbacks for 900s. HerdRemote
uses the more conservative **900s** Claude base interval (above the requested
120s), independently of 20s token/index updates. Consecutive 429s back off
900/1800/3600/7200s, capped at 7200s for the exponential component, and always
honor a longer `Retry-After` (seconds or HTTP date). Success resets the counter.
Other providers have a 20s base interval and the same persisted reservation.

On an HTTP/RPC failure or worker timeout, keep each last-good window until its
own reset, retain its original sample time, send `ok`, and show `limits Xm old`.
The last-good object is persisted in the row and shared ledger. Expired 5h
values disappear while a still-valid week remains. `err` means no usable good
window remains; local tokens still count. Unknown-reset samples have bounded
5h/week lifetime. No reset is invented.

An older running daemon cannot participate in this ledger. New code detects
same-user herdremote processes without a matching PID/start-time registration
and blocks new Claude calls while any remain. It does not shorten an existing
backoff, change the old daemon, or restart a service. This machine's real dump
therefore shows `err legacy Claude poller active`: no valid prior Claude
sample exists. **It made zero new Claude quota requests.** The deployed old
code still polls every 20s; these worktree changes take effect only when the
separate deployment owner installs them.

## Local token sources and windows

| Service | Local source | Fields / deduplication |
| --- | --- | --- |
| Codex | `~/.codex/sessions/**/*.jsonl` and `.jsonl.zst` archives | `turn_context.payload.model`; `token_usage_record.payload.usage` per response, deduplicated by response ID. Modern records take precedence for their session. Older `token_count.info.total_token_usage` uses class deltas, repeated checkpoints ignored. Log limits are only an offline-parser fallback, never the normal live quota source. |
| Claude | `~/.claude/projects/**/*.jsonl`, including `subagents/` | Assistant `message.model/usage`; merge component maxima for message ID/request ID across streamed chunks and copied partial logs. `cache_creation` splits 5m and 1h writes. |
| Grok | `~/.grok/sessions/<project>/<session>/usage.json` | Completed `turns[].endedAt/turnNumber/modelUsage[model]`. Do not add duplicate `session` totals or `updates.jsonl`; do not use `costUsdTicks` as API list price. |
| agy | `~/.gemini/antigravity-cli/brain/<conversation>/.system_generated/logs/transcript.jsonl` | Canonical `PLANNER_RESPONSE`, `created_at/step_index/input_tokens/cache_read_tokens/output_tokens`. Skip duplicate full/chunk transcripts. Missing model IDs stay unknown and unpriced; thinking text is not a numeric counter. |
| Standard Gemini CLI | `~/.gemini/tmp/**/session-*.json` | `messages[].id/timestamp/model/tokens`, changed snapshots only; `input` includes `cached`, while `output` and `thoughts` are disjoint. |

A future weekly reset selects `[reset - 7 days, reset)`, excluding future
records. Without a current reset, use trailing seven days. An expired weekly
sample falls back to trailing seven days without inventing the next reset.
The index can therefore be reused when a quota window changes.

Codex and native Grok input includes cached input, and output includes reasoning.
Those subsets are split for pricing and counted once. Codex cache write is
split out of input. Claude's top-level input/cache read/cache creation/output
are disjoint; its thinking detail is already output. Gemini CLI thoughts are
added to output. Unknown models count tokens but cost zero.

Local statistics exclude other machines, web UI activity, deleted logs, and
missing telemetry. They have different coverage from account-wide quotas.
Native Grok in-progress turns are not included until persisted as completed.
Restored legacy Codex logs that begin mid-session cannot locate their initial
cumulative usage in time. Existing Grok Codex-wrapper configs name a gateway,
not a subscription quota source; they are not called or counted as native
Grok sessions. agy usage includes any models used through that client, under
its Gemini service row.
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

## Persistent index and background scheduling (Amendment 2)

The old implementation indexed only on `us`, in 3s slices, discarded the index
on restart, and marked partial results `err`. That could leave the device grey
for an extended period. The replacement starts automatically in `Daemon.run`,
independent of whether Usage is open. `us` still sends the current cache before
requesting work. Four spawned worker processes run at nice +10 under one
supervisor thread; no indexing runs on the serial/herdr thread.

- Claude quota probes use 900s plus 429 backoff; other providers use at least
  20s. Shared persisted reservations survive restarts and scan-cache overrides.
  Background index slices reuse the last probe; opening Usage creates no extra probes.
- Each scan slice has a 0.35s wall budget and a 0.15s scan-CPU budget, followed
  by at least 0.75s idle while incomplete. A rotating file cursor prevents a
  growing active log from starving older pending files. The hard 5s collector
  watchdog remains. Normal completed refreshes run every 20s.
- Before opening logs, use authoritative weekly start (or trailing seven days)
  to discard files whose mtime predates the window. Within a selected file,
  preserve the model context and cumulative baseline: no unsafe random seek
  assumes timestamps are monotonic or that the model appears in every record.
  Large unrelated JSONL messages are skipped without JSON parsing. Eligible zstd
  archives stream through Python 3.14 `compression.zstd` (or optional
  `zstandard` on older Python), retaining the decoder between slices and saving
  the decompressed offset for restart. Unchanged complete archives are never
  decompressed again. An unavailable decoder is an explicit error, never a
  silently omitted archive.
- Persist under `~/.cache/podbox-herdremote/usage/<service>/`: versioned,
  compressed per-file JSON containing path identity, inode/size/mtime, last
  complete byte offset, parse context, counter baseline, numeric response
  records, and dated per-window model/class aggregates. Only counters and
  metadata are retained, never prompts or credentials. Numeric response keys
  retain cross-file deduplication; global totals do not blindly add copied
  per-file aggregates.
- Same-directory temporary files (mode 0600) and atomic replace prevent a
  half-written cache. Corrupt versions/shapes/compression rebuild that file.
  Partial appends retry from the last complete line; truncation/replacement
  resets only that file. Seven-day numeric records allow rolling expiry and
  changed quota windows to reaggregate correctly.
- Small versioned `<service>-row.json` snapshots load immediately at restart.
  File indexes are then validated and only appended bytes read. A corrupt row
  is ignored, not served. A timeout retains prior numeric fields and marks a
  real error; it does not erase on-disk progress.
- Incomplete totals return `ok` if limits exist, otherwise `tok`, with
  `indexing N%` (and unpriced caveat when needed). They are never `err` merely
  for being incomplete. `err` is reserved for real I/O/RPC/HTTP/timeouts.

The table in the backend report records measured cold completion, warm restart,
and steady refresh CPU on this machine. These are local measurements, not a
claim about the deployed service. `HERDREMOTE_USAGE_CACHE_DIR` can redirect only
our index cache for isolated cold/warm measurements; it changes no source or
authentication location.

## Read-only diagnostic and tests

```
python3 tools/herdremote/herdremote.py --usage-dump --usage-wait 120
python3 -m unittest discover -s tools/herdremote/tests -v
```

The dump starts the background collectors, waits up to the chosen budget for
indexing, prints one final protocol frame, and exits. It never constructs the
herdr daemon or probes serial devices. Tests mock every HTTP, keyring, and live
RPC request. pty integration uses a private fake herdr and fake iPod. See
`/home/wilf/data/PodJS/ipod/AGENTS-USAGE-BACKEND.md` for the real output,
measurements, Omarchy-source comparison, and remaining upstream omissions.
