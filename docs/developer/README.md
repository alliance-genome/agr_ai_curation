# Developer Documentation

Documentation for the AI Curation Platform developers.

> Scope: These guides are primarily for repository contributors and package
> maintainers. Standard installs should customize agents, tools, and defaults
> through `~/.agr_ai_curation/runtime/packages/` and
> `~/.agr_ai_curation/runtime/config/`, not by editing repo `config/` or
> `backend/src/` paths directly. For the public runtime contract, see
> [docs/deployment/modular-packages.md](../deployment/modular-packages.md).

## Directory Structure

| Directory | Purpose |
|-----------|---------|
| `api/` | API reference documentation |
| `guides/` | Developer guides and how-tos |
| `traces/` | Langfuse trace analysis tools |

## Quick Navigation

### Getting Started

Start here for new developers:

1. **[CONFIG_DRIVEN_ARCHITECTURE.md](guides/CONFIG_DRIVEN_ARCHITECTURE.md)** -- Repository architecture and package-aware runtime loading
2. **[ADDING_NEW_AGENT.md](guides/ADDING_NEW_AGENT.md)** -- How to add an agent bundle, with separate notes for runtime packages vs source checkouts
3. **[ADDING_NEW_TOOL.md](guides/ADDING_NEW_TOOL.md)** -- How to add a tool, with separate notes for package-owned exports vs runtime internals
4. **[DOMAIN_ENVELOPES.md](guides/DOMAIN_ENVELOPES.md)** -- 0.7.0 domain-envelope/domain-pack source-of-truth, validation, curator review, export, and submission contracts
5. **[GENE_EXPRESSION_0_7_0.md](guides/GENE_EXPRESSION_0_7_0.md)** -- Gene Expression 0.7.0 release contract, pinned LinkML policy, fixtures, validation behavior, export shape, and non-Alliance domain-pack walkthrough
6. **[TEST_STRATEGY.md](TEST_STRATEGY.md)** -- Docker-first validation strategy and domain-envelope release gates
7. **[DEVELOPMENT_DOCTRINE.md](guides/DEVELOPMENT_DOCTRINE.md)** -- Forward-only development rules for fallbacks, compatibility, and migrations
8. **[AGENTS_DEVELOPMENT_GUIDE.md](guides/AGENTS_DEVELOPMENT_GUIDE.md)** -- Comprehensive agent/runtime architecture reference
9. **[UPLOAD_RUNTIME_CONTRACT.md](guides/UPLOAD_RUNTIME_CONTRACT.md)** -- Upload runtime contract (status/cancellation/rollback/idempotency; implementation in ALL-23)

### Developer Guides

| Guide | Description |
|-------|-------------|
| [DEVELOPMENT_DOCTRINE.md](guides/DEVELOPMENT_DOCTRINE.md) | Forward-only development policy: remove fallbacks, avoid compatibility shims, prefer explicit migrations |
| [CONFIG_DRIVEN_ARCHITECTURE.md](guides/CONFIG_DRIVEN_ARCHITECTURE.md) | Full architecture guide for repo contributors -- package loading, database runtime, loaders, deployment |
| [DOMAIN_ENVELOPES.md](guides/DOMAIN_ENVELOPES.md) | Domain-envelope architecture: source of truth, field paths, structural checks, validator dispatch, lookup attempts, curator review, materialization, export/submission, and Agent Studio metadata |
| [GENE_EXPRESSION_0_7_0.md](guides/GENE_EXPRESSION_0_7_0.md) | Gene Expression 0.7.0 release contract: LinkML pin, fixtures, validation behavior, export handoff, known limitations, and non-Alliance domain-pack pattern |
| [TEST_STRATEGY.md](TEST_STRATEGY.md) | Docker-first test commands plus domain-envelope contract, LinkML, live DB, fixture, and release-gate expectations |
| [ADDING_NEW_AGENT.md](guides/ADDING_NEW_AGENT.md) | Add agent bundles for runtime packages or source-checkout shipped-package maintenance |
| [ADDING_NEW_TOOL.md](guides/ADDING_NEW_TOOL.md) | Add package-owned tools or maintain Alliance Defaults tool catalogs/runtime tool plumbing |
| [AGENTS_DEVELOPMENT_GUIDE.md](guides/AGENTS_DEVELOPMENT_GUIDE.md) | Comprehensive reference: unified agents table, dynamic supervisor, tool bindings, prompt management |
| [DOCKER_CLI_REMOVAL_VERIFICATION.md](guides/DOCKER_CLI_REMOVAL_VERIFICATION.md) | 2026-03-24 backend audit for Docker CLI/socket hardening and remaining Docker references |
| [DEV_RELEASE_SMOKE_STRATEGY.md](DEV_RELEASE_SMOKE_STRATEGY.md) | Verbose source of truth for the deep dev release smoke gate: API coverage, gaps, evidence, rollout plan |
| [2026-04-13-chat-curation-evidence-alignment-plan.md](../design/2026-04-13-chat-curation-evidence-alignment-plan.md) | Deep-dive plan for aligning chat and curation PDF evidence navigation before the production hotfix |
| [PDF_EVIDENCE_CONSISTENCY_STRATEGY.md](PDF_EVIDENCE_CONSISTENCY_STRATEGY.md) | Deep-dive architecture strategy for replacing split Home/Curation PDF viewers with one persistent route-level PDF.js host |
| [PDF_HIGHLIGHT_VERIFICATION.md](guides/PDF_HIGHLIGHT_VERIFICATION.md) | Verification checklist and diagnostics for PDF chunk highlighting bugs |
| [SYMPHONY_FLOW_AND_OPTIMIZATION.md](guides/SYMPHONY_FLOW_AND_OPTIMIZATION.md) | Current Symphony runtime flow, lane transition map, and prompt/context optimization audit based on the live `.symphony` implementation |
| [SYMPHONY_INCUS_VM_REBUILD.md](guides/SYMPHONY_INCUS_VM_REBUILD.md) | Rebuild `symphony-main` from a tracked cloud-init source, including default git safety scanners |
| [SYMPHONY_VM_CODEX_SHORTCUTS.md](guides/SYMPHONY_VM_CODEX_SHORTCUTS.md) | Interactive Codex shortcuts in the Symphony VM, including PAT-backed `co` and rebuild persistence |
| [UPLOAD_RUNTIME_CONTRACT.md](guides/UPLOAD_RUNTIME_CONTRACT.md) | Upload runtime behavioral contract: status precedence, cancellation, rollback matrix, and idempotency expectations (implementation tracked in ALL-23) |

