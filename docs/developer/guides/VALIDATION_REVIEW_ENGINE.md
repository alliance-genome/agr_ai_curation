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
   is rerun. Return no requests for structurally missing required inputs. A baseline
   or curator prerequisite returns an explicit `non_validator_prerequisite`
   not-runnable outcome until an actor adapter supports that source.
8. After a prerequisite changes, explicitly request reconsideration of a previously
   attempted dependent. Supply a trusted `refresh_inputs(original_request, sources)`
   callback to `scheduling.reconsider` (or the bound tool/reviewer factory). This
   pure callback applies the binding's declared selectors and returns only the new
   `selected_inputs`; return `None` when required inputs are unavailable. The engine
   freezes current explicitly reviewed prerequisite pins, a fresh request ID, the
   unchanged extraction/evidence/result anchors and the saved validator receipt.
   The retry consumes the same target/run/batch allowances. Changed target lineage
   is never refreshed this way. Replaying an operation returns its original work
   before invoking the builder or inspecting changed selections.

Tool input and compare-and-set conflicts return structured, atomically unapplied
results for the reviewer to correct. Per-target retry limits do not stop unrelated
review decisions. Episode-wide deadline, tool, turn and token stops still stop the
run. The supervisor's final prose cannot override ledger readiness.

## Unknown completion and recovery

Claims are fenced by durable leases. The executor renews its lease while a validator
callback runs; renewal never extends the episode deadline. Lost ownership or an
explicit episode stop cancels the callback. Recovery never blindly resends submitted
work. Unsubmitted expired claims may be delivered
again. Once submission starts, a disconnected worker is uncertain: do not replay
its provider call. Keep the reservation. Recovery/uncertainty facts remain in the
immutable ledger, and late results are retained without granting selection rights.
An acknowledgement can name the exact uncertain attempt without inventing a
completion. A later real answer still requires its own explicit review. If that
answer arrives after a retry completed the episode, the next authorized ledger
operation structurally reopens the same episode and records why. It retains the
accepted retry, original deadline and all charges. Expired, cancelled, superseded
and retired episodes cannot publish late selections; no new allowance is granted.

Expiry cancels awaited work through the deadline timeout and prevents new work or
selection publication. Hosts call `admission.cancel` with the authenticated owner and reason, and cancel
their running reviewer task. Batch/workspace hosts also stop admitting new child
operations. They must not delete scopes/jobs. A known pre-generation admission
refusal records its exact stop reason/resource as an operational failed completion,
not an unknown scientific answer. Once any provider call is admitted, ambiguous
stops retain reservations and unknown outcomes. Scientific disagreement, unresolved answers,
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
same token pool; only the named reviewer consumes reviewer-turn allowances. A
reviewer budget requires an explicit `reviewer_agent_id`. Deadline checks cache the
fixed deadline locally instead of locking the batch row for each streamed delta;
quota admission/settlement runs off the event loop.

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

## Integration handoff and calibration

E/F/G must bind their real prompt/tool variants (compact, non-compact, single,
batch, saved custom validators), supply the selector refresh adapter, and include
`RECONSIDERATION_PROTOCOL` in validator invocations. Those tickets must replace
impossible follow-up guidance in any caller that lacks review/reconsideration tools;
D does not turn existing extractor prompts into supervisors ahead of activation.

ALL-1446 must specifically exercise small episodes: reserving an existing 128k
output ceiling against a 150k one-target initial pool leaves roughly 22k for input.
This conservative refusal is visible; D does not silently lower output capacity.
The fake-provider Runner test exercises the real factory, tools and measured seam
without production calls. It does not replace H's representative live calibration.
