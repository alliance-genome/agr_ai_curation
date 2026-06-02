# Agent Studio: a "Validation" section, plus moving all curator-facing content out of Python

Date: 2026-06-02. Status: **DESIGN — approved by Chris 2026-06-02; revised after the package-tree finding;
not yet implemented.**
Origin: after the large auto-validation build-out (disease / gene-expression / phenotype validators,
multivalued-field validation, experimental conditions, submission gating), curators have no way to *see*
what validation an agent performs. Chris: add a **Validation** section to the Agent Studio agent detail
view, and while we are at it, "make sure that none of this information is in Python code for the agent
browser, and all of it is in the domain package or in the tools' Python code, but like a docstring," so
curators can browse and get up to speed faster.

## Problem

Two problems, one root cause.

1. **No validation is surfaced.** The agent detail view (`AgentDetailsPanel.tsx`) has Overview, Guidance,
   Prompts (and a conditional Envelope) tabs. There is nothing telling a curator what the platform
   validates, what blocks submission, or what is still in development — even though that machinery is now
   substantial and entirely real.
2. **Curator-facing content is hardcoded in Python.** The descriptive content the browser shows today does
   not live with the thing it describes; it lives in two large Python dictionaries in the agent-studio
   layer. Adding or changing an agent/tool means editing Python, and coverage is uneven: the
   `AGENT_DOCUMENTATION` dict has exactly 20 entries, several live agents (the newer validators —
   `agm_validation`, `controlled_vocabulary_validation`, `reference_validation`, `subject_entity_validation`,
   `data_provider_validation`, `experimental_condition_validation`, `ontology_term_validation`) have **no
   entry at all** and fall back to a one-line `{"summary": description}`, and the dict mixes real package
   agents with synthetic flow nodes (`task_input`, `curation_prep`) that have no agent folder.

Root cause: the agent-studio layer is the *author* of content rather than an *assembler* of content that
lives with each agent, tool, and domain pack.

## Two agent trees (read this first)

There are two agent directories. Only one is live.

- `packages/alliance/agents/` — **LIVE.** Has the package manifest `packages/alliance/package.yaml`; loaded
  by `load_agent_definitions()` (`agent_loader.py:382-428`, via `resolve_agent_config_sources`), which scans
  runtime packages plus `config/agents/` overrides (`agent_loader.py:5-7`; `registry_builder.py:7`:
  "Runtime packages are the primary source of truth"). 24 agents; all recent work lands here. It is not the
  only live package — `supervisor` ships from `packages/core/agents/supervisor/`, so the migration is
  per-package-bundle, not "alliance only." The loader is package-agnostic (a sibling `docs.yaml` works in
  any package).
- `alliance_agents/` — **LEGACY / DEAD.** No manifest, referenced nowhere in backend/package Python. Not
  loaded. Out of scope here (a separate cleanup could delete it later).

Two `AGENT_DOCUMENTATION` keys are **synthetic flow nodes** with no agent folder — `task_input` and
`curation_prep` (`task_input` is synthesized inline in `registry_builder.build_agent_registry`). Their prose
needs a non-folder YAML home (see Phase 1).

**Everything in this doc targets `packages/alliance/agents/`.** (An earlier draft pointed at the legacy
tree; corrected here.)

### Agent roles and their relationship to validation

Validation relates to two kinds of agent in opposite directions:

| Role | Agents | Pack link | Validation tab shows |
|---|---|---|---|
| **Domain extractor** | `disease_extractor`, `gene_extractor`, `allele_extractor`, `gene_expression` (`gene_expression_extraction`), `phenotype_extractor` | yes — `curation.domain_pack_id` | the linked pack's validators + required fields that run on **what this agent produces** |
| **Validator / specialist** | the `*_validation` agents: `ontology_term_validation`, `controlled_vocabulary_validation`, `reference_validation`, `gene_validation`, `experimental_condition_validation`, `subject_entity_validation`, `data_provider_validation`, `allele_validation`, `disease_validation`, `agm_validation`, `chemical_validation` | no | **reverse view**: "this agent *is* the validator dispatched by these bindings, across these packs/fields" |
| Generic extractor | `pdf_extraction` | no | empty state |
| Lookups / formatters / system | `*_lookup`, `*_formatter`, `supervisor`, `task_input` | no | empty state |

