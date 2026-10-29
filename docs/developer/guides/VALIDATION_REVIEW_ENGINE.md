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
review decisions. Episode-wide deadline, tool and turn stops still stop the run. The supervisor's final prose cannot override ledger readiness.

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

## Context capacity and actual usage

Ordinary review work does not have a cumulative token completion ceiling and does
not reserve a model's hypothetical maximum output. The shared context-capacity
path checks actual request fit, preserves full archived source history, and uses
native or standalone OpenAI compaction as needed. The compaction threshold is a
context-management trigger, not a maximum request size or lifetime token budget.
Provider output settings are unchanged by review accounting.

Each outgoing provider operation is registered durably, then its reported input
and output tokens are aggregated once across its episode and parent scopes.
Native Responses usage and standalone compaction both pass through measurement.
Usage without a terminal report stays explicitly uncertain; it is not reported as
zero cost. Status exposes reported tokens, pending calls and uncertain calls
separately from remaining structural quotas. Compaction never resets usage,
semantic attempts, deadlines or scientific history. Provider usage is not itself
a dollar amount; any explicit operator spending policy is separate.

The pinned WebSocket adapter does not replay a frame after sending begins. HTTP
clients must have internal retries disabled; SDK retries are separately measured.
Only the named reviewer consumes reviewer-turn allowances. Standalone compaction
does not consume a semantic attempt or reviewer turn, but retains review ownership,
cancellation and the fixed deadline. Unsupported direct request shapes remain
explicit errors. No transport downgrade is introduced.

Migration e3f7a9b1c2d4 requires drained review workers. It archives complete original
scope/episode rows in an immutable transition table before retiring only token
policy/allowance fields. Existing charges, reservations, coverage facts, attempt
counts, deadlines and scientific history remain intact. It neither resumes
uncertain work nor grants another round. Existing old provider reservations can
still reconcile their actual usage. Historical token coverage credits are retained
as evidence and no longer interpreted as quotas.

## Limits and exact reads

Structural settings remain documented in `.env.example` and forwarded to backend,
benchmark worker and tests: 2 attempts/target, 2 extraction attempts per entry,
16 extra attempts/document operation and 64/batch. Reviewer model calls, tools and
whole-review deadlines are uncapped by default: leave `VALIDATION_REVIEW_REVIEWER_TURNS`,
`VALIDATION_REVIEW_REVIEWER_TOOLS` and `VALIDATION_REVIEW_DEADLINE_SECONDS` blank.
Positive values opt into operator caps. Existing frozen integer policies retain
those caps and their consumed allowance; changing defaults does not refill them. Structural quota warnings remain at 75%/90%. The former
reviewer/initial/reconsideration token ceiling settings are retired. Explicit
curator renewal preserves previous history. Extractor/formatter limits are unchanged.

Protocol transport defaults are 8000 reason/instruction characters, 100 decision
entries, 100 evidence references and 64KiB serialized history pages. Oversized
inputs are rejected, not truncated. Grouped review and escaped/non-ASCII multi-page
fixtures exercise these contracts. The delivery lease defaults to 60 seconds.

Packets pin original extraction, complete fact references, decisions, prerequisites
and budget snapshots. `read_validation_history` returns canonical ASCII JSON byte
ranges with exact SHA and continuation offset; concatenate all content pages.
`fact_id=baseline` reads the bound artifact's immutable original. Paging has no
separate total-data cap and never truncates the stored body. General reviewer tool allowances and the fixed deadline still apply during an
active review; provider usage is metered without a cumulative token ceiling.

Manifest units expose their decision-unit metadata before selection. A null
`decision_unit` or empty configured `decision_units` means whole-target review:
use the frozen attempt's `input.target_id` for both `target_id` and
`decision_unit_id`. For component bindings, keep the binding parent as `target_id`
and select the child `decision_unit_id` from the attempt's `coverage`. The manifest
unit's `target_id` identifies that unit and its `version` supplies `expected_version`.
`logical_attempt_id` is the attempt reference's fact ID, not the completion's ID.
These identifiers route an explicit scientific decision; they never choose its action.
Pending
answer blockers carry exact `attempt_ref` and `completion_ref` fact IDs/SHAs;
unfinished execution carries only `attempt_ref`. These references follow the
existing episode/retained-review coverage and do not choose an answer or change
readiness. They let the supervisor navigate directly to the request and answer
without joining the full audit index itself. Previously pinned packets remain
immutable; a new packet reflects the current state and these reference fields.

