# Disease — Builder-Migration Grounding (Phase 2)

Grounding doc for migrating the `disease` data type from the envelope pattern
(`disease_extractor` agent has `output_schema: DiseaseExtractionResultEnvelope`,
one-shot structured output) to the gene_expression **builder pattern** (stage
candidates via tools → `finalize_disease_extraction` materializes the envelope;
inline validation runs in the chat turn). Read alongside
`docs/design/2026-05-31-cross-domain-first-pass-runbook.md` §§3–5. gene_expression
is the proven reference shape; this doc captures the three real sources before any
code is written.

> Scope note: the migration target is the **`disease_extractor`** agent
> (`agent_id: disease_extractor`, `output_schema: DiseaseExtractionResultEnvelope`,
> `curation.adapter_key: disease`, `domain_pack_id: agr.alliance.disease`). The
> sibling `disease` agent (`agent_id: disease_validation`, the DOID validator) is
> NOT being migrated — it is a validator agent and stays as-is; its DOID lookup is
> reused as a validator binding (`disease_ontology_term_lookup`).

---

## 1. Target LinkML class + slots

Source (read-only clone @ `1b11d0888f19eba4ca72022200bb7d96b30d4a52`):
`temp_agr_curation_schema/model/schema/phenotypeAndDiseaseAnnotation.yaml`,
with identity/embedded ranges in `core.yaml`, `ontologyTerm.yaml`,
`reference.yaml`, `controlledVocabulary.yaml`.

### Class hierarchy

- `Annotation` (is_a `SingleReferenceAssociation`) — parent. Contributes:
  `curie`, `unique_id`, `primary_external_id`, `mod_internal_id`,
  `condition_relations`, `related_notes`, `data_provider`,
  `data_provider_cross_reference`; `single_reference` **required**.
- `DiseaseAnnotation` (is_a `Annotation`) — **abstract: true**. The conceptual
  target. Adds the disease-specific slots below.
- Three concrete subtypes (the real write targets, picked by subject kind):
  - `GeneDiseaseAnnotation` (is_a `DiseaseAnnotation`) — `disease_annotation_subject` **required**, range `Gene`; `relation` range `VocabularyTerm` (CV 'Disease Relation' subset 'Gene Disease Relation'); extra slot `sgd_strain_background`.
  - `AlleleDiseaseAnnotation` (is_a `DiseaseAnnotation`) — `disease_annotation_subject` range `Allele`; `relation` subset 'Allele Disease Relation'; optional `inferred_gene`, `asserted_genes`.
  - `AGMDiseaseAnnotation` (is_a `DiseaseAnnotation`) — `disease_annotation_subject` range `AffectedGenomicModel`; `relation` subset 'AGM Disease Relation'; optional `inferred_gene`, `inferred_allele`, `asserted_genes`, `asserted_alleles`.

Because `DiseaseAnnotation` is abstract and the concrete subtype depends on the
subject biological-entity kind, the existing pack emits a **pending** envelope on
the abstract class and **blocks writes** until concrete subject materialization is
verified. The builder migration keeps that posture (writes stay blocked; see §6
open questions).

### Key slots (required vs optional, value types/enums)

| Slot | Req? | Range / type | Notes |
|---|---|---|---|
| `disease_annotation_object` | **required** | `DOTerm` | The Disease Ontology term. CURIE namespace `DOID:` (see §2). |
| `disease_annotation_subject` | required on concrete subtypes | `Gene` / `Allele` / `AffectedGenomicModel` | Drives which concrete subtype is written. Abstract parent does not require it. |
| `relation` | **required** | `VocabularyTerm` (CV 'Disease Relation', per-subtype subset) | e.g. `is_implicated_in`, `is_model_of`, `is_marker_for`. Pack field name: `disease_relation_name`. |
| `single_reference` | **required** | `Reference` | The reference asserting the association. |
| `evidence_codes` | **required, multivalued** | ontology terms | ECO CURIEs (see §2). Pack field: `evidence_code_curies[]`. |
| `data_provider` | required (`Annotation`) | `Organization` | MOD/source. Pack field: `data_provider.abbreviation`. |
| `negated` | optional (boolean) | — | Negative qualifier. |
| `genetic_sex` | optional | `VocabularyTerm` (CV 'Genetic Sex') | Sparsely filled (see §2). |
| `annotation_type` | optional | `VocabularyTerm` | manually/automatically asserted. |
| `with_or_from` | optional | — | Human ortholog for ISS/ISO evidence (SGD). |
| `disease_qualifiers` | optional, multivalued | `VocabularyTerm` | — |
| `secondary_data_provider` | optional | `Organization` | — |
| `disease_genetic_modifier_genes/alleles/agms` + `_relation` | optional | — | Genetic-modifier context. |
| `condition_relations` | optional (`Annotation`) | `ConditionRelation` → `ExperimentalCondition` | Experimental-condition context (ZECO/CHEBI/etc. — see §2 condition rows). |
| `with_or_from`, `related_notes` | optional | — | — |

