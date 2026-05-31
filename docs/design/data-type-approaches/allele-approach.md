# Allele extractor — envelope → builder migration approach (Phase 4)

Date: 2026-05-31. Owner: Phase-4 agent. Reference: gene_expression (the proven, structurally-clean
builder). This doc grounds the allele migration in LinkML + the curation DB + the existing envelope
pack before any code, per the runbook §3. The builder path is added ALONGSIDE the existing envelope
path (no legacy deletion; that is Phase 6). READ-ONLY grounding pass — no code written.

## Naming note (important — same trap as gene)

There are TWO allele agents:
- `packages/alliance/agents/allele_extractor/` (`agent_id: allele_extractor`, category Extraction,
  `curation.adapter_key: allele`, `domain_pack_id: agr.alliance.allele`, `output_schema:
  AlleleExtractionResultEnvelope`). **This is the type that migrates to the builder pattern.**
- `packages/alliance/agents/allele/` (`agent_id: allele_validation`, category Validation,
  `output_schema: AlleleResultEnvelope`, tools `agr_curation_query` only). **This is the VALIDATOR
  agent** — it is the reused validator binding target, NOT the extractor, and is untouched by this
  migration. The runbook's "`allele.yaml` (`Allele`)" reference is the LinkML class; the extractor
  is at `agents/allele_extractor/`.

A defining feature of the allele type (vs gene/gene_expression): the extractor NEVER materializes
an `Allele` object or an `allele_identifier`. Final allele identity is resolved at validation time
by `allele_validation`. The extractor emits a pending paper/evidence ASSOCIATION graph (multiple
linked objects), not a single annotation row.

## 1. Target LinkML class + slots

LinkML clone @ `1b11d088` (`temp_agr_curation_schema/model/schema/allele.yaml`, identity in
`core.yaml`/`biologicalentity`).

- **Target class: `Allele`** — `is_a GenomicEntity is_a BiologicalEntity`. `exact_mappings: SO:0001023`.
- **Identity slots (from BiologicalEntity / core.yaml)** — these are what the validator resolves,
  NOT what the extractor writes:
  - `primary_external_id` (range string, **required** on `AlleleDTO`) — the Alliance allele ID.
  - `taxon` (range NCBITaxonTerm) — NCBI Taxon CURIE.
  - `modinternalid`, `curie` (optional; see DB reality — both empty for alleles in practice).
- **Allele key slots:**
  - `allele_symbol` (**required**, range `AlleleSymbolSlotAnnotation`; one current accepted symbol,
    e.g. `wg<sup>Sp-1</sup>`). The DTO requires `allele_symbol_dto`.
  - `allele_full_name` (optional, single, `AlleleFullNameSlotAnnotation`).
  - `allele_synonyms` (optional, multivalued).
  - `allele_secondary_ids` (optional, multivalued).
  - `references` (range Reference, from core) — the paper(s) supporting the allele.
  - `is_extinct`, `is_extrachromosomal`, `is_integrated` (optional booleans; WB-specific for the
    last two), `in_collection`, `laboratory_of_origin` (optional).
