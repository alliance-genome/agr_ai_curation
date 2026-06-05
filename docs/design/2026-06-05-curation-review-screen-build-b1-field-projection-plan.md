# Build B1 — Curation Field Projection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the review screen render each domain object cleanly by authoring per-pack `workspace_display` (groups / summary fields / render hints) from the field-coverage audit, plus the small materializer support those hints need — so the curator sees ~5–12 meaningful fields (grouped, with chips/sub-tables for nested data) instead of a 17–32-field dump.

**Architecture:** `workspace_display` is freeform `object_definition.metadata` read ad-hoc by `materialization.py` (no schema to change). "Hide a field" = omit it from a group's `fields`. Two thin materializer additions: (1) `hide_when_empty` — drop an empty workspace field; (2) confirm field-`metadata.render_as` (chip / curie-chip / sub-table / evidence-locator / term-chip / divergence) passes through to the projected draft field for B2 to render. The bulk is YAML: revise gene_expression's groups; add `workspace_display` to gene, allele, disease, phenotype per the audit.

**Tech Stack:** Python 3 / SQLAlchemy / Pydantic / pytest (`asyncio_mode=auto`); domain-pack YAML. Backend/config only. **Companion:** `docs/design/2026-06-04-curation-field-coverage-audit.md` is the authoritative per-field source — this plan operationalizes it; consult it for the rationale behind each keep/hide/collapse.

**Key facts (from code reference):**
- Projected field = `DomainEnvelopeReviewRowSummaryField{field_path, label, value, field_type, metadata}` (`backend/src/schemas/curation_workspace.py:963`). `read_only`/`required`/group/order/`render_as` all live inside `metadata`.
- `_workspace_fields` (`materialization.py:1421`) only fires when `workspace_display.groups` exists; otherwise the draft falls back to summary fields (read-only).
- Field `metadata` is open; the materializer already spreads it into the projected field via `_field_definition_metadata` (`:1523`). So `render_as` on a field definition reaches `draft_field.metadata.field_metadata.render_as` with **no** backend change.
- `read_only = protected or not editable` (`:1535`). B1 does **not** change editability (no `editable:true` additions) — read-only stays read-only per the audit.
- `object`/`array` field_type → JSON textarea in `FieldRow.tsx:201` (B2 changes that; B1 only carries `render_as`).

---

## File Structure

**Modify:**
- `backend/src/lib/domain_packs/materialization.py` — add `hide_when_empty` handling in `_workspace_fields` (`:1421-1474`).
- `packages/alliance/domain_packs/gene_expression/domain_pack.yaml` — revise `workspace_display` (`:310`).
- `packages/alliance/domain_packs/gene/domain_pack.yaml` — add `groups` to `workspace_display` (`:80`).
- `packages/alliance/domain_packs/allele/domain_pack.yaml` — add `workspace_display` to its object definitions.
- `packages/alliance/domain_packs/disease/domain_pack.yaml` — add `workspace_display`.
- `packages/alliance/domain_packs/phenotype/domain_pack.yaml` — add `workspace_display`.

**Create:**
- `backend/tests/unit/lib/domain_packs/test_field_projection_hints.py` — materializer hint tests.
- `backend/tests/unit/lib/domain_packs/test_pack_workspace_display.py` — per-pack projection assertions (loads the real packs).

---

## Task 1: Materializer `hide_when_empty` + confirm `render_as` passthrough

**Files:**
- Modify: `backend/src/lib/domain_packs/materialization.py:1421-1474`
- Create: `backend/tests/unit/lib/domain_packs/test_field_projection_hints.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/lib/domain_packs/test_field_projection_hints.py`. Reuse the materialization test pattern (`backend/tests/unit/lib/domain_packs/test_materialization.py`): build a `DomainPackMetadata` with one object whose `workspace_display.groups` lists two fields — one with `metadata.hide_when_empty: true` (empty value) and one with `metadata.render_as: chip` — then materialize and assert.