### API Reference

- **[API_USAGE.md](api/API_USAGE.md)** -- Complete HTTP reference with streaming, auth, and workflows

### Trace Analysis

- **[TRACE_REVIEW_API.md](traces/TRACE_REVIEW_API.md)** -- Trace review service API documentation
- **[TRACE_REVIEW_SPECIFICATION.md](traces/TRACE_REVIEW_SPECIFICATION.md)** -- Trace review system specification

## Configuration Reference

For configuration files, see:

| File | Description |
|------|-------------|
| [config/README.md](../../config/README.md) | Configuration directory overview |
| [config/agents/README.md](../../config/agents/README.md) | Agent configuration reference |
| [config/models.yaml](../../config/models.yaml) | Model catalog (curator-selectable LLMs) |
| [config/groups.yaml.example](../../config/groups.yaml.example) | Group/Cognito mapping template |
| [config/connections.yaml.example](../../config/connections.yaml.example) | External connections template |

## Common Tasks

### Add a New Agent

Choose the path that matches your goal:

- Standard install or org customization: create a runtime package under `~/.agr_ai_curation/runtime/packages/<package>/agents/<agent>/` and install that package.
- Repository package maintenance: use the repo template under `config/agents/_examples/basic_agent`, then keep the shipped supervisor copy in `packages/core/agents/` or the shipped specialist copy in `packages/alliance/agents/` aligned before shipping.
- UI-only customization: create custom agents in Agent Studio with no file changes.

See [ADDING_NEW_AGENT.md](guides/ADDING_NEW_AGENT.md) and
[config/agents/README.md](../../config/agents/README.md) for details.

### Add a New Tool

Choose the path that matches your goal:

- Standard install or org customization: add Python code plus `tools/bindings.yaml` to a runtime package under `~/.agr_ai_curation/runtime/packages/<package>/`.
- Repository package maintenance: update the shipped tool sources in `packages/alliance/python/src/...` and `packages/alliance/tools/bindings.yaml`.
- Runtime-internal development: only edit `backend/src/...` when the runtime itself needs new loader, resolver, or execution behavior.
- Dependency-backed tools are not agent-ready until the upstream dependency is
  released or pinned, backend and package runtime dependencies are aligned, the
  package tool wrapper is exported, consuming agent prompts/schemas are updated,
  and tests cover the end-to-end path.

See [ADDING_NEW_TOOL.md](guides/ADDING_NEW_TOOL.md) and
[backend/tools/README.md](../../backend/tools/README.md) for details.

### Keep Project-Agnostic Tests Neutral

This repository ships both a project-agnostic runtime core and bundled Alliance
defaults. Generic runtime tests should prove that loaders, registries, tool
binding discovery, and runtime validation work for packages that are not
`agr.alliance`. Use neutral package and fixture values such as `org.custom`,
`demo_agent`, `demo_search_tool`, `DEMO`, and `curator@example.test`.

