# Gene extractor — envelope → builder migration approach (Phase 1)

Date: 2026-05-31. Owner: Phase-1 agent. Reference: gene_expression (the proven, structurally-clean
builder). This doc grounds the gene migration in LinkML + the curation DB before any code, per the
runbook §3. The builder path is added ALONGSIDE the existing envelope path (no legacy deletion;
that is Phase 6).

## Naming note (important)

The "gene extractor" lives at `packages/alliance/agents/gene_extractor/` (`agent_id: gene_extractor`,
category Extraction). `packages/alliance/agents/gene/` is the gene VALIDATOR (`agent_id:
gene_validation`, category Validation) — NOT the extractor, and is untouched by this migration. The
runbook's reference to `agents/gene/agent.yaml` means the extractor; the real path is
`agents/gene_extractor/`.

## 1. Target LinkML class + slots

- Class: `Gene` (`temp_agr_curation_schema/model/schema/gene.yaml`), `is_a GenomicEntity is_a
  BiologicalEntity`. Identity slots come from `core.yaml`:
  - `primary_external_id` (BiologicalEntity, range string) — the Alliance Gene CURIE.
  - `taxon` (BiologicalEntity, range NCBITaxonTerm) — NCBI Taxon CURIE.
  - `gene_symbol` (Gene, range GeneSymbolSlotAnnotation; display text) — current accepted symbol.

The gene extractor does NOT create or mutate a `Gene` row. It produces an envelope-only
`gene_mention_evidence` validated-reference object that grounds those three Gene/BiologicalEntity
identity slots from verified paper evidence. This is the existing, deliberate contract (see the
existing `gene` domain pack + `conversion.py`). It is NOT a full annotation like
gene_expression's `GeneExpressionAnnotation`.

## 2. Curation-DB reality (read-only, SELECT-only)

Inspected the live curation DB from the backend container (CURATION_DB_URL, never printed):

- `gene` table is narrow: `id, genetype_id, popularity, gcrpcrossreference_id`. Gene identity lives
  on the parent `biologicalentity` table: `primaryexternalid`, `taxon_id`, `dataprovider_id`.
- Sample `primaryexternalid | taxon`: `FB:FBgn0000001 | NCBITaxon:7227`, … (15 rows inspected).
- Provider-prefix distribution across all genes (`split_part(primaryexternalid,':',1)`):
  `MGI` 633315, `WB` 311061, `FB` 255964, `RGD` 109215, `Xenbase` 49264, `HGNC` 44645,
  `ZFIN` 38056, `SGD` 8097, `RefSeq` 28.
- So real CURIE namespaces are `MGI:`, `WB:`, `FB:`, `RGD:`, `Xenbase:`, `HGNC:`, `ZFIN:`, `SGD:` —
  exactly the `data_provider_hint` set the existing extractor schema already knows. `gene_symbol`
  display text lives in `slotannotation.displaytext`. Identity resolution against this DB is the job
  of the `gene_validation` validator agent at validation time, NOT of the extractor.

## 3. The gene extractor's curatable objects/fields

One `gene_mention_evidence` object per retained gene/evidence pairing. Payload (LinkML-grounded
identity hints + verified evidence fields), confirmed from the existing
`gene_extractor/schema.py:GeneMentionEvidencePayload` and `domain_packs/gene/domain_pack.yaml`:

- Identity (validator-owned final values, extractor stages hints only):
  `mention` (required, paper text), `primary_external_id`, `gene_symbol`, `taxon` (the three
  validatable Gene slots), `species`, `taxon_hint`, `data_provider_hint`,
  `proposed_primary_external_id`, `proposed_gene_symbol`, `proposed_taxon`,
  `identity_resolution_notes` (required, >=1), `confidence` (required enum high/medium/low).
- Evidence (verified, immutable): `evidence_record_id`, `verified_quote`, `page`, `section`,
  `chunk_id` (required), `subsection`, `figure_reference` (optional). The object's
  `evidence_record_ids` must equal exactly `[payload.evidence_record_id]` and the payload evidence
  fields must match `metadata.evidence_records[]`.

It does NOT create a separate Gene object — scalar fields are materialized on the
`gene_mention_evidence` target. (Asserted by `test_alliance_gene_domain_pack.py`.)

## 4. Validator bindings (REUSE — no new bindings)