```python
import pytest

from src.lib.domain_packs.materialization import DomainPackMetadataReviewRowMaterializer
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope, CuratableObjectStatus, DomainEnvelope, DomainEnvelopeStatus,
)
from src.schemas.domain_pack_metadata import (
    DomainPackFieldDefinition, DomainPackFieldType, DomainPackMetadata, DomainPackObjectDefinition,
)

pytestmark = pytest.mark.provider_agnostic_domain_pack


def _pack() -> DomainPackMetadata:
    return DomainPackMetadata(
        pack_id="fixture.hints",
        version="0.1.0",
        object_definitions=[
            DomainPackObjectDefinition(
                object_type="Thing",
                display_name="Thing",
                metadata={
                    "object_role": "curatable_unit",
                    "workspace_display": {
                        "primary_label_field": "name",
                        "groups": [
                            {"id": "main", "label": "Main", "fields": ["name", "tags", "code"]},
                        ],
                    },
                },
                fields=[
                    DomainPackFieldDefinition(field_path="name", field_type=DomainPackFieldType.STRING),
                    DomainPackFieldDefinition(field_path="tags", field_type=DomainPackFieldType.ARRAY,
                                              metadata={"hide_when_empty": True, "render_as": "chip"}),
                    DomainPackFieldDefinition(field_path="code", field_type=DomainPackFieldType.STRING,
                                              metadata={"render_as": "curie-chip"}),
                ],
            ),
        ],
    )


def _materialize(payload: dict):
    envelope = DomainEnvelope(
        envelope_id="env-1", domain_pack_id="fixture.hints", domain_pack_version="0.1.0",
        status=DomainEnvelopeStatus.EXTRACTED,
        objects=[CuratableObjectEnvelope(object_type="Thing", object_id="thing-1",
                                         status=CuratableObjectStatus.PENDING, payload=payload)],
    )
    rows = DomainPackMetadataReviewRowMaterializer(_pack()).materialize(envelope, envelope_revision=1)
    return rows[0].metadata["workspace_fields"]


def test_hide_when_empty_drops_empty_workspace_field():
    fields = _materialize({"name": "n", "tags": [], "code": "X:1"})
    paths = [f["field_path"] for f in fields]
    assert "tags" not in paths           # empty list + hide_when_empty -> dropped
    assert paths == ["name", "code"]


def test_hide_when_empty_keeps_populated_field():
    fields = _materialize({"name": "n", "tags": ["a"], "code": "X:1"})
    assert [f["field_path"] for f in fields] == ["name", "tags", "code"]


def test_render_as_metadata_passes_through_to_projected_field():
    fields = _materialize({"name": "n", "tags": ["a"], "code": "X:1"})
    by_path = {f["field_path"]: f for f in fields}
    assert by_path["tags"]["metadata"]["render_as"] == "chip"
    assert by_path["code"]["metadata"]["render_as"] == "curie-chip"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec backend pytest tests/unit/lib/domain_packs/test_field_projection_hints.py -v`
