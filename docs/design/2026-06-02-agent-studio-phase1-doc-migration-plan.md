# Agent Studio Phase 1 — Agent Docs to `docs.yaml` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move every agent's curator-facing documentation (summary, capabilities, data sources, limitations, tips) out of the Python `AGENT_DOCUMENTATION` dict into a per-bundle `docs.yaml` (plus a small system-docs YAML for the two synthetic flow nodes), add a `tips` field end-to-end, render tips from the API, delete the Python dict and its fallback, and add a guard test so agent documentation can never silently go missing.

**Architecture:** Agent definitions load from runtime packages (`packages/alliance/agents/<folder>/`, `packages/core/agents/<folder>/`) via `load_agent_definitions()`. Today `AgentDefinition.documentation` comes from an inline `documentation:` key in `agent.yaml` and otherwise falls back to the `AGENT_DOCUMENTATION` dict in `registry_builder.py`. We add a sibling `docs.yaml` read (adjunct asset from `AgentConfigSource.agent_dir`) that populates `AgentDefinition.documentation`, migrate all content into those files, and delete the dict. The work is split into a **faithful port** (byte-equivalent to the dict, locked by a parity test) followed by a reviewed **improve** pass (curator-voice rewrites + net-new docs for agents the dict never covered, enforced by a completeness guard test).

**Tech Stack:** Python 3, pytest, dataclasses, PyYAML, Pydantic v2 (backend); TypeScript/React + MUI (frontend).

---

## File Structure

**Backend — modify:**
- `backend/src/lib/agent_studio/models.py` — add `tips` to `AgentDocumentation` (one responsibility: API contract models).
- `backend/src/lib/agent_studio/catalog_service.py` — `_convert_documentation` maps `tips`.
- `backend/src/lib/config/agent_loader.py` — load sibling `docs.yaml` into `AgentDefinition.documentation`.
- `backend/src/lib/agent_studio/registry_builder.py` — delete `AGENT_DOCUMENTATION`; wire synthetic entries (`task_input`, `curation_prep`) to a system-docs YAML loader.

**Backend — create:**
- `backend/src/lib/agent_studio/system_agent_docs.yaml` — docs for the two synthetic flow nodes (no agent folder).
- `backend/src/lib/agent_studio/system_agent_docs.py` — tiny loader for that YAML.
- `backend/tests/unit/api/test_agent_documentation_tips.py` — tips contract test.
- `backend/tests/unit/config/test_agent_docs_yaml_loader.py` — loader test.
- `backend/tests/unit/api/test_agent_documentation_parity.py` — parity test + committed baseline fixture.
- `backend/tests/unit/api/test_agent_documentation_completeness.py` — guard test.

**Per-agent — create (content migration):** one `docs.yaml` per agent bundle (table in Task 6).

**Frontend — modify:**
- `frontend/src/types/promptExplorer.ts` — add `tips?: string[]` to `AgentDocumentation`.
- `frontend/src/components/AgentStudio/AgentDetailsPanel.tsx` — render `documentation.tips` instead of literals.

---

## Task 1: Add `tips` to the documentation contract (backend model + converter)

**Files:**
- Modify: `backend/src/lib/agent_studio/models.py:40-51`
- Modify: `backend/src/lib/agent_studio/catalog_service.py:171-176`
- Test: `backend/tests/unit/api/test_agent_documentation_tips.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/api/test_agent_documentation_tips.py
"""Tips are part of the agent documentation contract and survive conversion."""
from src.lib.agent_studio.models import AgentDocumentation
from src.lib.agent_studio.catalog_service import _convert_documentation


def test_agent_documentation_has_tips_field_defaulting_empty():
    doc = AgentDocumentation(summary="s")
    assert doc.tips == []


def test_convert_documentation_maps_tips():
    doc_dict = {
        "summary": "Validates genes.",
        "capabilities": [{"name": "Gene lookup", "description": "Find genes"}],
        "tips": ["Include the species when possible"],
    }
    converted = _convert_documentation(doc_dict)
    assert converted is not None
    assert converted.tips == ["Include the species when possible"]


def test_convert_documentation_tips_defaults_empty_when_absent():
    converted = _convert_documentation({"summary": "Validates genes."})
    assert converted is not None
    assert converted.tips == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec backend pytest tests/unit/api/test_agent_documentation_tips.py -v`