The gene domain pack already declares ONE active binding,
`alliance_gene_reference_lookup` (validator agent `agr.alliance/gene_validation`,
`blocking: false`, `required: true`, `allow_opt_out: true`), validating
`primary_external_id` (→ curie), `gene_symbol` (→ symbol), `taxon` (→ taxon). The builder
migration changes only the EXTRACTION mechanism; the curation target + bindings are unchanged.

## 5. Evidence model

Identical to gene_expression: the extractor calls `record_evidence(span_ids=[...])` →
verified `evidence_record_id`; that ID is staged. Backend builder finalization copies verified
records into `metadata.evidence_records[]` and wires `evidence_record_ids` + RELATIVE
`metadata_refs` (`raw_mentions[N]`, `evidence_records[N]`). Unlike gene_expression there are NO
resolver-backed controlled fields (no `resolve_domain_field_term` loop) — the gene validator owns
identity, so staging requires evidence but NO resolver selections.

## 6. Builder mapping (what gets built)

- `materialize_gene_builder_state` (new, in `domain_packs/gene/conversion.py`): reads builder
  workspace candidates → emits the extraction-output payload (`summary`, `curatable_objects[]` of
  `gene_mention_evidence`, `metadata` with `raw_mentions`, `evidence_records`, RELATIVE
  `metadata_refs`, provenance, run_summary). Mirrors `materialize_gene_expression_builder_state` but
  with NO helper-selection / resolver machinery and NO mirror-field projection (gene has none).
  Output validated by a new `GeneBuilderExtractionOutput(GeneExtractionResultEnvelope)` whose
  `curatable_objects` are the existing `GeneMentionEvidenceObjectEnvelope`, so the proven
  envelope-shape contract validates the builder output too.
- `stage_gene_mention_evidence` / `patch_…` / `discard_…` / `list_staged_…` /
  `finalize_gene_extraction`: a per-domain builder-tools module
  (`tools/gene_builder_tools.py`) over the generic `ExtractionBuilderWorkspace`, calling
  `finalize_builder_extraction(materialize=<gene event-wrapped materializer>,
  materialized_candidate_prefix="gene-envelope", require_resolver_selections=False)`.
- bindings.yaml: register the five tools; `metadata.builder_finalization: true` on
  `finalize_gene_extraction` (Phase-0 detection picks it up; NO platform edit).
- agent.yaml: drop `output_schema: GeneExtractionResultEnvelope`; swap the tool list to the builder
  set. prompt.yaml: already a builder tool-loop (`stage_gene_mention_evidence` /
  `finalize_gene_extraction`) — only minor alignment needed.

## 7. Invariants (runbook §5) — how each is held

1. metadata_refs RELATIVE (`raw_mentions[N]`/`evidence_records[N]`), resolved against
   `extraction_metadata`; never absolute; never rewritten in a converter. (materializer emits
   relative refs exactly like GE.)
2. PENDING = not-yet-validated; NO object_not_pending check added.
3. Validator errors NON-FATAL → `validator_error` finding; extraction still persists (platform
   behavior already wired in Phase pre/0; gene path adds nothing that aborts).
4. Mirror fields via declared `materializes_to_field_paths` — gene has NO mirror fields, so nothing
   to special-case (none declared, none coded).
5. Inline validation runs in the chat turn on the builder-finalized envelope (Phase-0 generic
   dispatch; gene gets it automatically once detected as a builder agent).
6. Project-agnostic core; domain logic in the pack/adapter; NO fallback/compat shims.

## 8. Open questions

- The gene_mention_evidence object_role is `validated_reference` (export-only, non-mutating), unlike
  gene_expression's `curatable_unit`. The builder materializer keeps that role + the existing
  `_object_metadata()` (export_behavior, write_behavior). This is the most gene-faithful choice; no
  ambiguity, recorded for visibility.
- The gene domain pack `status` is `in_development` (vs gene_expression `active`). Left as-is — the
  migration changes extraction mechanism, not pack lifecycle status.

## 9. Test doc

E2E doc: `a31b1ff3-4fcd-42f8-9aec-0d299bcdbbe5` (the gene_expression test PDF; it contains gene
mentions). Drive with "Extract all gene mentions from this publication" and confirm
`extraction_results.agent_key == gene_extractor` (not gene_expression) and that
`finalize_gene_extraction` was called.
