# Builder-path inline validation, durable save, and cross-domain builder migration

Status: design / not yet implemented. Authoritative blueprint for the next round of
extractor work. Supersedes the migration *ordering* in
`2026-05-21-cross-domain-builder-rollout-plan.md` and
`2026-05-21-allele-builder-tool-design.md` (those are STALE — see "Stale docs" below).
Builds on `2026-05-29-piecewise-extraction-builder-and-trace-diagnostics.md` and
`2026-05-20-extractor-validator-envelope-rollout-goal-archive.md`.

Date: 2026-05-31.

## AFTERNOON UPDATE (2026-05-31) — materialization-quality work + a fragility to decide

Committed + pushed to `main` this session:

- **`entity_assayed_mismatch` FIXED** (commit `2ec6b3b9`). The materializer now copies a resolved
  value to the field's declared `materializes_to_field_paths` mirrors, so a resolved subject gene
  also lands on `expression_experiment.entity_assayed.{primary_external_id,gene_symbol}` and the
  LinkML "entity_assayed must match expression_annotation_subject" contract passes. Driven entirely
  by domain-pack metadata (no gene-expression-specific code), in both
  `materialize_validator_results_into_envelope` and `_validated_reference_payload`
  (`backend/src/lib/domain_packs/materialization.py`). New helper
  `_propagate_materialized_mirror_paths`. Verified four ways: new unit test; 92 materialization/
  contract/dispatch tests; **end-to-end sandbox run ge15: 9 → 0** `entity_assayed_mismatch`
  findings; and direct payload inspection (subject == entity_assayed for every object,
  e.g. WB:WBGene00003969/pef-1, WB:WBGene00001675/gpa-13).

Remaining gene_expression materialization-quality findings (root causes now characterized, NOT yet
fixed — counts from ge15 envelope `2e5664d2`):

- **`object_not_pending` (5)** — DESIGN DECISION. `materialize_validator_results_into_envelope`
  advances the object to `status=CuratableObjectStatus.VALIDATED` (materialization.py:487), but the
  gene_expression contract (`conversion.py:1778`) requires objects stay `PENDING` after conversion.
  Lifecycle order is PENDING→EXTRACTED→NEEDS_REVIEW→VALIDATING→VALIDATED→READY_FOR_EXPORT. Either the
  materializer should not advance status (keep PENDING; validation lives in findings/metadata) or
  the contract should accept VALIDATED. This is cross-pack core behavior — needs Chris.
- **`validator_materialization_invalid` (8)** — validator results returned `resolved_objects` with no
  `canonical_id` ("resolved_objects[0].canonical_id is required"). Validator-output-quality / schema
  issue; needs to find which validators omit canonical_id and why.
- **`metadata_refs_missing` (5)** — objects carry `metadata_refs` to `raw_mentions[N]`/
  `evidence_records[N]` that don't exist under `envelope.metadata`. Builder-side metadata-index gap:
  finalization sets per-object refs but the envelope-level `raw_mentions`/`evidence_records` arrays
  aren't populated. Needs tracing builder finalize → `envelope.metadata`.

**FRAGILITY discovered (separate from the fix, NEEDS A DECISION):** A1 inline validation makes any
validator execution error fatal to the chat turn. `streaming_tools.py:1857` raises
`SpecialistOutputError` when `has_validator_error` (any validator lookup attempt with
`outcome=="error"`, e.g. a flaky `get_data_provider` call). That aborts the turn and the extraction
is never persisted — ge14 finalized 10 observations and lost them all (bootstrap 422). In the old
pre-A1 flow the extraction was saved before validation, so a validator hiccup only produced a
finding. This is flaky (ge13/ge15 succeeded on the same paper, ge14 did not). Recommended direction:
a validator execution error should be recorded as a finding and the extraction still persisted — do
not lose curated data because one LLM validator made a bad tool call.

## MORNING HANDOFF (overnight 2026-05-31, for Chris)

What I did while you slept, and what's waiting on you. Nothing is committed — it's all on the
sandbox + host working tree for your review.

### DONE + TESTED: Part 1 (builder-path inline validation + fold-back)