Expected: FAIL — `AgentDocumentation` has no attribute/field `tips`.

- [ ] **Step 3: Add the `tips` field to the model**

In `backend/src/lib/agent_studio/models.py`, inside `class AgentDocumentation`, after the `limitations` field (currently lines 49-51):

```python
    limitations: List[str] = Field(
        default_factory=list, description="Known limitations as simple strings"
    )
    tips: List[str] = Field(
        default_factory=list,
        description="Curator-friendly 'tips for best results', plain-language strings",
    )
```

- [ ] **Step 4: Map `tips` in the converter**

In `backend/src/lib/agent_studio/catalog_service.py`, in `_convert_documentation`, change the final `return` (currently lines 171-176) to include tips:

```python
    return AgentDocumentation(
        summary=doc_dict.get("summary", ""),
        capabilities=capabilities,
        data_sources=data_sources,
        limitations=doc_dict.get("limitations", []),
        tips=doc_dict.get("tips", []),
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `docker compose exec backend pytest tests/unit/api/test_agent_documentation_tips.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/src/lib/agent_studio/models.py backend/src/lib/agent_studio/catalog_service.py backend/tests/unit/api/test_agent_documentation_tips.py
git commit -m "feat(agent-studio): add tips field to agent documentation contract"
```

---

## Task 2: Load a sibling `docs.yaml` into `AgentDefinition.documentation`

**Files:**
- Modify: `backend/src/lib/config/agent_loader.py:320-337`
- Test: `backend/tests/unit/config/test_agent_docs_yaml_loader.py`

The loader currently builds each `AgentDefinition` from `agent.yaml` only (`from_yaml`, which reads an inline `documentation:` key). We read a sibling `docs.yaml` from `source.agent_dir` and, when present, use it as the documentation source. `docs.yaml` and an inline `documentation:` key must not both be present (fail fast — no ambiguous source).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/config/test_agent_docs_yaml_loader.py
"""A sibling docs.yaml populates AgentDefinition.documentation."""
import textwrap
import pytest

from src.lib.config.agent_loader import load_agent_definitions, reset_cache


def _write_agent_bundle(root, folder, agent_yaml, docs_yaml=None):
    bundle = root / folder
    bundle.mkdir(parents=True)
    (bundle / "agent.yaml").write_text(textwrap.dedent(agent_yaml))
    if docs_yaml is not None:
        (bundle / "docs.yaml").write_text(textwrap.dedent(docs_yaml))
    return bundle


def test_docs_yaml_populates_documentation(tmp_path):
    _write_agent_bundle(
        tmp_path,
        "gene",
        agent_yaml="""
            agent_id: gene_validation
            name: "Gene Validation Agent"
            model_config:
              model: "gpt-5.5"
        """,
        docs_yaml="""
            summary: "Checks gene names against the Alliance database."
            capabilities:
              - name: "Gene lookup"
                description: "Find a gene by its symbol, name, or ID"
            tips:
              - "Include the species when you can"
        """,
    )
    reset_cache()
    agents = load_agent_definitions(agents_path=tmp_path, force_reload=True)
    reset_cache()

    doc = agents["gene_validation"].documentation
    assert doc is not None
    assert doc["summary"] == "Checks gene names against the Alliance database."
    assert doc["capabilities"][0]["name"] == "Gene lookup"
    assert doc["tips"] == ["Include the species when you can"]


def test_inline_documentation_and_docs_yaml_conflict_raises(tmp_path):
    _write_agent_bundle(
        tmp_path,
        "gene",
        agent_yaml="""
            agent_id: gene_validation
            name: "Gene Validation Agent"
            model_config:
              model: "gpt-5.5"
            documentation:
              summary: "inline summary"
        """,
        docs_yaml="""
            summary: "docs.yaml summary"
        """,
    )
    reset_cache()
    with pytest.raises(ValueError, match="both an inline 'documentation' block and a docs.yaml"):
        load_agent_definitions(agents_path=tmp_path, force_reload=True)
    reset_cache()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec backend pytest tests/unit/config/test_agent_docs_yaml_loader.py -v`
Expected: FAIL — documentation is `None` (docs.yaml not read) / no conflict raised.

- [ ] **Step 3: Read docs.yaml in the loader**

In `backend/src/lib/config/agent_loader.py`, inside `_load_agent_definition_indexes`, replace the existing construction block (currently lines 328-337):

