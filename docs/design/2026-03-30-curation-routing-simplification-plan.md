# Curation Routing Simplification Plan

**Date:** 2026-03-30
**Status:** Draft
**Audience:** Humans and coding agents implementing the next curation/evidence cleanup wave
**Implementation doctrine:** Forward-only development. No compatibility shims. No fallback logic. No legacy-preservation layer.

## Goal

Replace the current extraction-to-curation routing model with a simpler, package-driven contract:

- `agent_key` tells us who produced the result.
- `adapter_key` tells us which curation adapter owns the result.

The shared curation substrate should stop carrying `domain_key` and `profile_key` as first-class routing concepts.

## Why This Change Is Needed

The current pipeline asks the same question several times in different ways:

- extraction persistence infers `domain_key` from `agent_key`
- prep preview blocks on missing `adapter_key`
- prep mapping sometimes falls back from `domain_key` to `adapter_key`
- bootstrap only works if a persisted prep result already exists
- frontend launch code passes `adapter_keys`, `profile_keys`, and `domain_keys`

That creates avoidable complexity and directly caused the current bug:

- chat extraction knew the result was gene-related
- prep refused to continue because `adapter_key` was missing
- review launch refused to continue because prep had not been materialized yet

The system already had enough information to proceed, but the routing contract was split across too many fields and too many phases.

## Design Principles

1. One routing truth: shared routing is `adapter_key`, not `domain_key`.
2. `agent_key` is provenance, not routing.
3. Package-owned agent metadata defines curation routing. Core Python must not infer Alliance-specific targets from naming conventions.
4. Complex workflows are handled by explicit composition agents, not by adding more shared scope dimensions.
5. `Review & Curate` should mean `ensure prep exists, then open review`.
6. The substrate stays project-agnostic. Alliance-specific extractors and adapters live in package-owned definitions and exports.
7. If a result cannot be routed because package metadata is incomplete, fail fast. Do not guess.

## Target State

### Shared persisted extraction contract

Persisted extraction records used by prep and review launch should keep:

- `document_id`
- `agent_key`
- `adapter_key`
- `source_kind`
- `origin_session_id`
- `trace_id`
- `flow_run_id`
- `user_id`
- `candidate_count`
- `conversation_summary`
- `payload_json`
- `metadata`

Shared extraction records should not keep:

- `domain_key`
- `profile_key`

`source_kind` stays intentionally. It is execution-surface provenance and replay scoping metadata
such as `CHAT` versus `FLOW`. It is not a curation-target routing dimension and should not be
expanded into one.

### Shared prep and bootstrap contract

Prep and bootstrap selection should use:

- `document_id`
- `origin_session_id`
- `flow_run_id`
- `adapter_key`

Prep and bootstrap selection should not use:

- `domain_key`
- `profile_key`

### Package-owned agent routing contract

Each agent that can emit launchable curation extraction results should declare its curation target in `agent.yaml`.

Proposed shape:

```yaml
curation:
  adapter_key: gene
  launchable: true
```

Rules:

- `adapter_key` is required for any agent whose extraction results can feed prep/review.
- `launchable: true` means the extraction results can be prepared directly into a review session.
- `launchable: false` means the agent is an upstream/helper extractor and must feed a composition step before review launch.
- The runtime stamps persisted extraction results with this adapter key.
- The runtime does not infer adapter routing from the agent name.
- The runtime does not read adapter routing from LLM output as the primary source of truth.

### Complex future workflows

For cases like disease association:

- helper extractors may emit supporting results
- a dedicated composition or association agent emits the launchable result
- that composition agent declares the final `adapter_key`

Example:

- `disease_extractor` -> `launchable: false`
- `allele_extractor` -> `launchable: false`
- `phenotype_extractor` -> `launchable: false`
- `disease_association_builder` -> `adapter_key: disease_association`, `launchable: true`

Core code should not try to deduce this relationship from arbitrary combinations of helper extractors.

## Current Pressure Points

