# Agent Prompt-Stack Optimization — Phase A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Slim the auto-generated runtime-contract portion of the `core_generated` prompt layer (the "Generated Contract" / "Output structure" panel) for all agents, removing audit-only enumeration the model does not act on while keeping the few action-relevant lines — with no loss of information (everything removed stays retrievable via `get_agent_contract`).

**Architecture:** All work is in the backend prompt assembler `backend/src/lib/prompts/assembly.py`. `_build_compact_runtime_contract()` and `_build_domain_pack_contract_lines()` currently emit a full tool inventory, envelope-object dump, per-field validator-binding map, and per-binding selector lines with literal CURIE allow-lists. We replace the verbose domain-pack enumeration with a compact summary (pack id + a single capped "validators own these fields; do not invent" line + the existing one-line binding-ownership summary) and drop the redundant tool-inventory line, keeping the required-tool-call policy, evidence policy, `get_agent_contract` pointer, output-contract line, and runtime safety rule. The removed detail is already served by `get_agent_contract` (topics `tools`/`domain_envelope`/`validator_bindings`/`ontology_constraints`/`field`), which we lock in with a retrievability guard test.

**Tech Stack:** Python 3.11, pytest. Tests run in the `ai-curation-unit-tests:latest` image: `docker compose -f docker-compose.test.yml run --rm backend-unit-tests` (or a one-off `docker run` against that image with the repo mounted). No DB is required for `build_agent_core_prompt`.

**Deferred out of Phase A (flagged):** "A2" — slimming the structured-output instruction prose (`STRUCTURED_OUTPUT_INSTRUCTION_TEMPLATE` in `prompt_utils.py`) for `output_schema` agents. A code comment (`prompt_utils.py:20-22`) states that boilerplate is a deliberate safety net for reasoning models that emit JSON as text. Removing it safely needs a live check, which conflicts with the "no live A/B" constraint, so A2 is handled separately (see "Follow-ups").

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `backend/src/lib/prompts/assembly.py` | Prompt-layer assembly | Modify `_build_compact_runtime_contract`, `_build_domain_pack_contract_lines`; reuse `_format_validator_bound_fields`/`_join_limited` for the compact line |
| `backend/src/lib/prompts/size_report.py` | New: per-agent per-layer size report | Create |
| `backend/tests/unit/lib/prompts/test_prompt_size_budget.py` | New: soft per-agent `core_generated` budget guard | Create |
| `backend/tests/unit/lib/prompts/test_assembly.py` | Assembly unit tests | Re-baseline `test_core_generated_contract_summarizes_tool_and_domain_metadata` |
| `backend/tests/unit/lib/prompts/test_core_generated_retrievability.py` | New: prove removed data is still served by `get_agent_contract` | Create |
| `docs/design/2026-06-03-prompt-size-report-phaseA.md` | Committed before/after size artifact for the reviews | Create (Task 6) |

The per-extractor contract/policy tests (`backend/tests/unit/test_gene_extractor_domain_envelope_contract.py`, `test_disease_extractor_domain_envelope_contract.py`, `test_phenotype_extractor_domain_envelope_contract.py`, `test_chemical_extractor_domain_envelope_contract.py`, `test_allele_extractor_mgi_prompt_policy.py`, `test_gene_expression_prompt_policy.py`) are checked and re-baselined in Task 4 only where they assert removed `core_generated` fragments.

---

## Task 1: Prompt-size report utility + baseline

**Files:**
- Create: `backend/src/lib/prompts/size_report.py`
- Create: `backend/tests/unit/lib/prompts/test_prompt_size_budget.py`

- [ ] **Step 1: Write the report utility**