```python
            agent = AgentDefinition.from_yaml(
                source.folder_name,
                data,
                package_id=source.package_id,
                package_path=source.package_path,
            )
            agent_registry[agent.agent_id] = agent
            agents_by_folder[source.folder_name] = agent
            if agent.package_id is not None:
                agents_by_package_and_id[(agent.package_id, agent.agent_id)] = agent
```

with:

```python
            docs_yaml_path = source.agent_dir / "docs.yaml"
            docs_data = None
            if docs_yaml_path.exists():
                if data.get("documentation"):
                    raise ValueError(
                        f"Agent '{source.folder_name}' declares both an inline "
                        f"'documentation' block in agent.yaml and a docs.yaml; "
                        f"keep curator docs only in docs.yaml."
                    )
                with open(docs_yaml_path, "r", encoding="utf-8") as docs_file:
                    docs_data = yaml.safe_load(docs_file)

            agent = AgentDefinition.from_yaml(
                source.folder_name,
                data,
                package_id=source.package_id,
                package_path=source.package_path,
            )
            if docs_data is not None:
                agent.documentation = docs_data
            agent_registry[agent.agent_id] = agent
            agents_by_folder[source.folder_name] = agent
            if agent.package_id is not None:
                agents_by_package_and_id[(agent.package_id, agent.agent_id)] = agent
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec backend pytest tests/unit/config/test_agent_docs_yaml_loader.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/src/lib/config/agent_loader.py backend/tests/unit/config/test_agent_docs_yaml_loader.py
git commit -m "feat(agent-loader): load sibling docs.yaml into AgentDefinition.documentation"
```

---

## Task 3: Capture the documentation parity baseline (before any content moves)

**Files:**
- Create: `backend/tests/unit/api/test_agent_documentation_parity.py`
- Create (generated): `backend/tests/unit/api/fixtures/agent_documentation_baseline.json`

This locks the **current** per-agent documentation so the faithful port (Tasks 5-6) can be proven lossless. Run it once now, while `AGENT_DOCUMENTATION` is still the source, to generate the baseline.

- [ ] **Step 1: Write the parity test (self-baselining)**

```python
# backend/tests/unit/api/test_agent_documentation_parity.py
"""The per-agent documentation served by the catalog must not change during the
faithful port. Regenerate the baseline intentionally (DELETE the json + rerun)
only when authored content legitimately changes (Task 8)."""
import json
from pathlib import Path

from src.lib.agent_studio.registry_builder import build_agent_registry
from src.lib.agent_studio.catalog_service import _convert_documentation

BASELINE = Path(__file__).parent / "fixtures" / "agent_documentation_baseline.json"


def _current_documentation_snapshot() -> dict:
    registry = build_agent_registry()
    snapshot = {}
    for agent_id, entry in registry.items():
        doc = _convert_documentation(entry.get("documentation"))
        snapshot[agent_id] = doc.model_dump() if doc is not None else None
    return snapshot


def test_agent_documentation_matches_baseline():
    snapshot = _current_documentation_snapshot()
    if not BASELINE.exists():
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
        # First run writes the baseline; rerun asserts against it.
        return
    expected = json.loads(BASELINE.read_text())
    assert snapshot == expected, (
        "Per-agent documentation changed. If this is an intentional authored "
        "change (Task 8), delete the baseline json and rerun to regenerate."
    )
```

- [ ] **Step 2: Generate the baseline (first run) against the CURRENT dict-based source**

Run: `docker compose exec backend pytest tests/unit/api/test_agent_documentation_parity.py -v`
Expected: PASS (writes `fixtures/agent_documentation_baseline.json`).

- [ ] **Step 3: Verify the baseline is non-empty and contains known agents**

Run: `docker compose exec backend python -c "import json,glob; d=json.load(open(glob.glob('tests/unit/api/fixtures/agent_documentation_baseline.json')[0])); print(len(d), 'gene_validation' in d, d['gene_validation']['summary'][:30])"`
Expected: prints a count (>= 20) and `True` and the gene summary prefix.

- [ ] **Step 4: Run again to confirm it now asserts (not regenerates)**

Run: `docker compose exec backend pytest tests/unit/api/test_agent_documentation_parity.py -v`
Expected: PASS (asserts equality against the just-written baseline).

- [ ] **Step 5: Commit**

