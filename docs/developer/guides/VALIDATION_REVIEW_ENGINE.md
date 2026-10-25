# Shared validation review engine

The unreleased validation-review integration branch contains the shared engine
(ALL-1442), chat adapter (ALL-1443), and configured flow/batch adapter with targeted
extractor follow-up (ALL-1444). ALL-1445 owns the curator UI integration. Production
activation requires representative calibration and cost-policy approval in ALL-1446.

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
   requests must contain a dictionary `request.selected_inputs`, with all
   prerequisite-derived selector values in that mapping. Other request fields
   retain original extraction/evidence only. Requests get pinned prerequisite
   versions and the same episode; malformed shapes are rejected before dispatch. No extraction
   is rerun. Return no requests for structurally missing required inputs. A baseline
   or curator prerequisite returns an explicit `non_validator_prerequisite`
   not-runnable outcome until an actor adapter supports that source.
8. After a prerequisite changes, explicitly request reconsideration of a previously
   attempted dependent. Supply a trusted `refresh_inputs(original_request, sources)`
   callback to `scheduling.reconsider` (or the bound tool/reviewer factory). This
   pure callback applies the binding's declared selectors and returns only the new
   `selected_inputs`; return `None` when required inputs are unavailable. The engine
   freezes current explicitly reviewed prerequisite pins, a fresh outer attempt identity, the
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

`test_canonical_review_runtime.py` composes the real SDK/tool and persistence
boundaries across chat, direct and saved-profile extraction, configured flow CSV
attachments, the serial batch worker, and fresh workspace review. Provider replies
are scripted: these tests prove history, dispatch and publication wiring, not
scientific quality or actual provider token cost. The trace assertion parses real
committed events and checks attempt/completion references, rejection/retry parentage
and exact selected-publication identity against durable history.

Executable settings contracts use `model_settings_contract`, which records SDK
JSON settings plus a versioned declaration of the actual retry policy. The default
factory registers that declaration against the unchanged SDK callable; budget and
backoff are read from the live settings. Change the declaration revision whenever
policy behavior changes without an already-declared parameter change. Unknown
callbacks fail explicitly; do not serialize functions with `repr`, infer closure
contents, or drop the policy from the contract. Resume reconstructs authorized
code and compares settings; it never executes a policy from saved JSON. Changed
policy/budget settings can require a fresh review round. Existing historical
records are not rewritten to claim a policy they never recorded.

## Acceptance coverage map

Producer composition and state-machine contracts are complementary evidence.
The canonical scenario runs the shared review meaning through regular/direct
chat, saved-profile extraction, configured flows, serial batch execution and both
workspace origins. It does not rerun every state-machine permutation in every
producer. The narrower tests below isolate the identity and failure boundaries;
run them as part of the final persistence/release gates rather than treating the
canonical scenario as a substitute.