DTO-side (ingest) slot names worth noting (used in pack `provider_refs`):
`DiseaseAnnotationDTO` carries `disease_relation_name`, `do_term_curie`,
`evidence_code_curies`, `annotation_type_name`, `genetic_sex_name`,
`disease_genetic_modifier_identifiers`, `disease_genetic_modifier_relation_name`;
per-subtype DTOs add the subject identifier (`gene_identifier` /
`allele_identifier` / `agm_identifier`).

### "X must match Y" constraints → `materializes_to_field_paths` mirrors

- **Disease term**: `disease_annotation_object.curie` / `.name` are the validated
  DOID identity; the embedded `DOTerm` snapshot must mirror the validated term.
  Mirror target: `disease_annotation_object` (curie/name) ← validated ontology
  lookup result. (Pattern is exactly gene_expression's subject-gene →
  `entity_assayed` mirror.)
- **Subject identity** (blocked): when materialized, the validated subject
  (`disease_annotation_subject.subject_identifier`/`subject_label`) must mirror
  the concrete `Gene`/`Allele`/`AGM` identity resolved by `subject_entity_validation`.
- **Reference** (blocked): `single_reference.curie` / `.reference_id` must mirror
  the resolved `Reference` (AGRKB curie — see §2).

These mirrors should be declared as `materializes_to_field_paths` metadata in
`domain_pack.yaml` (invariant §5.4), not code special-casing.

---

## 2. Curation-DB reality (discovered tables + real rows)

Read-only `CURATION_DB_URL` from the backend container (SELECT only; URL never
printed). Discovery via `information_schema.tables` `ILIKE '%disease%'`.

### Discovered tables (15)

```
diseaseannotation                 (base, 81,227 rows)
genediseaseannotation             (43,148)   allelediseaseannotation (5,551)   agmdiseaseannotation (32,528)
diseaseannotation_conditionrelation, diseaseannotation_gene,
diseaseannotation_modifieragm, diseaseannotation_modifierallele, diseaseannotation_modifiergene,
diseaseannotation_note, diseaseannotation_ontologyterm (evidence codes),
diseaseannotation_vocabularyterm,
agmdiseaseannotation_allele, agmdiseaseannotation_gene, allelediseaseannotation_gene
```

`diseaseannotation` is the base; the concrete subtype is determined by which of
`{gene,allele,agm}diseaseannotation` carries the same `id`. Evidence codes live
in `diseaseannotation_ontologyterm.evidencecodes_id → ontologyterm`.

### Slot fill rates (n = 81,227 base rows)

| Column | Filled | Read as |
|---|---|---|
| `diseaseannotationobject_id` (disease term) | 81,227 / 100% | **always present** |
| `relation_id` | 81,227 / 100% | **always present** |
| `evidenceitem_id` (single_reference) | 81,227 / 100% | **always present** |
| `dataprovider_id` | 81,227 / 100% | **always present** |
| `negated` | 81,227 (boolean, mostly false) | always set |
| `annotationtype_id` | 35,404 / 44% | common |
| `modinternalid` | 16,480 / 20% | sometimes |
| `secondarydataprovider_id` | 5,767 / 7% | rare |
| `geneticsex_id` | 1,598 / 2% | **rare** |
| `primaryexternalid` | 1,599 / 2% | rare (WB only) |