```bash
git add backend/tests/unit/api/test_agent_documentation_parity.py backend/tests/unit/api/fixtures/agent_documentation_baseline.json
git commit -m "test(agent-studio): lock agent documentation parity baseline before migration"
```

---

## Task 4: Add the system-docs YAML loader for synthetic flow nodes

**Files:**
- Create: `backend/src/lib/agent_studio/system_agent_docs.yaml`
- Create: `backend/src/lib/agent_studio/system_agent_docs.py`
- Test: add to `backend/tests/unit/config/test_agent_docs_yaml_loader.py`

`task_input` and `curation_prep` have no agent folder; their docs are synthesized in `registry_builder`. Move their prose to a small data file with a tiny loader (no prose in Python).

- [ ] **Step 1: Write the failing test (append to the loader test file)**

```python
# append to backend/tests/unit/config/test_agent_docs_yaml_loader.py
from src.lib.agent_studio.system_agent_docs import get_system_agent_documentation


def test_system_agent_docs_has_task_input_and_curation_prep():
    assert get_system_agent_documentation("task_input") is not None
    assert get_system_agent_documentation("curation_prep") is not None
    assert get_system_agent_documentation("task_input")["summary"]


def test_system_agent_docs_unknown_returns_none():
    assert get_system_agent_documentation("does_not_exist") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec backend pytest tests/unit/config/test_agent_docs_yaml_loader.py -k system_agent_docs -v`
Expected: FAIL — module `system_agent_docs` does not exist.

- [ ] **Step 3: Create the system-docs YAML (port the two synthetic dict entries)**

Create `backend/src/lib/agent_studio/system_agent_docs.yaml` by copying the current `AGENT_DOCUMENTATION["task_input"]` (registry_builder.py:78-99) and `AGENT_DOCUMENTATION["curation_prep"]` (registry_builder.py:720-754) verbatim under their keys:

```yaml
# Curator-facing docs for synthetic flow nodes that have no agent bundle folder.
# Voice: curator-friendly, no developer jargon (see design doc "Audience and voice").
task_input:
  summary: "<copy from AGENT_DOCUMENTATION['task_input']['summary']>"
  capabilities: []        # copy the real list verbatim
  data_sources: []        # copy verbatim
  limitations: []         # copy verbatim
curation_prep:
  summary: "<copy from AGENT_DOCUMENTATION['curation_prep']['summary']>"
  capabilities: []
  data_sources: []
  limitations: []
```

(Replace placeholders with the exact existing values; the parity test in Task 3 verifies these match.)

- [ ] **Step 4: Create the loader**

Create `backend/src/lib/agent_studio/system_agent_docs.py`:

```python
"""Loader for curator-facing docs of synthetic flow nodes (no agent bundle folder).

Keeps the prose for task_input / curation_prep in YAML, not Python literals.
"""
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

_DOCS_PATH = Path(__file__).with_name("system_agent_docs.yaml")


@lru_cache(maxsize=1)
def _load() -> Dict[str, Dict[str, Any]]:
    with open(_DOCS_PATH, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data


def get_system_agent_documentation(agent_id: str) -> Optional[Dict[str, Any]]:
    """Return the documentation dict for a synthetic flow node, or None."""
    return _load().get(agent_id)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `docker compose exec backend pytest tests/unit/config/test_agent_docs_yaml_loader.py -k system_agent_docs -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/src/lib/agent_studio/system_agent_docs.yaml backend/src/lib/agent_studio/system_agent_docs.py backend/tests/unit/config/test_agent_docs_yaml_loader.py
git commit -m "feat(agent-studio): YAML-backed docs for synthetic flow nodes"
```

---

## Task 5: Faithful port — create one `docs.yaml` (worked example: gene)

**Files:**
- Create: `packages/alliance/agents/gene/docs.yaml`

This establishes the exact transform: an `AGENT_DOCUMENTATION[agent_id]` dict becomes a `docs.yaml` with the same top-level keys (`summary`, `capabilities`, `data_sources`, `limitations`). Byte-equivalent content (faithful port); the parity test guarantees correctness. Do NOT rewrite voice yet — that is Task 8.

- [ ] **Step 1: Create `packages/alliance/agents/gene/docs.yaml`**

Port `AGENT_DOCUMENTATION["gene_validation"]` (registry_builder.py:159-189) verbatim:

```yaml
# Curator-facing documentation for the Gene Validation Agent.
# See docs/design/2026-06-02-...-migration.md "Audience and voice".
summary: "Validates gene identifiers against the Alliance Curation Database."
capabilities:
  - name: "Gene lookup"
    description: "Find genes by symbol, name, ID, or cross-reference"
    example_query: "Look up the gene daf-16"
    example_result: "Returns gene ID, symbol, name, species, and synonyms"
  - name: "Batch validation"
    description: "Validate multiple genes at once"
    example_query: "Look up these genes: daf-16, lin-3, unc-54, act-1"
    example_result: "Returns validation results for each gene"