| Contract | Focused evidence and producer boundary |
| --- | --- |
| Dependency waves and changed prerequisites | `test_validation_review.py::test_dependent_wave_waits_for_explicit_review_then_pins_selected_answer`; `test_chat_review_adapter.py::test_dependent_initial_work_reads_selected_prerequisite_only_after_review`; `test_curator_round_reuses_accepted_dependency_until_supervisor_explicitly_reconsiders` in that module covers the workspace adapter. Flow/batch use the same `ValidatorAdapter`; canonical flow/batch prove that wiring. |
| Shared executions and composite decision groups | `test_validation_history.py::test_shared_execution_and_grouped_cas_is_atomic` and `test_composite_units_and_pinned_prerequisite_selections` verify shared-execution identity, separate decision coverage and stale dependency refusal. Canonical producers verify grouped review through actual tools, not composite-result permutations. |
| Extraction-only and nonexecuted required validation | `test_validation_history.py::test_extraction_only_selection_cannot_claim_a_later_contribution_as_original`; `test_selected_value_authority.py::test_missing_execution_still_has_review_obligation`, `test_required_validation_cannot_accept_baseline`, and `test_unavailable_required_mapping_cannot_be_hidden_by_extraction_only_acceptance`. |
| Explicit empty results | `test_flow_lifecycle_execute_flow_sse.py::test_execute_flow_saves_and_replays_finalized_empty_extraction` tests the persisted API path; `test_executor.py::test_flow_preserves_finalized_empty_and_sibling_output` covers attachments and siblings. Empty extraction is not an invented validation attempt. |
| Accepted unresolved versus successful resolution | `test_canonical_review_runtime.py::test_canonical_flow_exports_accepted_unresolved_without_inventing_identifier` runs real flow-to-CSV with an explicitly unresolved answer and no resolved identifier. `test_selected_value_authority.py::test_unresolved_is_selectable_without_inventing_identity` and `test_curator_selected_authority.py::test_accepted_unresolved_revalidation_withdraws_override_without_claiming_resolution` check scientific state separately from review eligibility. Submission validation remains a separate contract. |
| Pending inspection and export | Canonical chat-adapter test refuses publication of the same source before review. `test_flow_review_steps.py::test_saved_extraction_remains_pending_until_actual_decisions` and `test_executor.py::test_fresh_flow_extract_inspect_then_next_step` cover saved flow state and inspection ordering. |
| Curator precedence and record scope | `test_curator_selected_authority.py::test_only_authorized_accepted_revalidation_replaces_exact_curator_version` includes later-edit protection; `test_chat_review_adapter.py::test_workspace_b_round_calls_only_b_and_retains_a_c` checks actual adapter dispatch. Canonical workspace cases execute fresh bootstrap/curator commands through real SDK review. |
| Exact historical publication | `test_selected_value_authority.py::test_exact_publication_survives_later_selection_and_failed_publication_rolls_back` checks the old published reference and atomic rollback; canonical producers compare selected CSV values and immutable originals. |
| Transport and prompt identity | `test_compatible_client_lifetime.py` uses real loopback HTTP alongside the websocket provider fakes, including same-named validator runtime copies and client cleanup. |

Paths in the table are under `backend/tests/integration/persistence` unless they
name `test_flow_lifecycle_execute_flow_sse.py` (integration root),
`test_executor.py` (unit/lib/flows), or `test_compatible_client_lifetime.py`
(unit/lib/openai_agents). Historical-reference reconciliation, production-derived
dev replay, full frontend/backend gates and measured budget/cost policy remain
release evidence; this map does not assert those tasks passed.

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

## Chat and direct-agent execution (ALL-1443)

Actual chat routes opt into `validation_review.chat.ChatReview` through the
server's `inline_chat_persistence` capability. Route labels alone do not activate
review: shared Studio, flow and benchmark runners retain their explicit lifecycle
until their own integration work. No deployment is implied by the integration branch.

The extractor's immutable artifact is planned and saved as a discoverable CHAT
result before initial validator work. The saved plan freezes target-to-binding
relationships, custom revision pins and executable definitions. Initial calls and
reconsideration use the same admitted durable work executor. Completion and the
normalized candidate recipe commit together; only explicit decisions publish a
selected checkpoint. Curation preparation checks that checkpoint against the same
source hash and refuses pending results. Initial validation is not repeated by
curation bootstrap.

Candidate recipes retain the authenticated group context used for that exact
validator execution, separately from the scientific request and original plan.
Group-scoped bindings record normalized groups; unscoped bindings record explicit
null. Selected findings replay this immutable audit context, never current user
memberships or provider-supplied values. Live execution still checks current
permission independently. A historical recipe without this field cannot be newly
published: it requires a fresh authenticated validation/review round. Its source,
completion, decisions and previously stored checkpoints remain historical records;
replay does not backfill or rewrite them.

An accepted unresolved answer remains unresolved. Its declared descendant writes
have a separate `validator_unresolved_value_materialization` audit event so a
list element can retain the validator's outcome without permitting whole-list
replacement or falsely recording a validated identity.

After initial answers, the actual supervisor enters a bounded continuation with
its existing model, settings and policy. Direct/preferred/custom extraction resolves
the requested supervisor settings for this phase. The runtime replaces its tool
surface with review/history operations and removes handoffs and direct paper
readers. No generic agent dispatch, full extraction restart, workflow edits or
model-selected validator replacement is available. The bound follow-up tool may
invoke only the original configured extractor for an existing entry. A reconsideration uses the
saved binding and rechecks authorization/configuration before provider submission.
Validators retain supplied-context-only tools; they never inherit paper readers.
The supervisor delegates paper investigation to that extractor; workflow changes still require curator action.

