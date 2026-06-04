# Curation Field-Coverage Audit (Build B prerequisite)

**Date:** 2026-06-04
**Status:** Audit / findings — drives Build B (review-screen) density cleanup (§4 of the Build B spec).
**Method:** Per-domain-pack analysis of `packages/alliance/domain_packs/<pack>/domain_pack.yaml` +
`backend/src/lib/domain_packs/materialization.py`, **cross-checked against real candidate drafts in the
`agrmainsandbox` sandbox DB** (live sessions per pack). Covers the five packs that exist:
gene_expression, gene, allele, disease, phenotype (chemical has no pack yet).
**Companions:** `2026-06-04-curation-interface-diagnosis.md`, `2026-06-04-curation-review-screen-redesign-design.md`.

---

## 1. Cross-cutting patterns (read this first — these are the universal rules)

These hold across every pack and should become **shared rendering rules** in the redesigned review
surface, not per-pack special cases:

1. **`workspace_display` is the lever — and most packs lack it.** Only `gene_expression` and `gene`
   declare any `workspace_display`; **allele, disease, phenotype declare none**, so they fall back to
   read-only "every present payload field" projection (`_summary_fields`, `materialization.py:1361`)
   and emit **zero editable fields** (`_workspace_fields`, `:1421`). Adding `workspace_display.groups`
   per pack is the single change that unlocks both grouping and editability. Editability is then still
   gated per field by `editable`/`protected` metadata (`read_only = protected OR not editable`,
   `materialization.py:1535`).

2. **Hide internal/system/scaffolding fields.** Every pack surfaces resolver/export plumbing as
   "fields": `ontology_lookup_hint.*`, `export_state`, `write_blocked_reason`, `*_vocabulary`,
   `*_id`, `chunk_id`, `evidence_record_id(s)`, `annotation_kind`/`association_kind` routing
   constants, `confidence`. **Hide these as rows; express state (resolved / needs-review / blocked)
   as a chip/badge instead.** This is the biggest, safest density win everywhere.

3. **Mirror/duplicate fields → show once.** gene_expression `expression_experiment.entity_assayed.*`
   mirrors `expression_annotation_subject.*`; gene_expression `expression_experiment.single_reference.reference_id`
   mirrors `single_reference.reference_id`; disease/phenotype materialize standalone validated-reference
   rows (`DOTerm`, `DiseaseAnnotationSubject`, `PhenotypeTerm`, `Reference`) that **duplicate** the
   parent object's inlined fields. Collapse mirrors into one value; collapse duplicate
   validated-reference rows into the parent as chips.

4. **Evidence/anchor fields belong in the PDF, not the form.** `verified_quote`, `source_mentions`,
   `evidence_record_ids`, page/section/subsection/figure locators → the inline evidence quote chip +
   pdf.js highlight (Build B §2), never editable form rows.

5. **Nested arrays/objects → chips or a compact sub-table, never raw JSON.** `condition_relations.*`
   (disease/phenotype/gene_expression) → a collapsible "N conditions" sub-table; UBERON/qualifier
   term lists → chips; object_refs rendered as raw `{...}` JSON (phenotype) → identity chips.

6. **Hide-when-empty.** Many fields are empty in 100% of real candidates (gene_expression: 13 of 32;
   phenotype Reference row: all fields). Don't render empty fields/rows; the form should adapt to
   what the envelope actually carries.

7. **CURIE+label pairs → one chip.** Ontology fields come as `.curie` + `.name`/`.label` pairs; render
   as a single chip (label shown, CURIE on hover/edit), not two rows.

8. **Pair the "⌕ Browse terms" affordance to a uniform resolver surface.** Every ontology field carries
   a `term_helper`/`ontology_family`; the resolver tools (`search_domain_field_terms` /
   `resolve_domain_field_term` / `inspect_ontology_term`) are uniform, so the future term-browser
   popup (Build B §5 Phase 3) is **one component parameterized by `ontology_family`** — anatomy/WBbt,
   GO, UBERON, life-stage, ECO, DOID, ZECO, ChEBI, NCBITaxon, MMO.

9. **proposed-vs-validated → show validated, surface proposed only on divergence** (gene; applies
   anywhere a `proposed_*` mirror exists). Real data shows divergence is rare but load-bearing (the
   `cep-290`→`ccep-290` case, where the *validated* value is the wrong one).

**Net effect of applying #1–#9:** a typical review object drops from ~17–32 rendered fields to ~5–12
curator-facing fields + an evidence chip + (when present) a conditions sub-table — with **no loss of
curated content** (every cut is a mirror, an empty field, internal scaffolding, or an evidence anchor).

---

## 2. gene_expression (`packages/alliance/domain_packs/gene_expression/domain_pack.yaml`)

One curatable object (`GeneExpressionAnnotation`). Real session `a1419a0e-…` (14 candidates): **13 of 32
fields empty in 100% of candidates.** Target: 32 → ~12.