Evidence codes (multivalued, required) are reliably present via the
`diseaseannotation_ontologyterm` join. So the **reliably-filled curated slots** a
builder must always produce are: disease term (DOID), relation, single_reference,
evidence_codes (ECO), data_provider — matching the LinkML `required` set.

### Real CURIE namespaces (from curated rows)

- **Disease term** (`disease_annotation_object`): `DOID:` only.
  Examples: `DOID:0080662` "atrial standstill 1", `DOID:9452`
  "steatotic liver disease", `DOID:1793` "pancreatic cancer", `DOID:9970` "obesity".
- **Evidence codes** (`evidence_codes`): `ECO:` only. Top real values:
  `ECO:0007191`, `ECO:0000270`, `ECO:0007013`, `ECO:0000033`, `ECO:0000315`,
  `ECO:0000314`, `ECO:0000304`.
- **Relation** (`relation`, CV 'Disease Relation'): real values are
  `is_implicated_in` (30,841), `is_model_of` (23,238), `is_marker_for` (17,858),
  `is_ameliorated_model_of` (5,863), `is_exacerbated_model_of` (3,427). (Note: in
  curated data the relation vocabulary is shared across subtypes, not split by the
  LinkML per-subtype subset comment.)
- **Genetic sex** (`genetic_sex`, CV 'Genetic Sex'): `hermaphrodite` (1,586),
  `male` (12) — almost entirely WormBase; near-absent elsewhere.
- **single_reference** (`evidenceitem_id` → `informationcontententity.curie`):
  `AGRKB:` curies, e.g. `AGRKB:101000000829489`. (No raw PMID in this column;
  references are pre-resolved to AGRKB.)
- **Subject identity** (`diseaseannotationsubject_id → biologicalentity.primaryexternalid`):
  MOD-specific namespaces — `HGNC:` (human via OMIM/RGD), `RGD:`, `WB:WBGene…` /
  `WB:WBVar…` / `WB:WBGenotype…`, `FB:`, etc.
- **Data provider** (`dataprovider_id → organization.abbreviation`):
  `OMIM`, `RGD`, `WB`, `MGI`, `SGD`, `FB`, `ZFIN`.

### 5 real example rows

| id | subtype | subject | disease | relation | data_provider | reference |
|---|---|---|---|---|---|---|
| 18392914 | Gene | HGNC:4279 | DOID:0080662 (atrial standstill 1) | is_implicated_in | OMIM | AGRKB:101000000829489 |
| 209079950 | Gene | HGNC:4964 | DOID:4481 (allergic rhinitis) | is_marker_for | RGD | AGRKB:101000000445369 |
| 7824671 | Gene | WB:WBGene00004271 | DOID:9970 (obesity) | is_implicated_in | WB | AGRKB:101000000622621 |
| (allele) | Allele | WB:WBVar00092305 | DOID:11758 | is_implicated_in | — | — |
| (agm) | AGM | FB:FBgo0504845 | DOID:9884 | is_model_of | — | — |

Representative condition row (from existing pack inspection):
`diseaseannotation_id=209127194`, `condition_relation_type=has_condition`,
`condition_class=ZECO:0000111`, `condition_id=ZECO:0000238`,
`condition_chemical=CHEBI:6909`.

---

## 3. Curatable objects / fields (existing envelope pack — REUSED)

From `packages/alliance/domain_packs/disease/domain_pack.yaml` +
`.../python/.../domain_packs/disease/conversion.py`. The builder migration changes
the **extraction mechanism**, not the curation target — these objects/fields and
their `definition_state` posture are reused verbatim.

- **Curatable unit object**: `DiseaseAnnotation` (`object_role: curatable_unit`,
  `model_ref: PendingDiseaseAssertionPayload`, `domain_pack_id: agr.alliance.disease`,
  schema pinned to abstract `DiseaseAnnotation` @ commit 1b11d088). `metadata.write_behavior.status: blocked` (ALL-425), `export_behavior: blocked`.