Implemented in `backend/src/lib/openai_agents/streaming_tools.py`:
- `_dispatch_domain_envelope_validators_for_chat` gained an `is_builder_envelope` flag that
  bypasses the output-schema gate (so the dispatch runs on a builder's finalized envelope).
- `run_specialist_with_events` got an `elif` builder branch (~3502) that runs that dispatch on
  `builder_finalization.payload` and folds the validated envelope back via `replace(...)`.
- `from dataclasses import ... replace` added.

Tested on gene_expression (session `ge-test11`, trace `b146c6f8008f6b37f8d138fb6b854ce1`):
- The validator dispatch now fires **in the chat turn** (log: `chat domain-envelope validation
  dispatched 98 binding(s), 70 validator result(s), 70 finding(s)`, ~46s), BEFORE the reply and
  BEFORE any bootstrap.
- The reply now shows the **validated** gene id: `pef-1 — WB:WBGene00003969` (every prior run
  showed bare `pef-1`).
- The persisted `extraction_results` row carries the validated DomainEnvelope (real curies
  `WB:WBGene00003969`, `WB:WBGene00001675`), single row.
- So the requirement "validate before we reply" is met. The reply reflects validated values.
- NO-REGRESSION: bootstrapping `ge-test11` afterward still succeeds (HTTP 200, 7 candidates) — the
  validated DomainEnvelope-shape payload flows cleanly into the curator path. Bootstrap still
  re-validates today (224 conflicts, same structural `object_not_pending` / entity_assayed-match
  gaps; literature resolved the PMID), because Parts 2-3 (durable inline save + reuse) are not
  done yet. Parts 2-3 are what make bootstrap stop re-validating.

Observations worth your eye:
- Inline validation adds ~46s to the chat turn (70 validator jobs). That is the cost of
  validating before replying (the requirement). Optimization (batching / parallelism / skipping
  no-op bindings) is a later concern, not a blocker.
- The builder path's persisted payload is now the **DomainEnvelope shape** (`objects[]`,
  `envelope_id`), same as the envelope extractors — the fold-back deliberately matches the proven
  envelope path. (Before, it was the extraction `curatable_objects[]` shape.)
- 70 findings include the `object_not_pending` / `metadata_refs_missing` structural gaps we saw
  earlier — separate materialization-quality work, not part of this.

### UPDATE (midday 2026-05-31): A1 approved by Chris; Parts 2-4 implemented

- **Part 4 (double-persist source fix): DONE + TESTED.** `_build_extraction_candidate_from_tool_event`
  now accepts ONLY `INTERNAL_EXTRACTION_RESULT` (dropped the supervisor `TOOL_COMPLETE` source);
  the dedupe band-aid in `_persist_extraction_candidates` is deleted. ge-test12 persisted exactly
  ONE extraction_results row (was two).
- **Part 2 (durable save): NO chat-time write needed.** The validated payload is already a
  `DomainEnvelope` with `validation_findings` embedded (70 on ge-test11), and
  `_domain_envelope_from_extraction_result` (curation_prep_service.py:325-326) takes the
  `DomainEnvelope.model_validate` fast path for it, so `ensure_domain_envelope_materialization`
  writes the durable envelope WITH the inline findings. The transient `chat-runtime` envelope_id is
  used self-consistently (the `_extraction_envelope_id` re-key path at :340 is only reached for the
  raw-extraction shape, which we skip). Re-keying is an optional cosmetic cleanup, not required.
- **Part 3 (reuse / skip re-validation): IMPLEMENTED (A1).** The chat inline dispatch sets
  `envelope.metadata["inline_validator_dispatch_complete"] = True`
  (streaming_tools `_dispatch_domain_envelope_validators_for_chat`). The curation bootstrap
  `_refresh_domain_envelope_validation_for_ref` (pipeline.py) checks that flag and SKIPS
  `dispatch_active_validator_bindings` (the ~46s validator-agent pass), running only the cheap
  structural checks and reusing the saved findings. Structural checks preserve existing findings
  (`append_validation_findings_to_envelope` starts from `list(envelope.validation_findings)`), so
  the inline findings survive. Curator edits still re-validate via the session validation service
  (a separate path, unchanged). Verifying on ge-test13 (extract once, bootstrap, confirm the
  validator dispatch runs only in the chat turn, not at bootstrap).

So the flow is now: extract -> validate (chat turn) -> save (validated envelope + findings) ->
reply -> curate (reads saved findings, no re-validation). Part 1 was committed
(42b978c1); Parts 2-4 are in the working tree pending the ge-test13 confirmation, then commit.

### HELD for your call (not done overnight, on purpose) [now resolved per A1 above]

- **Part 2 — durable DomainEnvelope + findings save in the chat turn.** Right now the *validated
  extraction* is saved, but the durable `DomainEnvelope` + `domain_validation_findings` are still
  written at bootstrap. Wiring the inline durable save (via `write_domain_envelope_checkpoint`)
  needs the `project_key` decision (see Open decisions) — I would not guess at it unsupervised.
- **Part 3 — make curator-open reuse the saved envelope** (skip re-validation). Depends on Part 2.
- **Part 4 — double-persist source fix** (single `INTERNAL_EXTRACTION_RESULT` source; delete the
  dedupe band-aid). Clean, but it changes shared persistence behavior — better with you awake. The
  dedupe band-aid in `chat_common.py` is still in place.
- **Other domains.** Untouched. They are still envelope pattern; migrating them is the separate
  project sequenced below, and must not start before Part 2-4 land.

### Uncommitted files in this working tree (for review)

