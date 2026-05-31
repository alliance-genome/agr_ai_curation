# chemical_condition — Builder-Migration Approach (GROUNDING)

Phase 5 of the cross-domain builder migration (`docs/design/2026-05-31-cross-domain-first-pass-runbook.md`).
This is the LinkML + curation-DB + existing-pack grounding that justifies the code. gene_expression
is the proven reference shape (§2 of the runbook); copy it. No code is written here.

Grounded 2026-05-31 against three real sources:
1. LinkML clone `temp_agr_curation_schema/` @ `1b11d0888f19eba4ca72022200bb7d96b30d4a52`.
2. AWS curation DB (read-only, SELECT-only) via the backend container `CURATION_DB_URL` (URL never printed).
3. Existing envelope pack: `packages/alliance/domain_packs/chemical_condition/domain_pack.yaml`,
   `packages/alliance/agents/chemical_extractor/{agent.yaml,prompt.yaml,schema.py}`, and the python
   pack `packages/alliance/python/src/agr_ai_curation_alliance/domain_packs/chemical_condition/`.

> Directory-name reality (confirmed): the curatable agent is **`chemical_extractor`**
> (`agent_id: chemical_extractor`), NOT a `chemical_condition/` agent dir. `agents/experimental_condition/`
> is the *validator* agent (`agent_id: experimental_condition_validation`), not the extractor.
> The python pack has **no `conversion.py` yet** — only `submit.py`, `export.py`, `constants.py`,
> `__init__.py`. The migration ADDS `conversion.py` with `materialize_chemical_condition_builder_state`,
> mirroring `domain_packs/gene_expression/conversion.py`.

---

## 1. Target LinkML class + slots

Target write class: **`ExperimentalCondition`** (`is_a: AuditedObject`), defined in
`model/schema/phenotypeAndDiseaseAnnotation.yaml` (lines 450-508). It is wrapped for annotations by
**`ConditionRelation`** (`single_reference` + `condition_relation_type` + `conditions[]`, lines 526-552),
which is what joins a condition to a host annotation. The DTO ingest forms are
`ExperimentalConditionDTO` / `ConditionRelationDTO`.

### ExperimentalCondition slots (`phenotypeAndDiseaseAnnotation.yaml` slot defs ~616-718)

| Slot | Range | Required? | Notes |
|---|---|---|---|
| `condition_class` | `ZECOTerm` | **REQUIRED** (`required: true`, line 644) | High-level ZECO grouping term (AllianceSlim). `condition_class_curie` is `required: true` in the DTO. |
| `condition_chemical` | `ChemicalTerm` (`values_from: CHEBI, WBMol`) | optional | The specific chemical for `chemical condition`. THE defining slot for this data type. |
| `condition_id` | `ExperimentalConditionOntologyTerm` (ZECO or XCO) | optional | More-specific condition term when not covered by chemical/taxon/anatomy. |
| `condition_quantity` | string | optional | Free-text units/amount/degrees (e.g. `3 pM`, `2 mg/ml`, `20 ug/mL`). |
| `condition_free_text` | string | optional (`required: false`, line 657) | Dedicated free-text. |
| `condition_summary` | string | optional (`required: false`, line 713) | Human-readable summary; **generated centrally at the Alliance, NOT submitted by DQMs.** |
| `condition_anatomy` | `AnatomicalTerm` (UBERON/WBbt/ZFA/FBbt/GO) | optional | Not chemical-condition-relevant. |
| `condition_gene_ontology` | `GOTerm` | optional | Not chemical-condition-relevant. |
| `condition_taxon` | `NCBITaxonTerm` | optional | e.g. bacterial-infection conditions. |
| `unique_id` | string | optional (`required: false`) | Generated centrally at AGR. |

### ConditionRelation slots (~533-552)

| Slot | Range | Required? | Notes |
|---|---|---|---|
| `condition_relation_type` | `VocabularyTerm` (CV 'Condition Relation Type') | **REQUIRED** (`required: true`, line 551) | e.g. `has_condition`. `condition_relation_type_name` is `required: true` in the DTO. |
| `single_reference` | `Reference` | optional in LinkML (`required: false`, line 549) | The source paper; in practice every relation has one. |
| `conditions` | `ExperimentalCondition[]` | (multivalued) | 1+ conditions per relation. |
| `handle`, `unique_id` | — | optional | Generated at AGR. |

### Chemical / condition ontology classes (`model/schema/ontologyTerm.yaml`)