`get_validation_record_source(target_id)` pins one original normalized record and
its attached supporting evidence. Object, nested field/element and binding targets
resolve to the same object source reference; they cannot select another owner or
artifact. Read the returned fact through `read_validation_history`, including all
continuation pages. The view records the baseline SHA, object-target SHA, source
document, provenance digest and explicit historical/original-availability status.
It is not a current selected value, validator answer or reconstructed typed source.
Later contributions without a baseline original must be inspected through their
exact contribution facts. Retired original records remain readable for audit.

The supervisor chooses which records and answers are relevant. The full immutable
baseline, typed source when available, and other history remain accessible when
needed. Per-record access does not remove old validation findings, summarize away
evidence, choose scientific relevance in code, or grant paper-reading powers.
The locked review protocol encourages incremental assessment and recording of
supported decisions. Runtime compaction manages working context; the supervisor
must not invent a context allowance from anticipated transcript growth. It can
switch an unnecessary broad read to complete scoped source views while retaining
access to the full baseline. Partial reads never establish absence or completeness,
and details needed after compaction must be recovered through exact saved references.
Actual runtime admission, deadline and capacity stops still apply. This does not
add automatic review restarts or override a scientific needs-review judgment.

Changes to this locked protocol or the generated review-tool contract invalidate
previously frozen supervisor snapshots. Before executing an unstarted benchmark
or calibration case on an upgraded runtime, freeze its supervisor contract again
through the normal staging path. Keep completed runs and their usage receipts;
do not weaken exact-contract checks or replay completed work to refresh a pin.

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
Each watcher owns a Redis client on its current event loop, reuses it across
polls, and joins connection cleanup before the loop can close. This prevents
short-lived batch document loops from borrowing or contaminating the HTTP
loop's shared connection pool. An unreadable stop signal is an operational
failure, reported through the existing sanitized review reporter; it is not
interpreted as permission to continue. Batch Cancel remains enforced by its
persisted status and document checkpoints; this connection lifecycle does not
add immediate interruption of in-flight batch provider calls.

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

Historical extraction-only units without an attested original report
`historical_review_unknown`; the review tools do not offer baseline decisions
or automatic continuation for those units. Saved historical data remains intact.
Fresh captures without a supported saved extractor configuration also do not
receive automatic continuation for targeted extraction or validation of an
extraction contribution. Independent validation reconsideration remains available.


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
usage is recorded in the reviewer token category without a token ceiling; its
child budget does not consume reviewer-turn counts. Existing deadlines and saved extractor max-turn settings apply.
Original evidence stays read-only in supplied history. The follow-up workspace
contains only newly recorded evidence, collected from verified paper-tool outputs
before immutable contributions are retained. Both specialist phases log their actual
prompt/tool configuration and bind an application-owned prompt manifest for TraceReview.

The targeted provider output uses a separate strict transport schema. Arbitrary
proposed values travel as `value_json`, containing one complete JSON value (including
JSON quotes for strings). The phase decodes this once into native values before
retaining contributions. Duplicate keys, malformed JSON and nonfinite numbers fail
explicitly through the existing operational-error owner; no legacy shape or text
fallback is accepted. Stored proposals and scientific values keep their canonical
shape. The encoding instructions live in the output schema, leaving the saved
extractor's pinned follow-up protocol unchanged.

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

A compound identity can live at the object root or inside a nested value. Its
internal root address is empty; external field-edit paths must still name a field.
Scoped revalidation authorizes the complete declared identity, including unchanged
identifier/name members, rather than all fields below its container or only the
changed leaves. Membership comes from the exact edit event and immutable checkpoint
identity audit. Match the unique audit for the event's patch within that revision's
appended audits: one save can edit several identities, and client patch IDs can
repeat across revisions. Missing or ambiguous historical evidence fails explicitly; no
current package definition or inferred value similarity replaces that evidence.
Only a fresh accepted matching validation can release the specifically authorized
override version; unrelated fields, rejected/incomplete answers and later curator
edits retain their authority. Mirror writes remain part of the source identity's
atomic edit. Copied mirror values do not establish authority to revalidate their source.
Saving, withdrawing or restoring an identity does not itself dispatch validation.

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

Retiring the cumulative token fields changes the frozen benchmark review contract.
Existing frozen supervisor benchmark sources must be explicitly repinned before
replay; they are rejected by contract comparison rather than silently adopting
the new policy. The migration does not edit those scientific snapshots.

### Incomplete-round lineage