The agent→pack link **already exists** in the live tree. `phenotype_extractor/agent.yaml`:

```yaml
curation:
  adapter_key: "phenotype"
  domain_pack_id: "agr.alliance.phenotype"
  launchable: true
```

Pack-linked extractors today: `allele_extractor`→`agr.alliance.allele`, `disease_extractor`→
`agr.alliance.disease`, `gene_expression`→`agr.alliance.gene_expression`, `gene_extractor`→`gene`,
`phenotype_extractor`→`agr.alliance.phenotype`.

**Pre-existing data note (out of scope):** the `gene` pack's `pack_id` is the bare `gene` while the others
are `agr.alliance.<domain>`. `gene_extractor` correctly references `gene`, so the link works; the pack
naming is just inconsistent. Leave as-is unless we decide to normalize separately.

### Current state (grounded)

**Hardcoded in Python — to be removed:**

- `backend/src/lib/agent_studio/registry_builder.py` — `AGENT_DOCUMENTATION` dict (~lines 77–814):
  `summary`, `capabilities[]`, `data_sources[]`, `limitations[]` per agent. Attached at ~line 814:
  `doc = agent_def.documentation or AGENT_DOCUMENTATION.get(agent_def.agent_id)` — a fallback chain.
- `backend/src/lib/agent_studio/catalog_service.py` — `CURATED_TOOL_REGISTRY` dict (~lines 187–791): every
  tool's `name`, `description`, `category`, `source_file`, parameter docs. Assembled into `TOOL_REGISTRY`
  at ~lines 1152–1206 alongside introspection and `TOOL_OVERRIDES`.
- `frontend/src/components/AgentStudio/AgentDetailsPanel.tsx` — a hardcoded "Tips for Best Results" block
  (~lines 537–573).

**Rails that already exist (we extend, not invent):**

- `agent_loader.py` `AgentDefinition.documentation` is read from `agent.yaml` and already takes precedence
  over the Python dict. `CurationConfig` (~lines 111–116: `adapter_key`, `domain_pack_id`, `launchable`) is
  already populated on the extractor agents.
- `tool_introspection.py` `introspect_tool()` (line 30) turns a `@function_tool` into name + description +
  parameter docs. The OpenAI Agents SDK derives `description` from the **docstring** and the parameter
  schema from the **signature**. `CURATED_TOOL_REGISTRY` is a hardcoded layer over capability that already
  works.
- Validator metadata lives in domain-pack YAML (`packages/alliance/domain_packs/<pack>/domain_pack.yaml`,
  `metadata.validator_bindings.active[]` / `under_development[]`). The schema
  (`backend/src/schemas/domain_pack_metadata.py`: `DomainPackActiveValidatorBinding` ~326–362,
  `DomainPackUnderDevelopmentValidatorBinding` ~365–398) already has `description:` — it is just **empty**;
  the only prose is developer code-comments. `under_development` already requires `state_explanation`.

## Goal and principles

By the end, the agent browser is a pure assembler. Each content type has exactly one home, next to the
thing it describes:

| Content | Single source of truth |
|---|---|
| Agent prose (summary, capabilities, data sources, limitations, tips) | `packages/alliance/agents/<agent>/docs.yaml` |
| Tool prose (description, parameter docs) | the tool's **docstring**, via `introspect_tool()` |
| Validation (validators, field requirements, policy, explainer) | `packages/alliance/domain_packs/<pack>/domain_pack.yaml` |

Principles:

- **Curator-friendly voice (the top requirement).** Every piece of prose that reaches the UI —
  agent summaries/capabilities/tips, tool descriptions and parameter docs, validator descriptions, the
  "how validation works" explainer — MUST be written for a working biocurator with **no programming or
  developer background**. Plain language, no jargon, no implementation detail, no code identifiers as the
  primary explanation. Describe *what it does and why a curator should care*, not how it is built. This
  applies wherever content is authored or migrated in this initiative; it is called out again at each phase.
- **No fallbacks.** The Python dicts are *deleted*, not demoted to a fallback. A missing `docs.yaml` is a
  real, visible empty state (and a guard-test failure), never a silent fall-through to Python.