- **`complete` (extractor-owned) payload fields** — what the builder must stage/fill:
  - `mention` (string, required) — disease mention as written.
  - `disease_annotation_object` (object → `DiseaseOntologyTermSnapshotPayload`, required) with `.name` (required) and optional `.curie` (DOID fast-path). Validatable via `disease_ontology_term_lookup`.
  - `role` (enum `DiseaseAssertionRole`: primary/background/comparative/model_context/unspecified, required).
  - `confidence` (enum `DiseaseAssertionConfidence`: high/medium/low, required).
  - `data_provider` + `data_provider.abbreviation` (workflow context; validatable via `disease_data_provider_lookup`).
  - `evidence_record_ids[]` (required) and `evidence_records[]` (required, model `EvidenceRecordPayload`) with per-record `evidence_record_id`, `verified_quote`, `page`, `section`, `chunk_id` (+ optional `subsection`, `entity`, `figure_reference`).

- **`under_development` fields**: `disease_relation_name`,
  `condition_relations[]` (+ nested `condition_relation_type.name`, `conditions[].condition_class/id/chemical/taxon.curie`, `condition_free_text`, `condition_summary`).

- **`blocked` fields** (write targets, blocked by ALL-410 / ALL-425):
  `disease_annotation_subject` (+ `.subject_type` enum gene/allele/agm/unknown, `.subject_label`, `.subject_identifier`), `single_reference` (+ `.curie`), `evidence_code_curies[0]`, `data_provider` (workflow-only).

- **`REQUIRED_DISEASE_PAYLOAD_FIELDS`** (`constants.py`, the contract the builder
  materializer output must satisfy): `mention`, `disease_annotation_object`,
  `disease_annotation_object.name`, `role`, `confidence`, `data_provider`,
  `data_provider.abbreviation`, `evidence_record_ids`, `evidence_records`.

- **Forbidden legacy flat fields** (`conversion.py`): `normalized_id`,
  `normalized_label`, `disease_curie`, `disease_name` — use the nested
  `disease_annotation_object.{curie,name}` instead. `FORBIDDEN_LEGACY_COLLECTIONS`
  (items/annotations/genes/alleles/diseases/chemicals/phenotypes/…) must not appear.

---

## 4. Validator bindings (REUSE — do not redefine)

From `domain_pack.yaml` `validator_bindings`. The migration reuses every binding
unchanged; the builder envelope runs the active ones inline (invariant §5.5).

**Active bindings** (run on builder output):

| binding_id | validator_agent | field path(s) | accepted prefix |
|---|---|---|---|
| `disease_ontology_term_lookup` | `ontology_term_validation` | `disease_annotation_object.curie`, `.name` | `DOID` (ontology_term_type `DOTerm`, family `disease`) |
| `disease_relation_cv_lookup` | `controlled_vocabulary_validation` | `disease_relation_name` | CV 'Disease Relation' |
| `disease_condition_relation_lookup` | `controlled_vocabulary_validation` | `condition_relations[0].condition_relation_type.name` | CV 'Condition Relation Type' |
| `disease_data_provider_lookup` | `data_provider_validation` | `data_provider.abbreviation` | — |

All active bindings: `required: true`, `blocking: false`, `allow_opt_out: true`,
`curator_override.allowed: false`.

**Under-development bindings** (declared, not dispatching — kept blocked):
`disease_pending_envelope_validator` (the object-level `validator_binding_id`),
`experimental_condition_validation`, `disease_subject_materialization`
(subject_entity_validation, ALL-410), `disease_reference_materialization`
(reference_validation, ALL-425), `disease_evidence_code_lookup` (ECO via
ontology_term_validation, ALL-425). These stay as-is; the builder does not
activate them.

---

## 5. Evidence model

Identical to gene_expression's evidence contract; reused wholesale:

- Evidence is recorded via the shared `record_evidence` tool → run-scoped
  `metadata.evidence_records[]` (id, verified_quote, page, section, chunk_id,
  optional subsection/entity/figure_reference). The builder reads the active-run
  evidence snapshot at finalize time (`get_active_evidence_records_snapshot`).