- `streaming_tools.py` — Part 1 (this session) + the earlier closure-bound run-state fix.
- `agent_studio/catalog_service.py` — the (A) curation-inheritance fix (prerequisite for Part 1).
- `api/chat_common.py` — the dedupe band-aid (to be replaced by Part 4).
- `agr_curation.py`, `resolver_call_ledger.py`, `gene_expression/prompt.yaml` — the earlier
  resolver-call-id simplification. (Exact `git status -s` modified set: `chat_common.py`,
  `catalog_service.py`, `resolver_call_ledger.py`, `streaming_tools.py`,
  `gene_expression/prompt.yaml`, `agr_curation.py`.)
- This design doc.
- Sandbox-only (not in git): `ELASTICSEARCH_HOST` enabled (literature), backend container
  recreated with all compose-time env vars (see runbook warning).

### Test PDFs

gene_expression is covered (doc_id `a31b1ff3-4fcd-42f8-9aec-0d299bcdbbe5`). A clean per-class set
(gene/disease/chemical/allele/phenotype) is NOT staged — deferred as low-urgency because those
domains are still envelope pattern (not builder) and aren't migrating yet. Inventory + how to
stage is in "Test PDF inventory" below.

### Suggested first moves when you're back

1. Review the Part 1 diff in `streaming_tools.py` and the `ge-test11` evidence above.
2. Decide the Part 2 `project_key` question (Open decisions) so the validated envelope + findings
  get saved durably in the turn and bootstrap can stop re-validating.
3. Then Part 4 (kill the double-persist at the source, delete the dedupe band-aid).

---

## TL;DR

The intended flow is **extract -> validate -> save -> reply -> curate**, with validation
happening *in the chat turn before we reply to the user*, and the validated result *saved*
so the curation step never re-validates. The original (envelope) extractors do this. The
newer **builder** extractor (gene_expression — the only one on the builder pattern) **skips
inline validation**, so its validators only run later at curation "bootstrap". That drift is
the root of a pile of confusing downstream behavior. The fix is to run the validators on the
builder's finalized envelope *in the chat turn*, persist the validated envelope + findings
durably right there, return the validated result, and make the curation-open path read the
saved envelope instead of re-validating.

This is also the **prerequisite** for migrating the other five extractors to the builder
pattern: migrating any of them today would silently *remove* their inline validation, because
the builder path has no inline validation wired.

## The requirement (from Chris, 2026-05-31)

> During a chat we must do the validation as part of the process BEFORE we reply to the user,
> and that result must be SAVED so we don't need to re-run validation later. If we don't
> validate before returning to the user, the user gets an incomplete response. The curator
> can't judge the quality of results that aren't fully validated.

So validation is part of *producing the answer*, not a later step. The chat reply must reflect
fully-validated, materialized values, and those must be persisted so nothing re-validates.

## Two extraction patterns (current reality)

- **Envelope pattern** (allele, chemical, disease, gene, phenotype extractors; also `pdf`):
  the extractor RETURNS a domain-envelope as its structured output (it HAS an
  `expected_output_type` / output schema). The chat runtime then validates that envelope
  INLINE via `_dispatch_domain_envelope_validators_for_chat` before returning to the
  supervisor. Matches the intended flow.
- **Builder pattern** (gene_expression only, agent_id `gene_expression_extraction`): uses
  run-state-bound builder tools (`record_evidence`, `resolve_domain_field_term`, `stage_*`,
  `finalize_gene_expression_extraction`) and is FORBIDDEN an output schema
  (`expected_output_type is None`). It assembles + finalizes via `ExtractionBuilderWorkspace`.
  The chat runtime currently SKIPS inline validation for this path.

Audit (2026-05-31, `builder-migration-audit` workflow): **1 of 7** Extraction-category agents
is on the builder pattern. There is **no generic builder engine** — the builder runtime is
hard-coded to gene_expression. The other five curation extractors + `pdf` are envelope.

## Intended vs actual flow

```
INTENDED (and what envelope extractors do):
  chat: extract -> VALIDATE (materialize authoritative IDs/terms) -> SAVE -> reply (validated)
  curator opens doc: reads the saved, validated envelope

ACTUAL for gene_expression (builder) today:
  chat: extract -> [no validation] -> reply (UNVALIDATED) -> persist raw extraction_results row
  curator opens doc ("bootstrap"): materialize -> validate -> save  <-- too late
```

## Root cause: where the builder path skips validation

All in `backend/src/lib/openai_agents/streaming_tools.py`, function
`run_specialist_with_events` (def line 2274), in the post-stream stretch ~3410-3535:

- `builder_finalization = builder_workspace.finalization` (3414).
  - BUILDER path: already SET (the agent called `finalize_gene_expression_extraction` in its
    tool loop), so `builder_finalization is not None`.
  - ENVELOPE path: `None` (agent returns the envelope as its final output).
- Gate #1 — `if builder_finalization is None:` at 3432 (evidence-summary) and **3460**
  (validator dispatch -> `_dispatch_domain_envelope_validators_for_chat`, 3462). The builder
  path is non-None here, so **the validator dispatch block is skipped**.
