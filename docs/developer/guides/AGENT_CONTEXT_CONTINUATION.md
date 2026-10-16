# Context continuation

The shared native OpenAI Responses boundary owns working-context compaction for
main chat, Agent Studio, supervisors, specialists, validators, saved/custom
agents and flow/batch runs. A tool result, user message or validation result does
not lose content merely because it crosses the compaction trigger. Main chat and
Studio retain independent owner-scoped sessions; they share implementation,
not conversation history.

## Request policy

1. Archive canonical input before constructing the model view. Keep document-local
   numeric references in model-facing PDF tools and canonical UUID provenance in
   storage. Never summarize scientific findings in application code.
2. Assemble the actual installed SDK Responses request, including instructions,
   tools, structured output, reasoning and provider settings. Count input through
   `/responses/input_tokens`. This adds an HTTP preflight and its latency to every
   native request; count failures stop before generation and are recorded. The
   OpenAI client's existing retry policy applies, without an extra retry loop.
3. Send complete input that fits the model's verified input/context capacity.
   `context_management` requests native compaction at the configured token
   threshold (default 250000). Crossing this threshold is not a local rejection
   or permission to clip a tool result. Provider-returned native compaction items
   are retained; only their covered, completed prefix may be omitted later.
4. If incoming content exceeds actual capacity, first compact closed historical
   prefixes that themselves fit `/responses/compact`. Preserve pending parallel
   calls/results and their reasoning. The standalone response's **whole output
   window** becomes canonical; do not extract just its opaque item. Each iteration
   must consume additional history and reduce size.
5. If textual content still cannot fit, retain its exact body in authorized
   history and substitute a source descriptor. `read_pending_context` delivers
   capacity-derived contiguous pages with hashes and ranges, in source order.
   It never executes the original tool again. All queued content must be delivered
   before ordinary tools/final output resume. Pages may be compacted as work
   continues. Delivered coverage proves transport, not semantic retention: opaque
   compaction is not a lossless scientific archive. Exact source remains readable.

The final boundary owns input, tool selection, native compaction and session
chaining. `extra_body` cannot override those SDK-owned fields. Other effective
request settings, including model/instructions/output reserve, are included in
counting and capacity selection. Automatic provider truncation and unmeasurable
stored prompts/server conversation chains fail explicitly. Fixed instructions,
tool definitions and non-text content that alone exceed capacity also fail
explicitly; textual ingestion cannot shrink them safely.

## Persistence and failure behavior

SDK working history stores complete protocol items, including tool exchanges,
reasoning and opaque compaction, separately for each owner/chat kind/session.
Canonical visible transcripts and exact run archives remain separate. Authorized
projection anchors match the actual SDK history; reconnect reconciles only exact
pages committed there, not an uncommitted delivery receipt. Projection windows
are stored once, with key-only anchors, and checkpointed on changes/native
boundaries. Restore streams event metadata and reads payloads individually.

A PostgreSQL advisory lock prevents concurrent invocations in the same chat.
It uses a dedicated unpooled connection so a long model run does not occupy the
application connection pool. Cancellation drains owned work before releasing it.
Each active conversation still consumes one PostgreSQL connection; database-wide
connection capacity remains an operational prerequisite.

Required archive/checkpoint failures stop before compaction can discard the only
working copy. A model-output archive failure stops before publishing that output
or executing its new tools. Without authorized history, actual-capacity recovery
fails before any paid standalone compaction. Completed actions are never retried
by this mechanism. PDF read failures retain the terminal error contract.

This is not automatic whole-job replay after worker failure. Restoring an entire
nested job also needs builder/in-flight-write reconciliation. OpenAI-compatible
Chat Completions providers retain full selected data but do not receive native
Responses compaction; they have no claimed equivalent capacity recovery.

## Restrictions and runtime coverage

| Control | Disposition and purpose |
| --- | --- |
| Local 60000/40000 estimates, last four results | Removed; no age/count eviction |
| Global 32768-byte and Studio 12000-character tool envelopes | Removed |
| Section 30/100 chunk limits, default span windows | Removed; full selected section/chunk by default; explicit ranges remain |
| Exact text/detail caps across history, diagnostics, source files, flows and outputs | Removed; caller-selected ranges remain |
| Workshop main/group/metadata clipping | Removed before the shared boundary |
| Native trigger | `AGENT_CONTEXT_COMPACTION_TOKEN_THRESHOLD=250000`; tunable optimization point |
| Model capabilities | Model catalog plus `AGENT_MODEL_CAPACITY_OVERRIDES_JSON`; verified provider limits, not policy guesses |
| Main/Studio turns | 256; suggestion submission 64; stop at its submission tool |
| Specialist calls/deadline | 256 total, 64 per specialist, 3600 seconds; bulk optimization cannot lower base turns |
| Validator calls / synthesis retry | 64; specialized allele bindings also 64 |
| Query/list pagination, concurrency, retries, timeouts | Remain environment-configurable operational controls; a selected record's text is complete |
| Explicit inventory/index previews | Remain labeled previews with full exact-detail access; no complete-content claim |
| Finalized extraction/validator handoffs | Persisted typed receipts/manifests retain ownership and acceptance contracts; exact detail is addressable, original outputs archived |
| Authoring/transport/storage field constraints | Remain explicit validation/resource contracts, not silent context clipping; the benchmark portal assistant bridge retains its 1 MiB reply transport limit |
| Redacted trace/UI previews | Diagnostic displays only; never substitute for canonical evidence |

One-shot classifiers retain their existing execution backstops. All owned SDK
runner entry points attach the common context configuration; authenticated runs
attach exact-history tools. Unauthenticated utilities cannot invent an owner.
Standalone compaction uses the existing measured direct-request path plus a
model-generation span and benchmark invocation accounting. Native compaction
usage remains part of its normal model response, never a second synthetic bill.

`runtime.context_view`, `runtime.context_capacity` and model-request measurements
report archive health, native/standalone compactions, pre/post input counts,
capacity, pending source count and count-endpoint latency/calls. TraceReview
retains these numeric metrics while redacting credentials. Canonical ingestion
bodies remain in authorized run history, not unrestricted diagnostic output.

## Upgrade and verification

Remove retired quota variables from private deployment configuration. Explicit
old values override changed defaults: review the deployed native threshold and
Studio/validator/supervisor limits. Production Compose passes the active capacity
and turn settings to backend and benchmark worker. No saved-content repair or
reindex is required; saved-agent/prompt upgrade inventory remains a release gate.

Deterministic tests exercise real SDK HTTP/WebSocket serialization, repeated
native compaction and more than 64 productive turns, exact ingestion, no repeated
write, archive failure, source provenance and separate PostgreSQL sessions with
cancellation/reconnect. These are mocked provider responses, not paid scientific
quality or cost comparisons. Release evaluations must use actual provider usage,
completion and scientific correctness; fewer estimated tokens alone prove none
of those.

Official references checked October 16, 2026:

- [Compaction](https://developers.openai.com/api/docs/guides/compaction): native threshold and standalone whole-window semantics; standalone input must fit.
- [Input token counting](https://developers.openai.com/api/docs/guides/token-counting): Responses-shaped exact request accounting.
- [Agents SDK sessions](https://openai.github.io/openai-agents-python/sessions/): application-owned conversation persistence.
- [WebSocket mode](https://developers.openai.com/api/docs/guides/websocket-mode): continuation and reconnect behavior.
- Installed `openai==2.41.0`, `openai-agents==0.17.4`: request builders, SDK output-to-input conversion and session persistence are the implementation contract.