- **No content duplication.** The Validation section reads the same YAML the engine reads; it does not
  restate validator behavior in a second place.
- **Documentation cannot silently go missing.** Guard tests fail CI when a curator-facing agent, a
  referenced tool, or a validator binding lacks its required curator-facing docs (see Cross-cutting).

### Audience and voice (applies to all surfaced prose)

The reader is a biologist doing curation, not an engineer. Concretely:

- Lead with the curator's question: "what does this do for me / what will it check / what does this mean for
  my submission." Avoid internal field paths, class names, and pipeline mechanics in the explanatory text
  (a field path may appear as a secondary "checks:" detail, but never as the explanation itself).
- No undefined acronyms or developer shorthand. Spell out the first use (e.g. "Disease Ontology (DOID)").
- Prefer short sentences and everyday words over precise-but-opaque terminology.
- Bad: "Resolves `disease_annotation_object.curie` via the DO validator binding, blocking on UNRESOLVED."
  Good: "Checks that the disease you selected is a real term in the Disease Ontology. If it can't be
  matched, you'll need to fix it before submitting."

This is the standard the human author/reviewer applies; the guard tests enforce *presence*, voice is
review-gated (see Cross-cutting).

## Phase 1 — Agent docs to `docs.yaml`

Layout (matches the existing split — `prompt.yaml` and `group_rules/` are already separate files):

```
packages/alliance/agents/disease_extractor/
  agent.yaml        # config only
  prompt.yaml       # (existing)
  group_rules/      # (existing, where present)
  docs.yaml         # NEW — curator-facing prose
```

