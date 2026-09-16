# Extraction-only acknowledgment

Database validation coverage is derived from explicit mapping fields, immutable
profile mapping history, and an agent's previously selected profiles. Shape-compatible
fields, free-text names, semantic-class prose, and species/provider guesses do not
establish a validation requirement. Arbitrary notes without a declared mapping
history remain valid extraction fields. A configured but unavailable capability is
a repair error, not an extraction-only option.

When applicable coverage is removed or an optional flow check is disabled, save and
launch require a human acknowledgment. The UI lists affected fields and starts with
an unchecked checkbox. Cancel changes neither the configuration nor acknowledgment.
The endpoint is intentionally not an AI Chat tool. API clients must explicitly submit
`acknowledge_extraction_only: true`; AI-proposed changes cannot supply this implicitly.

`validation_acknowledgments` records the authenticated actor, server timestamp, exact
coverage scope and its SHA-256 fingerprint. It grants no access, validation outcome,
readiness, or submission permission. Every save/launch recomputes coverage; records
are reusable only by the same actor for the same configuration and missing-field
scope. Changes to the contract or relevant coverage require another acknowledgment.
Historical execution/profile records are not rewritten by this migration.

Flow runtime persists `database_validation_coverage` separately from validation
outcomes. Affected objects receive open warning findings. Formatter artifacts retain
coverage and warnings; JSON evidence export retains the audit scope, and CSV/TSV adds
`database_validation_status` when any exported step has extraction-only coverage.
Existing readiness and required-validator rules still apply.

Database mappings cannot be inferred for a brand-new arbitrary free-text structure.
The UI says when no database validator is attached; the acknowledgment requirement
is limited to coverage established by configuration/history and optional check
opt-outs. This is not a claim that other fields have been checked or verified.