| Area | Problem | Files |
|------|---------|-------|
| Agent metadata | No package-declared curation target. Core code compensates with inference and duplicated scope logic. | `backend/src/lib/config/agent_loader.py`, `backend/src/lib/agent_studio/registry_builder.py`, `backend/src/lib/agent_studio/catalog_service.py`, `packages/alliance/agents/*/agent.yaml`, `config/agents/README.md` |
| Extraction persistence | `domain_key` is inferred from `agent_key`; `adapter_key` is optional; payload-level routing is partially supported. | `backend/src/lib/curation_workspace/extraction_results.py`, `backend/src/api/chat.py`, `backend/src/schemas/curation_workspace.py`, `backend/src/lib/curation_workspace/models.py` |
| Prep preview and run | Shared scope is split into adapter/profile/domain arrays; preview blocks even when routing is already obvious. | `backend/src/lib/curation_workspace/curation_prep_invocation.py`, `backend/src/schemas/curation_prep.py`, `frontend/src/features/curation/services/curationPrepService.ts`, `frontend/src/features/curation/components/PrepScopeConfirmationDialog.tsx`, `frontend/src/components/Chat.tsx` |
| Prep mapping | One function treats `domain_key` as adapter fallback, another does not; shared routing is internally inconsistent. | `backend/src/lib/curation_workspace/curation_prep_service.py` |
| Bootstrap | Review launch only replays persisted `curation_prep` results and does not auto-materialize prep from chat extraction results. | `backend/src/lib/curation_workspace/bootstrap_service.py`, `frontend/src/features/curation/navigation/openCurationWorkspace.ts`, `frontend/src/components/Chat.tsx` |
| Supervisor and flows | Confirmation and narrowing logic revolve around adapter/profile/domain scope triples. | `backend/src/lib/openai_agents/agents/supervisor_agent.py`, `config/agents/supervisor/prompt.yaml`, `backend/src/lib/flows/executor.py` |
| Inventory and API surface | Query params, saved views, filters, and frontend types carry domain/profile even when adapter is the real routing key. | `backend/src/api/curation_workspace.py`, `backend/src/lib/curation_workspace/session_service.py`, `backend/src/schemas/curation_workspace.py`, `frontend/src/features/curation/types.ts`, `frontend/src/features/curation/services/curationSessionQueryParams.ts`, inventory components/tests |
| Adapter plug-in boundary | Core pipeline and export registry still hardcode the reference adapter directly. | `backend/src/lib/curation_workspace/pipeline.py`, `backend/src/lib/curation_workspace/export_adapters/registry.py`, `backend/src/lib/curation_adapters/reference/*`, `backend/src/lib/packages/models.py` |

## Implementation Rules

These rules are intentional and should be enforced during implementation review.

- Do not add helpers like `effective_domain_key`, `effective_scope`, or similar compatibility layers.
- Do not keep `domain_key` in shared schemas "for now."
- Do not keep `profile_key` in shared routing contracts "for now."
- Do not infer adapter routing from `agent_key` naming conventions.
- Do not let the LLM decide the routing target unless the runtime explicitly declares that mode. This plan does not use that mode.
- Do not hardcode Alliance adapter keys in core pipeline code.
- If a package-owned agent that should feed curation lacks `curation.adapter_key`, fail loudly during startup or persistence.

## Deployment Sequencing Rule

This refactor removes fields and code paths, but destructive schema removal must still be sequenced
carefully.

- Stop writing `domain_key` and `profile_key` before dropping their columns.
- Remove all runtime reads of `domain_key` and `profile_key` before dropping their columns.
- If this work spans multiple PRs, the Alembic migration that drops columns must be the final PR in
  the sequence, not the first.
- If this work lands in one branch/PR, treat the drop migration as the last implementation step in
  that branch after all code references are gone.

This is not a compatibility requirement. It is simply the safe ordering for a destructive forward-only change.

## Proposed Implementation Phases

## Phase 1: Introduce package-driven curation routing metadata

**Goal:** make package metadata, not naming inference, the routing source of truth.

**Files to update**

- `backend/src/lib/config/agent_loader.py`
- `backend/src/lib/agent_studio/registry_builder.py`
- `backend/src/lib/agent_studio/catalog_service.py`
- `backend/src/lib/agent_studio/registry_types.py`
- `config/agents/README.md`
- `packages/alliance/agents/README.md`
- relevant `packages/alliance/agents/*/agent.yaml`

- [ ] Add a `curation` block to `AgentDefinition`.
- [ ] Parse `curation.adapter_key` and `curation.launchable` from `agent.yaml`.
- [ ] Surface that metadata through registry/catalog APIs so runtime orchestration can read it by `agent_key`.
- [ ] Update agent documentation to require `curation.adapter_key` for any curation-producing agent.
- [ ] Add `curation.adapter_key` to the Alliance extractor bundles that should route directly to review.
- [ ] Mark helper or future composition-oriented extractors explicitly as `launchable: false` if needed.
- [ ] Add loader and metadata unit tests proving the new fields are parsed and exposed.

**Notes for implementers**

