# Documentation Index

This index defines where authoritative project knowledge lives.

## Authoritative Sources by Domain

| Domain | Source of truth | Notes |
|---|---|---|
| Repository startup map | `AGENTS.md` | Fast orientation for agents and humans |
| Developer documentation index | `docs/developer/README.md` | Entry point for technical guides |
| Curator documentation index | `docs/curator/README.md` | Entry point for curator workflows |
| Configuration model | `config/README.md` | Configuration hierarchy and ownership |
| Test scope and intent | `docs/developer/TEST_STRATEGY.md` | Test boundaries and health guidance |
| Guardrail catalog | `docs/testing/guardrail-catalog.md` | Invariant, scan, and smoke guards with their protecting test paths |
| Domain-envelope architecture | `docs/developer/guides/DOMAIN_ENVELOPES.md` | 0.7.0 envelope/domain-pack source-of-truth, validation, materialization, export, and submission contracts |
| Gene Expression 0.7.0 release contract | `docs/developer/guides/GENE_EXPRESSION_0_7_0.md` | Pinned LinkML source, fixtures, validation behavior, export shape, known limitations, and non-Alliance domain-pack walkthrough |
| Symphony execution contract | `.symphony/WORKFLOW.md` | State and behavior for unattended execution |
| Deployment procedures | `docs/deployment/` | Environment and rollout runbooks |

## Precedence Rule

When documentation conflicts:

1. Runtime contract files (`.symphony/WORKFLOW.md`, CI workflows) take precedence for automation behavior.
2. Domain-specific docs (`config/README.md`, `docs/developer/TEST_STRATEGY.md`) take precedence for their domain.
3. Index docs (`README.md`, this file) should be updated to point to current sources.

## Drift Control

- Keep links in docs valid.
- Update authoritative docs in the same change where behavior changes.
- Use automation to detect drift:
  - `./scripts/testing/agent_pr_gate.sh`
  - `./scripts/maintenance/harness_hygiene.sh`