```python
# backend/src/lib/prompts/size_report.py
"""Per-agent prompt-layer size report (no DB required: core layers only)."""
from __future__ import annotations

from src.lib.config.agent_loader import (
    canonical_system_agent_key,
    load_agent_definitions,
)
from src.lib.prompts.assembly import build_agent_core_prompt


def core_layer_sizes() -> dict[str, dict[str, int]]:
    """Return {agent_id: {layer_kind: char_count, "total": int}} for core layers."""
    report: dict[str, dict[str, int]] = {}
    for agent in load_agent_definitions().values():
        agent_id = canonical_system_agent_key(agent)
        try:
            bundle = build_agent_core_prompt(agent_id)
        except Exception:  # agents without resolvable core layers are skipped
            continue
        sizes = {layer.kind: len(layer.content) for layer in bundle.layers}
        sizes["total"] = sum(len(layer.content) for layer in bundle.layers)
        report[agent_id] = sizes
    return report


def format_report(report: dict[str, dict[str, int]]) -> str:
    """Render the report as a stable, sorted text table."""
    lines = [f"{'agent_id':40s} {'core_static':>11} {'core_generated':>14} {'total':>7}"]
    for agent_id in sorted(report):
        s = report[agent_id]
        lines.append(
            f"{agent_id:40s} {s.get('core_static', 0):11d} "
            f"{s.get('core_generated', 0):14d} {s.get('total', 0):7d}"
        )
    return "\n".join(lines)
```

- [ ] **Step 2: Write the soft-budget test (records current sizes; fails if `core_generated` grows past budget)**

```python
# backend/tests/unit/lib/prompts/test_prompt_size_budget.py
"""Soft budget so the generated contract can't silently re-bloat."""
from src.lib.prompts.size_report import core_layer_sizes

# Post-slim ceilings (chars) for core_generated. Generous headroom over the
# expected ~1-1.5K compact contract; tighten in a later pass if desired.
CORE_GENERATED_BUDGET = 2500


def test_core_generated_within_budget_for_all_agents():
    report = core_layer_sizes()
    assert report, "expected at least one agent with core layers"
    over = {
        agent_id: sizes["core_generated"]
        for agent_id, sizes in report.items()
        if sizes.get("core_generated", 0) > CORE_GENERATED_BUDGET
    }
    assert not over, f"core_generated over budget ({CORE_GENERATED_BUDGET} chars): {over}"
```

- [ ] **Step 3: Run the test — expect FAIL now (pre-slim contracts exceed budget)**

Run: `docker compose -f docker-compose.test.yml run --rm backend-unit-tests python -m pytest tests/unit/lib/prompts/test_prompt_size_budget.py -v`
Expected: FAIL — gene_expression/disease_extractor/phenotype_extractor `core_generated` exceed 2500 (this confirms the budget bites pre-slim). Record the failing agents/sizes in the commit message.

- [ ] **Step 4: Commit (test is intentionally red until Task 2 lands)**

Note: this is the one place we commit a temporarily-failing test, because it is the regression guard the slim must satisfy. Mark it `@pytest.mark.xfail(reason="passes after Task 2 slim", strict=True)` so CI is green and it flips to pass after Task 2.

Add the xfail marker:
```python
import pytest

@pytest.mark.xfail(reason="core_generated slim lands in Task 2", strict=True)
def test_core_generated_within_budget_for_all_agents():
    ...
```

Run: `docker compose -f docker-compose.test.yml run --rm backend-unit-tests python -m pytest tests/unit/lib/prompts/test_prompt_size_budget.py -v`
Expected: XFAIL (green).

```bash
git add backend/src/lib/prompts/size_report.py backend/tests/unit/lib/prompts/test_prompt_size_budget.py
git commit -m "test(prompts): per-agent core_generated size report + soft budget (xfail until slim)"
```

---

## Task 2: Slim the runtime-contract enumeration

**Files:**
- Modify: `backend/src/lib/prompts/assembly.py` (`_build_compact_runtime_contract:323-367`, `_build_domain_pack_contract_lines:382-432`)
- Modify: `backend/tests/unit/lib/prompts/test_assembly.py` (`test_core_generated_contract_summarizes_tool_and_domain_metadata:115-158`)

- [ ] **Step 1: Re-baseline the assembly test to the new compact contract (write the failing spec first)**

Replace the body assertions of `test_core_generated_contract_summarizes_tool_and_domain_metadata` (currently lines 145-156) with:

```python
    # KEEP — action-relevant lines
    assert "call at least one document retrieval tool" in generated
    assert "get_agent_contract" in generated
    assert "Domain envelope pack: agr.alliance.phenotype v0.1.0" in generated
    assert "No extractor should invent exact ontology CURIEs" in generated
    # NEW — single compact validator-owned-fields line replaces the per-field map
    assert "Validators own these fields" in generated
    assert "do not invent" in generated
    assert "PhenotypeTerm.curie" in generated  # at least one field named in the capped list

    # REMOVED — audit enumeration must no longer be inlined
    assert "Tool inventory from agent.yaml" not in generated
    assert "PhenotypeAnnotation(PhenotypeAnnotationPayload role=" not in generated
    assert "Pending unresolved shapes:" not in generated
    assert "->phenotype_term_ontology_validator" not in generated
    assert "accepted_prefixes<-literal:" not in generated

    # tighter size bounds for the compact contract
    assert len(generated.splitlines()) <= 15
    assert len(generated.split()) <= 300
    assert "prompt_templates:" not in bundle.layers[1].source_ref
    assert "domain_pack:agr.alliance.phenotype" in bundle.layers[1].source_ref
```

- [ ] **Step 2: Run the test — expect FAIL (assembly still emits the verbose contract)**

Run: `docker compose -f docker-compose.test.yml run --rm backend-unit-tests python -m pytest tests/unit/lib/prompts/test_assembly.py::test_core_generated_contract_summarizes_tool_and_domain_metadata -v`
Expected: FAIL on the `not in` assertions and the size bounds.

- [ ] **Step 3: Slim `_build_compact_runtime_contract` — drop the tool-inventory enumeration**

In `_build_compact_runtime_contract` (`assembly.py:323-367`), delete the tool-inventory line while keeping the required-tool policy, tool-policy summaries, output-contract line, domain lines, and safety rule. Remove:

```python
    if agent.tools:
        lines.append(f"- Tool inventory from agent.yaml: {', '.join(agent.tools)}.")
        required_tools = required_tool_names_for_available_tools(
```

so the block begins:

```python
    if agent.tools:
        required_tools = required_tool_names_for_available_tools(
```

(Keep everything else in that block unchanged — the required-tool policy lines and the `TOOL_POLICY_SUMMARIES` loop stay.)

- [ ] **Step 4: Replace the verbose domain-pack enumeration with a compact summary**

Rewrite `_build_domain_pack_contract_lines` (`assembly.py:382-432`) so that after the pack line it emits ONE capped validator-owned-fields line and the existing one-line ownership summary, and drops the schema-refs, object-summary, pending-summary, and per-binding enumeration. Replace the body after `lines = [...pack line...]` with:

```python
    lines = [
        f"- Domain envelope pack: {metadata.pack_id} v{metadata.version} "
        f"({metadata.status.value}{semantic_suffix})."
    ]

    validator_fields = _format_validator_bound_fields(metadata.object_definitions)
    if validator_fields:
        lines.append(
            "- Validators own these fields; do not invent their identifiers: "
            f"{validator_fields}. "
            "Use get_agent_contract (topic=validator_bindings, detail_level=detail) "
            "for the full bindings, selectors, and accepted ontology terms."
        )

    active_bindings = [
        binding
        for binding in registry.bindings
        if binding.state is ValidationBindingState.ACTIVE
    ]
    if active_bindings:
        lines.append(
            "- Active validator bindings own validator result fields and envelope "
            "validation findings; do not author validator outputs yourself."
        )

    return lines
```

Delete the now-unused per-binding enumeration loop (`for binding in active_bindings: lines.append(f"- Active validator binding: ...")`) and the `provider_refs`/`object_summary`/`pending_summary` blocks (lines ~401-416). Leave `_format_schema_refs`, `_format_object_summary`, `_format_pending_object_summary`, `_format_active_validator_binding`, `_format_input_selectors` in place for now (still used by `get_agent_contract`/other callers — Task 3 verifies; remove only if Task 3 shows zero references).

- [ ] **Step 5: Run the assembly test — expect PASS**

Run: `docker compose -f docker-compose.test.yml run --rm backend-unit-tests python -m pytest tests/unit/lib/prompts/test_assembly.py -v`
Expected: PASS (all `test_assembly.py` tests, including the re-baselined one and the order/lock/hash tests).

- [ ] **Step 6: Flip the budget test from xfail to pass**

Remove the `@pytest.mark.xfail(...)` decorator added in Task 1 Step 4.
Run: `docker compose -f docker-compose.test.yml run --rm backend-unit-tests python -m pytest tests/unit/lib/prompts/test_prompt_size_budget.py -v`
Expected: PASS (all agents now under 2500 chars `core_generated`).