data_sources:
  - name: "Alliance Curation Database"
    description: "Comprehensive gene data from all MODs"
    species_supported:
      - "C. elegans"
      - "D. melanogaster"
      - "D. rerio"
      - "H. sapiens"
      - "M. musculus"
      - "R. norvegicus"
      - "S. cerevisiae"
    data_types: ["genes", "symbols", "synonyms", "cross-references"]
limitations:
  - "Only validates against Alliance group data"
  - "Some newly published genes may not be in the database yet"
```

- [ ] **Step 2: Verify the loader picks it up and parity still holds**

Because `agent.documentation` now comes from `docs.yaml` for gene (and `AGENT_DOCUMENTATION` still has the identical entry as a no-op fallback), the snapshot is unchanged.

Run: `docker compose exec backend pytest tests/unit/api/test_agent_documentation_parity.py -v`
Expected: PASS (gene documentation identical; baseline unchanged).

- [ ] **Step 3: Commit**

```bash
git add packages/alliance/agents/gene/docs.yaml
git commit -m "refactor(agent-studio): port gene documentation to docs.yaml (faithful)"
```

---

## Task 6: Faithful port — create the remaining 17 `docs.yaml` files

**Files (create — folder, source dict key, source lines):**

| docs.yaml path | dict key (registry_builder.py) | source lines |
|---|---|---|
| `packages/core/agents/supervisor/docs.yaml` | `supervisor` | 100-122 |
| `packages/alliance/agents/pdf/docs.yaml` | `pdf_extraction` | 123-158 |
| `packages/alliance/agents/gene_ontology/docs.yaml` | `gene_ontology_lookup` | 191-240 |
| `packages/alliance/agents/go_annotations/docs.yaml` | `go_annotations_lookup` | 241-284 |
| `packages/alliance/agents/allele/docs.yaml` | `allele_validation` | 285-327 |
| `packages/alliance/agents/orthologs/docs.yaml` | `orthologs_lookup` | 328-370 |
| `packages/alliance/agents/disease/docs.yaml` | `disease_validation` | 371-414 |
| `packages/alliance/agents/chemical/docs.yaml` | `chemical_validation` | 415-458 |
| `packages/alliance/agents/gene_expression/docs.yaml` | `gene_expression_extraction` | 459-508 |
| `packages/alliance/agents/gene_extractor/docs.yaml` | `gene_extractor` | 509-557 |
| `packages/alliance/agents/allele_extractor/docs.yaml` | `allele_extractor` | 558-606 |
| `packages/alliance/agents/disease_extractor/docs.yaml` | `disease_extractor` | 607-655 |
| `packages/alliance/agents/phenotype_extractor/docs.yaml` | `phenotype_extractor` | 656-705 |
| `packages/alliance/agents/chat_output/docs.yaml` | `chat_output_formatter` | 706-719 |
| `packages/alliance/agents/csv_formatter/docs.yaml` | `csv_output_formatter` | 755-768 |
| `packages/alliance/agents/json_formatter/docs.yaml` | `json_output_formatter` | 769-782 |
| `packages/alliance/agents/tsv_formatter/docs.yaml` | `tsv_output_formatter` | 783-810 |

- [ ] **Step 1: Create each file by porting its dict entry verbatim**

For each row: open `registry_builder.py` at the listed lines, transcribe the Python dict literal into YAML using exactly the same keys/values (the same transform shown for gene in Task 5). Keep `example_query`/`example_result` where present; omit keys the entry omits.

- [ ] **Step 2: Run the parity test after each batch**

Run: `docker compose exec backend pytest tests/unit/api/test_agent_documentation_parity.py -v`
Expected: PASS after every file (each `docs.yaml` is identical to its dict entry, so the snapshot is unchanged). If it FAILS, the YAML differs from the dict — fix the YAML, do not edit the baseline.

- [ ] **Step 3: Commit**

```bash
git add packages/core/agents/supervisor/docs.yaml packages/alliance/agents/*/docs.yaml
git commit -m "refactor(agent-studio): port remaining agent documentation to docs.yaml (faithful)"
```

---

## Task 7: Delete `AGENT_DOCUMENTATION` and wire synthetic entries to the YAML loader

**Files:**
- Modify: `backend/src/lib/agent_studio/registry_builder.py:65-810` (delete dict), `:814`, `:897`, `:969`

- [ ] **Step 1: Replace the three `AGENT_DOCUMENTATION` references**

In `registry_builder.py`:

At the top of the file, add the import:

```python
from src.lib.agent_studio.system_agent_docs import get_system_agent_documentation
```

Line 814 — drop the dict fallback (docs.yaml is now the source via `agent_def.documentation`):

```python
    doc = agent_def.documentation
    if not doc and agent_def.description.strip():
        doc = {"summary": agent_def.description.strip()}
```

Line 897 (`build_agent_registry`, task_input entry):

```python
        "documentation": get_system_agent_documentation("task_input"),
```

Line 969 (`get_registry_entry`, task_input entry):

```python
        "documentation": get_system_agent_documentation("task_input"),
```

- [ ] **Step 2: Delete the dict definition**

Delete the entire `AGENT_DOCUMENTATION: Dict[str, Dict[str, Any]] = { ... }` literal and its explanatory comment block (registry_builder.py:65-810).

- [ ] **Step 3: Run the parity test to prove the move was lossless**

Run: `docker compose exec backend pytest tests/unit/api/test_agent_documentation_parity.py -v`
Expected: PASS — every agent's documentation is byte-identical to the pre-migration baseline (now sourced from docs.yaml + system_agent_docs.yaml instead of the deleted dict).

- [ ] **Step 4: Run the broader agent-studio suite for regressions**

Run: `docker compose exec backend pytest tests/unit/api/test_agent_studio_catalog_endpoints.py tests/unit/api/test_agent_studio_metadata.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/lib/agent_studio/registry_builder.py
git commit -m "refactor(agent-studio): delete AGENT_DOCUMENTATION dict; docs come from YAML"
```

---

## Task 8: Improve pass — curator-voice rewrites + net-new docs for uncovered agents

**Files:**
- Modify: any `docs.yaml` whose prose reads developer-first.
- Create: `docs.yaml` for live agents with no dict entry (the newer validators).
- Regenerate: `backend/tests/unit/api/fixtures/agent_documentation_baseline.json`

Agents with **no** prior dict entry (currently fall back to `{"summary": description}`): `agm_validation`, `controlled_vocabulary_validation`, `reference_validation`, `subject_entity_validation`, `data_provider_validation`, `experimental_condition_validation`, `ontology_term_validation` (verify the live set with the command below).

- [ ] **Step 1: List palette agents missing documentation**

The loader sets `AgentDefinition.documentation` from `docs.yaml` (Task 2), so a falsy `documentation` on a palette agent means no `docs.yaml` exists for it:

Run: `docker compose exec backend python -c "
from src.lib.config.agent_loader import load_agent_definitions
for aid, a in sorted(load_agent_definitions().items()):
    if a.frontend.show_in_palette and not a.documentation:
        print('MISSING', aid, '(folder=%s)' % a.folder_name)
"`
Expected: prints each palette agent that still needs a `docs.yaml` (the newer validators).

- [ ] **Step 2: Author a `docs.yaml` for each missing agent (curator voice)**

Use this template; write plain-language, non-developer prose (see design doc "Audience and voice"). Example for `controlled_vocabulary_validation` (folder `controlled_vocabulary`):

```yaml
# packages/alliance/agents/controlled_vocabulary/docs.yaml
summary: "Checks that a value you entered is one of the approved choices for that field."
capabilities:
  - name: "Approved-term check"
    description: "Confirms a value matches the official list of allowed terms for a field, so submissions stay consistent."
    example_query: "Is 'is_model_of' an allowed disease relation?"
    example_result: "Confirms the term is approved, or flags it so you can pick a valid one."
limitations:
  - "Only checks fields that have an official approved-term list."
```

Repeat for each missing agent with content true to what that validator does.

- [ ] **Step 3: Rewrite any ported `docs.yaml` that reads developer-first**

Review each Task 5/6 file against the bad/good examples in the design doc. Rewrite jargon-heavy entries in plain language. (This intentionally changes the snapshot.)

- [ ] **Step 4: Regenerate the parity baseline (intentional content change)**

```bash
rm backend/tests/unit/api/fixtures/agent_documentation_baseline.json
docker compose exec backend pytest tests/unit/api/test_agent_documentation_parity.py -v
```
Expected: PASS (writes a new baseline). Review the git diff of the baseline json with Chris before committing — it is the record of what curators will now read.

- [ ] **Step 5: Commit**

```bash
git add packages/*/agents/*/docs.yaml backend/tests/unit/api/fixtures/agent_documentation_baseline.json
git commit -m "docs(agent-studio): curator-voice rewrites + net-new agent docs"
```

---

## Task 9: Documentation-completeness guard test

**Files:**
- Create: `backend/tests/unit/api/test_agent_documentation_completeness.py`

- [ ] **Step 1: Write the guard test**

```python
# backend/tests/unit/api/test_agent_documentation_completeness.py
"""Every curator-facing agent must have non-trivial documentation. This fails CI
when a new agent is added without a docs.yaml (or with an empty one)."""
import pytest

from src.lib.config.agent_loader import load_agent_definitions

# Synthetic flow nodes are documented via system_agent_docs.yaml, not a bundle.
_SYNTHETIC = {"task_input", "curation_prep"}


def _palette_agents():
    return {
        aid: a
        for aid, a in load_agent_definitions().items()
        if a.frontend.show_in_palette and aid not in _SYNTHETIC
    }


@pytest.mark.parametrize("agent_id", sorted(_palette_agents().keys()))
def test_palette_agent_has_nonempty_documentation(agent_id):
    agent = _palette_agents()[agent_id]
    doc = agent.documentation
    assert doc, f"{agent_id}: missing docs.yaml (no documentation loaded)"
    summary = (doc.get("summary") or "").strip()
    assert len(summary.split()) >= 3, f"{agent_id}: summary too short / empty"
    caps = doc.get("capabilities") or []
    assert len(caps) >= 1, f"{agent_id}: needs at least one capability"
    for cap in caps:
        assert (cap.get("name") or "").strip(), f"{agent_id}: capability missing name"
        assert (cap.get("description") or "").strip(), f"{agent_id}: capability missing description"


def test_synthetic_nodes_documented():
    from src.lib.agent_studio.system_agent_docs import get_system_agent_documentation
    for node in _SYNTHETIC:
        doc = get_system_agent_documentation(node)
        assert doc and (doc.get("summary") or "").strip(), f"{node}: missing system doc"
```

- [ ] **Step 2: Run the guard test**

Run: `docker compose exec backend pytest tests/unit/api/test_agent_documentation_completeness.py -v`
Expected: PASS (Task 8 authored docs for every palette agent). If any agent FAILS, author its `docs.yaml` — do not weaken the test.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/unit/api/test_agent_documentation_completeness.py
git commit -m "test(agent-studio): guard that every palette agent has documentation"
```

---

## Task 10: Render tips from the API (frontend)

**Files:**
- Modify: `frontend/src/types/promptExplorer.ts:27-32`
- Modify: `frontend/src/components/AgentStudio/AgentDetailsPanel.tsx:536-573`

- [ ] **Step 1: Add `tips` to the TS type**

In `frontend/src/types/promptExplorer.ts`, in `interface AgentDocumentation`, after `limitations`:

```typescript
export interface AgentDocumentation {
  summary: string
  capabilities: AgentCapability[]
  data_sources: DataSourceInfo[]
  limitations: string[]
  tips?: string[]
}
```

- [ ] **Step 2: Replace the hardcoded tips block with data-driven rendering**

In `AgentDetailsPanel.tsx`, replace the entire `{/* Tips section - static for now */}` block (lines 536-573) with one that renders `documentation?.tips`:

```tsx
            {/* Tips section - sourced from docs.yaml */}
            {documentation?.tips && documentation.tips.length > 0 && (
              <Box sx={{ mb: 3 }}>
                <SectionTitle>
                  <LightbulbOutlinedIcon fontSize="small" color="info" />
                  Tips for Best Results
                </SectionTitle>
                <List dense disablePadding>
                  {documentation.tips.map((tip, idx) => (
                    <ListItem key={idx} sx={{ pl: 0 }}>
                      <ListItemIcon sx={{ minWidth: 28 }}>
                        <LightbulbOutlinedIcon fontSize="small" sx={{ color: 'info.main', fontSize: '1rem' }} />
                      </ListItemIcon>
                      <ListItemText
                        primary={tip}
                        primaryTypographyProps={{ variant: 'body2' }}
                      />
                    </ListItem>
                  ))}
                </List>
              </Box>
            )}