- Each `DiseaseAnnotation` candidate carries `evidence_record_ids[]` referencing
  those records; the materializer **snapshots** the full records into the payload's
  `evidence_records[]` and asserts `payload.evidence_record_ids == obj.evidence_record_ids`
  and that every referenced id resolves to a verified record with a non-empty
  `verified_quote` (see `_validate_payload_evidence_snapshot` in `conversion.py`).
- A per-object INFO/RESOLVED finding (`alliance.disease_assertion.tool_verified` /
  `…domain_envelope_extracted`) records that evidence was verified pre-conversion.
- `metadata.raw_mentions[]` must preserve harvested mentions (entity_type
  `disease`) — the contract rejects objects with no `raw_mentions`.

---

## 6. Builder mapping (envelope → builder)

Target end state (per runbook §1): a fresh extraction persists a structurally
clean pending envelope — zero `entity_assayed_mismatch`, `object_not_pending`,
`metadata_refs_missing`, `validator_materialization_invalid`; only
`validator_resolved` (INFO) + genuine `validator_unresolved`/`validator_error`.

### Tools the agent stages with (mirror gene_expression; per-domain module)

- `stage_disease_observation` — stage one pending `DiseaseAnnotation` candidate
  with: `mention`, `disease_annotation_object` (name + optional DOID curie),
  `role`, `confidence`, `data_provider.abbreviation`, `evidence_record_ids[]`, and
  optional `disease_relation_name` / `condition_relations`. Controlled selectors
  (disease term, relation, data provider) reference prior
  `resolve_domain_field_term` call IDs (resolver provenance), as gene_expression does.
- `patch_disease_observation` — bounded repairs to a staged candidate.
- `discard_disease_observation` — discard a staged candidate (keeps builder trace).
- `list_staged_disease_observations` — compact builder-state summaries.
- `finalize_disease_extraction` — the materializer-finalize tool.
- Shared (already on `disease_extractor`/gene_expression): `search_document`,
  `read_chunk/section/subsection`, `record_evidence` (+ list/get/attach/detach/
  discard/update), `get_agent_contract`, `agr_species_context_lookup`,
  `search_domain_field_terms`, `inspect_ontology_term`, `resolve_domain_field_term`.

Register all five in `bindings.yaml`; put `metadata.builder_finalization: true`
**only** on `finalize_disease_extraction` (Phase-0 detection derives the
finalize-tool set from that flag — no platform/`streaming_tools.py` edit).

### What `materialize_disease_builder_state` must produce

Add `materialize_disease_builder_state(*, workspace, candidate_ids, evidence_records,
resolver_entry_lookup, produced_by="disease_extractor")` in
`domain_packs/disease/conversion.py`, mirroring
`materialize_gene_expression_builder_state`. For each finalized candidate it emits
one `CuratableObjectEnvelope` (object_type `DiseaseAnnotation`, role
`curatable_unit`, model_ref `PendingDiseaseAssertionPayload`, schema_ref pinned to
abstract `DiseaseAnnotation` @ 1b11d088, `definition_state: in_development`,
`status: PENDING`, `metadata.write_behavior.status: blocked`,
`metadata.assertion_kind: pending_disease_assertion`) whose `payload` is built by
the existing `_payload_for_assertion` shape (`mention`,
`disease_annotation_object.{curie,name}`, `role`, `confidence`,
`data_provider.abbreviation`, `evidence_record_ids[]`, snapshot `evidence_records[]`,
optional `disease_relation_name`/`condition_relations`/`evidence_code_curies`).

The overall output payload is the extraction-output shape
`{summary, curatable_objects, metadata}` where `metadata` is
`ExtractionEnvelopeMetadata` carrying `raw_mentions[]` (entity_type `disease`) and
`evidence_records[]`.

**RELATIVE metadata_refs** (invariant §5.1): each object's `metadata_refs` point
into the extraction-metadata namespace, never absolute `extraction_metadata.<path>`:
- `raw_mentions[N]` (role `source_mention`) — one per object.
- `evidence_records[M]` (role `verified_evidence`) — one per referenced evidence id.
Resolved against `envelope.metadata.extraction_metadata`; never rewritten in a
converter.

