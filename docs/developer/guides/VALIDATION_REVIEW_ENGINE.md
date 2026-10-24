# Shared validation review engine

ALL-1442 supplies the shared engine on the unreleased validation-review integration
branch. It does not activate review in chat, flows, batch execution or the curator
UI. ALL-1443/1444/1445 own those adapters. Production activation requires the
representative calibration and cost-policy approval in ALL-1446.

## Runtime contract

1. Authenticate the owner/document/session. Construct `HistoryScope` and immutable
   extraction history before any validator call. Declare all episode obligations,
   including inactive, unavailable and missing-input bindings.
2. Create durable scopes with `admission.create_scope`: optional batch/workspace
   parent, document operation, then attach each artifact episode. Server-owned
   operation IDs/cardinalities survive reconnect and worker restarts. A human
   renewal names its predecessor, authenticated owner and reason; it retains the
   parent allowances. It does not refill them.
3. Freeze requests and exact saved validator receipts through `scheduling.initial`.
   Attempt creation, admission reservation and work scheduling commit together.
   Do not substitute package defaults for saved custom validator revisions.
4. Run `execution.execute` with a trusted validator adapter. The adapter must
   reauthorize the receipt, retain supplied-context-only validator tools, use the
   measured model boundary, and return one `ValidatorOutcome` per named attempt.
   Shared executions still retain separate logical target attempts. Reconsideration
   payloads carry original requests, parent decisions, prior answers/uncertainty,
   exact evidence bodies/references and the locked reconsideration protocol.
5. Construct the reusable `validation_reviewer` with `reviewer.create_reviewer`
   and run it through `reviewer.run`, or bind the same tools and execution budget
   to an actual named runtime supervisor. Supply authenticated reading tools and
   a checkpoint loader; model arguments never choose ownership, episode, actor,
   validator revision or envelope/source linkage. Apply the tool surface inside
   the budget scope, including nested agents.
6. Decisions publish the current envelope, selected-value ledger and curator
   authority changes in one transaction. The checkpoint may start at revision 0
   with valid durable source/session linkage. `expected_version` means the target
   head CAS version, not `selection_generation`. Conflicts roll back the whole group.
   Replaying identical decision operations returns the saved response without a
   new publication. A completed episode does not expire retroactively.
7. For a dependent wave, `waves.advance` waits for explicit review of selected
   prerequisites, then invokes a trusted, pure input-selector callback under the
   structural transaction. The builder must use declared structural selectors,
   not a semantic quality test. It receives exact selected answers; returned
   requests get pinned prerequisite versions and the same episode. No extraction
   is rerun. Return no requests for structurally missing required inputs.

## Unknown completion and recovery

Claims are fenced by durable leases. Unsubmitted expired claims may be delivered
again. Once submission starts, a disconnected worker is uncertain: do not replay
its provider call. Keep the reservation. Recovery/uncertainty facts remain in the
immutable ledger, and late results are retained without granting selection rights.
An acknowledgement can name the exact uncertain attempt without inventing a
completion. A later real answer still requires its own explicit review.

Expiry cancels awaited work through the deadline timeout and prevents new work or
selection publication. Hosts must cancel their task when the user cancels a run;
they must not delete its scopes/jobs. Scientific disagreement, unresolved answers,
exhaustion and operational uncertainty are distinct from acceptance.

## Hard admission and supported transports

The measured SDK model seam reserves exact input tokens plus the existing allowed
output ceiling before generation, explicitly sends that ceiling, and reconciles
actual input+output usage (including cached/resent input). Missing usage retains the
full reservation. Provider retries are individual reservations, not new scientific
attempts. Optional cost-ledger reporting is not the enforcement mechanism.

Native OpenAI Responses requests are supported with official exact input counting.
The pinned WebSocket adapter does not replay a frame after sending begins. HTTP
clients must have internal retries disabled; SDK retries remain measured. Hosted
tools, stored provider context/prompt templates, uncountable providers, and native
or direct compaction with no enforceable output ceiling stop explicitly. No
character estimate is presented as hard admission, and no permanent WebSocket
transport switch is introduced. Nested review-related model calls inherit the
same token pool; only the named reviewer consumes reviewer-turn allowances.

## Limits and exact reads

All `VALIDATION_REVIEW_*` settings are documented in `.env.example` and forwarded
to backend, benchmark worker and test services. Defaults: 2 attempts/target,
16 extras/document operation, 64/batch, 64 reviewer calls, 256 review tools,
1800 seconds, 1M reviewer tokens, initial pool 150k × trusted targets and
reconsideration pool 150k × allowed extras; warnings at 75%/90%. Pools allow uneven
per-target usage. They are ceilings, not spending goals. Extractor/formatter
limits are unchanged.

Protocol transport defaults are 8000 reason/instruction characters, 100 decision
entries, 100 evidence references and 64KiB serialized history pages. Oversized
inputs are rejected, not truncated. Grouped review and escaped/non-ASCII multi-page
fixtures exercise these contracts. The delivery lease defaults to 60 seconds.

Packets pin original extraction, complete fact references, decisions, prerequisites
and budget snapshots. `read_validation_history` returns canonical ASCII JSON byte
ranges with exact SHA and continuation offset; concatenate all content pages.
`fact_id=baseline` reads the bound artifact's immutable original. Paging has no
separate total-data cap and never truncates the stored body. General reviewer tool
and token budgets still apply to requests made during an active review.

## Observability and validation

Committed `validation_review.*` facts are emitted to the existing extraction
TraceReview timeline. Their IDs/SHA identify the exact durable body even when the
trace preview is abbreviated. Provider measurements include episode and
reservation IDs. The TraceReview analyzer groups review operations explicitly.
Sentry owns unexpected execution errors once, with a fixed sanitized message;
scientific outcomes and expected budget stops do not produce new errors.

Persistence tests use real PostgreSQL commits and concurrent sessions: reservation
races, parent quotas, restart/lease fencing, unknown usage, expiry, exact paging,
grouped decisions, idempotent publication and selected prerequisite waves. Fake
validator callbacks exercise the reusable executor without paid provider replay.
Unit tests cover provider admission, configuration, locked prompts and tool scope.