```

- [ ] **Step 3: Update the Guidance empty-state condition**

The empty state (lines 576-582) currently shows only when there are no limitations. Update it to also account for tips so the tab is never visually empty-but-claims-content:

```tsx
            {(!documentation?.limitations || documentation.limitations.length === 0) &&
             (!documentation?.tips || documentation.tips.length === 0) && (
              <Box sx={{ textAlign: 'center', py: 4, color: 'text.secondary' }}>
                <Typography variant="body2">
                  No specific guidance documented for this agent.
                </Typography>
              </Box>
            )}
```

- [ ] **Step 4: Author the migrated tips into the relevant `docs.yaml`**

The three former hardcoded tips were generic. Add the two universal ones to agents where they make sense (or to a broadly-applicable set), in curator voice, e.g. in `packages/alliance/agents/gene/docs.yaml`:

```yaml
tips:
  - "Be specific - include gene symbols, IDs, or species when you can."
  - "Not sure how this agent can help? Use the 'Discuss with Claude' button."
```

(The group-rules tip was conditional on `agent.has_group_rules`; that behavior is dropped in favor of explicit per-agent tips. Add a species/group tip to group-rule agents' docs.yaml where relevant.)

- [ ] **Step 5: Build the frontend to verify it compiles**

Run: `docker compose exec frontend npm run build` (or the project's typecheck: `docker compose exec frontend npm run typecheck` if defined)
Expected: build/typecheck succeeds, no TS errors about `tips`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types/promptExplorer.ts frontend/src/components/AgentStudio/AgentDetailsPanel.tsx packages/alliance/agents/gene/docs.yaml
git commit -m "feat(agent-studio): render Tips from docs.yaml instead of hardcoded literals"
```

