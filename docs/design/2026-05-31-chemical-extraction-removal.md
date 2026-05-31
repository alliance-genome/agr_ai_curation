# Remove the standalone `chemical_extractor` (placeholder) — removal map

Date: 2026-05-31. Decision (Chris): the standalone **chemical extractor was a test/placeholder agent**
and is being removed. Chemicals are NOT a standalone curation target in the LinkML model — they are a
referenced ontology term used as a *component* of other annotations — so "chemical" belongs as a
**validator** (which already exists), not an extractor.

## Why (LinkML grounding)

`ChemicalTerm` (`ontologyTerm.yaml:393`, subclasses `CHEBITerm` + WBMol `Molecule`) is a controlled-
vocabulary ontology term, like a gene curie or a DOID. There is **no standalone "Chemical" annotation
class**. A chemical only ever appears as a component of something else:

| Use of `ChemicalTerm` / chemical | Host class |
|---|---|
| `condition_chemical` (in `ExperimentalCondition`) | conditions, attached to disease/phenotype/gene-expression annotations via `condition_relations` (the `condition_relations` slot is on the `Annotation` base — `expression.yaml:68`, `phenotypeAndDiseaseAnnotation.yaml:63`) |
| `chemical_mutagen` (`allele.yaml:891`) | allele annotations (mutagenesis) |
| `GeneMolecularInteraction` (`geneInteraction.yaml:157`) | gene–molecule interactions (separate type, not in the six) |

`ExperimentalCondition` (`phenotypeAndDiseaseAnnotation.yaml:450`) → `ConditionRelation` (526) →
attaches to a host `Annotation`. So conditions are never standalone; the standalone
`chemical_extractor` produced `ChemicalCondition` objects with no model home (hence its perpetual
export-blocked posture).

## Three chemical-related agents today

| Agent dir | agent_id | category | disposition |
|---|---|---|---|
| `packages/alliance/agents/chemical_extractor` | `chemical_extractor` | Extraction | **REMOVE** |
| `packages/alliance/agents/chemical` | `chemical_validation` ("Chemical Ontology Agent") | Validation | **KEEP** — the reusable chemical→ChEBI/WBMol resolver |
| `packages/alliance/agents/experimental_condition` | `experimental_condition_validation` | Validation | **KEEP** — condition-structure validator (used when conditions return) |

`chemical_validation` is exactly the "chemical as a validator" service that disease's `condition_chemical`,
allele's `chemical_mutagen`, and the later condition work will call. It already exists.

## Removal map (file-level; gathered via grep, 2026-05-31)

### Delete outright
- `packages/alliance/agents/chemical_extractor/` (agent.yaml, prompt.yaml, schema.py, group_rules)
- `packages/alliance/domain_packs/chemical_condition/` (domain_pack.yaml + fixtures)
- `packages/alliance/python/src/agr_ai_curation_alliance/domain_packs/chemical_condition/` (constants.py, export.py, submit.py, `__init__.py`)
- `backend/tests/unit/test_chemical_extractor_domain_envelope_contract.py`
- `backend/tests/contract/alliance/domain_packs/test_chemical_condition_domain_pack.py`
- `backend/tests/fixtures/domain_packs/chemical_condition/` (`tool_verified_chemical_output.yaml`, `tool_verified_pending_envelope.yaml`)
- `backend/tests/fixtures/evidence/tool_verified_chemical_paper.json`

### Edit — platform source (remove the references; file stays)
- `packages/alliance/package.yaml` — remove the `chemical_extractor` agent entry (line ~37). KEEP `experimental_condition` (line ~50) and the `chemical` (chemical_validation) entry.
- `packages/alliance/python/src/agr_ai_curation_alliance/curation_adapters.py` — remove the `chemical_condition` import (line 6) and the three `"chemical"` map entries: pack-id map (line 40 `"chemical": "agr.alliance.chemical_condition"`), export adapter (48 `ChemicalConditionExportAdapter`), submission adapter (55 `ChemicalConditionSubmissionBlockerAdapter`).
- `backend/src/lib/agent_studio/registry_builder.py` — remove the `chemical_extractor` registry entry (line ~656).
- `backend/src/lib/agent_studio/trace_agent_metadata.py` — remove the `chemical_extraction`/`ask_chemical_extractor_*` mappings (lines ~24, 26, 27).
- `backend/src/lib/agent_studio/flow_tools.py` — remove `chemical_extractor` from the extractor routing list (lines ~180, 237).
- `backend/src/lib/agent_studio/diagnostic_tools/tool_definitions.py` — remove `chemical_extractor` from the docstring extractor list (line ~766).
- `backend/src/lib/openai_agents/audit_labels.py` — remove the `ask_chemical_extractor_specialist` label (line ~19).
- `backend/src/lib/openai_agents/models.py` — remove `class ChemicalExtractionResultEnvelope` (line ~634) AFTER confirming no remaining importers (the agent `schema.py` that imported it is deleted).

### Edit — shared test files (drop only the `chemical_extractor`/`chemical_condition` cases)
~24 test files reference `chemical_extractor` in parametrize lists / assertions / registries and must be
trimmed (NOT deleted) — e.g. `test_executor.py`, `test_system_agent_sync.py`, `test_registry_builder.py`,
`test_bundled_alliance_package_aware_loaders.py`, `test_audit_labels.py`, `test_runner_tool_labels.py`,
`test_streaming_tools_helpers.py`, `test_domain_envelope_extraction.py`, `test_domain_envelope_repair_prompt_contract.py`,
`test_record_evidence_prompt_contract.py`, `test_non_gene_evidence_prompt_policy.py`, `test_validation_attachments.py`,
`test_batch_capabilities.py`, `test_phenotype_agent_migration.py`, `test_project_agnostic_runtime_guardrails.py`,
`test_domain_envelope_pdf_corpus.py`, `test_flows_endpoints.py`, `test_agent_studio_metadata.py`, the
export/submission + live-db-lookup + reference-validation + validation-metadata contract tests,
`tests/fixtures/domain_packs/export_submission/projection_fixtures.yaml`, and `tests/integration/test_curation_submission_e2e.py`.

> **CRITICAL keep-vs-remove classification:** some files mention BOTH the chemical *extractor* (remove)
> and the *validators* (keep). `test_disease_chemical_validator_result_contract.py` and
> `test_experimental_condition_validation_agent.py` are about the KEPT validators — preserve their
> validator cases; only strip extractor/chemical_condition-pack references.

### DB seed migration
`backend/alembic/versions/f7a8b9c0d1e2_add_chemical_extractor_system_agent.py` seeds the
`chemical_extractor` row into the unified `agents` table. Do NOT edit the historical migration — add a
**new forward migration** that removes the seeded row, and confirm the package.yaml→agents sync prunes it
on bootstrap (`RUN_DB_BOOTSTRAP_ON_START`). `test_system_agent_sync.py` covers this sync.

## Verification gate (the real safety net)
After removal, the **full backend test suite must be green** (no NEW failures vs the pre-existing baseline —
note the known-unrelated `test_gene_expression_builder_tools.py` SDK-introspection failures) and the
sandbox backend must boot (`/docs` 200, supervisor routing intact with no dangling `chemical_extractor`
reference). Then Opus 4.8 review → git-safe commit.

## Not removed / future
- KEEP `chemical_validation` + `experimental_condition_validation`.
- Experimental conditions (chemical + ZECO + relation) return LATER as `condition_relations` consumed by
  host annotations (disease/phenotype/gene-expression), per the disease D6 decision — NOT as a standalone
  extractor.