**HIDE:** `expression_experiment.entity_assayed.primary_external_id`, `…entity_assayed.gene_symbol`
(mirror of subject); `expression_experiment.single_reference.reference_id` (mirror); `single_reference.curie/title/pmid/doi`
(empty/redundant); `expression_experiment.detection_reagents`, `…specimen_genomic_model`, `…specimen_alleles`
(under_development); `internal` (policy flag); `negated`/`uncertain` (empty — fold into one optional "flags" toggle).
**COLLAPSE → chips (hide when empty):** `…anatomical_structure_uberon_terms`, `…cellular_component_qualifiers`,
`…stage_uberon_slim_terms`; `condition_relations` → "N conditions" sub-table. **Pair CURIE+name** for
anatomical_structure, cellular_component, developmental_stage_start, expression_assay_used.
**KEEP-EDITABLE:** `expression_annotation_subject.primary_external_id`, `…gene_symbol`,
`single_reference.reference_id`, `where_expressed_statement`, `when_expressed_stage_name`, `relation.name`.

**Proposed groups (7 → 5):** Subject · Reference · Assay · Expression site (+ conditions when present) ·
Stage & relation (data_provider read-only context; negated/uncertain as collapsed flags).

**Ontology fields:** assay (MMO) `expression_assay_used.curie`; anatomy (WBbt) `anatomical_structure.curie`;
GO/CC `cellular_component.curie` + qualifiers; UBERON `*_uberon_terms`, `stage_uberon_slim_terms`;
life-stage `when_expressed_stage_name`, `developmental_stage_start.curie`; CV `relation.name`; and the
in-condition ZECO/ChEBI/taxon curies.

---

## 3. gene (`packages/alliance/domain_packs/gene/domain_pack.yaml`)

One object (`gene_mention_evidence`, validated_reference). **No `workspace_display.groups`** → 9 read-only
summary fields today. Validator patches resolved values onto the same object (no separate row).

**Headline decision — proposed-vs-validated:** default to the **resolved** triplet (`primary_external_id`,
`gene_symbol`, `taxon`) as editable; show the matching `proposed_*` only as a muted "AI proposed: X"
sub-label **when it diverges** (fires 1/11 in real data: `cep-290`/`ccep-290`). **Hide**
`proposed_primary_external_id` (never populated).

**Correctness fix:** `species` ("C. elegans") is currently **silently dropped** despite being in
`summary_fields` — restore it as read-only context beside the taxon CURIE.
**COLLAPSE:** the 5 evidence-locator fields (`page`, `section`, `subsection`, `figure_reference`, +hide
`chunk_id`) → one "Evidence location" line; `identity_resolution_notes` → collapsible notes; `data_provider_hint`
→ provenance footer. **Move to header/chip:** `confidence` (badge), `verified_quote` (inline evidence chip).
**HIDE:** `taxon_hint`, `evidence_record_id`, `chunk_id`.
**Only ontology field:** `taxon` (NCBITaxon). `primary_external_id`/`gene_symbol` use the gene validator
+ direct edit, not the term browser.

**Proposed groups (add):** Gene identity (editable: gene_symbol, primary_external_id, taxon, species) ·
AI proposal (divergence-only) · Evidence (collapsed locator) · Provenance & notes.

---

## 4. allele (`packages/alliance/domain_packs/allele/domain_pack.yaml`)

**No `workspace_display`** → under-projected. Real session: 6 `AllelePaperEvidenceAssociation` rows each
show only `association_kind` + `evidence_record_ids[0]` (the **least** useful), and the curator **cannot
see which allele the row is about**. Inverse of the density problem.

**Biggest win:** promote `allele_identifier` (+ a resolved allele symbol/CURIE chip) onto the association
row; **HIDE** `association_kind` (routing constant) and `evidence_record_ids[0]` (internal id).
**De-duplicate** the shared `Reference`/`title` (all 6 rows = same paper) → session header; drop the
per-row `reference` object_ref. **COLLAPSE** `evidence_quote`/`mention` → evidence chip + PDF highlight.
`Allele.allele_symbol` is the **only `editable:true` field in the pack** (when an Allele row materializes).
**Ontology/ID fields:** `allele_identifier`, `Allele.primary_external_id` (allele CURIE, direct-edit +
allele validator), `Allele.taxon` (NCBITaxon). AlleleMention/EvidenceQuote are `metadata_only` → no rows.

**Proposed groups (add):** Association → Allele (allele_identifier chip) + Evidence chip; Allele row →
Identity (allele_symbol editable, primary_external_id/taxon read-only). Set explicit `summary_fields` so
the fallback stops surfacing `association_kind`/`evidence_record_ids`.

---

## 5. disease (`packages/alliance/domain_packs/disease/domain_pack.yaml`)

Most complex. Abstract `DiseaseAnnotation` + 3 concrete subtypes (Gene/Allele/AGM, one per candidate) +
validated-reference sub-objects. Pack-level **write/export blocked** (`blocked_by: ALL-425`). Real AGM
unit rendered 6 fields; richer envelopes carry more.