---

## Task 11: Full Phase 1 verification

- [ ] **Step 1: Run the full agent-studio + config unit suites**

Run:
```bash
docker compose exec backend pytest tests/unit/api/test_agent_documentation_tips.py tests/unit/config/test_agent_docs_yaml_loader.py tests/unit/api/test_agent_documentation_parity.py tests/unit/api/test_agent_documentation_completeness.py tests/unit/api/test_agent_studio_catalog_endpoints.py -v
```
Expected: all PASS.

- [ ] **Step 2: Confirm `AGENT_DOCUMENTATION` is gone**

Run: `cd /home/ctabone/agr_ai_curation && grep -rn "AGENT_DOCUMENTATION" backend/src || echo "removed"`
Expected: prints `removed` (no references remain in source).

- [ ] **Step 3: Manual spot-check in the running app**

Open Agent Studio → Agents → a domain agent → Overview and Guidance tabs. Confirm capabilities and Tips render and read in plain, curator-friendly language. Confirm an agent that previously had no docs (e.g. a validator) now shows authored content.

- [ ] **Step 4: Final commit (if any spot-check fixes)**

```bash
git add -p
git commit -m "fix(agent-studio): Phase 1 doc-migration spot-check adjustments"
```

---

## Notes for the implementer

- **Do not use `--no-verify`** on commits — this repo has mandatory git-safety hooks.
- **`docs.yaml` is read by convention** from the agent bundle dir (`AgentConfigSource.agent_dir`), not as a manifest export. If a later phase wants it as a formal package export (like `prompt.yaml`), that is a separate change.
- **Parity vs. voice:** Tasks 5-7 are a *faithful* port (parity-locked, proves the mechanism is lossless). Task 8 is the *reviewed* content change (voice + coverage); only there does the baseline json change, and Chris reviews that diff.
- **Curator voice is the top requirement** (design doc "Audience and voice"): no jargon, no field paths/class names as the explanation, spell out acronyms. The guard test enforces presence, not tone — tone is human-reviewed.