- Keep this generic. Do not special-case `gene_extractor`, `disease_extractor`, or Alliance package IDs in core code.
- If an agent is not a curation-producing agent, it does not need a `curation` block.

## Phase 2: Make `adapter_key` mandatory on persisted extraction results

**Goal:** stop persisting shared extraction records that rely on `domain_key` inference.

**Files to update**

- `backend/src/lib/curation_workspace/extraction_results.py`
- `backend/src/api/chat.py`
- `backend/src/lib/flows/executor.py`
- `backend/src/schemas/curation_workspace.py`
- `backend/src/lib/curation_workspace/models.py`
- new Alembic migration under `backend/alembic/versions/`

- [ ] Remove `_infer_domain_key_from_agent_key`.
- [ ] Remove `domain_key` and `profile_key` from `ExtractionEnvelopeCandidate`.
- [ ] Stop reading routing metadata from envelope payloads as the primary persistence contract.
- [ ] Resolve `adapter_key` from package-owned agent metadata when persisting agent-produced extraction results.
- [ ] Keep explicit pipeline-owned `adapter_key` for non-agent producers such as `curation_prep`.
- [ ] Make `adapter_key` required for all persisted extraction results.
- [ ] Update `CurationExtractionPersistenceRequest` so `adapter_key` is required.
- [ ] Delete `domain_key` and `profile_key` from `CurationExtractionPersistenceRequest` and `CurationExtractionResultRecord`.
- [ ] Add an Alembic migration that drops `domain_key` and `profile_key` from `extraction_results` only after all readers are removed.
- [ ] Update persistence tests to assert missing `adapter_key` is an error, not something the runtime infers.

**Notes for implementers**

- This phase should leave persisted extraction routing fully deterministic.
- If startup validation is easier than persistence-time failure for missing package metadata, add startup validation too.

## Phase 3: Simplify prep contracts to adapter-only scope

**Goal:** remove redundant scope dimensions from prep preview and prep execution.

**Files to update**

- `backend/src/schemas/curation_prep.py`
- `backend/src/lib/curation_workspace/curation_prep_invocation.py`
- `backend/src/lib/curation_workspace/curation_prep_service.py`
- `frontend/src/features/curation/services/curationPrepService.ts`
- `frontend/src/features/curation/components/PrepScopeConfirmationDialog.tsx`
- `frontend/src/components/Chat.tsx`

- [ ] Remove `domain_keys` and `profile_keys` from prep preview, prep run request, prep run response, and `CurationPrepScopeConfirmation`.
- [ ] Make prep preview summarize adapter scope only.
- [ ] Make prep preview ready when there is exactly one launchable adapter in the current chat extraction context.
- [ ] Make prep preview block only for real reasons:
  - no extraction results
  - zero candidates
  - multiple launchable adapters with no user choice
- [ ] Remove adapter/domain fallback logic from prep service and replace it with direct adapter matching.
- [ ] Remove `profile_key` propagation from prep candidates unless it is moved into adapter-owned payload or metadata.
- [ ] Update prep UI to show only adapter choices when disambiguation is needed.
- [ ] Update tests so the gene case is a one-adapter happy path with no domain/profile scope.

**Notes for implementers**

- The prep dialog can remain as an explicit action, but it should no longer teach the curator about internal scope dimensions the system can already resolve.

## Phase 4: Change review launch to "ensure prep then open"

**Goal:** clicking `Review & Curate` should work from extraction results, not only from pre-existing persisted prep rows.

**Files to update**

- `backend/src/lib/curation_workspace/bootstrap_service.py`
- `frontend/src/features/curation/navigation/openCurationWorkspace.ts`
- `frontend/src/components/Chat.tsx`
- `frontend/src/features/curation/components/ReviewAndCurateButton.tsx`

- [ ] Replace the current bootstrap assumption "prep must already exist" with "ensure prep exists."
- [ ] When launch is requested:
  - resolve the target adapter
  - find an existing reusable session first
  - if needed, find a persisted prep result for that adapter
  - if none exists, materialize prep from raw extraction results for that adapter
  - then bootstrap the review session
- [ ] Remove `domain_key` and `profile_key` from `CurationDocumentBootstrapRequest`.
- [ ] Remove `domainKeys` and `profileKeys` from `CurationWorkspaceLaunchTarget`.
- [ ] Update the evidence-card `Review & Curate` path to pass only `documentId`, `originSessionId`, and `adapterKeys`.
- [ ] Make the top-level chat `Prepare for Curation` button reuse the same adapter-only launch logic or remain an explicit prep-only action with the same routing contract.