### Finalize adapter (thin — delegates to `finalize_builder_extraction`)

`finalize_disease_extraction` is a thin domain adapter (mirror
`_finalize_gene_expression_extraction_impl`): validate its `candidate_ids` input,
snapshot active evidence records + resolver ledger, then call
`finalize_builder_extraction(workspace=…, candidate_ids=…,
materialize=<disease materializer wrapped with builder events>,
evidence_records=…, resolver_entry_lookup=…,
materialized_candidate_prefix="disease-annotation-pending")`. All structural
staging/finalize control flow stays in the project-agnostic
`finalize_builder_extraction` (do NOT clone `ExtractionBuilderWorkspace`).

### Agent + prompt

`agents/disease_extractor/agent.yaml`: set `output_schema: null`; swap the tool
list to the builder set above; keep `curation.adapter_key: disease`,
`domain_pack_id: agr.alliance.disease`. Rewrite `prompt.yaml` into a builder
tool-loop (record evidence → resolve disease/relation/provider selectors → stage
observations → finalize), copying gene_expression's prompt structure.

`domain_pack.yaml`: declare the `materializes_to_field_paths` mirrors from §1
(disease term snapshot ← validated DOID; and, when unblocked, subject & reference
mirrors). Reuse the existing validator bindings (§4) unchanged.

### Detection + tests + e2e

- Detection: `finalize_disease_extraction` `builder_finalization: true` is enough
  (Phase-0 generalized detection). No `streaming_tools.py` edit.
- Fixtures/tests: add a golden pending fixture with RELATIVE metadata_refs +
  unit/contract tests mirroring `test_gene_expression_domain_pack.py`.
- e2e: pick a representative processed PDF with curated disease assertions (record
  the chosen `DOC` id here once confirmed in sandbox) and confirm 0 structural
  findings per runbook §7.

---

> RESOLVED 2026-05-31 (Chris) — see the "## Decisions" section at the END of this file. The questions
> below are kept for context; the directive is FULL LinkML alignment ("nothing is blocked").

## Open questions for Chris (genuine design decisions — not guessed)

1. **Abstract-class / blocked-write posture under the builder.** The existing pack
   emits a *pending* envelope on the abstract `DiseaseAnnotation` and blocks all
   writes (ALL-410 subject materialization, ALL-425 reference/evidence-code/export).
   For this first builder pass, should the builder keep that exact blocked posture
   (builder materializes the same pending object, writes stay blocked), or is part
   of the goal of this migration to unblock concrete subject/reference materialization?
   gene_expression's reference is *not* write-blocked, so disease diverges from the
   template here — confirm the intended scope.

2. **Subject (`disease_annotation_subject`) in the builder.** Subject identity is
   required by every *concrete* LinkML subtype and selects which of
   Gene/Allele/AGMDiseaseAnnotation is written, but it is `blocked` (ALL-410) and
   the current extractor payload's `disease_annotation_subject` is optional. Should
   the builder *stage* a subject (subject_type + identifier/label) so the data is
   captured for later materialization, or stay subject-agnostic (mention + disease
   term only) like the current pending pack? This determines whether
   `stage_disease_observation` takes subject params and whether
   `disease_subject_materialization` should move toward active.

3. **`evidence_codes` (ECO) — required in LinkML, blocked in the pack.** ECO
   evidence codes are LinkML-`required`/multivalued and reliably present in curated
   data (100% via the join), yet `evidence_code_curies` is `blocked` (ALL-425) and
   not in `REQUIRED_DISEASE_PAYLOAD_FIELDS`. Should the builder extract/stage ECO
   evidence codes now (and activate `disease_evidence_code_lookup`), or continue to
   treat them as out-of-scope for the pending envelope?

4. **`single_reference` — extract or inherit from document context?** Curated
   references are pre-resolved `AGRKB:` curies (no raw PMID in the column). The
   loaded document already has an identity in the curation workspace. Should the
   builder stage a reference from document context (mirroring it into
   `single_reference`), or leave it blocked (ALL-425) and let a later materializer
   bind it from the workspace document? Affects whether
   `disease_reference_materialization` activates.