`docs.yaml` schema (mirrors today's `AgentDocumentation` so the API shape is unchanged):

```yaml
summary: "Extracts disease assertions from uploaded PDFs into Alliance disease domain envelopes."
capabilities:
  - name: "Evidence-first extraction"
    description: "Records evidence spans before staging assertions"
    example_query: "Extract disease annotations from this paper"
    example_result: "Staged DiseaseAnnotation objects with grounded terms and evidence"
data_sources:
  - name: "Uploaded PDF"
    description: "The loaded document under curation"
    data_types: ["disease annotations", "evidence spans"]
limitations:
  - "Operates on one document at a time"
tips:                       # absorbs the frontend "Tips for Best Results" block
  - "Include the relevant figure/table when the assertion is data-driven"
```

Work:

1. Add a `docs.yaml` loader (sibling read in the package-agent load path; populate
   `AgentDefinition.documentation`).
2. Port each of the 18 folder-backed `AGENT_DOCUMENTATION` entries into the matching agent bundle's
   `docs.yaml` (in whichever package owns it — `packages/alliance/agents/<folder>/` for most,
   `packages/core/agents/supervisor/` for the supervisor). The 2 synthetic entries (`task_input`,
   `curation_prep`) move to a small system-docs YAML loaded by `registry_builder` (no prose in Python).
   Porting is not just a copy — review each entry against the curator-friendly standard (Audience and
   voice) and rewrite anything that reads developer-first. Additionally, **author net-new `docs.yaml` for
   live agents the dict never covered** (the newer validators) so the guard test can pass.
3. **Delete** `AGENT_DOCUMENTATION` and the `or AGENT_DOCUMENTATION.get(...)` fallback in
   `registry_builder.py`.
4. Move the frontend "Tips for Best Results" content into `docs.yaml` `tips[]`; the frontend renders
   `documentation.tips` instead of literals (`AgentDetailsPanel.tsx` ~537–573).
5. **Parity test:** the `/api/agent-studio/catalog` `documentation` block for each live agent is identical
   before and after the move.

## Phase 2 — Tool docs to docstrings

1. Write a structured docstring on each `@function_tool` (summary line, then parameter docs). The SDK +
   `introspect_tool()` already turn that into `description` + per-parameter `description`.
   **Voice note:** these docstrings are now dual-purpose — they brief the model *and* they are shown to
   curators. Write them in plain, curator-approachable language (see Audience and voice). The clear,
   jargon-free phrasing that helps a non-developer also helps the model; we do not keep a separate
   developer-only description. If a tool genuinely needs model-only technical nuance, it can live deeper in
   the docstring body, but the surfaced summary and parameter descriptions must read for a non-programmer.
2. Make `introspect_tool()` the source of truth and **delete `CURATED_TOOL_REGISTRY`**. `source_file` is
   introspectable (`inspect.getfile`). The one thing a docstring cannot carry is `category`: use a
   lightweight convention (a `category:` line in the docstring, or a small `@tool_category(...)` marker)
   rather than a Python dictionary. (Decision D3.)
3. **Tool audit ("both"):** build the union of every `tools:` entry across all live `agent.yaml` files and
   reconcile against what each agent actually uses — close coverage gaps, and register the newer
   validation-era tools (e.g. the builder/grounding tools seen on `phenotype_extractor`) on the right
   agents. Each added tool ships with its docstring.
4. After this phase, adding a tool to an agent = add its name to `agent.yaml` `tools:` + write its docstring.
   No Python registry edit.

## Phase 3 — The Validation section (bidirectional)

### 3a. Agent→pack link

The `curation.domain_pack_id` block already exists on the extractor agents. Work here is **verify, don't
invent**: confirm every domain extractor carries it (a guard test enforces this), and decide whether to
normalize the `gene`/`agr.alliance.gene` `pack_id` (default: leave it).

### 3b. API: a `validation` block, assembled live

Add a `validation` field to the catalog/agent response, computed from the domain-pack registry
(`backend/src/lib/domain_packs/validation_registry.py`: `DomainPackValidationRegistry`,
`ValidatorMetadataEntry`, `FieldValidationPolicy` ~282–339). Its content depends on the agent's role:

- **Extractor agent** (has `curation.domain_pack_id`): the linked pack's
  - active validators: `display_name`, authored `description`, `applies_to` field paths, policy flags
    (`required`, `blocking`, `allow_opt_out`, `curator_override.allowed`);
  - required fields: field policies that are required / export-blocking;
  - under-development validators: `display_name` + `state_explanation` (roadmap);
  - explainer: shared "how validation works" text (severity + gating).
- **Validator/specialist agent** (a `*_validation` agent): the **reverse map** — scan every pack's
  `validator_bindings` for `validator_agent.agent_id == this agent` and list each binding (pack,
  `display_name`, fields it checks, policy). A validator referenced by no binding (today: `agm_validation`,
  `chemical_validation`) returns "not currently referenced by any binding."
- **Everything else**: empty `validation` (drives the empty state).

No behavior is restated — the UI and the engine read the same YAML.

Reverse-map reality check (binding references per validator agent, 2026-06-02): `ontology_term_validation`
10, `controlled_vocabulary_validation` 8, `reference_validation` 4, `gene_validation` 3,
`experimental_condition_validation` 3, `subject_entity_validation` 2, `data_provider_validation` 2,
`allele_validation` 2, `disease_validation` 1, `agm_validation` 0, `chemical_validation` 0.

### 3c. Author the curator-facing content (in YAML)

- Fill the empty `description:` on each `active[]` and `under_development[]` binding across the 5 packs
  (`domain_pack.yaml`). This is the bulk of the content work; it lands in pack YAML, already the engine's
  source of truth. **These descriptions must be curator-friendly** (see Audience and voice): say what the
  check verifies and what it means for the curator's data, not which binding/field/agent implements it. The
  existing developer code-comments (e.g. "D5 ENFORCEMENT…") are NOT the source text — author fresh,
  plain-language descriptions.
- Confirm required/blocking field policies have curator-friendly `display_name`s.
- Write one shared explainer (severity levels INFO/WARNING/ERROR/BLOCKER from `domain_envelope.py:70-76`,
  and what BLOCKER means for submission) stored in the `agr.alliance.base` pack. (Decision D2.)

### 3d. Frontend

Add a `'validation'` tab to `AgentDetailsPanel.tsx` (extend the `TabValue` union ~line 153, add a
`<StyledTab>` ~line 398, add a render block in `TabContent` after ~line 690). The tab consumes only the API
`validation` block — no hardcoded copy. It renders one of three shapes based on the block: extractor view,
validator reverse view, or empty state.

Extractor view (mockup, no emojis):

```
Validation
-----------------------------------------------------------
How validation works
  Findings are graded INFO / WARNING / ERROR / BLOCKER.
  BLOCKER findings prevent submission until resolved.

Active validators (3)
  Disease ontology lookup                 [required] [can opt out]
    Resolves the disease term against the Disease Ontology (DOID).
    Checks: disease_annotation_object.curie, .name

  Disease relation vocabulary lookup      [required] [blocking]
    Verifies the disease relation against the controlled vocabulary,
    restricted to the subset for the staged subject type.
    Checks: disease_relation_name

Required fields
  disease_annotation_object.curie    blocks submission if missing

In development (1)
  Experimental-condition reference lookup
    "Reference-backed condition dispatch is still being wired."
```

Validator reverse view (mockup):

```
Validation
-----------------------------------------------------------
This agent is a validator. It is dispatched by 10 validator
bindings across 4 domain packs.

Disease pack
  Disease ontology lookup        checks disease_annotation_object.curie, .name
Gene expression pack
  Anatomy term lookup            checks where_expressed.anatomy_term
  ...
```

Empty state:

```
Validation
-----------------------------------------------------------
No automated validation applies to this agent.
```

## Cross-cutting — documentation-completeness guard tests

A test suite (CI) that **fails when documentation is missing**, so we never ship an undocumented agent,
tool, or validator:

1. **Agents:** every curator-facing live agent (e.g. `frontend.show_in_palette: true` under
   `packages/alliance/agents/`) has a `docs.yaml` with a non-empty `summary` and ≥1 capability.
2. **Tools:** for the union of every `tools:` entry across all live `agent.yaml`, `introspect_tool()` yields
   a non-empty `description` and a description for every parameter.
3. **Validators:** every `active[]` and `under_development[]` binding across all packs has a non-empty
   `description`.
4. **Pack links:** every domain extractor (any agent whose output is a domain envelope) declares
   `curation.domain_pack_id`, and that id resolves to a real pack.
5. **Explainer:** the shared "how validation works" text exists in `agr.alliance.base`.

These are the anti-rot mechanism: the cost of adding something new includes documenting it, enforced by CI
rather than memory.

**Presence vs. voice.** Guard tests enforce that docs *exist* and are non-trivial — they cannot judge
"approachable." Curator-friendliness (the top principle) is **review-gated**: it belongs in the PR
checklist/template and in human review, with Chris/curators as the final arbiters of tone. Light,
mechanical lint is in scope where it helps without false confidence (e.g. flag a description that is *only*
a code identifier or field path, an empty/one-word description, or an undefined ALL-CAPS token), but no
automated check is treated as a substitute for a curator-facing read.

## Sequencing

Build in order; each phase is independently shippable and parity-tested.

1. **Phase 1** — agent docs → `docs.yaml`; delete `AGENT_DOCUMENTATION`; move frontend tips. (+ guard test 1)
2. **Phase 2** — tool docs → docstrings; delete `CURATED_TOOL_REGISTRY`; tool audit + new tools. (+ guard test 2)
3. **Phase 3** — verify pack links, API `validation` block (extractor + validator reverse views), authored
   validator descriptions + explainer, frontend Validation tab + empty state. (+ guard tests 3, 4, 5)

## Decisions

- **D1 — agent→pack link shape.** `curation: { domain_pack_id: ... }` block. *Already the live pattern* on
  extractor agents; verify + extend, don't invent.
- **D2 — explainer home.** Shared "how validation works" text in the `agr.alliance.base` domain pack.
- **D3 — tool `category`.** Lightweight in-docstring/marker convention, not a Python map.
- **D4 — validator agents.** Bidirectional Validation tab: extractors show "what validates my output";
  `*_validation` agents show the reverse map "what I validate and for whom."

## Testing strategy

- **Parity:** catalog API `documentation` and tool details identical before/after Phases 1–2.
- **Contract:** the new `validation` block has a stable shape; snapshot one extractor agent, one validator
  agent, and one no-pack agent.
- **Guard:** the five completeness tests above.
- **Frontend:** the three Validation views render purely from API data (no literals).

## Out of scope

- The **Agent Workshop** (cloning/editing agents) — separate, later.
- Deleting the legacy `alliance_agents/` tree — separate cleanup.
- Normalizing the `gene` pack's `pack_id` — separate, optional.
- Changing validation *behavior* — this initiative only surfaces and relocates content.