Dependent initial validation can advance only after its configured prerequisites
have explicit current selections. Selector callbacks run without provider calls
and pin the exact selected prerequisites. A later retry is an explicit charged
reconsideration, not an automatic dependency rerun. Scientific acceptance,
including acceptance with caveats, belongs to the supervisor; readiness and
ownership checks are structural.

One durable document operation may discover several extraction artifacts. Its
`discovered_document_run` scope registers append-only coverage once per new
artifact, adding only the existing per-target/episode allowances. The shared
16-extra-attempt default is not multiplied by the number of extractors. Policy,
existing episode deadlines and consumed budgets survive resume. All limits remain
configured through the existing `VALIDATION_REVIEW_*` environment settings.

Chat dispatch fences prevent a reconnect to the same turn/tool/input from blindly
resubmitting an uncertain extraction. Stop cancels active processing without
permanently cancelling the review episode. Saved results remain discoverable;
queued review work can resume, while ambiguous provider submissions are retained
as uncertain and are not replayed. Late validator answers retain their immutable
completion and candidate recipe. Expired work never gets an automatic fresh quota.
A genuinely new curator extraction request has a new durable turn and preserves
the earlier source/history.

Terminal `validation_review` metadata comes from the ledger, independently of model
prose. Pending results also get a visible chat notice. Source inspection remains
available, but selected exports and curation preparation cannot claim pending
results are complete. TraceReview's `validation_review.*` timeline includes saved
plans, coverage grants, executable validator configuration and supervisor phases.
Expected budget/deadline stops are pending outcomes; operational failures use the
existing sanitized reporting owner.

Direct extractor follow-ups first use the resolved supervisor to interpret the
curator's intent. Continuing saved review does not invoke extraction. Only an
explicit fresh-extraction request permits the already chosen extractor; the
routing phase has no tools or handoffs and cannot select a different agent.
Its decision is durably fenced per turn. Lookup, Q&A and formatter calls are not
subject to the extraction fence. This ordinary intent-routing call does not
create a review episode or replenish any review budget.

The review phase has a separate trace name and decision actor, with the same
resolved supervisor model/settings and group policy. Its locked review prompt
replaces routing instructions; no instruction requests unavailable routing or
inspection tools. Prompt layers and actual tools are recorded for TraceReview.
Stop polls the shared cancellation signal during non-streaming review and
validator work; real answers that arrive after cancellation remain in history.

Direct extraction presentation uses the current selected checkpoint, scoped to
the current extraction turn. Immutable originals are retained separately. The
HTTP stream emits `VALIDATION_REVIEW_STATUS`, the durable assistant payload saves
it, and the chat UI displays readiness independently of prose. Replaying a saved
direct response refreshes owned references and current selected values without
model calls or rewriting the historical transcript. Named-supervisor replay
preserves historical prose and refreshes the separate review status.

The D provider boundary still applies: hard review admission currently supports
OpenAI Responses with SDK HTTP retries disabled. Unsupported providers are not
silently switched and cannot receive unmetered review calls; their results remain
pending with an operational refusal in the review history. This is a release
constraint, not authorization to weaken admission or deploy the integration branch.

The review supervisor's explanation and expected stop reason are append-only
history facts, keyed by result, episode, actor and durable chat turn. Direct
presentation and replay include the latest saved explanation; named-supervisor
handoffs carry it even when a continuation invokes no extractor. Reconnecting
the same completed review turn reuses that outcome without calling the reviewer
again. A new curator turn can continue the same episode without resetting budgets.
Explicit Stop produces a saved interruption notice rather than a provider-error
alert. Restored chat badges are labelled as status at the time of the response;
current delivery and authenticated replay still read readiness from the ledger.
Only notes produced in the current turn are automatically appended to ordinary
named-supervisor or lookup answers; earlier notes remain reference context. Direct
saved-review presentation can show the latest notes alongside saved selections.
Browser-cache restoration also labels readiness as historical. A request-local
Stop flag survives SDK tool-task cancellation even when the SDK consumes the
cancellation exception; the outer chat wrapper still reports interruption.


## Targeted extractor follow-up (ALL-1444)