**Notes for implementers**

- The simplest user mental model is: extraction exists, so review can start.
- Prep remains an internal stage, but it should not be a brittle prerequisite that the user must manually satisfy in the common path.

## Phase 5: Collapse supervisor and flow confirmation to adapter-only scope

**Goal:** remove the three-axis scope confirmation model from shared orchestration.

**Files to update**

- `backend/src/lib/openai_agents/agents/supervisor_agent.py`
- `config/agents/supervisor/prompt.yaml`
- `backend/src/lib/flows/executor.py`
- related supervisor and flow tests

- [ ] Remove `domain_keys` and `profile_keys` from supervisor prep confirmation contracts.
- [ ] Narrow and confirm scope using `adapter_keys` only.
- [ ] Update the supervisor tool description so it asks for adapter confirmation only when multiple launchable adapters exist.
- [ ] Remove domain/profile scope summary generation from flows.
- [ ] Keep the multi-extractor future path by requiring a composition agent to emit the final adapter target instead of asking the supervisor to guess.

**Notes for implementers**

- This is where the future disease-association path gets simpler: helper extractors do not become launch targets; the composition result does.

## Phase 6: Remove `domain_key` and `profile_key` from shared inventory/query contracts

**Goal:** stop exposing deleted routing concepts through shared APIs and saved views.

**Files to update**

- `backend/src/schemas/curation_workspace.py`
- `backend/src/api/curation_workspace.py`
- `backend/src/lib/curation_workspace/session_service.py`
- `frontend/src/features/curation/types.ts`
- `frontend/src/features/curation/services/curationSessionQueryParams.ts`
- `frontend/src/features/curation/inventory/*`
- saved-view tests and fixtures

- [ ] Remove `domain_keys` and `profile_keys` from `CurationSessionFilters`.
- [ ] Remove related query params from the curation workspace API.
- [ ] Remove domain/profile filtering logic from session queries.
- [ ] Remove domain/profile inventory filter chips, saved-view state, and query-string generation on the frontend.
- [ ] Delete or explicitly invalidate saved views that store deleted filter fields.
- [ ] Update inventory tests to filter by `adapter_key` only.

**Notes for implementers**

- Because this is forward-only development, do not silently reinterpret legacy saved views.
- No saved-view migration UX is required for this cleanup.
- If old saved views depend on deleted fields, they can simply be dropped or invalidated as part of
  the change.

## Phase 7: Remove core hardcoding of specific curation adapters

**Goal:** keep the substrate package-driven and project-agnostic.

**Files to update**

- `backend/src/lib/packages/models.py`
- new package-loader support for curation adapter exports
- `backend/src/lib/curation_workspace/pipeline.py`
- `backend/src/lib/curation_workspace/export_adapters/registry.py`
- `backend/src/lib/curation_adapters/reference/*`
- package manifests and adapter export files

- [ ] Add a package-owned export mechanism for curation adapters.
- [ ] Define a generic adapter registration contract for:
  - candidate normalizer
  - export adapter
  - optional field layout / validation / evidence hooks
- [ ] Remove the hardcoded `REFERENCE_ADAPTER_KEY` registry construction from core pipeline code.
- [ ] Move the reference adapter registration behind the new package export mechanism.
- [ ] Keep the core pipeline ignorant of Alliance-specific adapter names.

**Proposed direction**

This phase should get its own short follow-on design note before code starts if the team wants to
split delivery. A concrete starting sketch is:

- add `ExportKind.CURATION_ADAPTER`
- allow package manifests to export a python-callable registration entry such as
  `python/src/<package>/curation_adapters.py:register`
- let that callable register one or more adapter-owned components with the runtime:
  - candidate normalizer
  - export adapter
  - optional field layout / validation / evidence hooks

The exact API can still be refined, but implementation should not start this phase without choosing
one concrete package contract first.

## Phase 8: Clean up remaining adapter-owned metadata

**Goal:** ensure anything not shared by all curation projects becomes adapter-owned rather than substrate-owned.

**Files to inspect during implementation**

- `backend/src/lib/curation_workspace/session_service.py`
- `backend/src/lib/curation_workspace/pipeline.py`
- `frontend/src/pages/CurationWorkspacePage.tsx`
- `frontend/src/features/curation/editor/ManualAnnotationDialog.tsx`
- adapter-specific normalizer/field-layout code