- [ ] **Step 7: Commit**

```bash
git add backend/src/lib/prompts/assembly.py backend/tests/unit/lib/prompts/test_assembly.py backend/tests/unit/lib/prompts/test_prompt_size_budget.py
git commit -m "refactor(prompts): slim core_generated runtime contract to action-relevant lines

Drop the inlined tool inventory, envelope-object dump, per-field validator-binding
map, and per-binding selector/CURIE-allow-list lines; keep the required-tool policy,
evidence policy, get_agent_contract pointer, output contract, a single capped
validators-own-these-fields line, and the runtime safety rule. Removed detail stays
retrievable via get_agent_contract (guarded in the next task)."
```

---

## Task 3: Retrievability guard — removed data still served by `get_agent_contract`

**Files:**
- Create: `backend/tests/unit/lib/prompts/test_core_generated_retrievability.py`

- [ ] **Step 1: Write the guard test (characterization — proves nothing was lost)**

```python
# backend/tests/unit/lib/prompts/test_core_generated_retrievability.py
"""Everything Task 2 removed from the prompt must still be fetchable on demand."""
from src.lib.agent_contracts import get_agent_contract

AGENT = "phenotype_extractor"  # canonical system agent id used in test_assembly fixtures


def test_tool_inventory_still_available():
    result = get_agent_contract(agent_id=AGENT, topic="tools")
    assert result.get("tools"), "tool inventory must remain retrievable"


def test_validator_bindings_with_selectors_available_at_detail():
    result = get_agent_contract(
        agent_id=AGENT, topic="validator_bindings", detail_level="detail"
    )
    text = repr(result)
    # selectors / accepted-term lists that were stripped from the prompt
    assert "validator" in text.lower()
    assert result.get("validators") or result.get("validator_bindings")


def test_envelope_required_flags_available_at_detail():
    result = get_agent_contract(
        agent_id=AGENT, topic="domain_envelope", detail_level="detail"
    )
    assert result.get("domain_pack_id")
    assert result.get("objects") or result.get("object_definitions") or result.get("fields")
```

- [ ] **Step 2: Run — expect PASS (no production change; `get_agent_contract` already serves these)**

Run: `docker compose -f docker-compose.test.yml run --rm backend-unit-tests python -m pytest tests/unit/lib/prompts/test_core_generated_retrievability.py -v`
Expected: PASS. If `domain_envelope`/`validator_bindings` return shapes differ from the keys asserted, adjust the assertions to the ACTUAL returned keys (read `backend/src/lib/agent_contracts.py` `_object_summary`/`_object_detail`/`validator_bindings` builders) — the test must assert the real shape, not invent one.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/unit/lib/prompts/test_core_generated_retrievability.py
git commit -m "test(prompts): guard that core_generated-removed detail stays fetchable via get_agent_contract"
```

---

## Task 4: Re-baseline per-extractor contract/policy tests

**Files:**
- Modify (only if red): the per-extractor tests listed in "File Structure".

- [ ] **Step 1: Run the full prompt/contract test surface**

Run: `docker compose -f docker-compose.test.yml run --rm backend-unit-tests python -m pytest tests/unit/lib/prompts/ tests/unit/test_gene_extractor_domain_envelope_contract.py tests/unit/test_disease_extractor_domain_envelope_contract.py tests/unit/test_phenotype_extractor_domain_envelope_contract.py tests/unit/test_chemical_extractor_domain_envelope_contract.py tests/unit/test_allele_extractor_mgi_prompt_policy.py tests/unit/test_gene_expression_prompt_policy.py tests/unit/api/test_agent_studio_domain_envelope_prompt_policy.py -v`
Expected: most PASS; any failures will be assertions on the removed `core_generated` fragments (tool inventory, per-binding selector lines, object dump).

- [ ] **Step 2: For each failing assertion, decide keep-or-rebaseline**

For every failure: if it asserts a fragment Task 2 intentionally removed from `core_generated`, update the assertion to check the new compact form (e.g. replace a per-binding-selector assertion with `assert "Validators own these fields" in <bundle text>`), OR move the assertion to fetch via `get_agent_contract` if it was really checking metadata. If it asserts base-prompt content (NOT `core_generated`), it must still pass unchanged — a failure there means Task 2 changed something it shouldn't have; investigate rather than edit the test.

- [ ] **Step 3: Re-run until green**

Run the same command as Step 1.
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/unit
git commit -m "test(prompts): re-baseline per-extractor contract assertions to the compact core_generated"
```

