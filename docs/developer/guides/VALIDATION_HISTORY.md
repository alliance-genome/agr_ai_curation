# Validation history foundation (ALL-1439)

This is the first part of the ALL-1438 integration series. **Do not deploy this
runtime change independently.** The foundation captures immutable facts; it does
not install the supervisor review loop or make current materialized values
reviewed. ALL-1441 supplies selected-value projections, ALL-1442 supplies review
operations and budgets, ALL-1443/1444 integrate chat/flows, and ALL-1445 supplies
curator controls. ALL-1440 owns evidence version authority; ALL-1446 verifies the
complete cutover.

## Authority and identities

`validation_artifacts` owns the immutable typed extractor output and normalized
prevalidation envelope, source document hash, producer, owner and source scope.
Capture occurs before inline dispatch, independently of final extraction-row
persistence. A curator-authored artifact is explicitly labeled. A historical
observed artifact has no invented typed extraction or supervisor decision.

`validation_facts` contains immutable targets, shared executions, logical
attempts, completions, runner receipts, scheduling outcomes, decisions and
selection versions. Bodies are content addressed in `validation_bodies`.
PostgreSQL triggers reject updates and deletes, including deletes of archived
checkpoints. Current `validation_heads` are mutable indexes, not historical
truth. Application document/envelope deletion does not cascade into history.
Access to document-backed history still requires the existing owned source
document. Whole-document deletion policy remains a release blocker (see below).

A producer identity is unique within owner/source scope. Retries must reuse it
only for the same baseline. Each real dispatch has a distinct operation identity;
redelivery of that identity must contain identical frozen requests. Equal answers
from different attempts remain distinct facts. One deduplicated/batch execution
can cover several logical targets. Runner receipts describe an **agent execution**,
not a fabricated count of provider requests. They pin available prompt text,
model/settings, tool schemas, input and execution revision, and link the existing
runtime-history scope/trace. Callable prompt text is explicitly unavailable in
this receipt; provider request usage remains in the existing runtime/cost ledger.

The runtime passes server-assigned object and nested element IDs in sidecar
metadata. A binding target replaces positional indices with element IDs.
`reorder_element_projection` requires an explicit permutation of those IDs,
including when two payloads are identical. `retire_element_projection` updates
surviving positional aliases and nested sidecars. Existing curator remove actions
commit retirement with their checkpoint; rollback rolls back both. Never guess
an element's identity from scientific labels or equality of values.

New objects materialized by a validator are separately labeled contributions.
They are not extraction originals or accepted selections. Existing materializers
are still provisional projection writers in this integration branch. Consumer C
must replace their automatic writeback with selected-value projection, including
explicit identity maps for collection replacement/extension; unsupported collection
rewrites are rejected before the materialized envelope is returned. This foundation's reorder and
retirement APIs must be used rather than reconstructing identities from indices.

## Service contract

Use `src.lib.validation_history.runtime` at authenticated producer/runner
boundaries and `store` for structural persistence. Do not expose `store` directly
as an LLM tool. The caller supplies trusted actor identity; D must derive it from
the server runtime and enforce curator override and review-episode policies.

1. `capture_extraction` freezes a baseline and registers object/element identities.
2. `begin_jobs` / `begin_execution` commits requests, evidence, execution links and
   current target/ancestor versions **before** calling a provider. No history
   transaction or row lock spans the model call.
3. `complete_jobs` / `complete_attempt` records a real answer separately from an
   operational failure. Cancellation records interrupted. Abrupt process death
   leaves a begun attempt with no invented completion. Recovery must reconcile
   these explicitly before retrying; D owns dispatch/recovery policy.
4. `apply_decisions` uses expected-version CAS and an explicit entry per target.
   A grouped decision is all-or-nothing. It can select an exact completion or a
   nonhistorical baseline. Code validates structure and freshness, not scientific
   status, explanation words or caveats. Completed unresolved answers can be
   selected. An incomplete/failed execution cannot be accepted as an answer.
5. `read_fact` returns an immutable exact version; `list_history` paginates by
   monotonically increasing sequence using `INSPECT_RESULTS_VALIDATION_PAGE_SIZE`.
   All reads recheck owner/document/source scope. Do not read bodies by hash through
   an unauthenticated endpoint.

Execution/scheduling, completeness, scientific outcome, review and selection are
separate. Scheduling can record no validator, zero item, opt-out, inactive,
missing input, unsupported preflight or a flow skip. These do not fabricate model
attempts. Dispatch integrations record their actual outcomes; later D/F scheduling
must use the same contract for decisions those consumers introduce.

Retirement hides an item from current use without deleting facts. Restoration
appends an event on the same identity, clears selection and leaves review pending.
Replacement creates a new target and an explicit link. Retiring a parent blocks
child dispatch and acceptance. Late answers are retained; changed target lineage
prevents their acceptance even after restoration. C must apply active ancestry to
current views/exports, while pinned historical reads stay exact.

## Readers and storage

New `validator_results` entries contain version-2 descriptors, not full answers.
`read_validator_original` resolves them through authorized facts and frozen input.
Known version-1 references remain inline and unchanged in migrated envelopes;
the containing extraction must already be authorized before reading them.
Manifests label the latest attempt, never imply that latest means accepted.
Offline TraceReview reports external-body availability explicitly instead of
pretending the full answer is present in the trace.