- [ ] Remove any remaining shared `profile_key` fields from prepared session, candidate, and manual annotation contracts.
- [ ] If an adapter needs variants or sub-modes, move them into adapter-owned payloads or adapter-owned metadata.
- [ ] Update workspace UI to read adapter-owned metadata through adapter registries rather than shared profile columns.

**Notes for implementers**

- This phase is where the "only `agent_key` and `adapter_key`" rule becomes fully true across the shared substrate.
- If this phase is too large for one PR, it can be split, but the target state should remain unchanged.
- Current inspection shows the reference adapter implementation under `backend/src/lib/curation_adapters/reference/`
  does not branch on `profile_key`; current `profile_key` usage is generic transport, filtering, and
  display plumbing rather than adapter-specific semantics.
- That means no semantic migration is planned for old `profile_key` values. Existing sessions that
  still depend on shared `profile_key` behavior may be invalidated or recreated as part of this
  forward-only cleanup.

## Concrete Delete List

These are the main things that should disappear by the end of the work:

- `_infer_domain_key_from_agent_key()` in `backend/src/lib/curation_workspace/extraction_results.py`
- `enrich_extraction_result_scope()` backfilling inferred scope in `backend/src/lib/curation_workspace/extraction_results.py`
- `domain_key` on persisted extraction-result records
- `profile_key` on persisted extraction-result records
- `_resolve_candidate_adapter_key()` preferring `domain_key` over `adapter_key` in `backend/src/lib/curation_workspace/curation_prep_service.py`
- prep-level unscoped fallback in `_filter_extraction_results_for_scope()` in `backend/src/lib/curation_workspace/curation_prep_service.py`
- supervisor-level unscoped fallback in `_filter_extraction_results_for_scope()` in `backend/src/lib/openai_agents/agents/supervisor_agent.py`
- `domain_keys` from prep preview/run contracts
- `profile_keys` from prep preview/run contracts
- bootstrap recovering adapter ownership from prep candidate payloads in `_resolved_adapter_key()` in `backend/src/lib/curation_workspace/bootstrap_service.py`
- `domain_key` from bootstrap request/availability selection
- `profile_key` from bootstrap request/availability selection
- `domain_keys` and `profile_keys` from shared inventory filters
- supervisor prep confirmation text that asks for domain/profile scope
- `PassthroughCandidateNormalizer` as the default normalization path in `backend/src/lib/curation_workspace/pipeline.py`
- hardcoded reference-adapter registry construction in core workspace pipeline code

## Test Plan

At minimum, implementation should update and run targeted tests in these areas:

- backend unit:
  - `tests/unit/lib/curation_workspace/test_extraction_results.py`
  - `tests/unit/lib/curation_workspace/test_curation_prep_invocation.py`
  - `tests/unit/lib/curation_workspace/test_curation_prep_service.py`
  - `tests/unit/lib/curation_workspace/test_bootstrap_service.py`
  - `tests/unit/lib/curation_workspace/test_session_service.py`
  - `tests/unit/lib/flows/test_executor.py`
  - `tests/unit/lib/openai_agents/agents/test_supervisor_agent_runtime.py`
  - package/agent-loader tests for new `curation` metadata
- frontend unit:
  - `src/test/components/Chat.test.tsx`
  - `src/features/curation/navigation/openCurationWorkspace.test.ts`
  - `src/features/curation/components/PrepScopeConfirmationDialog.test.tsx`
  - inventory filter/query tests
- integration:
  - curation workspace sessions API
  - evidence pipeline
  - curation submission e2e where shared routing fields changed

Recommended commands:

```bash
docker compose -f docker-compose.test.yml run --rm backend-unit-tests
docker compose -f docker-compose.test.yml run --rm backend-contract-tests
docker compose exec frontend npm run test -- --run
python3 -m py_compile backend/src/lib/curation_workspace/*.py
```

Migration coverage:

- Add or update an Alembic migration unit test following existing repo patterns such as
  `backend/tests/unit/test_curation_workspace_migration.py` and
  `backend/tests/unit/test_curation_prep_candidate_cleanup_migration.py`.
- Verify forward migration behavior for existing rows carrying `domain_key` and `profile_key`.
- Verify the final schema no longer exposes those columns.

## Acceptance Criteria

- A launchable extractor persists extraction results with a required `adapter_key`.
- No shared routing code depends on `domain_key`.
- No shared routing code depends on `profile_key`.
- `Review & Curate` works directly from a fresh single-adapter chat extraction result.
- `Prepare for Curation` no longer errors on missing adapter scope when the adapter is already known from the producing agent.
- Multi-adapter ambiguity is resolved by adapter choice only.
- Complex future workflows are represented by explicit composition agents, not by reintroducing shared domain/profile routing fields.
- Core pipeline code no longer hardcodes Alliance/reference adapter registrations.

