# Curation Interface Diagnosis — Data Flow, Field Preservation, and UI

**Date:** 2026-06-04
**Status:** Diagnosis / findings (no implementation)
**Author:** Investigation pass requested by Chris after the agent prompt-stack work (PR #447) merged.
**Review:** Verified against the codebase by an external Codex 5.5 (`gpt-5.5`, reasoning=high)
pass; corrections folded in (submission-gate wording, field-projection fallback, the existing
batch "Review & Curate" affordance, stale `curation_prep` config prose, and Build A's
success-contract / identity / multi-adapter requirements).

## 1. Purpose & scope

Recent work substantially changed the *shape* of objects extraction agents emit (builder
migration, gene-expression materialization, multivalued per-element validation,
experimental-condition cross-type validation, the `curatable_objects[]` envelope contract).
This document answers three questions before any further building:

1. **Data flow** — what actually carries extracted objects into the "curation interface" where
   a curator opens a list of items and verifies them against the source paper?
2. **Field preservation** — given the new extraction shapes, what is and isn't getting passed
   through to the curator, and where can a field silently disappear?
3. **UI** — is the interface congested / approachable, and what should a UI pass target?

It also scopes two follow-on build efforts that fall out of the findings:

- **Build A — batch → curation handoff** (close the functional gap so batch runs can queue
  review sessions).
- **Build B — curation interface UI pass** (congestion / approachability).

All file references are relative to the repo root and were verified against `main` at HEAD
`336965f6` (the PR #447 merge).

---

## 2. TL;DR — key findings

1. **The "push to curator" agent already exists — but batch flows are hard-blocked from using
   it.** `curation_prep` (`config/agents/curation_prep/`) is the terminal agent that bridges
   extraction into a curator review session. It works on the **chat** path. It is **incomplete**
   on the **flow** path (stages the envelope but creates no review session). It is **rejected
   outright** on the **batch** path: `backend/src/lib/batch/validation.py:58-66` requires every
   exit node to have `file_output` capability and explicitly fails any flow ending in
   `chat_output` or anything lacking `file_output`. `curation_prep`'s capability is
   `curation_prep`, not `file_output`, so a batch flow ending in it fails validation with
   *"Flow must end with a file output agent (CSV, TSV, or JSON Formatter)."* **Net: a batch flow
   cannot be *built* to terminate in curation, so a batch run does not produce the prep/envelope
   records the curation interface needs.** Note the front-end affordance to open curation from a
   batch run *already exists* — `PreparedReviewAndCurateButton` is rendered on batch rows
   (`frontend/src/pages/BatchPage.tsx:1075,1167`) and calls `bootstrap-availability` → `bootstrap`
   per document. The gap is upstream: that button has nothing to open because the batch run never
   ran curation prep. Build A's real job is to make batch runs produce eligible prep/session
   records for that existing path (see §5).

2. **The chat path is a real, working two-layer pipeline.** Extraction → revisioned **Domain
   Envelope** (semantic source of truth) → **review session + candidates + editable drafts**
   (curator-facing). The mapping is fully **domain-pack-metadata driven** ("domainless prep
   mapper"); there is no per-domain mapping code.

3. **Field preservation is metadata-gated, and the raw payload is not carried on the
   candidate.** A field is projected via `workspace_display.summary_fields` (read-only) or
   `workspace_display.groups` (editable); when neither is configured the materializer falls back
   to declared pack fields present in the payload, then to leaf payload paths (so undeclared
   present-valued leaves still surface, but as a flat dump). The prepared candidate sets
   `normalized_payload={}` (`pipeline.py:570`); anything not projected survives only in the
   envelope and is invisible in the workspace. This is the most likely place a newly-added
   extraction field silently fails to surface, and the most likely source of an over-dense
   flat field dump for the rich nested domains (gene_expression).

4. **The UI has real congestion and a consistency problem.** There are **two divergent review
   surfaces** chosen at runtime (modern envelope card list vs. legacy 7-column table), 3+
   resizable panels competing on one screen, a 9-column inventory table forced to horizontal
   scroll, chip overload, and pervasive sub-0.85rem fonts on a dark gradient theme.

---

## 3. Architecture overview

The curation interface is the **Curation Workspace** subsystem. It is built on two layers:

- **Semantic layer — Domain Envelope** (revisioned source of truth).
  Tables (`backend/src/lib/curation_workspace/models.py`): `extraction_results` (raw agent
  output, `payload_json`), `domain_envelopes` (`envelope_json`, revisioned), `domain_envelope_objects`,
  `domain_validation_findings` (what the submission gate evaluates), `domain_envelope_history`,
  `domain_envelope_projection_index`.
- **Curator-facing layer — Review Session / Candidate.**
  Tables: `curation_review_sessions`, `curation_candidates` (the row a curator verifies),
  `annotation_drafts` (editable per-candidate fields), `evidence_anchors`,
  `validation_snapshots`, `curation_submissions`, `curation_action_log` (immutable audit),
  `curation_saved_views`.

Each candidate carries a **projection pointer** (`envelope_id` / `object_id` /
`envelope_revision`) back to the semantic layer; the raw payload is reachable only through that
pointer (see §5).

**API** — all under `/api/curation-workspace/...` (`backend/src/api/curation_workspace.py`).
Reads: `GET /sessions`, `GET /sessions/{id}?include_workspace=true` (the primary item-load),
`GET /domain-envelopes/{id}/review-rows`, `/sessions/stats`, `/flow-runs`, `/views`.
Writes: `POST /prep`, `POST /documents/{id}/bootstrap`, `POST /candidates/{id}/decision`,
`PATCH .../draft`, `PATCH .../envelopes/{id}/field`, `POST .../validate`,
`POST /sessions/{id}/submission-preview`, `POST /sessions/{id}/submit`.

**Frontend** (React 18 + TS + Vite, **MUI v5**, TanStack Query v5; `frontend/src/`):
- Inventory/queue: `/curation` → `pages/CurationInventoryPage.tsx`
  (`features/curation/inventory/CurationInventoryTable.tsx`).
- Review workspace: `/curation/:sessionId[/:candidateId]` → `pages/CurationWorkspacePage.tsx`,
  shell `features/curation/workspace/WorkspaceShell.tsx`, nested inside the same persistent
  two-pane PDF layout the chat UI uses (`components/pdfViewer/PersistentPdfWorkspaceLayout.tsx`,
  `variant="curation"`).
- This is **distinct** from the chat UI (`pages/HomePage.tsx`) and the Agent Studio / Workshop
  UI (`pages/AgentStudioPage.tsx`).

---

## 4. End-to-end data flow (the working chat path)

1. **Extract.** A chat/flow extraction agent emits `curatable_objects[]`. `persist_extraction_results()`
   writes a row to `extraction_results` with `payload_json` = the agent envelope
   (`backend/src/api/chat_common.py:332`; flow path `backend/src/lib/flows/executor.py:2558`).
2. **Curation Prep (deterministic mapper).** `run_curation_prep()`
   (`backend/src/lib/curation_workspace/curation_prep_service.py:75`) materializes each
   extraction result into a `DomainEnvelope` (`write_domain_envelope_checkpoint`), then writes a
   replayable prep `extraction_results` row with `agent_key = curation_prep` carrying
   `envelope_refs`.
3. **Bootstrap.** `POST /api/curation-workspace/prep` (or `/documents/{id}/bootstrap`) selects
   the newest prep result and builds a `PostCurationPipelineRequest`
   (`backend/src/lib/curation_workspace/bootstrap_service.py:92`).
4. **Post-curation pipeline.** `run_post_curation_pipeline → execute_post_curation_pipeline`
   (`backend/src/lib/curation_workspace/pipeline.py:254`) re-runs validation (structural checks +
   `dispatch_active_validator_bindings`, writing findings with `binding_state/blocking/required`
   policy metadata onto a new envelope revision), materializes `DomainEnvelopeReviewRow`s, and
   maps each to a `PreparedCandidateInput` (`pipeline.py:505`).
5. **Persist curator rows.** `upsert_prepared_session()`
   (`backend/src/lib/curation_workspace/prepared_session_service.py:89`) inserts the
   `curation_review_sessions` row plus, per item, `curation_candidates` + `annotation_drafts` +
   `evidence_anchors` + `validation_snapshots`, and a `SESSION_CREATED` audit entry.
6. **Serve.** UI lists via `GET /sessions`, opens via
   `GET /sessions/{id}?include_workspace=true` → `CurationWorkspaceResponse` with hydrated
   candidates (each with its editable draft fields, evidence anchors, validation summary).
7. **Verify.** Per-candidate Accept/Reject, draft edits, envelope-field patches, finding waivers.
8. **Submission gate.** See §6.

---

## 5. The three entry points compared (the gap)

| Path | Trigger | Runs envelope materialization? | Creates a review session (curator-visible items)? | Status |
|---|---|---|---|---|
| **Chat** | "Prepare for curation" → `POST /prep` → `prepare_chat_curation_sessions` (`bootstrap_service.py:92`) | Yes | **Yes** (runs the full pipeline in the same call) | **Works** |
| **Flow step** | `curation_prep` node; tool calls only `run_curation_prep` and returns JSON (`executor.py:2092-2108`) | Yes (stage 1) | **No** — deferred to a later `bootstrap` action | **Incomplete** |
| **Batch** | n/a — `validate_flow_for_batch` rejects any exit node without `file_output` (`validation.py:58-66`) | — | **No** — flow fails batch validation | **Blocked at flow-build time** |
| **Per-document button** | "Review & Curate" on a batch/document row → `bootstrap-availability` → `POST /documents/{id}/bootstrap` (`BatchPage.tsx:1075,1167` → `openCurationWorkspace.ts`) | Reuses prep records if present | **Yes — IF** a prep/envelope record exists for that document | **Path exists; starved of inputs from batch** |

**Verified evidence:**
- `validation.py:63-66` — `chat_output` exit → error; non-`file_output` exit → *"Flow must end
  with a file output agent (CSV, TSV, or JSON Formatter)."*
- `curation_prep/agent.yaml:38-39` — `batch_capabilities: [curation_prep]` (not `file_output`).
- `executor.py:2092-2108` — flow `curation_prep` tool returns `prep_output.model_dump_json()`;
  no post-curation pipeline / session creation.
- **Stale config prose:** `curation_prep/agent.yaml` `description` says it "creates a curator
  review session," but the flow tool does not (only stage 1). Treat that YAML description as
  misleading; the chat path is what creates the session.

**Implication for Build A.** The functional gap Chris described ("batch flows just add stuff to
the curation interface for them to look at later") is real but more precise than "batch can't
reach curation": the *front-end* path exists (`PreparedReviewAndCurateButton` →
`bootstrap-availability` → `bootstrap`), but a batch run never produces the prep/envelope record
that `bootstrap-availability` looks for, so the button has nothing to open. Two candidate
approaches (to be brainstormed):

- **A1 — make `curation_prep` a legal batch terminal that runs the full pipeline.** Add a
  capability (e.g. `curation_handoff`) recognized by `validate_flow_for_batch`, and have the
  batch processor run the post-curation pipeline (stage 2) instead of waiting for `FILE_READY`.
- **A2 — add a dedicated batch-curation terminal agent** (sibling to the file formatters) that
  is batch-eligible and triggers the full pipeline, leaving `curation_prep` unchanged. *(Codex
  5.5 review leans A2: it avoids overloading chat/flow `curation_prep` semantics and can own the
  batch success contract cleanly. A1 is viable if it explicitly handles session creation,
  multi-adapter behavior, and non-file batch completion.)*

Build A must also resolve three things the validation change alone does not:
1. **Batch success contract.** The batch processor hard-fails unless `_execute_flow_for_document`
   returns a `FILE_READY` download URL (`backend/src/lib/batch/processor.py:226-244`). A
   curation-terminal path needs an equivalent success signal (e.g. session ids / "curation
   ready" metadata) instead of a file.
2. **Identity ownership.** Batch execution passes the Cognito subject as flow `user_id` and an
   integer DB user id as `db_user_id`, while curation sessions store a string `created_by_id`.
   Decide explicitly which identity stamps the session so "my inventory" scoping (see ALL-557 /
   KANBAN-1342) works.
3. **Multi-adapter behavior.** `run_curation_prep` requires exactly one adapter key
   (`curation_prep_service.py` `_resolve_required_adapter_key` raises otherwise), but a flow can
   carry multiple upstream adapters. Define how a multi-adapter batch flow hands off (per-adapter
   sessions vs. one combined session).

The "click over to curation" UX is largely already present (`PreparedReviewAndCurateButton`
on batch rows; inventory `flow_run_id` "By flow run" grouping), so Build A is mostly a backend
input-production problem, not a new UI.

---

## 6. Field-preservation analysis

The extraction → curation mapping is **domain-pack-metadata driven** and domain-agnostic
(`backend/src/lib/domain_packs/materialization.py`). There is no per-domain mapping code; what
shows up is governed by each pack's `workspace_display` config.

**How a field reaches the curator:**
- One **review row per non-`metadata_only` object** (`materialization.py:165-166`).
- **Summary fields** (read-only): `workspace_display.summary_fields` if declared; else every
  declared pack field present in the payload; else leaf payload paths
  (`_summary_fields`, `materialization.py:1361`).
- **Editable workspace fields**: produced **only** when the object declares
  `workspace_display.groups` (`_workspace_fields`, `materialization.py:1421`). Otherwise the
  draft falls back to summary fields (`pipeline.py:589-596`).

**Where fields are dropped or hidden:**
- **`metadata_only` objects → no row.** Allele `AlleleMention` / `EvidenceQuote`, phenotype
  `EvidenceQuote`, etc. never become candidates; their content is surfaced as evidence anchors
  instead.
- **Nested payloads are flattened** to dotted `field_path`s (e.g.
  `expression_experiment.entity_assayed.gene_symbol`). The curator sees flat label/value pairs,
  not the object tree.
- **Raw payload is not carried on the candidate.** `_prepared_candidate_input_from_review_row`
  sets `normalized_payload={}` (`pipeline.py:570`); only projected summary/workspace field
  values + a `projection_ref` are stored. **Any payload field that is neither a declared pack
  field nor a present leaf is invisible in the UI** (it remains in the envelope only).
- **Envelope/run metadata is not row data**: `raw_mentions`, `exclusions`, `ambiguities`,
  `notes`, `evidence_records` live on envelope metadata, not as review-row fields.

**Per-domain notes (current packs: `agr.alliance.base`, `gene`, `allele`, `disease`,
`phenotype`, `gene_expression`; chemical pack does not yet exist):**
- **gene** (`gene_mention_evidence`): no `workspace_display.groups` declared → editable fields
  fall back to summary fields. Rich proposal/validated field pairs
  (`proposed_*` vs `primary_external_id`/`gene_symbol`/`taxon`).
- **gene_expression** (active builder): very rich nested payload (`expression_experiment.*`,
  `condition_relations.conditions.*`, mirror-propagated subject fields). High risk of either a
  long flat dump of dotted paths *or* missing fields if `workspace_display` isn't kept in sync.
- **disease / phenotype** (grounded-only; export/write blocked): concrete subtype objects
  (`GeneDiseaseAnnotation` etc.) plus validated-reference rows; condition relations nested.
- **allele**: `AllelePaperEvidenceAssociation` (curatable) + `Allele`/`Reference`
  (validated-reference) rows; mentions/quotes are `metadata_only`.

**Implication.** A **field-coverage audit per domain pack** should be treated as **required
before Build B** (not optional): for each extractor's current output, confirm every
curator-relevant field is declared in `workspace_display` (and decide read-only vs editable),
paying special attention to gene_expression's nested groups where the leaf-path fallback can
produce an over-dense flat dump. This is the "is everything getting passed through / is it
congested" question made checkable. *(Audit not yet performed — see open questions.)*

---

## 7. Submission gate

The gate is a readiness computation, not a class
(`backend/src/lib/curation_workspace/session_submission_service.py`). It does **not** remove
candidates from the curator's view — all candidates are reviewable regardless of findings. It
only decides `ready` vs `blocking_reasons` at submission time.

A validation finding blocks submission when its `binding_state` is **absent or `"active"`**
(explicit non-active states are skipped, `_finding_blocks_readiness`,
`session_submission_service.py:785`) **AND** `blocking == True` **AND** `required == True`
(`_policy_metadata_blocks_readiness`, `:801`, which also recurses into nested `field_policy`).
`blocking` without `required` raises HTTP 500 (misconfiguration). `RESOLVED`/`WAIVED` findings don't block (waiver
allowed via `allow_opt_out`). Missing required/blocking **fields** also block
(`_field_policy_blockers`). Blocked candidates are excluded from the outbound submission payload
and surfaced as readiness blockers. Policy values originate from domain-pack `ValidatorBinding`
(`backend/src/lib/domain_packs/validation_registry.py:113`).

This gate is **load-bearing and correct as designed** — gating keys on the binding policy
(blocking+required+active), not on severity. No change proposed here; documented so the UI pass
and Build A respect it.

---

## 8. UI / congestion findings (for Build B)

Observational only (no redesign proposed yet). Frontend is MUI v5 + Emotion `sx`/`styled`, dark
"workbench" gradient theme (`#05111f`/`#071524`).

1. **Two divergent review surfaces chosen at runtime** — modern envelope card list
   (`features/curation/workspace/EnvelopeObjectReviewTable.tsx`) vs. legacy 7-column table
   (`features/curation/entityTable/EntityTagTable.tsx`), selected by `hasEnvelopeObjectRows`
   (`CurationWorkspacePage.tsx`). Inconsistent layout and field labels between sessions. **This
   is the biggest structural UX issue.**
2. **3+ resizable panels on one screen** — PDF (min 20%) | object list | field editor — under a
   fixed AppBar + workspace sub-header. Middle/right panes get very narrow on a laptop.
3. **Inventory table forced to `minWidth: 1220`** → horizontal scroll; 9 columns each with 1–3
   stacked sub-values (`CurationInventoryTable.tsx`).
4. **Chip overload** on review cards (up to 5 chips + 2 buttons per row at 0.68rem).
5. **Pervasive tiny fonts** (0.65–0.84rem) across tables, chips, inputs — dense for sustained
   verification reading.
6. **Summary truncation** to 3 fields / 72 chars forces pane-switching into the editor to verify
   full values.
7. **Bottom evidence panel** competes for vertical space inside the already-narrow middle pane.
8. **Duplicated Accept/Reject** affordances (on each card and in the editor footer).
9. **Dark high-contrast gradient** surfaces — legibility concern for dense text scanning.

Evidence linkage is a strength: clicking an evidence quote drives native pdf.js text-layer
highlighting in the left pane (`components/pdfViewer/PdfViewer.tsx`, fuzzy quote matching), with
per-field evidence slots in the editor.

---

## 9. Implications & proposed decomposition

This is **two build efforts**, each its own spec → plan → implementation cycle:

- **Build A — auto-push curation handoff (A2).** A dedicated terminal agent that, when it is the
  exit node of a **flow or batch**, runs the full pipeline and auto-creates the review
  session(s) on completion. Relax batch validation to accept it as a terminal, give the batch
  processor a non-`FILE_READY` success signal, stamp session ownership with the runner's
  identity, and create one session per adapter. Completes the flow path too. (See §10 for the
  settled decisions.)
- **Build B — curation review-screen pass.** Full-quality redesign of the `/curation/:sessionId`
  review workspace (review output + PDF + evidence + accept/decline), collapsing the two review
  surfaces into one. Inventory table is out of scope (ALL-557). Use the approach that worked for
  the Agent Studio Workshop pass.

A **field-coverage audit per domain pack** (§6) is **required before Build B**, since it
determines what the review screen must display.

---

## 10. Resolved decisions (2026-06-04, Chris)

1. **Build A shape → A2.** A dedicated terminal "curation handoff" agent, separate from
   `curation_prep`, that owns the batch/flow success contract. (Codex 5.5 concurred.)
2. **Batch UX → auto-push, for flows AND batches.** A flow or batch run that terminates in the
   curation-handoff agent **automatically** runs the full pipeline and creates the review
   session(s) on completion — no manual "Review & Curate" click. The curator opens the curation
   UI and the items are already waiting. This also completes the **flow** path (today it only
   stages the envelope). Existing download buttons stay where they are. Consequences baked into
   the Build A spec:
   - **Ownership:** auto-created sessions are stamped with the identity of whoever ran the
     flow/batch (their curator identity), which is also what makes the ALL-557 "my inventory"
     scoping work.
   - **Multi-adapter:** a run that produced multiple adapters' extractions creates **one session
     per adapter** (the chat path already loops adapters; `run_curation_prep` requires exactly
     one adapter key per call).
   - **Success signal:** the batch processor must accept "sessions created" / "curation ready"
     metadata as success instead of hard-requiring `FILE_READY`
     (`backend/src/lib/batch/processor.py:226-244`).
3. **Build B scope → full-quality pass on the curation REVIEW screen only.** The
   `/curation/:sessionId` workspace where the curator reviews curation output, views the PDF
   alongside it, sees evidence, and accepts/declines. Make it genuinely good for the upcoming
   release. **Includes** collapsing the two divergent review surfaces into one. **Excludes** the
   9-column inventory table rework (separate; overlaps ALL-557).
   - **Core design principle — envelope-driven flexibility.** The single review surface must
     render whatever curatable-object shape and fields the incoming domain envelope carries,
     driven by the domain pack's projection metadata (`workspace_display` summary/groups), and
     **group nested fields cleanly** (e.g. gene_expression's `expression_experiment.*`,
     `condition_relations.*`) rather than dumping flat dotted paths or truncating to 3 fields.
     The modern `EnvelopeObjectReviewTable` + `CandidateFieldEditor` are already generic over
     projected fields; Build B standardizes on that path, retires the rigid legacy 7-column
     table, and makes the rendering good for varied/nested envelopes (informed by the §6 field
     audit). A new domain/envelope shape should render correctly with no bespoke per-domain UI
     code — keeping the screen project-agnostic.
4. **Field-coverage audit → yes**, required before Build B (per-domain, esp. gene_expression's
   nested groups; see §6).
5. **Build order → TBD.** A2 is well-scoped enough to start; Build B is release-urgent ("people
   want to use this in the upcoming release"). Decide A→B, B→A, or parallel (they touch mostly
   different code: A is backend handoff, B is the frontend review screen + projection).

---

## Appendix — most load-bearing file references

- Batch exit-node rule: `backend/src/lib/batch/validation.py:38-68`
- Batch processor (FILE_READY requirement): `backend/src/lib/batch/processor.py:226-244`
- curation_prep agent config: `config/agents/curation_prep/agent.yaml`
- Flow curation_prep tool (stage 1 only): `backend/src/lib/flows/executor.py:2065-2115`
- Curation prep service: `backend/src/lib/curation_workspace/curation_prep_service.py:75,259`
- Post-curation pipeline + candidate mapping: `backend/src/lib/curation_workspace/pipeline.py:254,505,570,589`
- Prepared-session persistence: `backend/src/lib/curation_workspace/prepared_session_service.py:89`
- Review-row projection (envelope → row): `backend/src/lib/domain_packs/materialization.py:122,165,1361,1421`
- Submission gate: `backend/src/lib/curation_workspace/session_submission_service.py:785,801`
- Validator binding policy: `backend/src/lib/domain_packs/validation_registry.py:113`
- Curation workspace API: `backend/src/api/curation_workspace.py`
- Models: `backend/src/lib/curation_workspace/models.py`
- Frontend inventory: `frontend/src/features/curation/inventory/CurationInventoryTable.tsx`
- Frontend workspace: `frontend/src/pages/CurationWorkspacePage.tsx`, `frontend/src/features/curation/workspace/WorkspaceShell.tsx`
- Frontend review surfaces: `frontend/src/features/curation/workspace/EnvelopeObjectReviewTable.tsx`, `frontend/src/features/curation/entityTable/EntityTagTable.tsx`
- PDF evidence highlighting: `frontend/src/components/pdfViewer/PdfViewer.tsx`