- `ChemicalTerm` (line 393): `is_a: OntologyTerm`, `abstract: true`; adds chem slots
  `inchi, inchi_key, iupac, formula, smiles`. Concrete subclasses: `XSMOTerm`, `Molecule` (WormBase).
- `ZECOTerm` (line 249) / `XCOTerm` (line 252): `is_a: ExperimentalConditionOntologyTerm` (abstract,
  `is_a: OntologyTerm`). ZECO is the condition_class CV; XCO appears for some `condition_id` rows.

### "X must match Y" constraints / mirrors

- LinkML does NOT declare an entity_assayed-style cross-field mirror for ExperimentalCondition (unlike
  gene_expression). The only invariant-style constraint the existing pack enforces is the extractor's
  `condition_relation_type.name == "has_condition"` (schema.py `_validate_condition_semantics`) — this is
  the dominant relation type (see DB reality below), not a LinkML mirror. No `materializes_to_field_paths`
  mirror is currently declared in `domain_pack.yaml`; the gene_expression-style mirror mechanism is
  available if a future mirror is needed but **none is required by LinkML here** (see Open Questions).
- FK constraints verified in the DB confirm `condition_chemical`/`condition_class`/`condition_id` all
  point at `public.ontologyterm(id)`; the relation/host joins are separate tables.

---

## 2. Curation-DB reality (READ-ONLY, SELECT-only)