5. **Relation vocabulary subset.** LinkML splits the 'Disease Relation' CV into
   per-subtype subsets ('Gene/Allele/AGM Disease Relation'), but curated data uses
   a shared set (`is_implicated_in`, `is_model_of`, `is_marker_for`,
   `is_ameliorated_model_of`, `is_exacerbated_model_of`) across subtypes. Should
   `disease_relation_cv_lookup` validate against the umbrella 'Disease Relation' CV
   (matches curation reality) or enforce the per-subtype subset once the subject
   type is known (matches LinkML)? Relevant only if subject staging (Q2) lands.

6. **Condition relations / experimental conditions scope.** `condition_relations`
   (ZECO/CHEBI/etc.) are `under_development` and the composite
   `experimental_condition_validation` is metadata-only. Are condition relations in
   scope for the first builder pass (staged + materialized as optional context), or
   explicitly deferred so the builder ignores them this pass?

---

## Decisions (2026-05-31, Chris) — these supersede the open questions above

**Directive: nothing is "blocked."** The existing pack's pending / abstract / write-blocked /
under-development posture is a PLACEHOLDER, not a constraint. Bring disease into FULL LinkML-model
alignment now — the validators are ready. Each decision below resolves the matching open question.

- **D1 — Write posture → UNBLOCK.** Do NOT preserve the pending/abstract/write-blocked posture.
  Materialize the concrete subtype (GeneDiseaseAnnotation / AlleleDiseaseAnnotation /
  AGMDiseaseAnnotation) selected by subject kind, per LinkML. The ALL-410 / ALL-425 "blocked" flags are
  retired for disease.
- **D2 — Subject → STAGE + RESOLVE.** `stage_disease_observation` captures the subject
  (subject_type + identifier/label); activate `subject_entity_validation` to resolve concrete
  Gene/Allele/AGM identity. The subject is required by every concrete subtype and selects which subtype
  is written, so it drives D1.
- **D3 — ECO evidence codes → EXTRACT.** Stage `evidence_code_curies[]` (ECO; LinkML-required,
  multivalued, 100% present in curated data) and activate the evidence-code lookup binding.
- **D4 — single_reference → BIND FROM THE LOADED DOCUMENT.** Source the reference from the curation
  workspace document identity (the paper under curation), not from free text. Implementation note:
  confirm a durable reference_id / AGRKB is available at chat-extraction time. The SAME pending-Reference
  gap exists in phenotype + allele — solve reference binding UNIFORMLY across all three.
- **D5 — Relation vocabulary → PER-SUBTYPE SUBSETS (verified; no divergence).** Validate the relation
  against the subject-type's subset. VERIFIED 2026-05-31 that LinkML, the formal CV subsets, and curator
  usage ALL AGREE (the §2 "shared umbrella" grounding note was WRONG — it read the base `diseaseannotation`
  table without splitting by subtype). Per-subtype membership = de-facto usage:

  | Subtype | CV subset | Members (= curator usage, counts) |
  |---|---|---|
  | Gene | 'Gene Disease Relation' | `is_implicated_in` (25,290), `is_marker_for` (17,858) |
  | Allele | 'Allele Disease Relation' | `is_implicated_in` (5,551) |
  | AGM | 'AGM Disease Relation' | `is_model_of` (23,238), `is_ameliorated_model_of` (5,863), `is_exacerbated_model_of` (3,427) |

  Also `'Via Orthology Disease Relation'` (`is_implicated_via_orthology`, `is_marker_via_orthology`) for
  orthology-inferred relations. No Chris-Grove ticket (premise disproven).
- **D6 — Condition relations → DEFER.** Experimental-condition context (`condition_relations`) is OUT of
  scope this pass. Conditions are not standalone in the model (ExperimentalCondition → ConditionRelation →
  host annotation); the condition→host linkage will be reintroduced later with the host-annotation work
  (and `chemical_condition` is being removed — see that doc). Capture the straightforward optional disease
  slots (negated, genetic_sex, annotation_type, etc.) as full alignment allows; do NOT build the
  condition→host linkage now.