---

## Task 5: Custom-agent inheritance + Agent Studio render smoke

**Files:**
- Create: `backend/tests/unit/lib/prompts/test_core_generated_inheritance_smoke.py`

- [ ] **Step 1: Write the smoke test**

```python
# backend/tests/unit/lib/prompts/test_core_generated_inheritance_smoke.py
"""The slim contract must reach assembled bundles (incl. the rendered string)."""
from src.lib.prompts.assembly import build_agent_core_prompt


def test_core_generated_is_compact_and_present_in_render():
    bundle = build_agent_core_prompt("phenotype_extractor")
    rendered = bundle.render()
    assert "## Generated Runtime Contract" in rendered
    assert "Validators own these fields" in rendered
    # compact: the generated layer is a small fraction of its old ~9K size
    core_generated = next(l for l in bundle.layers if l.kind == "core_generated")
    assert len(core_generated.content) <= 2500
```

- [ ] **Step 2: Run — expect PASS**

Run: `docker compose -f docker-compose.test.yml run --rm backend-unit-tests python -m pytest tests/unit/lib/prompts/test_core_generated_inheritance_smoke.py -v`
Expected: PASS.

Note: the Agent Studio UI renders `core_generated` generically (`PromptWorkshop.tsx`, `AgentDetailsPanel.tsx` just map `layer.content`), so no frontend change is needed; this backend smoke covers the render path. A manual catalog/combined-prompt check is part of the phase review, not a unit test.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/unit/lib/prompts/test_core_generated_inheritance_smoke.py
git commit -m "test(prompts): smoke that the compact contract renders into assembled bundles"
```

---

## Task 6: Before/after size artifact for the phase review

**Files:**
- Create: `docs/design/2026-06-03-prompt-size-report-phaseA.md`

- [ ] **Step 1: Generate the after-report**

Run (captures the post-slim sizes):
```bash
docker compose -f docker-compose.test.yml run --rm backend-unit-tests \
  python -c "from src.lib.prompts.size_report import core_layer_sizes, format_report; print(format_report(core_layer_sizes()))"
```

- [ ] **Step 2: Write the artifact** with two tables: the pre-slim sizes (from the spec's measured table + Task 1 Step 3 recorded failures) and the post-slim sizes (Step 1 output), plus a one-line delta per agent. This is the deletion-only evidence the reviews consume.

- [ ] **Step 3: Commit**

```bash
git add docs/design/2026-06-03-prompt-size-report-phaseA.md
git commit -m "docs(design): Phase A before/after core_generated size report"
```

---

## Phase A gate (after all tasks)

Per the spec, before Phase B:
1. Dispatch the final **Opus 4.8** review of the Phase A diff (vs `origin/main`), tasked to confirm no action-relevant line was lost and the compact contract reads cleanly.
2. Run **`/external-llm-code-review`** (Codex, gpt-5.5/high) on the Phase A diff with the same task; show Chris the output verbatim.
3. Address findings, then proceed to writing the Phase B plan.

---

## Self-Review

**Spec coverage (Phase A portion):** A1 slim — Tasks 2; size report + soft budget — Task 1/6; re-baseline named contract-encoding tests — Tasks 2,4; `get_agent_contract` retrievability incl. `detail_level="detail"` — Task 3; deletion-only evidence — Task 6; custom-agent + UI render — Task 5; phase-gate reviews — Phase A gate. A2 (structured-output prose) explicitly deferred with reason. Covered.

**Placeholder scan:** No TBDs; every code/test step shows the actual code or the exact command + expected result. Task 4's "edit only if red" is conditional by design (re-baselining), with explicit keep-vs-rebaseline criteria.

**Type/name consistency:** `core_layer_sizes()`/`format_report()` defined in Task 1 and reused in Task 6; `CORE_GENERATED_BUDGET = 2500` used consistently in Tasks 1 and 5; `build_agent_core_prompt` and the `core_generated` layer kind match `assembly.py`. The compact line text "Validators own these fields" is asserted identically in Tasks 2, 5.