- **Value types / enums (controlled vocabularies and ontologies):**
  - `allele_mutation_types` → `AlleleMutationTypeSlotAnnotation.mutation_types` range **`SOTerm`**
    (`required: true`, multivalued). SO CURIEs.
  - `allele_inheritance_modes` → `inheritance_mode` range `VocabularyTerm` (CV 'Allele Inheritance
    Mode': dominant / semi-dominant / recessive / unknown / codominant).
  - `allele_germline_transmission_status` → `VocabularyTerm` (CV 'Allele Germline Transmission Status').
  - `allele_functional_impacts` → `functional_impacts` range `VocabularyTerm` (CV 'Allele Functional
    Impact', e.g. knockout/amorphic) + optional `phenotype_term` (PhenotypeTerm) / `phenotype_statement`.
  - `allele_database_status` → `VocabularyTerm` (CV 'Allele Database Status').
  - `allele_nomenclature_events` → `VocabularyTerm` (named/renamed).
- **Related-entity associations (subject = Allele, object = the related entity):**
  - `AlleleGeneAssociation` (object range **Gene**, required) — the related gene. This is the
    association the existing pack grounds its export to (`allelegeneassociation`).
  - `AlleleVariantAssociation` (Variant), `AlleleConstructAssociation` (Construct),
    `AlleleTranscriptAssociation` (Transcript), `AlleleProteinAssociation` (Protein),
    `AlleleCellLineAssociation` (CellLine), `AlleleImageAssociation` (Image),
    `AlleleOriginAssociation` (AffectedGenomicModel), `AlleleGenerationMethodAssociation`,
    `AlleleAlleleAssociation` (Allele↔Allele).
  - All allele associations carry `evidence` (NOT required on the abstract base) +
    `evidence_code` + `related_note`.
- **"X must match Y" constraints (LinkML mirror rules):** the abstract `AlleleAssociation` sets
  `allele_association_subject: range Allele, required: true`; each concrete association also fixes
  the object range (e.g. `allele_gene_association_object: range Gene`). The slot-annotation classes
  fix `single_allele: required: true` (every allele slot annotation must point back to its allele).
  There is **no `materializes_to_field_paths`-style scalar mirror** in the allele LinkML the way
  gene_expression mirrors subject-gene → `entity_assayed`; the allele extractor's curatable target
  is the association graph, and the only scalar that "mirrors" is the validator-owned
  `allele_identifier` (which the extractor must leave empty — see §3/§4). See Open Question 1.

The allele extractor does NOT create or mutate an `Allele` row, an `AlleleGeneAssociation` row, or
an `allele_reference` row. It emits a PENDING, write-blocked paper/evidence association graph that
grounds those LinkML targets from verified paper evidence. This is the existing, deliberate contract
(domain_pack.yaml + `domain_packs/allele/__init__.py` + `allele_extractor/schema.py`).

## 2. Curation-DB reality (read-only, SELECT-only — CURATION_DB_URL, never printed)

Inspected the live curation DB from the backend container.

**Discovered allele* tables (12):** `allele`, `allele_reference`, `alleleconstructassociation`
(+`_informationcontententity`), `allelediseaseannotation` (+`_gene`), `allelegeneassociation`
(+`_informationcontententity`), `allelephenotypeannotation` (+`_gene`), `allelevariantassociation`
(+`_informationcontententity`).

**Identity does NOT live on `allele`.** The `allele` table is narrow: `id, incollection_id,
isextinct, popularity` only. `curie` is NULL for all 3,674,385 alleles. Real identity is on the
parent **`biologicalentity`** table: `primaryexternalid`, `modinternalid` (NULL for all alleles),
`taxon_id`, `dataprovider_id`. Symbol text lives in **`slotannotation`** (single-table inheritance,
discriminator `slotannotationtype`; symbol in `displaytext` / `formattext`, joined by
`singleallele_id`). Mutation-type SO terms join via `slotannotation_ontologyterm.mutationtypes_id`.

**Real CURIE namespaces (`primaryexternalid` prefix → count over 3.67M alleles):**
`WB` 2,099,757 · `MGI` 809,107 · `FB` 661,816 · `ZFIN` 80,233 · `SGD` 22,514 · `RGD` 958.
(No HGNC alleles — human alleles are not in this set; matches the validator's provider table minus
HGNC in practice.)

**Reliably-present slots (slot-annotation coverage over 3.67M alleles):**
- `allele_symbol` — 3,674,385 (**100%**, every allele has a symbol annotation).
- `allele_mutation_types` — 3,066,028 (~83%).
- `allele_synonyms` — 1,845,682 (~50%); `allele_database_status` — 1,493,437 (~41%);
  `allele_full_name` — 903,244 (~25%); `germline_transmission_status` — 640,722 (~17%);
  `functional_impacts` — 139,349 (~4%); `secondary_ids` — 109,608 (~3%);
  `inheritance_modes` — 61,868 (~2%); `nomenclature_events` — 244 (negligible).
- `taxon` — present on every sampled row.

**3–5 real example rows (primaryexternalid | symbol displaytext | taxon | obsolete | isextinct):**
- `MGI:4075427 | Rpl18<sup>Gt(IST12499C1)Tigm</sup> | NCBITaxon:10090 | False | False`
- `WB:WBVar00000352 | b11 | NCBITaxon:6239 | False | (null)`
- `FB:FBal0392295 | Scer\GAL4<sup>nompB-2151-G4</sup> | NCBITaxon:7227 | True | False`
- `ZFIN:ZDB-ALT-240906-1 | hsc208 | NCBITaxon:7955 | False | False`
- `SGD:S000377798 | ssm4-(doa10-G1309L,N1314A) | NCBITaxon:559292 | False | False`
- `RGD:10054409 | Kcnj10<sup>em3Mcwi</sup> | NCBITaxon:10116 | False | (null)`

Notes: symbols carry HTML markup (`<sup>`) in `displaytext` and bracket form in `formattext`
(`Rpl18<Gt(IST12499C1)Tigm>`). Taxa are `NCBITaxon:` CURIEs. `isextinct` is frequently NULL.

**Top mutation-type SO terms (real, from `slotannotation_ontologyterm`):** `SO:1000002`
substitution (1.56M) · `SO:0001218` transgenic_insertion (1.08M) · `SO:0000667` insertion (237K) ·
`SO:0000159` deletion (90K) · `SO:1000008` point_mutation (50K) · `SO:1000029`
chromosomal_deletion (22K) · `SO:0001583` missense_variant (10K) · `SO:0001837`
mobile_element_insertion (9K).

**Association / reference linkage reality (the pack's grounded write targets):**
`allele_reference (allele_id, references_id)` has 4,813,071 rows; `allelegeneassociation` has
2,505,989 rows. The domain pack's `curation_db_grounding` (verified 2026-05-10) confirms the FK
shape: `allele_reference` → `allele(id)` + `reference(id)`; `allelegeneassociation` →
`allele(id)` (subject) + `gene(id)` (object); `allelegeneassociation_informationcontententity`
holds evidence. **Writes remain BLOCKED** in this pack (see §3/§6).

Identity resolution against this DB is the job of the `allele_validation` agent at validation time,
NOT of the extractor.

## 3. Curatable objects / fields (from the existing envelope pack)

Pack `agr.alliance.allele` (`domain_pack.yaml`, version 0.1.0, status `in_development`). The
extractor emits a 4-object PENDING association graph per retained allele mention. Confirmed from
`domain_pack.yaml` `object_definitions` + `allele_extractor/schema.py`
`AlleleExtractionResultEnvelope` validator + `domain_packs/allele/__init__.py`
`build_pending_allele_envelope_from_tool_verified_fixture`:

- **`AllelePaperEvidenceAssociation`** (`object_role: curatable_unit`, model
  `AllelePaperEvidenceAssociationPayload`, schema_ref abstract `AlleleAssociation`). The pending
  curatable unit. Required payload: `association_kind` (= `"allele_paper_evidence"`, protected
  routing invariant), `evidence_record_ids[]`. `object_refs[]` MUST include `Reference`,
  `AlleleMention`, `EvidenceQuote` (and may NOT include validator-materialized `Allele`).
  `allele_identifier` MUST be absent (validator resolves it; extractor emitting it is an error).
  `metadata.write_behavior.status` and `metadata.export_behavior.status` MUST be `"blocked"`.
- **`Reference`** (`object_role: validated_reference`, model `ReferencePayload`, schema_ref
  `Reference`). The source paper. Fields: `reference_id` (int, required in pack; in practice the
  fixture path emits `title`/`filename` and leaves `reference_id` for reference materialization),
  `title`.
- **`AlleleMention`** (`object_role: metadata_only`, model `AlleleMentionPayload`). The extracted
  paper text + validator selector context. Fields: `mention.text` (required, protected — source
  anchor, not an edit target), `mention.normalized_hint`, `associated_gene.symbol`, `taxon.curie`,
  `source_mentions[0]`. **This is the validator-binding input object** (see §4).
- **`EvidenceQuote`** (`object_role: metadata_only`, model `EvidenceQuotePayload`). One
  record_evidence-verified quote + locator. Required: `verified_quote`. Plus `evidence_record_id`,
  `page`, `section`, `subsection`, `chunk_id`, `figure_reference`.
- **`Allele`** (`object_role: validated_reference`) — declared in the pack with fields
  `primary_external_id`, `allele_symbol`, `taxon` (each bound to `allele_mention_reference_validation`).
  **The extractor MUST NOT emit this object** (`schema.py` `_VALIDATOR_MATERIALIZED_OBJECT_TYPES`);
  it is materialized by the active allele validator. The builder migration preserves this rule.

Every association also produces a `alliance.allele.write_blocked` BLOCKER finding by design (pending
only), and a `alliance.allele.skipped_without_verified_evidence` WARNING for candidates lacking
verified evidence.

## 4. Validator bindings (REUSE — no new bindings)

The allele pack declares ONE **active** binding (reused unchanged; the migration changes the
EXTRACTION mechanism, not the curation target):

- **`allele_mention_reference_validation`** — validator agent `agr.alliance/allele_validation`,
  `required: true`, `blocking: true`, `allow_opt_out: false`, `curator_override.allowed: false`.
  - `applies_to`: object_type `AlleleMention`, field `mention.text`.
  - `input_fields`: `mention` ← `payload.mention.text`; `normalized_hint` ←
    `payload.mention.normalized_hint` (optional); `associated_gene` ←
    `payload.associated_gene.symbol` (optional); `taxon` ← `payload.taxon.curie` (optional);
    `evidence_quote` ← `evidence_record.verified_quote` (optional, context_only).
  - `expected_result_fields`: `curie → allele.primary_external_id`, `symbol → allele.allele_symbol`,
    `taxon → allele.taxon`. (These materialize onto the `Allele` validated-reference object.)

Two **under_development** bindings (declared, NOT dispatched — keep dormant):
- `allele_pending_envelope_validator` (data-check placeholder; no inputs/expected fields yet).
- `source_reference_validation` (validator agent `agr.alliance/reference_validation`; metadata-only
  until reference materialization + export flows are activated).

The builder migration must keep `allele_mention_reference_validation` firing on the
builder-materialized `AlleleMention` object exactly as the envelope path does. No new bindings.

## 5. Evidence model

Identical mechanism to gene_expression/gene: the extractor calls `record_evidence(span_ids=[...])`
→ verified `evidence_record_id`; that ID is staged on the candidate. Backend builder finalization
copies verified records into `metadata.evidence_records[]` and wires `evidence_record_ids` +
RELATIVE `metadata_refs`. Allele-specific points:
- The verified `EvidenceQuote` payload must carry `verified_quote`, `page`, `section`, `chunk_id`
  (required by `schema.py` `_REQUIRED_EVIDENCE_QUOTE_PAYLOAD_FIELDS`), plus optional
  `subsection`/`figure_reference`.
- The association's `payload.evidence_record_ids` MUST equal the curatable object's
  `evidence_record_ids`, and every ID MUST resolve in `metadata.evidence_records[]` (enforced by
  `schema.py`). Candidates with no verified evidence are SKIPPED (WARNING), never emitted.
- Unlike gene_expression there are NO resolver-backed controlled selectors at extraction time (no
  `resolve_domain_field_term` loop): allele identity, mutation-type SO terms, and inheritance/
  functional-impact CVs are owned by the validator, so staging requires evidence but NO resolver
  selections. (Mirrors the gene migration: `require_resolver_selections=False`.) See Open Question 2.

## 6. Builder mapping (what gets built — mirror gene_expression / gene)

- **`materialize_allele_builder_state`** (new, in a new
  `packages/alliance/python/.../domain_packs/allele/conversion.py` — note the allele pack currently
  has NO `conversion.py`; the helpers live in `__init__.py`; the new materializer should go in a
  `conversion.py` mirroring gene_expression's, and be re-exported from `__init__.py`). It reads the
  builder workspace candidates → emits the extraction-output payload
  (`summary`, `curatable_objects[]`, `metadata` with `raw_mentions`, `evidence_records`, RELATIVE
  `metadata_refs`, provenance, run_summary). Per retained candidate it emits the **4-object graph**:
  one `Reference` (shared across candidates), one `AlleleMention`, one-or-more `EvidenceQuote`, one
  `AllelePaperEvidenceAssociation` with `object_refs[]` wired to those pending refs — exactly the
  shape `build_pending_allele_envelope_from_tool_verified_fixture` produces, but from builder state
  instead of a fixture. Output validated by the existing
  `AlleleExtractionResultEnvelope(RuntimeAlleleExtractionResultEnvelope)` so the proven envelope
  contract validates the builder output too. metadata_refs are RELATIVE (`raw_mentions[N]`,
  `evidence_records[N]`); never absolute; never rewritten in a converter.
- **Builder tools** in a per-domain module
  (`packages/alliance/python/.../tools/allele_builder_tools.py`, NOT piled into the monolithic
  `agr_curation.py`): `stage_allele_observation`, `patch_allele_observation`,
  `discard_allele_observation`, `list_staged_allele_observations`, `finalize_allele_extraction`.
  The staged candidate carries: mention text (`mention`), `normalized_hint`, `associated_gene`,
  `taxon` curie, `source_mentions[]`, and `evidence_record_ids[]`. `finalize_allele_extraction` is
  a thin adapter that mirrors `_finalize_gene_expression_extraction_impl`: input-schema validation +
  run-state snapshots, then delegates to
  `finalize_builder_extraction(workspace=…, candidate_ids=…,
  materialize=<allele event-wrapped materializer>, evidence_records=…,
  resolver_entry_lookup=None, materialized_candidate_prefix="allele-paper-evidence-association")`.
  (`finalize_builder_extraction` is the project-agnostic Phase-0 orchestration in
  `tools/builder_finalization.py`; the gene_expression adapter at `agr_curation.py:6026` is the
  copy template; the materializer wrapper to copy is `_materialize_gene_expression_with_events` at
  `agr_curation.py:5955`.)
- **bindings.yaml**: register the five tools mirroring the gene_expression block
  (`bindings.yaml:760–829`); put `metadata.builder_finalization: true` on
  `finalize_allele_extraction` so the Phase-0 generalized detection
  (`streaming_tools._builder_finalization_tool_names`) picks it up — NO platform edit.
- **agent.yaml** (`agents/allele_extractor/agent.yaml`): drop
  `output_schema: AlleleExtractionResultEnvelope`; swap the tool list to the builder set (evidence
  tools `search_document/read_chunk/read_section/read_subsection/record_evidence` +
  `list_recorded_evidence/get_recorded_evidence/attach_evidence_to_object/…` +
  `agr_species_context_lookup` + the new `stage_allele_observation` … `finalize_allele_extraction`).
  Keep `curation.adapter_key: allele`, `domain_pack_id: agr.alliance.allele`.
- **prompt.yaml**: rewrite into a builder tool-loop (record evidence → stage allele observation with
  mention/normalized_hint/associated_gene/taxon + evidence_record_ids → finalize), copying
  gene_expression's prompt structure. Preserve the existing allele extraction guidance: search the
  LITERAL compact allele notation, never normalize before lookup, leave `allele_identifier` for the
  validator, inclusion requires verified evidence.
- **domain_pack.yaml**: object definitions, fields, validator bindings, and the blocked
  write/export behavior are already declared and CORRECT — reuse as-is. No `materializes_to_field_paths`
  mirror is declared for allele (none needed — see §1 and Open Question 1).
- **Detection / inline validation**: once `finalize_allele_extraction` carries
  `builder_finalization: true`, the Phase-0 generic dispatch detects the agent as a builder agent
  and runs inline validation in the chat turn on the finalized envelope, firing
  `allele_mention_reference_validation` on each `AlleleMention`. NO platform edit.

## 7. Invariants (runbook §5) — how each is held

1. metadata_refs RELATIVE (`raw_mentions[N]`/`evidence_records[N]`), resolved against
   `extraction_metadata`; never absolute; never rewritten in a converter. (materializer emits
   relative refs exactly like GE.)
2. PENDING = not-yet-validated; NO `object_not_pending` check added. (Note: the existing
   `validate_pending_allele_envelope` already only checks association refs / write-block / no
   extractor-owned identity — none of those is an "objects must be pending" check; keep it that way.)
3. Validator errors NON-FATAL → `validator_error` finding; extraction still persists (platform
   behavior wired in Phase pre/0; allele path adds nothing that aborts). The pack's own intentional
   `alliance.allele.write_blocked` BLOCKER is a domain finding, not a validator abort.
4. Mirror fields via declared `materializes_to_field_paths` — allele declares NONE, so nothing to
   special-case (none declared, none coded). Validator-owned scalars (`allele.primary_external_id`,
   `allele.allele_symbol`, `allele.taxon`) are materialized by the validator binding's
   `expected_result_fields`, not by extractor code.
5. Inline validation runs in the chat turn on the builder-finalized envelope (Phase-0 generic
   dispatch; allele gets it automatically once detected as a builder agent), firing the active
   `allele_mention_reference_validation` binding.
6. Project-agnostic core; domain logic in the pack/adapter/per-domain module; NO fallback/compat shims.

## 8. Test doc

E2E doc: TBD — confirm a processed PDF containing allele/variant mentions in the sandbox before the
e2e run (the gene_expression doc `a31b1ff3-4fcd-42f8-9aec-0d299bcdbbe5` is a candidate to check, but
allele coverage in it is unverified). Drive with "Extract all alleles from this publication" and
confirm `extraction_results.agent_key == allele_extractor` and that `finalize_allele_extraction`
was called. PASS = 0 structural findings (`entity_assayed_mismatch` / `object_not_pending` /
`metadata_refs_missing` / `validator_materialization_invalid`); the intentional
`alliance.allele.write_blocked` BLOCKER and `validator_resolved/unresolved` findings are expected and
NOT structural failures.

## 9. Open questions for Chris (do NOT guess)

1. **No `materializes_to_field_paths` mirror for allele.** gene_expression mirrors subject-gene →
   `entity_assayed` via declared mirror metadata; the allele pack declares none. The allele
   validator's `expected_result_fields` write `curie/symbol/taxon` onto the `Allele`
   validated-reference object instead. Confirm this is the intended allele mechanism (validator
   expected_result_fields, NOT a domain-pack mirror) — i.e. the allele builder needs no mirror-field
   declaration and the gene_expression `materializes_to_field_paths` parity item is N/A for allele.

2. **No resolver selectors at extraction time, but the schema has rich controlled vocabularies.**
   The DB shows `allele_mutation_types` (SO terms, ~83% coverage), `inheritance_modes`,
   `functional_impacts`, `database_status`, `germline_transmission_status` are all real, populated
   CVs/ontologies. The current envelope extractor captures NONE of these — it only stages a mention
   + evidence and defers ALL identity/typing to the validator. Should the builder migration stay
   strictly mention-only (mirror the existing contract, `require_resolver_selections=False`), or is
   capturing mutation-type SO terms (the highest-value, highest-coverage field) via a
   `resolve_domain_field_term` loop in scope for this pass? Recommend mention-only for parity this
   pass; flagging because the runbook §3 explicitly lists "mutation types SO terms, inheritance"
   as allele value types to ground, implying they may eventually be wanted.

3. **`conversion.py` does not exist for allele** (helpers live in `domain_packs/allele/__init__.py`;
   the pack also has `constants.py`, `export.py`, `submit.py`). gene_expression's materializer lives
   in `conversion.py`. Confirm the new `materialize_allele_builder_state` should land in a new
   `conversion.py` (re-exported from `__init__.py`) to match the gene_expression file layout, rather
   than appending into the large `__init__.py`.

4. **Reference object materialization is unresolved upstream.** The pack's `Reference` object and
   `source_reference_validation` binding are deliberately under-development / write-blocked; the
   fixture path emits `title`/`filename` but no durable `reference_id`. The builder materializer
   will likewise emit a pending `Reference` without a resolved `reference_id`. Confirm that
   emitting an unresolved `Reference` (matching the current envelope behavior) is acceptable for the
   builder e2e PASS, given the pack's required `reference_id` field is satisfied only after upstream
   reference materialization (out of scope for this migration pass).

5. **Write/export stay BLOCKED.** The entire allele pack intentionally blocks writes/exports
   (pending-only) and emits a `alliance.allele.write_blocked` BLOCKER per association. Confirm the
   builder migration must preserve this blocked posture (it should — the migration changes extraction
   mechanism only), so the e2e "0 structural findings" gate is evaluated WITH the expected
   `write_blocked` BLOCKER present (it is a domain finding, not one of the four structural codes).
