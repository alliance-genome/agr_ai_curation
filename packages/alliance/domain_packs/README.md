# Alliance Domain Packs

Alliance domain packs live in this package-owned directory so LinkML and
curation-database grounding stays outside the provider-agnostic envelope models.

Each `domain_pack.yaml` defines the Alliance-specific interpretation of a
provider-neutral `DomainEnvelope`:

- pinned Alliance LinkML schema refs and provider refs,
- model definitions and curatable object types,
- field paths, required flags, field metadata, and curator-edit policy,
- validator metadata and validator bindings,
- workspace display/projection metadata,
- export/submission behavior and adapter targets,
- fixture packs with concrete envelope examples.

The base scaffold defines shared object-role conventions and pinned Alliance
LinkML schema references. Concrete packs such as `gene_expression`, `gene`,
`allele`, `disease`, `chemical_condition`, and `phenotype` keep LinkML and AGR
curation DB grounding in package metadata, fixtures, and Alliance package Python
adapters instead of adding those fields to core schemas.

For new domain-pack runs, the persisted domain envelope is the semantic source
of truth. Workspace review rows, candidate rows, export bundles, and submission
payloads are projections over envelope objects at a known revision.

Validation is metadata-driven. Active validator bindings declare package-scoped
validator agents, under-development bindings are surfaced as informational
metadata, and required or export-blocking fields become readiness blockers
unless policy allows a curator override.

When adding or changing Alliance packs, update the matching contract tests under
`backend/tests/contract/alliance/domain_packs/` and fixture examples under
`backend/tests/fixtures/domain_packs/` or the pack's `fixtures/` directory.
