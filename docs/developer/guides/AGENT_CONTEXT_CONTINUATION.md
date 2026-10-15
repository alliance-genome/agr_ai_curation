# Context continuation

A growing working conversation must not fail merely because it crosses an
optimization target. The October 15 fix removes that terminal decision from the
shared model-input filter and enables native OpenAI Responses compaction during
an individual agent run. No new agent runner or workflow framework is involved.

## Data and execution contract

- Exact requests, tool arguments/results, model outputs, builder revisions and
  validation findings remain in owner/run/lineage-authorized PostgreSQL history.
- Older successful tool bodies can become references to exact stored data.
  History retrieval itself records structured error/navigation metadata before
  the SDK serializes it, including when the SDK uses Python repr. Re-externalized
  reads point directly to the original event/path/cursor rather than nested copies.
- Native Responses requests carry `context_management` with a configurable
  provider-token threshold. The returned opaque compaction state is retained by
  the pinned SDK. Subsequent stateless inputs can omit the prefix covered by a
  **native response** compaction item. Standalone `/responses/compact` windows
  are not pruned. A call/result crossing a boundary prevents prefix removal.
- Model-context checkpoints contain immutable item references, instructions and
  completed tool-call identities. These are recoverable independently of worker
  memory. The archive is never replaced by an opaque summary.
- The same SDK Runner and builder workspace continue. Compaction never invokes a
  tool, replays an export, changes candidate IDs or finalizes/aborts the builder.
  Existing validation/finalization rules continue to decide scientific completion.
- Standard chat persists native opaque state plus its following tool exchanges
  between turns. Its previous separate SDK compact-session policy is removed, so
  there is one compaction owner. Visible canonical chat history remains unchanged.

## Coverage

| Surface | Context owner | Exact data |
| --- | --- | --- |
| Standard chat supervisor | Shared native Responses loop; durable chat projection between turns | Chat transcript and run archive |
| Flow/batch supervisor | Shared native Responses loop | Run archive and persisted flow outputs |
| Extractors, validators, custom/saved agents | Shared native Responses loop per invocation | Run archive, builder revision history and persisted outputs |
| Tool-less structured finalization | Native compaction; no unreadable externalized references | Canonical history |
| OpenAI-compatible Chat Completions routes | Exact-history projection; native Responses settings are not sent | Run archive |
| Direct non-agent ingestion calls / non-OpenAI Studio provider | Existing bounded request policies | Existing ingestion/Studio history |

This patch does not add automatic job replay after a worker crash. Checkpoints
and action receipts survive, but safe restart of a whole nested task also needs
reconciliation of in-flight writes and restoration of its builder and pending
work. That is separate follow-up work. Never advertise opaque compaction as an
exact scientific archive or a proof of exhaustive source coverage.

## Configuration and diagnostics

`AGENT_CONTEXT_TRIGGER_ESTIMATED_TOKENS` and
`AGENT_CONTEXT_TARGET_ESTIMATED_TOKENS` control local output externalization.
They are soft estimates including instructions, schemas, tools and output reserve.
They are not model capacity limits. `AGENT_CONTEXT_COMPACTION_TOKEN_THRESHOLD`
(default 60000) controls native provider-token compaction; keep it well below the
selected model's capacity. `AGENT_CONTEXT_COMPACTION_ENABLED` is an incident switch.
All settings are documented in `.env.example`.

The retired `STANDARD_CHAT_CONTEXT_TOKEN_BUDGET` and
`STANDARD_CHAT_COMPACTION_THRESHOLD_PERCENT` settings no longer select a competing
chat policy. Remove them from private deployment configuration when upgrading.

`runtime.context_view` includes before/after estimates, native compaction count,
removed prefix size, retrieval counts/bytes and archive health, even when a
history-storage failure prevents reduction. Model-request measurements report
configured context management, compaction output count and actual provider usage.
Compaction is part of the normal Responses request, so its usage must not be
counted as a second synthetic request. TraceReview exposes these events through
its extraction timeline. Smaller local estimates alone do not establish lower
billed cost or preserved scientific correctness; those need workflow evaluation.

Curators see progress while native compaction runs. Provider/storage failures
retain their existing explicit error handling; completed tool actions are not
rerun by context recovery. This is not a promise of unlimited input size,
provider availability or automatic recovery of every interrupted task.

## Sources and verification

Official documentation checked October 15, 2026:

- https://developers.openai.com/api/docs/guides/compaction
- https://developers.openai.com/api/docs/guides/websocket-mode#compaction-and-creating-new-responses
- Installed `openai-agents==0.17.4`: model settings, Responses request builder,
  compaction item parsing, session persistence and websocket request chaining.

Tests exercise the real pinned Runner (streamed/non-streamed, HTTP/WS model
adapters), repeated native compactions, exact recovery of an early negative and
unresolved finding, one write despite rollover, durable projection/checkpoint
recovery, pending-call preservation, provider gating and the observed 13-output /
two-six-read regression. Deterministic fixtures establish runtime behavior, not
live-model semantic fidelity; retain live release evaluation separately.