- Gate #2 — even if gate #1 were removed, `_dispatch_domain_envelope_validators_for_chat`
  (def 1627) self-gates at line ~1636 on `_is_domain_envelope_output_json` (1371), which
  requires `_is_domain_envelope_extraction_output_type(expected_output_type)` (176). Builder
  agents have `expected_output_type is None` (forbidden output schema; guard at ~2331), so the
  dispatch would return early without validating. **The dispatch is keyed off the output
  schema the builder deliberately doesn't have.**
- At 3506 `if builder_finalization is not None:` the builder path emits
  `build_internal_extraction_result_event` from the UNVALIDATED `builder_finalization.payload`,
  which flows to the supervisor + persistence.

## The durable-save mechanism (key finding — these already exist, they just run too late)

- `dispatch_active_validator_bindings` (`backend/src/lib/domain_packs/validator_dispatch.py:129`)
  is **pure**: it takes `(envelope, domain_pack)`, runs the validator agents, and RETURNS an
  `ActiveValidatorDispatchResult` (validated `envelope` + `appended_findings`). No DB session,
  no persistence. Reusable inline as-is. (At bootstrap it produced 104 `validator_resolved`
  findings on gene_expression's materialized envelope — proven to work on builder output.)
- `write_domain_envelope_checkpoint` (`backend/src/lib/domain_envelopes/persistence.py:116`)
  is the **durable save**: given a `db: Session` + envelope, it writes the envelope JSON and
  REGENERATES the object/finding/projection indexes (i.e. persists the
  `domain_validation_findings` rows) from it, in one transaction.
- `ensure_domain_envelope_materialization` (`curation_prep_service.py:259`) +
  `_domain_envelope_from_extraction_result` build a `DomainEnvelope` from a finalized
  extraction.

So the bootstrap chain today is: materialize (`_domain_envelope_from_extraction_result`) ->
validate (`dispatch_active_validator_bindings`, via `_refresh_domain_envelope_validation_for_ref`,
`pipeline.py:433`) -> save (`write_domain_envelope_checkpoint`, `pipeline.py:479`).

**"Validate-and-save in the chat turn" = run exactly that same chain in the chat request**
(which has a `db` session), after the builder finalizes, before we reply.

## The fix (design)

Four parts. Parts 1-2 make gene_expression validate inline and save; parts 3-4 are the
correctness/cleanup that go with it.

### 1. Run validators on the builder's finalized envelope, in the chat turn  [DONE + TESTED 2026-05-31, see Morning Handoff]

- Extract the *core* of `_dispatch_domain_envelope_validators_for_chat` (the body after the
  output-schema gate: `build_extraction_envelope_candidate` -> `_domain_envelope_from_extraction_result`
  -> `dispatch_active_validator_bindings` -> serialize) into a helper that takes an envelope
  payload + `agent_key` + `specialist_name` + `tool_name` directly — no `expected_output_type`
  gate.
- In `run_specialist_with_events`, turn the `if builder_finalization is None:` at 3460 into an
  if/else: envelope path keeps the existing call; **builder path** calls the new core on
  `json.dumps(builder_finalization.payload)`.
- Mirror the existing error handling (the `except SpecialistOutputError -> record_validation_failure
  -> raise` block at 3468-3491).
- PREREQUISITE already in place: the core calls
  `build_extraction_envelope_candidate(envelope, agent_key="gene_expression")`, which needs the
  agent's curation/adapter to resolve. The 2026-05-31 `get_agent_metadata` curation-inheritance
  fix (`catalog_service.py:2236-2265`) makes that resolve for builder agents. Without it the
  builder-path validation can't even start.

### 2. Persist the validated envelope + findings durably — in the ENDPOINT, not the runtime

Important layering constraint (verified 2026-05-31): `run_specialist_with_events` does NOT
receive a `db: Session`, and the existing chat dispatch runs against a transient
`document_id="chat-runtime"` record. The real `document_id` / `session_id` / `project_key` and a
`db` session only exist at the chat ENDPOINT (`chat_stream.py` / `chat_common.py`), where the
extraction_results row is already persisted. So split the work by layer:

- **Runtime (pure, no DB)** — in `run_specialist_with_events`, part 1 runs validators and folds
  the validated envelope back into the in-memory finalization so the VALIDATED payload flows out
  on the `INTERNAL_EXTRACTION_RESULT` event:
  `ExtractionBuilderFinalization` is a `@dataclass(frozen=True)`
  (`extraction_builder_workspace.py:85`); validation changes only field *values* inside the
  payload (not candidate identity / evidence IDs / run_id / counts), so
  `builder_finalization = dataclasses.replace(builder_finalization, payload=validated_payload)`
  is sufficient for `build_internal_extraction_result_event` (511), persistence, and the
  supervisor summary to all carry the validated envelope (with findings embedded — the validator
  dispatch embeds findings into the envelope JSON; `write_domain_envelope_checkpoint` regenerates
  the `domain_validation_findings` rows from that JSON).
- **Endpoint (durable save, has DB)** — alongside `_persist_extraction_candidates`
  (`chat_common.py:327`, which has `db`, `document_id`, `session_id`, `user_id`), add a durable
  DomainEnvelope save driven by the now-validated `canonical_payload`: reconstruct the
  `DomainEnvelope` from the validated extraction payload via `_domain_envelope_from_extraction_result`
  (the same converter curation-prep uses, `curation_prep_service.py:318`), then call
  `write_domain_envelope_checkpoint(db, DomainEnvelopeCheckpointRequest(project_key=..., envelope=...,
  expected_revision=0, document_id=..., session_id=...))`.
  - `project_key` must be sourced in chat the way curation-prep does (`_checkpoint_project_key`)
    — confirm the chat user/group context provides it.
  - Accept one extra DomainEnvelope<->extraction-payload conversion here (reuses the existing
    `_domain_envelope_from_extraction_result`); it avoids threading `db`/`document_id` deep into
    the agent runtime, which is the worse coupling.

Net: validation stays a pure function in the runtime; durable persistence stays in the endpoint
where the DB session and document identity already live.

### 3. Make the curator-open path read the saved validated envelope (don't re-validate)

- `run_curation_prep` / `run_post_curation_pipeline` currently materialize + validate at
  bootstrap. Once inline validation persists a validated `DomainEnvelope`, the bootstrap path
  should REUSE it (key by document/adapter/envelope_id) and skip re-dispatch, re-validating
  only on curator EDITS.
- OPEN: confirm `dispatch_active_validator_bindings` is idempotent if a re-dispatch ever
  happens, so a transition period (inline + bootstrap both running) is safe.

### 4. Fix the double-persist at the source (replaces the dedupe band-aid)

- Today the canonical payload reaches persistence via TWO chat events: `INTERNAL_EXTRACTION_RESULT`
  AND the supervisor `ask_*_specialist` `TOOL_COMPLETE`. `_build_extraction_candidate_from_tool_event`
  (`chat_common.py:144`) accepts both -> two byte-identical `extraction_results` rows.
- Real fix: make `INTERNAL_EXTRACTION_RESULT` the single source — drop the `TOOL_COMPLETE`
  branch in `_build_extraction_candidate_from_tool_event` (confirm no extraction path emits
  only `TOOL_COMPLETE` first; for extraction specialists `INTERNAL_EXTRACTION_RESULT` is always
  emitted). Then DELETE the working-tree-only dedupe block in `_persist_extraction_candidates`
  (`chat_common.py:~350`), which is a fragile post-hoc band-aid (it relies on byte-identical
  serialization).

## Prerequisites already landed (sandbox, 2026-05-31; uncommitted)

- **(A) curation resolution for builder agents**: `get_agent_metadata` follows
  `template_source` to inherit curation when the parent definition declares `launchable`
  curation (`catalog_service.py:2236-2265`). Needed by part 1. Blast radius: exactly one agent
  (gene_expression) flips None->curation.
- **Literature OpenSearch enabled in sandbox**: `reference_validation` resolves PMIDs via the
  VPC OpenSearch endpoint (`ELASTICSEARCH_HOST=vpc-literature-search-...es.amazonaws.com`). The
  endpoint is reachable from the sandbox; only the env var was missing.
- **Dedupe band-aid** (`chat_common.py:_persist_extraction_candidates`): collapses byte-identical
  candidates. To be REPLACED by part 4 (delete it).

## What NOT to touch

- The `curation_prep` second `extraction_results` row (agent_key=`curation_prep`, with
  `envelope_refs` back to the extraction) is **load-bearing** — it is the bootstrap/pipeline
  replay handle (`bootstrap_service.py:287`, `pipeline.py`), a `CurationPrepAgentOutput`, a
  different shape than an extractor envelope. Do not remove it as part of this work.
- `get_agent_metadata` curation-inheritance also serves user custom agents via `template_source`
  (a permanent product feature). Keep the custom-agent path; at most narrow the builder-routing
  part later.
- The `_AGENT_ID_EQUIVALENTS` pairs in `flow_tools.py` for live validator/lookup agents
  (`gene<->gene_validation`, etc.) are NOT removed by this migration. Only the
  `gene_expression<->gene_expression_extraction` folder-vs-agent_id split is migration-related,
  and only removable via a folder rename / canonical-id migration.

## Cross-domain migration context (audit, 2026-05-31)

Migrating all extractors to the builder pattern then deleting envelope legacy is a real,
multi-phase project — NOT something to start now. Recommended sequence (re-derived from the
2026-05-29 design, not the stale 2026-05-21 plan):

1. Mark the 2026-05-21 docs SUPERSEDED.
2. **Generalize builder detection**: replace the hard-coded
   `_BUILDER_MATERIALIZER_FINALIZATION_TOOLS = {"finalize_gene_expression_extraction"}`
   (`streaming_tools.py:547`) + `_is_builder_materializer_agent` (554) with a registry/domain-pack
   derived set of finalize-tool names. Keep the forbid-output-schema guard (~2331).
3. **Wire builder-path inline validation + durable save** (this doc, parts 1-2). MUST come
   before migrating any other domain — otherwise migration silently removes inline validation.
4. **Build a shared builder engine + per-domain builder tools** (generic `ExtractionBuilderWorkspace`
   core + thin per-domain adapters; do NOT clone gene_expression's bespoke per-field tools).
5. **Migrate domains one at a time** (suggested canary: gene_extractor, then disease, chemical,
   phenotype, allele): set `output_schema: null`, swap envelope tools for builder tools, add the
   finalize tool to the generalized detection set, rewrite the prompt to a builder tool-loop, add
   the per-domain materializer.
6. **Decide `pdf`'s fate**: it is category Extraction with `PdfExtractionResultEnvelope` but no
   curation block / domain-pack validators. It blocks deleting the generic envelope output-schema
   machinery. Migrate it or accept those helpers survive.
7. **Only after all curation extractors are builders AND inline builder validation is live**:
   delete the envelope-only branches in `run_specialist_with_events` (3415-3427, 3432-3454,
   3460-3491, 3516-3535), then `_is_domain_envelope_output_json` (1371) /
   `_is_domain_envelope_extraction_output_type` (176), then the now-pass-through helpers
   (`_canonicalize_structured_output_text` 3410, `_reduce_specialist_output_for_supervisor` 3540).
8. **Per migrated domain**: delete that domain's `@model_validator(mode="before")` scaffold
   canonicalization in `packages/alliance/agents/<domain>/schema.py` only after grep confirms no
   replay/import/fixture validates a raw envelope for it. (gene_expression's
   `_canonicalize_gene_expression_scaffold` is already dead and removable now — the canary.)
9. **Adapter sanitizers** `_sanitize_gene_extraction_payload` / `_sanitize_phenotype_extraction_payload`
   (`extraction_results.py:157-336`) become unnecessary per-domain once that domain is a builder.

KEEP the dual-shape acceptance in `build_extraction_envelope_candidate`
(`_is_extraction_envelope_payload`, `extraction_results.py:704`) — it is the bridge that makes
the validator-dispatch core pattern-agnostic.

## Stale docs (process risk)

`2026-05-21-cross-domain-builder-rollout-plan.md` and `2026-05-21-allele-builder-tool-design.md`
assume allele was the first working builder and gene-expression migrates last, and reference
symbols that do not exist in the tree (`stage_allele_paper_evidence`, `finalize_allele_extraction`,
`DomainPackExtractionBuilder`, `extraction_staging.py`, `tools/extraction_builder.py` — all zero
grep hits). The code followed the 2026-05-29 design (gene-expression first). Do not drive
sequencing from the 2026-05-21 docs.

## Held-question mechanics (investigated 2026-05-31, for the decisions below)

- **project_key is NOT a blocker.** `_checkpoint_project_key` (curation_prep_service.py:403)
  derives it from `extraction_result.metadata["project_key"]` else `domain_pack_id.split(".")[0]`
  (-> `agr`) else adapter_key. The chat durable save can use the same logic; no new context needed.
- **The validated payload IS already a `DomainEnvelope`.** Verified on `ge-test11`:
  `DomainEnvelope.model_validate(extraction_results.payload_json)` succeeds (7 objects,
  domain_pack `agr.alliance.gene_expression`). So the durable save is just
  `write_domain_envelope_checkpoint(db, DomainEnvelopeCheckpointRequest(project_key="agr",
  envelope=DomainEnvelope.model_validate(payload), expected_revision=0, document_id, session_id))`
  in the endpoint — no re-conversion needed.
- **BUT the envelope_id is transient: `extraction-result:chat-runtime:<uuid>`.** The Part-1 inline
  dispatch runs through `_dispatch_domain_envelope_validators_for_chat`, which builds its candidate
  against a `document_id="chat-runtime"` record, so the envelope_id bakes in `chat-runtime`.
  Bootstrap/curation-prep key the envelope by the REAL extraction_result_id
  (`_extraction_envelope_id` -> `extraction-result:<extraction_result_id>`). For the durable save
  to align with bootstrap reuse, the endpoint must RE-KEY the envelope_id to the real
  extraction_result_id at save time (the endpoint has the real document_id + extraction_result_id;
  the runtime does not). This is the concrete wiring detail Part 2 turns on.
- **Bootstrap already has reuse machinery** (`find_reusable_prepared_session`,
  bootstrap_service.py:158) but `_refresh_domain_envelope_validation_for_ref` (pipeline.py:433)
  re-dispatches validators regardless. Making it skip re-validation needs an "already validated at
  revision N" gate keyed off the inline-saved envelope.
- **Part 4 is safe.** `INTERNAL_EXTRACTION_RESULT` is emitted for every extraction specialist that
  finalizes (builder, 3506) or returns a domain envelope (3516); the supervisor `TOOL_COMPLETE`
  only ever produced a duplicate candidate (or, for non-envelope output, nothing). Dropping the
  `TOOL_COMPLETE` source from `_build_extraction_candidate_from_tool_event` leaves
  `INTERNAL_EXTRACTION_RESULT` as the single source and lets the dedupe band-aid be deleted.

## Open decisions

- **`object_not_pending`: should validator materialization advance object `status` to `VALIDATED`,
  or keep objects `PENDING` until the curator acts?** RESOLVED (Chris, 2026-05-31): PENDING means
  "not yet validated by the automated validator"; validation legitimately removes PENDING and is
  unrelated to curator review. The `object_not_pending` check was removed (commit `91f7a784`).
- **Inline-validation error policy: abort vs persist + record a finding?** RESOLVED (Chris,
  2026-05-31): persist + record a finding, and distinguish a validator that *errored* (a distinct
  `validator_error`, shown to curators and logged) from one that *ran but found no match*
  (`validator_unresolved`). Implemented in commit `abfe55ed`.
- Inline validation should SEED the persisted `DomainEnvelope` so bootstrap reuses it (this
  doc's chosen direction, matching the requirement). Confirm the bootstrap/curation-prep path
  reuse logic and idempotency.
- `pdf` migration scope (see step 6).
- Shared builder engine vs. cloning (audit strongly recommends the shared engine).
- Naming policy for the `gene_expression` folder vs `gene_expression_extraction` agent_id.
- Per remaining domain: does each builder's finalize enforce evidence/provenance equivalently
  to the inline `_emit_specialist_evidence_summary_or_raise` check before that check is deleted?

## Sandbox testing runbook

The work happens in the Symphony main sandbox (Incus VM `symphony-main`, compose project
`agrmainsandbox`). The backend is at `http://127.0.0.1:8900` *inside the VM*; the host cannot
reach it directly, so run cur/python via `incus exec`. DEV_MODE is on, so no auth header is
needed (mock dev user `dev-user-123`).

### Deploy a source change to the running sandbox

The backend mounts source from the worktree `/home/ctabone/.symphony/sandboxes/agr_ai_curation/main`
and runs uvicorn `--reload`. To deploy an edited file:

```bash
WT=/home/ctabone/.symphony/sandboxes/agr_ai_curation/main
incus file push <hostfile> symphony-main$WT/<same/relative/path> --uid 1000 --gid 1000 --mode 0644
# wait ~5s for --reload, then:
incus exec symphony-main -- bash -lc 'docker exec agrmainsandbox-backend-1 python -c "import ast; ast.parse(open(\"/app/<path>\").read()); print(\"syntax OK\")"; curl -s -m6 -o /dev/null -w "health %{http_code}\n" http://127.0.0.1:8900/docs'
```

WARNING: never `docker compose down -v` or re-run the Symphony sandbox `prepare` — the worktree
is a checkout of origin/main and the uncommitted fixes (this work) would be lost. If the backend
container must be recreated, the **compose-time-only** env vars are NOT in the container env and
must be in `$WT/.env`: `BACKEND_HOST_PORT=8900`, `RUN_DB_BOOTSTRAP_ON_START=true`,
`RUN_DB_MIGRATIONS_ON_START=true`, `RERANK_AWS_CREDENTIALS_MOUNT_DIR=/home/ctabone/.symphony/secrets/agr_ai_curation/aws-rerank`,
`ELASTICSEARCH_HOST=vpc-literature-search-3ioqj2ykpx2jbthmp5ocnbs7vi.us-east-1.es.amazonaws.com`
(+ scheme `https`, port 443, index `references_index`). Recreate with
`cd $WT && docker compose -p agrmainsandbox up -d --no-deps --no-build --force-recreate backend`.

### End-to-end test for one data class

Per data class, with the document already uploaded + PDFX-processed (see "Test PDF inventory"):

```bash
BASE=http://127.0.0.1:8900
DOC=<document_id>
SID=<class>-test-$(date +%s)
# 1) make it the active chat doc
curl -s -X POST $BASE/api/chat/document/load -H 'Content-Type: application/json' -d "{\"document_id\":\"$DOC\"}"
# 2) extract (non-streaming /api/chat returns {response, session_id}; ~3-6 min)
curl -s -m600 -X POST $BASE/api/chat -H 'Content-Type: application/json' \
  -d "{\"message\":\"<domain extraction prompt>\",\"session_id\":\"$SID\"}"
```

Domain extraction prompts: gene_expression -> "Extract all gene expression from this publication";
gene -> "Extract all gene mentions..."; disease -> "Extract all disease annotations..."; etc.
(match the extractor; the supervisor routes by intent.)

Then verify (run python inside the backend container — `docker exec agrmainsandbox-backend-1 python <script>`):

```python
# extraction_results: expect ONE gene_<class> row after the dedupe fix (Part 4); pre-fix, two
from sqlalchemy import text
from src.models.sql.database import engine
with engine.connect() as c:
    for r in c.execute(text("SELECT agent_key, adapter_key, candidate_count, created_at::text "
                            "FROM extraction_results WHERE origin_session_id=:s ORDER BY created_at"),
                       {"s": SID}).mappings():
        print(dict(r))
```

For inline validation (Parts 1-2): grep the backend logs for the validator dispatch during the
chat turn (NOT just at bootstrap):

```bash
incus exec symphony-main -- bash -lc 'docker logs --since <chat_start_iso> agrmainsandbox-backend-1 2>&1 | \
  grep -iE "Active Validator Dispatch|chat domain-envelope validation dispatched|gene_validation|ontology_term_validation"'
```

Expect "chat domain-envelope validation dispatched N binding(s), M validator result(s), K finding(s)"
to appear *during* the extraction turn for the builder agent.

Findings breakdown (across runs):

```python
from sqlalchemy import text
from src.models.sql.database import engine
with engine.connect() as c:
    for code, n in c.execute(text("SELECT finding_json->>'code', count(*) FROM domain_validation_findings GROUP BY 1 ORDER BY 2 DESC LIMIT 15")):
        print(n, code)
# domain_pack.validator_resolved = validators succeeded; object_not_pending / metadata_refs_missing
# = structural materialization gaps to chase separately.
```

### Bootstrap (curation review) test

```bash
curl -s -m600 -X POST "$BASE/api/curation-workspace/documents/$DOC/bootstrap" \
  -H 'Content-Type: application/json' -d "{\"origin_session_id\":\"$SID\"}"
# response.session.validation.counts shows validated/conflict/etc.; warnings show service availability.
```

### Whole-run diagnostic (TraceReview, port 8901 in the VM)

```bash
TID=<trace_id from logs>
incus exec symphony-main -- bash -lc "curl -s 'http://127.0.0.1:8901/api/claude/traces/$TID/diagnostic_report?source=local&include_raw_args=true&include_raw_outputs=true&include_sibling_traces=true&session_id=$SID'"
```

`include_sibling_traces=true` + `session_id` is what folds validator sub-runs into one report.

## Test PDF inventory (per data class)

- **gene_expression**: COVERED. `temp_paper/daniela_gene_expression_papers/PMID39550471_...Barbelanne24_c-elegans-ppef...pdf`;
  loaded in sandbox as document_id `a31b1ff3-4fcd-42f8-9aec-0d299bcdbbe5`. Two more Daniela papers
  available in that folder.
- **gene / disease / chemical / allele / phenotype**: NOT yet a clean per-class set. Misc papers
  exist (`sample_fly_publication.pdf`, `WBPaper00061641.pdf`, micropub papers) but are not
  curated per class. Action (best-effort overnight): stage one open-access paper per class and
  record doc_ids here. NOTE: extraction only meaningfully exercises a class if the PDF actually
  contains that data type, and the other five extractors are still ENVELOPE pattern (so they test
  the existing inline-validation path, not the builder path) until they are migrated.

## Code anchor index

- `streaming_tools.py`: `run_specialist_with_events` 2274; builder_finalization 3414; gates 3432/3460;
  dispatch call 3462; internal-event emit 3506/3516; `_dispatch_domain_envelope_validators_for_chat`
  1627 (entry gate ~1636); `_is_domain_envelope_output_json` 1371; `_is_domain_envelope_extraction_output_type`
  176; `_is_builder_materializer_agent` 554 / `_BUILDER_MATERIALIZER_FINALIZATION_TOOLS` 547; forbid-output-schema ~2331.
- `extraction_builder_workspace.py`: `ExtractionBuilderFinalization` 85 (frozen); `finalize_extraction_payload`
  450; `stage_extraction_payload` 477; `build_internal_extraction_result_event` 511.
- `validator_dispatch.py`: `dispatch_active_validator_bindings` 129 (pure).
- `domain_envelopes/persistence.py`: `write_domain_envelope_checkpoint` 116 (durable save).
- `curation_workspace/curation_prep_service.py`: `run_curation_prep` 75; `ensure_domain_envelope_materialization`
  259; `_domain_envelope_from_extraction_result` 318.
- `curation_workspace/pipeline.py`: `run_post_curation_pipeline` 254; `_refresh_domain_envelope_validation_for_ref` 433.
- `api/chat_common.py`: `_build_extraction_candidate_from_tool_event` 144; `_persist_extraction_candidates` 327 (+ dedupe band-aid ~350).
- `agent_studio/catalog_service.py`: `get_agent_metadata` curation inheritance 2236-2265 (the (A) fix).
- `curation_workspace/extraction_results.py`: `build_extraction_envelope_candidate` 84; `_is_extraction_envelope_payload` 704; `get_agent_curation_metadata` 734; sanitizers 157-336.
- `agent_studio/flow_tools.py`: `_AGENT_ID_EQUIVALENTS` 143; `_GENE_EXPRESSION_AGENT_IDS` 185.