An explicit workspace revalidation derives prior review coverage from immutable
history, not the envelope publication pointer. A review that records no decisions
may never publish an envelope, but it still remains part of the review history.
Coverage can reference older historical episodes without runtime admission; a
new runtime round separately links to its last admitted predecessor. Neither
relationship creates acceptance or changes selected scientific values.

Preparation uses the new episode fact sequence as its recovery boundary, including
abandonment and admission facts. Later facts cannot change that round’s derived
predecessor on retry. An already admitted round retains its recorded predecessor,
policy, charges and deadline when resumed. Only an explicit curator request may
create a new round after an admitted review; automatic bootstrap cannot claim
curator renewal.


## Completed assessments with unresolved extraction fields

`record_validation_review` can accept an extraction-only assessment with optional
`unresolved_fields`, each containing an exact plain payload `field_path` and an
`explanation`. The supervisor chooses the uncertainty; code validates ownership,
field existence/type and selection version, then binds the field to stable history
anchors. Structured resolvable identities keep their validator-owned contract.
Missing executions, unreviewed records and pending investigation remain incomplete.

The immutable accepting decision owns the assessment. Its current selected
projection derives `validation_review.assessment_unresolved` findings, preserving
actor, decision, selection and source provenance without changing extracted data.
Review may complete and export its requested TSV columns: nonempty plain cells use
`value (unresolved)`, explicitly uncertain empty cells use `UNRESOLVED`, and unflagged
blanks and ordinary structural missing-field findings retain the requested missing-value display. Unsettled findings block external
submission. They do not create a new automatic-retry or unfinished-flow recovery UI.

In the existing curator field editor, **Save and confirm** explicitly acknowledges
an uncertain field even when its value is unchanged. Changed values or explicit
confirmation create durable curator overlays and settle that field's finding.
Collection editors show each affected item and explicitly confirm the listed
uncertainties using individual leaf anchors. Ordinary collection edits settle
only changed assessed leaves; unchanged siblings remain uncertain. Size-changing
collection edits with open assessments require explicit confirmation because
positions alone cannot identify which item changed. Active curator authority is
preserved by subsequent edits even if the review assessment is later revoked.
Opening the editor, saving an unchanged value without confirmation, or editing a
neighbor does not settle it. Rebuilding selected output replays curator authority;
new machine judgments cannot silently override it. TraceReview exposes the explicit
review input/result and finding provenance.

Typed `review_pending`, `review_exhausted` and `interrupted` flow outcomes retain
their reasons through persistence and replay. They do not become generic server
failures or imply a completed export. Genuine provider and persistence failures
retain their reporting owner and cannot be hidden by a later review-stop event.

### Work ownership and cancellation

The inner work heartbeat renews delivery ownership independently of scientific
packet construction. It locks only its work row, verifies the exact token and
unexpired claim, and reads episode status without taking scope, artifact, or
episode write locks. It neither reopens an episode nor grants scientific authority.
Provider admission still locks scope, artifact, episode, then work in that order,
and atomically rejects stale work before committing its reservation. Both validator
and targeted-extraction executors renew ownership while submitting claimed work.

Cancellation cancels queued validators and drains running validator threads before
their accounting owner exits. A cooperative stop prevents subsequent provider
admissions; already-running calls retain their usage and exact late answers.
An in-flight reservation transaction is also drained before work is classified.
If cancellation prevents the reservation from ever returning to its caller, no
provider request was sent: reconcile zero tokens while retaining the admitted
turn charge. Reconciliation of genuinely sent requests remains allowed after
ownership loss. No lease extension changes a frozen scientific deadline or grants
another attempt. The frontend Stop response remains immediate; backend cleanup
can wait for the current provider invocation to finish.

### Unsupported configured lookups

A configured preflight refusal is a reviewable `validation_source_input` fact,
not a validator answer or absent biological data. Its `unsupported_preflight`
discriminator records the policy and explanation alongside the selected input.
Initial dispatch, selected-source rebuilding and dependent waves all apply the
same preflight before scheduling a provider call. Supported requests still run.

The supervisor reads the existing `applicability_ref` and can accept the
limitation only with `absence_assessment="unresolved"`. This completes review
while retaining field-level uncertainty; it does not confirm an identity or
waive submission requirements. `omission_supported` and `acknowledge_failure`
cannot finalize an unsupported lookup. Exact fact/hash, current lineage and
prerequisite checks apply just as they do to optional-input applicability.
Source changes are reevaluated; no synthetic completion or historical conversion
is created. TraceReview retains the fact content and explicit decision.