Generic tests should not rely on Alliance-only values such as `agr.alliance`,
`agr_curation_query`, `alliance_api_call`, Alliance hostnames,
Alliance-specific curator email domains, or MOD group codes like `FB`, `WB`,
`MGI`, `RGD`, `SGD`, `ZFIN`, or `HGNC`.

Alliance defaults are still first-class shipped package coverage because this
repository includes `packages/alliance`. Tests may assert Alliance specialist
agents, AGR curation database tools, MOD prefixes, real biological fixture IDs,
and Alliance API hosts when they explicitly cover shipped Alliance package
contracts, prompt/tool policy, biological curation fixtures, frontend rendering
of bundled defaults, or current auth/deployment fixtures.

The synthetic non-Alliance package fixture lives at
`backend/tests/unit/lib/packages/fixtures/org_custom_runtime/`. It includes a
neutral agent, prompt, schema, group rule, tool binding, and curation adapter
export. `backend/tests/unit/lib/packages/test_project_agnostic_runtime_guardrails.py`
scans backend and frontend test files for Alliance-specific literals; new
generic/core test files should stay off that allowlist.

### Work with Domain Envelopes

For new domain-pack curation runs, `DomainEnvelope` is the semantic source of
truth. Workspace candidates, draft fields, frontend review rows, export payloads,
and submission payloads are projections over persisted envelope objects at an
expected revision. Do not add new behavior that treats prep candidates,
normalized payloads, or materialized review rows as a parallel semantic store.

Use domain-pack metadata for curatable object definitions, field paths, schema
refs, validator bindings, field/export readiness policy, validator flow
replacement/skip policy, workspace display, and export/submission behavior.
Shared runtime code must stay provider-agnostic; Alliance LinkML, curation DB
projections, and package-specific adapters belong in `packages/alliance/`.

For Agent Studio validation questions, Opus should inspect the domain-envelope
state, domain-pack validation plan, validator-agent prompt via `get_prompt` when
the plan supplies an agent ID, review rows, and export/submission readiness
instead of inferring behavior from static docs or legacy projection payloads.

See [DOMAIN_ENVELOPES.md](guides/DOMAIN_ENVELOPES.md) for the full contract.

### Configure Groups

```bash
# Copy template
cp config/groups.yaml.example config/groups.yaml

# Edit to map your identity provider groups
# to internal group IDs
```

### Configure External Connections

```bash
# Copy template
cp config/connections.yaml.example config/connections.yaml

# Edit to configure databases, APIs, caches
```

## Architecture Overview

The system uses a **config-driven, database-backed architecture** where:

1. **Package-backed YAML defines initial state** -- Agent metadata, prompts, and group rules come from bundles under `~/.agr_ai_curation/runtime/packages/*/agents/` in standalone installs. In a source checkout, `config/agents/` is the repo mirror for the shipped `agr.core` supervisor plus the shipped `agr.alliance` specialist bundles.
2. **Database is runtime authority** -- The unified `agents` table stores all agent records (system + custom)
3. **Dynamic discovery** -- The supervisor queries the database for enabled agents and builds streaming tools at runtime
4. **Declarative tool bindings** -- Package `tools/bindings.yaml` exports are normalized into `TOOL_BINDINGS` with explicit context requirements
5. **Domain packs define curation semantics** -- Extraction agents produce persisted domain envelopes; validation, review rows, export, and submission are driven by package metadata and envelope revisions
6. **No hardcoded agent files** -- All 16 original Python agent files have been replaced by generic construction from database rows

```text
~/.agr_ai_curation/runtime/packages/org-custom/
  package.yaml
  agents/
    my_agent/
      agent.yaml        # Agent definition
      prompt.yaml       # Instructions
      schema.py         # Output schema
      group_rules/      # Org-specific behavior
  tools/
    bindings.yaml       # Tool exports for the merged runtime registry

          |
          | Runtime loaders + seed/update flow populate:
          v

agents table            # Unified database (runtime authority)
  (system agents from package-backed YAML + custom agents from UI)

          |
          | get_agent_by_id() reads from DB
          v

Runtime Agent           # OpenAI Agents SDK Agent instance
  (tools resolved via TOOL_BINDINGS)
  (group rules injected from prompt cache)
  (output schema resolved from schema discovery)
```

For shipped-package maintenance in this repository, keep
`packages/core/agents/supervisor/` generic as the shipped baseline, use
`config/agents/supervisor/` as the repo-local or deployment-specific override,
and keep the remaining repo mirror bundles aligned with
`packages/alliance/agents/`. When the specialist catalog changes in a way that
affects routing or handoff style, review the config supervisor override too.

See [CONFIG_DRIVEN_ARCHITECTURE.md](guides/CONFIG_DRIVEN_ARCHITECTURE.md) and [AGENTS_DEVELOPMENT_GUIDE.md](guides/AGENTS_DEVELOPMENT_GUIDE.md) for the complete reference.