Checkpoints archive exact envelope revisions. New checkpoints store immutable
structural deltas; repeated growing history is not copied into every archive.
Historical migration imports only the current revision it actually observed;
earlier unavailable revisions stay unavailable. Reconstruction time grows with
checkpoint ancestry. Periodic compaction/anchors are not implemented; any future
retention/compaction work must preserve every externally pinned revision and may
not delete canonical history.

## Coordinated schema/worker/reader cutover

| Component | Before cutover | Required at coordinated cutover |
| --- | --- | --- |
| Schema | Existing current envelopes and inline results | All foundation migrations applied; immutable tables/triggers/indexes present |
| Old workers | Existing runtime | Drained, or explicitly interrupted and reconciled; no mixed old/new writers |
| Historical data | Known current projections, possibly no envelope | Explicit import completed or source reported for manual reconciliation |
| New producers | This branch records baseline and input facts | All A–G producer paths and supervisor review enforced; H verifies |
| Exact readers | Old inline bodies/current revisions | Version-2 authorized readers and archived revisions deployed together |
| Current consumers | Provisional automatic materialization | C accepted-selection projection and retirement filtering |
| TraceReview | Inline originals | External-reference-aware analyzer deployed with runtime |

After draining workers, run in the migrated backend environment:

```sh
python -m src.lib.validation_history.migration --workers-drained --all
# Or reconcile a specific source:
python -m src.lib.validation_history.migration --workers-drained --extraction UUID
python -m src.lib.validation_history.migration --workers-drained --envelope ENVELOPE_ID
```

The command stops on unsupported or ownerless data, prints successful source IDs,
and is safe to rerun. It never executes validators. Historical source payloads
are retained unchanged. Sources without an envelope are first normalized using
the existing historical normalizer and then imported with original unavailable.
No old result is expanded into an invented attempt count or decision.

There is no review-off switch or old-worker compatibility fallback. Keep the
series unreleased until H passes. Recovery is forward-only: stop incompatible
workers, repair/retry failed imports and roll forward the coherent application.
Downgrades that destroy immutable history are deliberately rejected. Database
backup/restore procedures remain required before the eventual production cutover.

## Verification

Real PostgreSQL fixtures in `test_validation_history.py` cover concurrent and
conflicting completions, request mutation, source ownership, grouped CAS,
retirement/restore/replacement, identical-element reorder, existing curator
remove rollback, custom-flow success/failure/cancellation, exact checkpoint
reads, historical imports and checkpoint growth. The existing inline persistence,
flow checkpoint and manual-profile persistence tests exercise shared integrations.
Unit fixtures replace only history I/O boundaries where a test isolates dispatch
or materialization; they are not evidence of durable storage by themselves.

## Foundation review clarifications

- Review CAS `version` is separate from `lifecycle_generation` and
  `selection_generation`. Deferral/rejection of another alternative does not
  consume or stale an unchanged answer. Retire/restore/replace/source edits
  invalidate affected input ancestry. Prerequisite pins name an exact accepted
  selection and its selected-input generation; its transitive inputs must still
  be current. Review never triggers an implicit model call.
- Pack bindings may declare `decision_units` (key plus result-field names).
  Profile mappings declare groups using their output slot names. Groups must
  partition all outputs without overlap. A group is indivisible; no declaration
  means the entire binding is one unit. No scientific independence is inferred
  from field names. One completion can cover several independent unit heads.
- Attempts freeze declared coverage, source ancestry, typed prerequisite pins,
  dispatch wave and available earlier completion IDs. Available provisional
  wave results are **not** accepted prerequisites. D/E/F populate explicit
  selected-prerequisite inputs at their review boundary. Budget scope IDs are
  derived from artifact/target, not LLM-supplied strings; D owns budget charging.
- Existing curator identity edits produce new target IDs, replacement links and
  new descendant identities in the same transaction as their checkpoint.
  Removed fields/items are retired. Parent input generations are invalidated
  conservatively. List reordering rebases both field and element sidecars.
- Materialization rejects whole-list/whole-element rewrites without an explicit
  identity map before returning the new envelope. Declared indexed descendant
  writes preserve identity. This provisional restriction is replaced by C's
  explicit contribution/selection projection contract.
- Schema-invalid model output is retained as an incomplete answer with its raw
  body. Provider failures have no fabricated answer. A failed pre-call receipt
  write is an operational unknown state, not a scientific failure. Sentry uses
  fixed-message exceptions with severed chains; sensitive SQL and provider text
  are excluded.
- Result descriptors do not cache mutable review status. Readers report
  `requires_history_read`; exact decisions remain in history. Trace summaries
  give a follow-up count without falsely claiming an empty question list.
- Owner identity is the authorization boundary. Document-backed artifacts also
  require current document ownership. Source session IDs partition immutable
  identities and are checked exactly, but are not independent user ACLs.
  Authenticated document-less chat uses a null document source, not a fake PDF.
- `VALIDATION_HISTORY_PAGE_SIZE` controls history facts per page (default 5).
  Capture registers all fields/elements in one transaction rather than one
  authorization/transaction per element.
- Drain workers **before Alembic** and the maintenance import. The importer can
  archive a missing current revision as historically observed without inventing
  earlier revisions. Runtime delta writes verify their predecessor hash.
- The executable transition fixture is
  `backend/tests/fixtures/validation_history/transitions.json` and is replayed
  against PostgreSQL for downstream implementers.

Whole-document deletion retention is awaiting an explicit product decision;
annotation retirement approval must not be treated as that decision. Do not
release this integration series while that policy is unresolved.