## Recommended Implementation Order

1. Phase 1: package metadata
2. Phase 2: mandatory adapter persistence
3. Phase 3: prep contract cleanup
4. Phase 4: ensure-prep-then-open bootstrap
5. Phase 5: supervisor/flow cleanup
6. Phase 6: inventory and saved-view cleanup
7. Phase 7: adapter registry decoupling
8. Phase 8: remaining profile cleanup

This order keeps the user-facing bug fix on the critical path while still ending at the broader simplification target.

## Recommended Execution Slices

These slices are the recommended implementation units for branches and PRs. They are intentionally
larger than a single micro-task but smaller than the full redesign.

## Slice 1: Routing Contract and Current Bug Fix

**Goal:** fix the current gene curation bug and establish `adapter_key` as the only routing truth on
the critical path.

**Includes**

- Phase 1 in full
- the non-destructive parts of Phase 2
- the critical-path parts of Phase 3 and Phase 4
- only the prep/bootstrap/supervisor cleanup needed to make the current single-adapter chat flow work

**Must do**

- add package-owned `curation.adapter_key` metadata to launchable agents
- require `adapter_key` for new extraction persistence
- remove domain inference and inferred-scope backfill from the extraction path
- remove domain-over-adapter routing in prep
- remove prep and supervisor unscoped fallbacks
- change review launch to `ensure prep then open`
- make the current gene extraction -> prep -> review path work without domain/profile routing

**Must not do yet**

- do not drop database columns yet
- do not do the big inventory/saved-view cleanup yet
- do not do the adapter registry export redesign yet

**Expected outcome**

- the current user-facing curation bug is fixed
- newly persisted extraction results are adapter-routed, not domain-routed
- the main chat prep/bootstrap path no longer relies on fallback behavior

## Slice 2: Shared API and UI Contract Cleanup

**Goal:** remove `domain_key` and `profile_key` from the shared frontend/backend curation surface.

**Includes**

- the remaining parts of Phase 3
- Phase 5
- Phase 6
- the shared-contract parts of Phase 8

**Must do**

- remove `domain_keys` and `profile_keys` from prep contracts
- remove `domain_key` and `profile_key` from bootstrap request contracts
- remove `domain_keys` and `profile_keys` from shared inventory filters and frontend query params
- remove saved-view handling that depends on deleted fields
- remove supervisor confirmation prompts and flow scoping logic that still expose domain/profile

**Must not do yet**

- do not drop database columns until all reads and writes are gone
- do not do the package-owned adapter registry redesign yet

**Expected outcome**

- shared curation APIs and UI speak adapter-only routing
- there is no remaining user-visible concept of domain/profile routing in the curation flow

## Slice 3: Destructive Schema Cleanup

**Goal:** remove the old columns after code no longer depends on them.

**Includes**

- the destructive part of Phase 2

**Must do**

- add the Alembic migration dropping `domain_key` and `profile_key` from `extraction_results`
- remove final ORM/schema remnants tied to those columns
- add migration tests following existing repo patterns

**Merge rule**

- Slice 3 must not merge before Slices 1 and 2 are complete

**Expected outcome**

- the database schema matches the simplified routing model
- old routing fields are gone, not merely unused

## Slice 4: Adapter Registry Decoupling

**Goal:** remove remaining hardcoded adapter assumptions from the core pipeline.

**Includes**

- Phase 7 in full
- the adapter-runtime parts of Phase 8

**Must do**

- choose a concrete package export contract for curation adapters
- remove hardcoded reference-adapter registration from core pipeline/export code
- remove the default passthrough normalizer path
- require package-owned adapter registration for launchable adapters

**Notes**

- this is the most architectural slice
- it may deserve its own small design follow-up before implementation starts
- it can be developed after Slice 1 is stable, but should not block the current bug fix

**Expected outcome**

- core curation pipeline is package-driven rather than Alliance-specific
- adapters plug in explicitly rather than being tolerated through fallback/default behavior

## Slice Summary

If we want the minimum practical split, use these four slices:

1. Routing contract and current bug fix
2. Shared API/UI contract cleanup
3. Destructive schema cleanup
4. Adapter registry decoupling

That is probably the right balance between control and overhead. It avoids a pile of tiny tickets,
but it also avoids trying to land the whole redesign in one risky branch.