Discovered condition tables (`information_schema.tables` ILIKE `%condition%`/`%experimentalcondition%`):
`conditionrelation`, `conditionrelation_experimentalcondition`, `experimentalcondition`,
`diseaseannotation_conditionrelation`, `phenotypeannotation_conditionrelation`,
`geneexpressionannotation_conditionrelation`. (Matches the pack's `provider_refs.alliance_curation_db`.)

`public.experimentalcondition` has **15,338 rows**. Columns: `id, uniqueid, conditionquantity,
conditionfreetext, conditionsummary, datecreated, internal, obsolete, conditionanatomy_id,
conditionchemical_id, conditionclass_id, conditiongeneontology_id, conditionid_id, conditiontaxon_id`.

### Real example rows (chemical-bearing, newest first; class + chemical resolved via `ontologyterm`)

| ec.id | condition_class | condition_chemical | quantity |
|---|---|---|---|
| 200016096 | `ZECO:0000111` chemical treatment | `CHEBI:9168` sirolimus | `3 pM` |
| 200016095 | `ZECO:0000111` chemical treatment | `CHEBI:29699` tunicamycin | `2 mg/ml` |
| 200016094 | `ZECO:0000111` chemical treatment | `CHEBI:50011` Calcofluor White | `20 ug/mL` |
| 200016088 | `ZECO:0000111` chemical treatment | `CHEBI:33216` bisphenol A | `1.3 mM` |
| 200010274 | `ZECO:0000111` chemical treatment | `WB:WBMol:00002751` Thiazoles | (null) |

`uniqueid` is a centrally-generated composite, e.g. `ZECO:0000111|CHEBI:9168|3 pM|chemical:sirolimus`.

### Real CURIE namespaces

- **`condition_chemical`** (7,975 non-null): `CHEBI:` 7,915 and `WB:WBMol:` 60. NOTE the WB form is the
  **full `WB:WBMol:00002751`** prefix, not bare `WBMol:`. (The pack/agent currently emphasize ChEBI only —
  see Open Questions on WBMol coverage.)
- **`condition_class`** (15,338 = 100% filled): all `ZECO:`. Top values:
  `ZECO:0000111` chemical treatment (12,867), `ZECO:0000104` experimental conditions (953),
  `ZECO:0000105` biological treatment (791), `ZECO:0000229` physical alteration (262),
  `ZECO:0000160` temperature exposure (244), `ZECO:0000208` radiation (88), `ZECO:0000112` diet (36).
- **`condition_id`** (3,956 non-null): `ZECO:` 3,876 and `XCO:` 80 (e.g. `ZECO:0000164` temperature shock,
  `XCO:0000367` surgical device implantation sham procedure).
- **`condition_relation_type`** vocab (via `conditionrelation` → `vocabularyterm`):
  `has_condition` 19,193, `ameliorated_by` 189, `induced_by` 113, `exacerbated_by` 43,
  `not_ameliorated_by` 10, `not_exacerbated_by` 1. `has_condition` dominates (matches the extractor's hard
  `== "has_condition"` rule), but other relation types DO exist in real data (see Open Questions).

### Reliably-present slots (fill rates over all 15,338 rows)

| Slot | Filled | % |
|---|---|---|
| `condition_class` | 15,338 | 100% |
| `condition_summary` | 15,338 | 100% (centrally generated — NOT a DQM submission target) |
| `condition_chemical` | 7,975 | 52% |
| `condition_quantity` | 6,455 | 42% |
| `condition_id` | 3,956 | 26% |
| `condition_taxon` | 237 | 1.5% |
| `condition_anatomy` | 353 | 2.3% |
| `condition_free_text` | 58 | 0.4% |

Takeaway: for chemical conditions the reliably-present payload is **condition_class (ZECO) + condition_chemical
(ChEBI/WBMol) + condition_relation_type (has_condition) + condition_quantity (often)**. `condition_free_text`
is rare; `condition_summary` is always present but Alliance-generated, so the extractor should not treat it
as a primary write target.

### Write behavior (from the pack, still true): EXPORT/WRITE BLOCKED

Chemical extraction does not identify the host annotation row or a durable source `reference_id`, so
inserts into `experimentalcondition` / `conditionrelation` / the `*_conditionrelation` join tables are
blocked. The builder migration's target is a **structurally-clean PENDING envelope**, not export — same as
gene_expression's pending-only end state.

---

## 3. Curatable objects / fields (from the existing pack — REUSED)

`domain_pack.yaml` (`pack_id: agr.alliance.chemical_condition`, version `0.1.0`,
`curatable_unit_object_type: ChemicalCondition`). Four object types, three roles
(`curatable_unit`, `validated_reference`, `metadata_only`):

- **`ChemicalCondition`** (`curatable_unit`, model `ChemicalConditionPayload`, schema `ExperimentalCondition`).
  Required fields: `chemical` (object_ref→ChemicalTerm), `source_reference` (object_ref→Reference),
  `evidence_quote` (object_ref→EvidenceQuote), `condition_relation_type` (+`.name`),
  `condition_class` (+`.name`; `.curie` optional fast-path), `condition_chemical` (+`.name`; `.curie`
  optional ChEBI fast-path), `source_chemical_mention` (protected evidence anchor), `confidence`,
  `evidence_record_ids[0]`, `host_annotation_type`/`host_annotation_id` (export-context).
  Optional: `condition_quantity`, `condition_free_text`, `condition_summary`, `role`, `timing`.
- **`ChemicalTerm`** (`validated_reference`, model `ChemicalTermPayload`): `curie` (optional fast path),
  `name` (required) — both ChEBI-validatable.
- **`Reference`** (`validated_reference`, model `ReferencePayload`): `reference_id` (export-required,
  pending until materialization wired), `title`, `filename`.
- **`EvidenceQuote`** (`metadata_only`, model `EvidenceQuotePayload`): `verified_quote` (required),
  `evidence_record_id`, `entity`, `page`, `section`, `subsection`, `chunk_id`, `figure_reference`.

Current extractor (`chemical_extractor/schema.py`) envelope confirms the same object set:
`ChemicalConditionPayload` carries `condition_relation_type`, `condition_class`, `condition_chemical`,
`source_chemical_mention`, `confidence`, `evidence_record_ids[≥1]`, `source_mentions[≥1]`, `role`
(treatment/assay_reagent/buffer/control/other/unspecified), and optional `condition_quantity /
condition_free_text / condition_summary / timing / host_annotation_*`. Hard rule:
`condition_relation_type.name == "has_condition"`.

These objects/fields are REUSED unchanged — the migration changes the extraction MECHANISM (one-shot
`output_schema` → builder stage/finalize tools), not the curation target.

---

## 4. Validator bindings (REUSE — list)

All from `domain_pack.yaml` (`metadata.validator_bindings`). The migration keeps these bindings; inline
validation runs them on the builder-finalized envelope (runbook invariant 5).

**Active bindings:**
- `chemical_condition.chebi_api_lookup` — agent `agr.alliance/chemical_validation`, tool `chebi_api_call`;
  applies to `ChemicalCondition` fields `condition_chemical.curie`, `condition_chemical.name`;
  result → `condition_chemical.curie` (chebi_id) + `condition_chemical.name`. required, non-blocking.
- `chemical_condition.term_chebi_api_lookup` — same agent/tool; applies to `ChemicalTerm` fields
  `curie`, `name`. required, non-blocking.
- `chemical_condition.condition_ontology_lookup` — agent `ontology_term_validation`; `ChemicalCondition`
  field `condition_class.curie` (+`.name` label); `accepted_prefixes: [ZECO]`, `ontology_term_type: ZECOTerm`,
  `exact_match: true`. required, non-blocking.
- `chemical_condition.condition_relation_type_lookup` — agent `controlled_vocabulary_validation`;
  `ChemicalCondition` field `condition_relation_type.name` against vocabulary `Condition Relation Type`.
  required, non-blocking.

**Active validators (declared)** also include `chemical_condition.domain_envelope_shape`,
`chemical_condition.required_payload_fields`, `chemical_condition.chebi_curie_format`,
`chemical_condition.export_context_blocker`.

**Under-development bindings (carried as-is, do NOT activate in this pass):**
`chemical_condition.pending_envelope_validator`, `source_reference_validation`,
`experimental_condition_validation` (composite), `chemical_condition.chebi_curie_format`,
`chemical_condition.term_chebi_curie_format`, `chemical_condition.export_projection_validator`
(host-annotation export/submission projection — blocked by ALL-425).

---

## 5. Evidence model

Identical to gene_expression's verified-evidence model (runbook invariants):
- The agent calls `record_evidence` (+ list/get/attach/detach/discard/update evidence tools) to build
  verified `metadata.evidence_records[]` entries (each with `verified_quote`, page/section/chunk locator).
- Each staged `ChemicalCondition` candidate carries non-empty `evidence_record_ids` referencing those
  verified records; the materializer rejects candidates with missing/blank `verified_quote`.
- `EvidenceQuote` is a `metadata_only` object preserving the record_evidence-verified quote + locator.
- `source_chemical_mention` / `source_mentions[]` are protected paper-evidence anchors (not edit targets).
- Evidence and resolver provenance are materialized by backend builder finalization — the payload never
  stores raw evidence text (gene_expression `FORBIDDEN_PAYLOAD_EVIDENCE_FIELDS` analogue).

---

## 6. Builder mapping (gene_expression-shaped target)

Mirror `domain_packs/gene_expression/conversion.py` + `tools/agr_curation.py` finalize adapter +
`builder_finalization.finalize_builder_extraction`. Per-type-owned files only (no platform edits;
Phase 0 already generalized builder detection via the `builder_finalization: true` binding flag).

### 6a. Staging tools (agent.yaml tool list; bindings.yaml registrations)

Drop `output_schema: ChemicalExtractionResultEnvelope` (set `output_schema: null`) on
`agents/chemical_extractor/agent.yaml`, and swap to the builder tool set (copy gene_expression's list):
- evidence tools: `record_evidence`, `list_recorded_evidence`, `get_recorded_evidence`,
  `attach_evidence_to_object`, `detach_evidence_from_object`, `discard_recorded_evidence`,
  `update_recorded_evidence_metadata`
- selector-resolution tools: `search_domain_field_terms`, `inspect_ontology_term`,
  `resolve_domain_field_term` (used for ZECO condition_class, ChEBI/WBMol chemical, and the
  Condition Relation Type vocabulary)
- staging tools (NEW, per-domain): `stage_chemical_condition_observation`,
  `patch_chemical_condition_observation`, `discard_chemical_condition_observation`,
  `list_staged_chemical_condition_observations`
- finalize tool (NEW): `finalize_chemical_condition_extraction`

Register all in `packages/alliance/tools/bindings.yaml`, with **`metadata.builder_finalization: true`**
on the finalize tool (this is what `streaming_tools._builder_finalization_tool_names` keys off — a
domain-pack edit, not a platform edit). PREFER a per-domain builder-tools module over piling into
`agr_curation.py` (runbook §9), to avoid shared-file conflicts.

### 6b. `materialize_chemical_condition_builder_state` (new `conversion.py`)

Mirror `materialize_gene_expression_builder_state`:
- Reads finalized builder candidates from the `ExtractionBuilderWorkspace` (`workspace.get_candidate`),
  pulls `staged_fields`, `evidence_record_ids`, resolver selection refs.
- Emits the extraction-output payload `{summary, curatable_objects, metadata}` where each
  `curatable_objects[]` is a PENDING `ChemicalCondition` (+ the `validated_reference`/`metadata_only`
  companions) with payload assembled from staged ZECO class, ChEBI/WBMol chemical, relation type,
  quantity, evidence ids.
- **`metadata_refs` MUST be RELATIVE** to the extraction-metadata namespace (runbook invariant 1):
  `raw_mentions[N]`, `evidence_records[N]` — never absolute `extraction_metadata.<path>`, and never
  rewritten in a converter. Resolve against `envelope.metadata.extraction_metadata` (mirror
  `_metadata_ref_findings`).
- Validates evidence presence/quotes; returns a `*MaterializationResult` with `payload`/`issues`/
  `source_candidate_ids`/`evidence_record_ids`.

### 6c. Finalize adapter (thin, in the per-domain tools module)

Mirror `_finalize_gene_expression_extraction_impl`: `finalize_chemical_condition_extraction(candidate_ids)`
delegates to:
```
finalize_builder_extraction(
    workspace=...,
    candidate_ids=...,
    materialize=_materialize_chemical_condition_with_events,   # wraps materialize_chemical_condition_builder_state + trace events
    evidence_records=...,
    resolver_entry_lookup=...,
    materialized_candidate_prefix="chemical-condition-envelope",
    require_evidence_record_ids=True,
    require_resolver_selections=True,   # confirm: see Open Questions on selector requiredness
)
```
All structural staging/finalize/idempotency control flow stays in the project-agnostic
`finalize_builder_extraction`; only the materializer + result shaping are domain-specific.

### 6d. Materializes-to-field-paths mirrors

None required by LinkML for ExperimentalCondition (see §1). Do NOT special-case in code; if a mirror is
ever needed, declare it as `materializes_to_field_paths` metadata in `domain_pack.yaml` (runbook invariant 4).

### 6e. Pass/fail gate (runbook §7)

Fresh e2e extraction must persist a structurally-clean envelope: zero `entity_assayed_mismatch`,
`object_not_pending`, `metadata_refs_missing`, `validator_materialization_invalid`; only
`validator_resolved` (INFO) / `validator_unresolved` / (maybe) `validator_error` remain.

---

## Open questions for Chris (do NOT guess)

1. **WBMol chemical coverage.** Real data has 60 `WB:WBMol:00002751`-style chemicals (full `WB:WBMol:`
   prefix), but the active validator binding is ChEBI-only (`chemical_condition.chebi_api_lookup` →
   `chebi_api_call`) and the pack copy emphasizes ChEBI. Should the chemical_condition builder support
   WBMol chemicals (and is there a WBMol lookup tool), or is ChEBI-only acceptable for the first pass with
   WBMol left as `validator_unresolved`?

2. **`condition_relation_type` beyond `has_condition`.** The extractor hard-rejects any relation type other
   than `has_condition` (`schema.py _validate_condition_semantics`), but real data contains `ameliorated_by`
   (189), `induced_by` (113), `exacerbated_by` (43), and negated variants. Should the builder keep the
   `has_condition`-only restriction, or stage the real relation types (they are valid CV rows and the
   `condition_relation_type_lookup` binding already validates the vocabulary)?

3. **Host-annotation / export blocker for the PENDING gate.** The pack marks export BLOCKED because chemical
   extraction cannot identify the host annotation row (`host_annotation_type`/`host_annotation_id`) or a
   durable `reference_id`, and there are `chemical_condition.export_context_blocker` /
   `export_projection_validator` (ALL-425) validators. For a "0 structural findings" pending gate, are the
   export-context blocker findings expected/acceptable (i.e. they are NOT in the structural-findings set), or
   must `host_annotation_*` be populated during extraction? gene_expression's gate ignores export blockers —
   confirm chemical_condition follows the same pending-only standard.

4. **`condition_id` (ZECO/XCO specific term) handling.** 26% of rows carry a `condition_id`
   (ZECO 3,876 / XCO 80) distinct from `condition_class`. The pack lists `condition_id.curie` in the
   under-development `experimental_condition_validation` input but has NO active `condition_id` field on the
   `ChemicalCondition` object or active binding. Should the builder stage `condition_id` for chemical
   conditions in this pass, or leave it out (chemical conditions are dominated by class+chemical and rarely
   need a separate condition_id)?

5. **Resolver-selection requiredness.** gene_expression's finalize requires resolver-backed
   `helper_selections` provenance for controlled fields (`require_resolver_selections=True`) and a hard
   provenance gate. For chemical_condition the controlled selectors are ZECO condition_class, the chemical
   term, and the Condition Relation Type vocab. Should ALL three require `resolve_domain_field_term`
   provenance (strict, gene_expression parity), or should chemical (which has the optional `.curie`
   ChEBI fast-path and name-only candidates) be exempted from the strict resolver-provenance gate?

6. **Representative test document for e2e.** The runbook's e2e harness needs a processed PDF doc id that
   actually contains chemical conditions (gene_expression used `DOC=a31b1ff3`). The `documents` table query
   used for gene_expression did not return rows under the same column names here, so a chemical-condition
   test doc id still needs to be identified/confirmed before the §7 e2e run. Which document should be the
   canonical chemical_condition extraction fixture?