The supervisor owns scientific decisions and the large-scale review. It cannot
read/search the paper itself. It can ask the original configured extractor to
investigate existing entries with specialized instructions. The default
`VALIDATION_REVIEW_EXTRACTION_ATTEMPTS_PER_ENTRY=2` means the original extraction
plus one targeted follow-up, not two retries. Multi-entry calls charge each named
entry. Reconnects, episodes and provider recovery do not replenish that entitlement.
Validation remains two total scientific attempts per target/binding lineage,
subject to the same shared 16 document/64 batch extra-attempt pools.

The actual specialist input includes original values/evidence, current selected
values, exact prior requests and answers, caveats, supervisor decisions/reasons,
new instructions and attempt state. A scoped history reader supplies exact paged
bodies when needed. Validators retain supplied-context-only tools. Follow-up
extractors retain only their configured paper tools and that history reader;
full builders, finalizers and arbitrary agent dispatch are unavailable. Follow-up
uses the existing reviewer token pool, while its child budget does not consume
reviewer-turn counts. Existing deadlines and saved extractor max-turn settings apply.
Original evidence stays read-only in supplied history. The follow-up workspace
contains only newly recorded evidence, collected from verified paper-tool outputs
before immutable contributions are retained. Both specialist phases log their actual
prompt/tool configuration and bind an application-owned prompt manifest for TraceReview.

Contributions are immutable proposals. The supervisor explicitly selects fields
and evidence before they affect current values. Collections require explicit
retain/revise/merge/split/add/retire relationships from the extractor. The runtime
checks identity coverage and ancestry, never infers scientific identity from text
or array positions. Nested split collections retain predecessor ancestry. A retained
or revised child moving under a new immutable parent receives a successor identity;
its original identity and consumed validation attempts remain in that ancestry. Original
items and earlier validator answers remain auditable; retired items are not deleted.
Affected validator inputs receive new version pins; unchanged siblings remain current.
Curator edits have authority and cannot be silently overwritten by source selection.

Selected-source assessment uses the same saved binding and attempt ledger.
Merge/split successors inherit predecessor attempt depth. Episode retry pools can
use the existing document allowance, but every charge also reaches the unchanged
parent pool. A source revision never grants a fresh document/batch quota.
A no-new-information result may remain unresolved without a redundant validator
call. Assessment completion and submission readiness remain separate facts.

## Configured flows, batch and frozen benchmarks (ALL-1444)

A flow extraction step claims a durable owner/document/session/run/node fence
before invoking its extractor. Inline capture saves one FLOW result and starts the
shared validator pipeline once. Automatic, opted-out, replacement, supplemental
and profile choices compile from the saved flow into a binding allowlist. Custom
validators keep their saved revision/receipt. The review supervisor cannot dispatch
agents outside that configured set. Profile mappings must remain exactly pinned.

The step stays open until recorded selections make the current episode ready.
The next configured step, formatter and curation handoff cannot advance on prose.
Repeated calls and recovery do not rerun initial extraction or validation. An
interrupted/pending step keeps its source/episode references available for inspection.
The flow emits distinct review_pending, review_exhausted or interrupted terminal
outcomes without claiming completed output. Batch stores these per document and
counts them separately from successful and failed documents. Shared batch admission
credits each newly discovered immutable artifact once; shared retry pools stay fixed.

Frozen supervisors pin review protocols, actual tool schemas, limits and scientific
policy layers as `supervisor-review/v2`. Old or incompatible snapshots require an
explicit repin; they cannot silently adopt live review settings. No paid provider
replay is part of the focused regression suite.

### Cutover and diagnostic evidence

Apply migrations through `c1d5e6f7a8b9` with review workers drained: targeted work
kind/policy, immutable flow-step fence, batch outcome columns/enums, and the
lineage-aware episode pool. Existing charges, originals and parent limits remain
intact. These migrations are forward-only; do not delete review history to recover.
This integration branch is not a deployment or production release.

TraceReview's committed `validation_review.*` facts retain specialist context,
request/contribution/source-selection references, decisions and work outcomes.
Use their artifact/fact IDs and SHA to retrieve exact history instead of interpreting
a truncated preview. Unexpected operational failures have one sanitized Sentry
owner; scientific uncertainty, no-new-information, and expected budget stops do
not create failure alerts. Source text and raw provider exceptions must not be
added to Sentry messages.