Expected: `test_hide_when_empty_drops_empty_workspace_field` FAILS (empty `tags` is currently kept as `None`); the render_as tests should already PASS (passthrough works today — that's intentional, they lock the convention).

- [ ] **Step 3: Add `hide_when_empty` to `_workspace_fields`**

In `backend/src/lib/domain_packs/materialization.py`, add a small helper near the other value helpers:

```python
def _is_empty_projection_value(value: Any) -> bool:
    if value is _MISSING or value is None:
        return True
    if isinstance(value, (str, list, tuple, dict, set)) and len(value) == 0:
        return True
    return False
```

Then in `_workspace_fields` (`:1421-1474`), inside the loop after `metadata = _field_definition_metadata(field_definition)` and after computing `value`, drop the field when hidden-on-empty:

```python
        if metadata.get("hide_when_empty") is True and _is_empty_projection_value(value):
            continue
        if value is _MISSING:
            value = None
```

(Place the `hide_when_empty` check BEFORE the existing `if value is _MISSING: value = None` line so `_MISSING` is treated as empty.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec backend pytest tests/unit/lib/domain_packs/test_field_projection_hints.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/lib/domain_packs/materialization.py backend/tests/unit/lib/domain_packs/test_field_projection_hints.py
git commit -m "feat(domain-packs): hide_when_empty workspace fields + render_as passthrough"
```

---

## Task 2: Revise `gene_expression` workspace_display (32 → ~12 fields)

Apply audit §2. Remove the entity_assayed mirrors, the redundant `single_reference` fields + the experiment mirror, the under-development fields, and `internal`; add `render_as` chips and `hide_when_empty`; collapse the term arrays and `condition_relations`.

**Files:**
- Modify: `packages/alliance/domain_packs/gene_expression/domain_pack.yaml:310-372`
- Modify: the relevant `fields[]` entries to add `render_as` / `hide_when_empty` metadata
- Test: `backend/tests/unit/lib/domain_packs/test_pack_workspace_display.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/lib/domain_packs/test_pack_workspace_display.py`. Load the real gene_expression pack and a representative object payload (model it on the sandbox candidate `pef-1`), materialize, assert the audit's headline outcomes. Use the existing real-pack loader (`load_domain_pack_metadata`; see `test_materialization.py:380-422` for the pattern).

```python
import pytest
from src.lib.domain_packs.materialization import DomainPackMetadataReviewRowMaterializer
from src.lib.domain_packs.loader import load_domain_pack_metadata   # confirm exact import via test_materialization.py
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope, CuratableObjectStatus, DomainEnvelope, DomainEnvelopeStatus,
)

pytestmark = pytest.mark.provider_agnostic_domain_pack


def _draft_paths(pack_id: str, object_type: str, payload: dict, object_role="curatable_unit"):
    meta = load_domain_pack_metadata(pack_id)
    env = DomainEnvelope(
        envelope_id="e", domain_pack_id=meta.pack_id, domain_pack_version=meta.version,
        status=DomainEnvelopeStatus.EXTRACTED,
        objects=[CuratableObjectEnvelope(object_type=object_type, object_id="o1",
                 status=CuratableObjectStatus.PENDING, payload=payload,
                 metadata={"object_role": object_role})],
    )
    row = DomainPackMetadataReviewRowMaterializer(meta).materialize(env, envelope_revision=1)[0]
    return [f["field_path"] for f in row.metadata.get("workspace_fields", [])]


def test_gene_expression_hides_mirror_and_under_development_fields():
    payload = {
        "expression_annotation_subject": {"primary_external_id": "WB:WBGene00006789", "gene_symbol": "pef-1"},
        "expression_experiment": {"entity_assayed": {"primary_external_id": "WB:WBGene00006789", "gene_symbol": "pef-1"},
                                  "expression_assay_used": {"curie": "MMO:0000640", "name": "GFP reporter"},
                                  "single_reference": {"reference_id": 1}, "detection_reagents": [], "specimen_alleles": []},
        "single_reference": {"reference_id": 1},
        "where_expressed_statement": "ciliated sensory neuron",
        "when_expressed_stage_name": "L4", "relation": {"name": "expressed_in"},
        "data_provider": {"abbreviation": "WB"}, "internal": False,
    }
    paths = _draft_paths("agr.alliance.gene_expression", "GeneExpressionAnnotation", payload)
    # mirrors + under-development + internal gone
    assert "expression_experiment.entity_assayed.primary_external_id" not in paths
    assert "expression_experiment.entity_assayed.gene_symbol" not in paths
    assert "expression_experiment.single_reference.reference_id" not in paths
    assert "expression_experiment.detection_reagents" not in paths
    assert "internal" not in paths
    # the essentials remain
    assert "expression_annotation_subject.gene_symbol" in paths
    assert "single_reference.reference_id" in paths
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec backend pytest tests/unit/lib/domain_packs/test_pack_workspace_display.py -k gene_expression -v`
Expected: FAIL — mirrors/under-development fields still present (current groups include them).

- [ ] **Step 3: Replace the gene_expression `workspace_display` block**

In `packages/alliance/domain_packs/gene_expression/domain_pack.yaml`, replace the `workspace_display` block (`:310-372`) with the revised five-group version:

```yaml
      workspace_display:
        primary_label_field: expression_annotation_subject.gene_symbol
        secondary_label_field: where_expressed_statement
        summary_fields:
          - expression_annotation_subject.gene_symbol
          - where_expressed_statement
          - when_expressed_stage_name
          - relation.name
          - expression_experiment.expression_assay_used.name
        groups:
          - id: subject
            label: Subject gene
            fields:
              - expression_annotation_subject.primary_external_id
              - expression_annotation_subject.gene_symbol
          - id: reference
            label: Reference
            fields:
              - single_reference.reference_id
          - id: assay
            label: Assay
            fields:
              - expression_experiment.expression_assay_used.curie
          - id: expression_site
            label: Expression site
            fields:
              - where_expressed_statement
              - expression_pattern.where_expressed.anatomical_structure.curie
              - expression_pattern.where_expressed.cellular_component.curie
              - expression_pattern.where_expressed.anatomical_structure_uberon_terms
              - expression_pattern.where_expressed.cellular_component_qualifiers
              - condition_relations
          - id: stage_relation
            label: Stage & relation
            fields:
              - when_expressed_stage_name
              - expression_pattern.when_expressed.developmental_stage_start.curie
              - expression_pattern.when_expressed.stage_uberon_slim_terms
              - relation.name
              - data_provider.abbreviation
```

Then add `metadata` hints on the relevant **field definitions** in `fields[]` (search each by `field_path`):
- `expression_experiment.expression_assay_used.curie`, `…anatomical_structure.curie`, `…cellular_component.curie`, `…developmental_stage_start.curie` → add `metadata: { render_as: curie-chip }` (and keep their existing metadata).
- `…anatomical_structure_uberon_terms`, `…cellular_component_qualifiers`, `…stage_uberon_slim_terms` → add `metadata: { render_as: chip, hide_when_empty: true }`.
- `condition_relations` → add `metadata: { render_as: sub-table, hide_when_empty: true }` (preserve its existing `multivalued: true`).

(The mirror/under-development/`internal`/`negated`/`uncertain`/redundant-reference fields are simply absent from the groups above → hidden. `data_provider.abbreviation` stays read-only via its existing `protected: true`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec backend pytest tests/unit/lib/domain_packs/test_pack_workspace_display.py -k gene_expression -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/alliance/domain_packs/gene_expression/domain_pack.yaml backend/tests/unit/lib/domain_packs/test_pack_workspace_display.py
git commit -m "feat(gene_expression): revise workspace_display per field audit (32->~12 fields)"
```

---

## Task 3: Add `workspace_display.groups` to `gene`

Apply audit §3. Gene has `workspace_display` with `summary_fields` but no `groups`; adding groups makes the draft grouped (and the already-`editable:true` identity fields editable). Show the resolved value; surface the proposed value only on divergence (via `render_as: divergence` for B2). Hide plumbing.

**Files:**
- Modify: `packages/alliance/domain_packs/gene/domain_pack.yaml:80-97`
- Test: `backend/tests/unit/lib/domain_packs/test_pack_workspace_display.py`

- [ ] **Step 1: Write the failing test**

```python
def test_gene_groups_show_identity_and_hide_plumbing():
    payload = {
        "mention": "pef-1", "gene_symbol": "pef-1", "primary_external_id": "WB:WBGene00006789",
        "taxon": "NCBITaxon:6239", "species": "Caenorhabditis elegans",
        "proposed_gene_symbol": "pef-1", "data_provider_hint": "WB",
        "confidence": "high", "section": "Results", "page": 4,
        "taxon_hint": "NCBITaxon:6239", "chunk_id": "abc", "evidence_record_id": "e1",
    }
    paths = _draft_paths("gene", "gene_mention_evidence", payload, object_role="validated_reference")
    assert "gene_symbol" in paths and "primary_external_id" in paths and "taxon" in paths
    assert "species" in paths
    assert "taxon_hint" not in paths and "chunk_id" not in paths and "evidence_record_id" not in paths
```

- [ ] **Step 2: Run test → FAIL** (gene has no groups; draft falls back to all summary fields incl. plumbing).

Run: `docker compose exec backend pytest tests/unit/lib/domain_packs/test_pack_workspace_display.py -k gene_groups -v`

- [ ] **Step 3: Add `groups` to gene's `workspace_display`**

In `packages/alliance/domain_packs/gene/domain_pack.yaml`, extend the `workspace_display` block (keep `primary_label_field`/`secondary_label_field`; you may drop the inert `evidence_quote_field`/`evidence_locator_fields`):

```yaml
      workspace_display:
        primary_label_field: mention
        secondary_label_field: gene_symbol
        summary_fields:
          - gene_symbol
          - primary_external_id
          - taxon
        groups:
          - id: identity
            label: Gene identity
            fields:
              - gene_symbol
              - primary_external_id
              - taxon
              - species
          - id: ai_proposal
            label: AI proposal
            fields:
              - proposed_gene_symbol
              - proposed_taxon
          - id: evidence_location
            label: Evidence location
            fields:
              - section
              - page
              - subsection
              - figure_reference
          - id: provenance
            label: Provenance & notes
            fields:
              - data_provider_hint
              - identity_resolution_notes
              - confidence
```

Field-definition `metadata` hints: `proposed_gene_symbol`, `proposed_taxon` → `metadata: { render_as: divergence, hide_when_empty: true }`; `section`/`page`/`subsection`/`figure_reference` → `metadata: { render_as: evidence-locator, hide_when_empty: true }`; `identity_resolution_notes` → `metadata: { render_as: notes, hide_when_empty: true }`. (`species` has no `editable:true` so it renders read-only — correct per audit. `mention` stays the label; `verified_quote`/`taxon_hint`/`chunk_id`/`evidence_record_id`/`proposed_primary_external_id` are omitted → hidden.)

- [ ] **Step 4: Run test → PASS.**
- [ ] **Step 5: Commit**

```bash
git add packages/alliance/domain_packs/gene/domain_pack.yaml backend/tests/unit/lib/domain_packs/test_pack_workspace_display.py
git commit -m "feat(gene): add workspace_display groups (identity/proposal/evidence/provenance)"
```

---

## Task 4: Add `workspace_display` to `allele`

Apply audit §4. The big win: promote `allele_identifier` onto the curatable association row; hide `association_kind` + `evidence_record_ids[0]`. Add a group to each relevant object definition (`AllelePaperEvidenceAssociation`, `Allele`).

**Files:**
- Modify: `packages/alliance/domain_packs/allele/domain_pack.yaml`
- Test: `backend/tests/unit/lib/domain_packs/test_pack_workspace_display.py`

- [ ] **Step 1: Write the failing test**

```python
def test_allele_association_promotes_identifier_hides_routing():
    payload = {"association_kind": "allele_paper_evidence", "allele_identifier": "WB:WBVar00012345",
               "evidence_record_ids": ["evidence-7cad0b8f"]}
    paths = _draft_paths("agr.alliance.allele", "AllelePaperEvidenceAssociation", payload)
    assert "allele_identifier" in paths
    assert "association_kind" not in paths
    assert "evidence_record_ids[0]" not in paths and "evidence_record_ids" not in paths
```

- [ ] **Step 2: Run → FAIL** (no `workspace_display`; summary fallback shows `association_kind`/`evidence_record_ids[0]`, not `allele_identifier`).

- [ ] **Step 3: Add `workspace_display` to the object definitions**

In `packages/alliance/domain_packs/allele/domain_pack.yaml`, under `object_definitions`, on the `AllelePaperEvidenceAssociation` object's `metadata`, add:

```yaml
        workspace_display:
          summary_fields:
            - allele_identifier
          groups:
            - id: allele
              label: Allele
              fields:
                - allele_identifier
```

And on the `Allele` object definition's `metadata`:

```yaml
        workspace_display:
          summary_fields:
            - allele_symbol
            - primary_external_id
          groups:
            - id: identity
              label: Identity
              fields:
                - allele_symbol
                - primary_external_id
                - taxon
```

Field hint: `allele_identifier` → `metadata: { render_as: chip }` (it is read-only today; Phase-2 direct-edit is out of scope). `Allele.allele_symbol` keeps its existing `editable: true`; `primary_external_id`/`taxon` stay read-only.

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit**

```bash
git add packages/alliance/domain_packs/allele/domain_pack.yaml backend/tests/unit/lib/domain_packs/test_pack_workspace_display.py
git commit -m "feat(allele): add workspace_display (promote allele_identifier, hide routing fields)"
```

---

## Task 5: Add `workspace_display` to `disease`

Apply audit §5. Groups: Disease / Subject / Evidence & codes / Experimental conditions (sub-table) / Optional qualifiers / Provenance. Hide evidence-record internals, R4 vocab plumbing, `confidence`, blocked `single_reference`. Apply to the concrete subtype objects (`GeneDiseaseAnnotation`, `AlleleDiseaseAnnotation`, `AGMDiseaseAnnotation`) and the abstract `DiseaseAnnotation` (same `workspace_display`).

**Files:**
- Modify: `packages/alliance/domain_packs/disease/domain_pack.yaml`
- Test: `backend/tests/unit/lib/domain_packs/test_pack_workspace_display.py`

- [ ] **Step 1: Write the failing test**

```python
def test_disease_groups_hide_plumbing_and_keep_curatable():
    payload = {
        "disease_annotation_object": {"curie": "DOID:0050200", "name": "x"},
        "disease_annotation_subject": {"subject_identifier": "WB:WBGene1", "subject_type": "gene", "subject_label": "pef-1"},
        "disease_relation_name": "is_implicated_in", "evidence_code_curies": ["ECO:0000033"],
        "data_provider": {"abbreviation": "WB"}, "confidence": "high",
        "annotation_type_vocabulary": "v", "annotation_type_id": "i",
    }
    paths = _draft_paths("agr.alliance.disease", "AGMDiseaseAnnotation", payload)
    assert "disease_annotation_object.curie" in paths
    assert "disease_annotation_subject.subject_identifier" in paths
    assert "evidence_code_curies" in paths
    assert "confidence" not in paths
    assert "annotation_type_vocabulary" not in paths and "annotation_type_id" not in paths
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Add `workspace_display` to each disease object definition**

Add this block to the `metadata` of `DiseaseAnnotation`, `GeneDiseaseAnnotation`, `AlleleDiseaseAnnotation`, and `AGMDiseaseAnnotation` (identical for all four — the abstract + 3 subtypes share the field set):

```yaml
        workspace_display:
          primary_label_field: disease_annotation_object.name
          secondary_label_field: disease_annotation_subject.subject_label
          summary_fields:
            - disease_annotation_object.name
            - disease_relation_name
            - disease_annotation_subject.subject_label
          groups:
            - id: disease
              label: Disease
              fields:
                - disease_annotation_object.curie
                - disease_relation_name
            - id: subject
              label: Subject
              fields:
                - disease_annotation_subject.subject_identifier
                - disease_annotation_subject.subject_type
                - disease_annotation_subject.subject_label
            - id: evidence_codes
              label: Evidence & codes
              fields:
                - evidence_code_curies
            - id: conditions
              label: Experimental conditions
              fields:
                - condition_relations
            - id: qualifiers
              label: Optional qualifiers
              fields:
                - disease_qualifier_names
                - genetic_sex_name
                - with_gene_identifiers
            - id: provenance
              label: Provenance
              fields:
                - data_provider.abbreviation
```

Field hints: `disease_annotation_object.curie` → `metadata: { render_as: curie-chip }`; `evidence_code_curies`, `disease_qualifier_names`, `with_gene_identifiers` → `metadata: { render_as: chip, hide_when_empty: true }`; `genetic_sex_name` → `metadata: { hide_when_empty: true }`; `condition_relations` → `metadata: { render_as: sub-table, hide_when_empty: true }`. (Everything in the audit's HIDE list — `evidence_records*`, `annotation_type_*`, `*_vocabulary`/`*_id`, `confidence`, `role`, `mention`, blocked `single_reference.*` — is omitted from the groups → hidden.)

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit**

```bash
git add packages/alliance/domain_packs/disease/domain_pack.yaml backend/tests/unit/lib/domain_packs/test_pack_workspace_display.py
git commit -m "feat(disease): add workspace_display (groups + conditions sub-table; hide R4 plumbing)"
```

---

## Task 6: Add `workspace_display` to `phenotype`

Apply audit §6. Biggest win: hide the 6 `ontology_lookup_hint.*` and the export/write scaffolding; de-duplicate the inlined term vs the standalone `PhenotypeTerm` row.

**Files:**
- Modify: `packages/alliance/domain_packs/phenotype/domain_pack.yaml`
- Test: `backend/tests/unit/lib/domain_packs/test_pack_workspace_display.py`

- [ ] **Step 1: Write the failing test**

```python
def test_phenotype_hides_lookup_hints_and_scaffolding():
    payload = {
        "phenotype_annotation_object": "dye-filling defect", "negated": False,
        "phenotype_terms": [{"curie": "WBPhenotype:0001191", "label": "dye-filling defect",
                             "ontology_lookup_hint": {"data_provider": "WB", "taxon_id": "NCBITaxon:6239"},
                             "export_state": "blocked", "write_blocked_reason": "x"}],
        "annotation_kind": "phenotype_assertion", "evidence_record_ids": ["e1"],
    }
    paths = _draft_paths("agr.alliance.phenotype", "PhenotypeAnnotation", payload)
    assert "phenotype_annotation_object" in paths
    assert "phenotype_terms[0].curie" in paths
    assert not any("ontology_lookup_hint" in p for p in paths)
    assert not any("export_state" in p or "write_blocked_reason" in p for p in paths)
    assert "annotation_kind" not in paths
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Add `workspace_display` to phenotype object definitions**

On `PhenotypeAnnotation`'s `metadata`:

```yaml
        workspace_display:
          primary_label_field: phenotype_terms[0].label
          secondary_label_field: phenotype_annotation_object
          summary_fields:
            - phenotype_terms[0].label
            - phenotype_annotation_object
          groups:
            - id: statement
              label: Phenotype statement
              fields:
                - phenotype_annotation_object
                - negated
            - id: term
              label: Phenotype term
              fields:
                - phenotype_terms[0].curie
            - id: subject
              label: Subject
              fields:
                - phenotype_annotation_subject
            - id: conditions
              label: Experimental conditions
              fields:
                - condition_relations
```

On `PhenotypeSubject`'s `metadata` (validated_reference): a small Identity group:

```yaml
        workspace_display:
          summary_fields:
            - subject_label
          groups:
            - id: identity
              label: Subject identity
              fields:
                - subject_identifier
                - taxon
                - subject_label
                - subject_type
```

Field hints: `phenotype_terms[0].curie` → `metadata: { render_as: term-chip }`; `phenotype_annotation_subject` → `metadata: { render_as: chip }`; `condition_relations` → `metadata: { render_as: sub-table, hide_when_empty: true }`. (The standalone `PhenotypeTerm` object gets **no** group → it stays summary-only/read-only, so the editable term lives only on the assertion, de-duplicating per the audit. All `ontology_lookup_hint.*`, `export_state`, `write_blocked_reason`, `annotation_kind`, `evidence_record_ids` are omitted → hidden.)

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit**

```bash
git add packages/alliance/domain_packs/phenotype/domain_pack.yaml backend/tests/unit/lib/domain_packs/test_pack_workspace_display.py
git commit -m "feat(phenotype): add workspace_display (hide lookup hints/scaffolding; de-dup term)"
```

---

## Task 7: Regression + live sandbox spot-check

- [ ] **Step 1: Run the full domain-pack + materialization suites**

Run: `docker compose exec backend pytest tests/unit/lib/domain_packs -v`
Expected: all PASS (new tests + existing materialization/validation tests — the workspace-group validation tests must still pass; if any pack's groups reference a non-existent field path, fix the path).

- [ ] **Step 2: Re-materialize one real session per pack and eyeball the field counts**

The sandbox sessions are envelope-backed; re-deriving review rows from the persisted envelope uses the new `workspace_display`. For each pack, count the draft fields a candidate now projects (should drop sharply, e.g. gene_expression 32 → ~12):

Run (gene_expression example):
```bash
incus exec symphony-main -- docker exec agrmainsandbox-postgres-1 sh -lc \
 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "select envelope_id, object_id, envelope_revision from curation_candidates where session_id='"'"'a1419a0e-d943-4718-a6d6-652fe35390a5'"'"' limit 1;"'
```
Then call `GET /api/curation-workspace/domain-envelopes/{envelope_id}/review-rows?revision={rev}` against the dev backend (`http://10.79.64.167:8900`) and confirm each row's workspace fields match the new groups. (Existing persisted candidates keep their old draft until re-bootstrapped; the review-rows endpoint reflects the new projection live.)

- [ ] **Step 3: Confirm no pack fails to load**

Run: `docker compose exec backend pytest tests/unit/lib/domain_packs/test_pack_workspace_display.py -v`
Expected: all per-pack tests PASS; no `DomainEnvelopeMaterializationError` (which would mean a malformed group — every group needs `id` + `label`).

- [ ] **Step 4: Commit any path fixes**

```bash
git add packages/alliance/domain_packs/ backend/tests/unit/lib/domain_packs/
git commit -m "test(domain-packs): regression pass for per-pack workspace_display"
```

---

## Notes for B2 (consumes B1's output)
- B2 renders by `draft_field.metadata.field_metadata.render_as`: `curie-chip` (label + CURIE, click→browse later), `chip` (multivalued list), `sub-table` (condition_relations etc.), `evidence-locator` (collapsed location line), `term-chip`, `divergence` (show proposed only when ≠ resolved), `notes`. Until B2 ships, these `render_as` values are inert (fields render with today's `field_type` widgets) — B1 is safe to land independently.
- Group-anchored evidence: evidence is anchored to a member `field_path` of a group (B1 didn't change evidence projection); B2 surfaces the group's evidence chip by matching anchors whose `field_path` is in the group's fields.

---

## gpt-5.5 Review Corrections (fold in before implementing)

Verdict: **Sound-with-corrections.** Apply these:

1. **Gene `where_expressed_statement` does not exist** in the gene pack — already fixed above (Task 3 `summary_fields` now uses `gene_symbol`/`primary_external_id`/`taxon`). Double-check every group/summary path you author against the pack's declared `fields[]`.

2. **Read-only wording.** "Summary fallback is read-only" is imprecise: the pipeline derives `read_only` from **field metadata** in *both* the summary-fallback and workspace-group paths (`pipeline.py:592,655,734`). What groups actually change is the **draft source** (summary fallback → explicit workspace fields), not editability. Editability stays metadata-gated (`read_only = protected or not editable`). Gene's `gene_symbol`/`primary_external_id`/`taxon` already have `editable: true` (`gene/domain_pack.yaml:193,211,229`) so they're editable in both paths. Reword §3/§13 accordingly; don't imply adding groups makes fields editable.

3. **The materializer tolerates unknown group field paths** (it projects a missing field as `field_type: "any"`, `value: None` — `test_materialization.py:526`), so a typo'd path will **not** fail the existing tests. Add an explicit **declared-field-path validity assertion** per pack: a test that every path listed in each pack's `workspace_display.groups`/`summary_fields` exists in that object's declared `fields[]` (or is a valid payload leaf). Add this as a step in Task 7.

4. **Add a draft-level `render_as` test.** The Task 1 tests assert at the review-row level. Add one test that runs the pipeline draft conversion (`_draft_fields_from_review_row`) and asserts `draft_field.metadata.field_metadata.render_as` and `draft_field.read_only` on an actual draft field — so B1 verifies the value B2 will read, not only the review-row metadata. (Confirmed: `render_as` does pass through via `**dict(field_definition.metadata)` → `pipeline.py:644`.)

5. **Minor:** Task 1's expected-failure note — an empty `tags: []` is currently kept as `[]` (not `None`); `_workspace_fields` only converts `_MISSING` → `None` (`materialization.py:1442,1454`). The `_is_empty_projection_value` helper is still correct (it preserves `False`/`0`, drops `_MISSING`/`None`/empty containers).

## Self-Review (completed)

- **Spec coverage (audit §1–§7):** universal hide-by-omission (all per-pack tasks) ✓; `hide_when_empty` (T1) ✓; `render_as` chips/curie-chips/sub-tables (T1 passthrough + per-pack hints) ✓; gene_expression revision (T2) ✓; gene groups + proposed-on-divergence + restore species read-only (T3) ✓; allele promote identifier (T4) ✓; disease groups + conditions sub-table + hide R4 plumbing (T5) ✓; phenotype hide lookup hints + de-dup term (T6) ✓. Editability deliberately unchanged (read-only stays read-only).
- **Placeholder scan:** no TBD/"add handling". The one live lookup (exact `load_domain_pack_metadata` import) is a verification step pointing at `test_materialization.py`.
- **Type consistency:** every test uses the same `_draft_paths` helper and reads `row.metadata["workspace_fields"]` → `field_path`; `render_as`/`hide_when_empty` are always field-definition `metadata` keys; group objects always carry `id`+`label`+`fields` (else the materializer raises). `_is_empty_projection_value` (T1) is the only new symbol, used once.