**KEEP-EDITABLE:** `disease_annotation_object.curie` (DOID), `disease_relation_name` (subtype-constrained
select), `disease_annotation_subject.subject_identifier` (the blocking gate field), `evidence_code_curies`
(ECO chips), and optional collapsed `genetic_sex_name`, `disease_qualifier_names`, `with_gene_identifiers`.
**COLLAPSE:** `condition_relations.*` → one compact sub-table (row per condition: summary · relation · class/id/chemical/taxon CURIE cells);
the three validated-reference rows (`DOTerm`, `DiseaseAnnotationSubject`, `Reference`) → into the parent as
chips/badges. **HIDE:** the whole `evidence_records*` block, R4 vocab plumbing (`annotation_type_*`,
`*_vocabulary`, `*_id`), `confidence`, and the D4-blocked `single_reference.*`.
**Subtype note:** `disease_relation_name`'s allowed CV depends on `subject_type` (gene: is_implicated_in/is_marker_for/…;
allele: is_implicated_in; AGM: is_model_of/…) — one template, CV populated per subtype.
~16 rows (≤25 with conditions) → ~7 + conditions sub-table + 2 optional collapsed chips.

**Proposed groups (add):** Disease · Subject · Evidence & codes · Experimental conditions (sub-table) ·
Optional qualifiers (collapsed) · Provenance (read-only). **Ontology fields:** DOID `disease_annotation_object.curie`;
ECO `evidence_code_curies`; CV `disease_relation_name`, `disease_qualifier_names`, `genetic_sex_name`,
`condition_relation_type.name`; ZECO/ChEBI/NCBITaxon condition curies. *(Build the grouping against
declared pack fields, not just what renders today — sandbox disease envelopes are minimal.)*

---

## 6. phenotype (`packages/alliance/domain_packs/phenotype/domain_pack.yaml`)

**No `workspace_display`** anywhere. Real session `9fee8f02-…`: 136 candidates (34 assertion + 34 subject
+ 34 term + 34 reference). Assertion = 17 flat fields, 11 read-only, 6 pure scaffolding. Write/export blocked.

**Biggest win — HIDE the 6 `ontology_lookup_hint.*` fields** (rendered on **both** the assertion's
`phenotype_terms[0].ontology_lookup_hint.*` and the standalone PhenotypeTerm row). **HIDE** export/write
scaffolding (`export_state`, `write_blocked_reason` ×2 → amber state chip), `annotation_kind`,
`evidence_record_ids[0]`. **De-duplicate** the inlined `phenotype_terms[0].*` block vs the standalone
PhenotypeTerm row (show the term once; the row owns CURIE editing). **COLLAPSE** the 4 raw-JSON object_ref
blobs (`phenotype_annotation_subject`, `phenotype_terms[0]`, `single_reference`, `evidence_quote`) → chips.
**Suppress empty validated-reference rows** (Reference renders 0 fields). **Hide `condition_relations.*`
when empty.**
**KEEP-EDITABLE:** `phenotype_annotation_object` (statement), `negated` (toggle), `phenotype_terms[0].curie`
/ PhenotypeTerm `curie` (term, "⌕ Browse"), PhenotypeSubject `subject_identifier` + `taxon`.
17 fields → ~5 + evidence chip + conditional conditions expander.

**Proposed groups (add):** Phenotype statement · Phenotype term (chip + CURIE) · Subject · Evidence &
reference · Experimental conditions (collapsed) · Internal/system (hidden). **Ontology fields:**
phenotype ontology (MP/WBPhenotype) `phenotype_terms[0].curie` + PhenotypeTerm `curie`; NCBITaxon
PhenotypeSubject `taxon`; ZECO/ChEBI/taxon condition curies; CV `condition_relation_type.name`.

---

## 7. Implementation guidance for Build B

- **Per-pack work = author `workspace_display.groups` + `summary_fields`** for allele, disease, phenotype
  (none today), and **revise** gene_expression (7→5 groups) and gene (add groups). Encode per-field
  `editable`/`protected`, `hide`, `hide_when_empty`, and a `render_as` hint (chip / curie-chip /
  sub-table / evidence) so the UI rule-set in §1 is data-driven, not hard-coded per domain.
- **Shared UI rules (§1) implemented once** in the single review surface: hide internal/scaffolding,
  mirror collapse, evidence→PDF, nested→sub-table, hide-when-empty, CURIE+label→chip, proposed-on-divergence,
  field-state chips, "⌕ Browse terms" affordance keyed by `ontology_family`.
- **Correctness fixes surfaced by the audit** (do as part of Build B): restore gene `species`; promote
  allele `allele_identifier`; de-duplicate disease/phenotype validated-reference rows.
- **Ontology-field inventory (per §5 of the Build B spec)** is captured per pack above; it defines where
  the Phase-1 "⌕ Browse terms" affordance appears and what `ontology_family` each maps to.
