# Domain Envelopes and Domain Packs

Domain envelopes are the 0.7.0 curation data contract. They replace
candidate-shaped extractor payloads as the semantic source of truth for new
domain-pack runs. Review rows, draft fields, export payloads, submission
payloads, and frontend tables are projections over persisted envelope objects at
a known revision.

This guide is grounded in the implemented runtime:

- Core schema: `backend/src/schemas/domain_envelope.py`
- Domain-pack metadata schema: `backend/src/schemas/domain_pack_metadata.py`
- Registry and validation policy: `backend/src/lib/domain_packs/`
- Persistence and checkpoints: `backend/src/lib/domain_envelopes/`
- Workspace materialization: `backend/src/lib/domain_packs/materialization.py`
- Flow validation attachments: `backend/src/lib/flows/validation_attachments.py`
- Export and submission readiness: `backend/src/lib/curation_workspace/session_submission_service.py`
- Alliance package metadata: `packages/alliance/domain_packs/`

## Source of Truth

For new domain-pack runs:

1. Extraction agents produce `DomainEnvelope` objects.
2. The runtime persists those envelopes as revisioned checkpoints.
3. Object, finding, history, and projection indexes are regenerated from the
   stored envelope JSON for the current revision.
4. The curation workspace materializes review rows from persisted envelope
   objects.
5. Export and submission services read envelope objects and revisions, not
   legacy normalized payloads, when assembling domain-pack payloads.

The remaining `CurationCandidate` rows are workspace projections. They carry
`envelope_id`, `object_id`, and `envelope_revision` so the workspace can join a
curator-visible row back to the envelope object it represents. New domain-pack
materialization sets candidate metadata such as
`semantic_source: domain_envelope.objects`; `normalized_payload` is intentionally
empty for those projected rows.

Legacy candidate/prep payload code may still exist for old sessions, migration
coverage, and non-domain-pack paths. Do not extend that code as a parallel source
of truth for new domain-pack work.

## Envelope Shape

`DomainEnvelope` is provider-neutral. It does not require Alliance LinkML,
curation database tables, or a specific LLM provider.

Important fields:

- `envelope_id`: stable envelope identifier.
- `domain_pack_id` and `domain_pack_version`: metadata package that defines the
  object semantics.
- `objects[]`: extracted `CuratableObjectEnvelope` records.
- `validation_findings[]`: findings attached to objects or fields.
- `history[]`: audit events for extraction, validation, curator edits, export,
  and submission.
- `metadata`: domain-pack-owned metadata outside the semantic object list.

Objects carry either a durable `object_id` or an extraction-time
`pending_ref_id`. Field references use object refs plus relative JSON field
paths such as `gene.symbol` or `evidence[0].snippet`. Object identity does not
belong inside field paths.

The schema validates object and field references inside the same envelope. A
finding or history event cannot point at an unknown object, and a field ref must
resolve inside the target object payload.

## Domain-Pack Metadata

Domain packs define the semantics that the core envelope deliberately avoids
hard-coding:

- schema refs and provider refs,
- model definitions,
- curatable object definitions,
- field paths, field types, required flags, enum/model/object refs,
- fixture packs,
- validator metadata and validator bindings,
- workspace display metadata,
- export/submission behavior and provider projections.

Metadata is validated through `DomainPackMetadata`. Pack IDs and versions are
strict, object and field references must resolve, and fixture packs must point at
known object types.

Keep shared runtime behavior project-agnostic. Alliance-specific LinkML classes,
AGR curation database projections, and submission adapters belong in
`packages/alliance/domain_packs/`, Alliance package Python modules, and package
metadata. Do not add Alliance-only assumptions to the core envelope schema.

## Validation

Automatic validation is metadata-driven. `DomainPackValidationRegistry` reads
`metadata.validators` and `metadata.validator_bindings` from the domain pack,
object definitions, and field definitions, then normalizes them into:

- active and under-development validator bindings,
- required/export-blocking field policies,
- field-level opt-out policy,
- Agent Studio validation attachment options,
- binding matches against envelope objects and fields.

`run_domain_envelope_structural_checks()` handles deterministic structural
checks such as required fields and writes new `ValidationFinding` records back
into the envelope. Under-development bindings are metadata visibility only; they
are not runtime validation findings. Active bindings are executed through
package-scoped validator dispatch and materialized as resolved or unresolved
validator results.

Active validator `input_fields` must use explicit selector objects. Supported
selector sources are `payload`, `envelope_metadata`, `object_metadata`,
`evidence_record`, `object_ref`, and `literal`. Active payload selectors are
checked against domain-pack object fields, with pinned provider `schema_ref`
slot/attribute metadata used only when the domain-pack field list cannot prove
the path directly. At runtime, selector failures become structured findings
such as `selector_missing`, `selector_ambiguous`, `selector_unresolved_ref`, or
`selector_missing_field`; the supervisor must not pick a first object ref or
guess through sibling objects.

Findings are targeted with `object_id`/`pending_ref_id` plus `field_path` where
possible. Stable finding IDs are derived from the envelope ID, code, severity,
message, target, and details when a validator does not provide one.

### Lookup Attempts

`lookup_attempts` is an audit trail, not only a failure marker. AGR lookup tools
return structured attempts with:

- source tool and method,
- provider,
- attempted query,
- target projection,
- lookup status,
- candidate count,
- resolved ID and label when available,
- explanation,
- optional error metadata.

Because attempts record each query made during lookup, a result can have
transient or not-found attempts even when the top-level lookup later succeeds
after retry or a detail fetch. Consumers should use the top-level
`lookup_status` for final outcome and preserve `lookup_attempts` for audit,
validation, and debugging context.

A successful lookup is not enough to resolve every declared field. When
domain-pack `expected_result_fields` say that a result value validates an
envelope field, that value must be present in the lookup response. If the lookup
partially succeeds but omits a declared result value, the supervisor retries the
validator once with the missing projection context and records an open
field-level finding if the value is still absent.

Shared status constants live in `backend/src/lib/lookup_status.py`; the backend
tool and packaged Alliance tool both use
`backend/src/agr_ai_curation_runtime/agr_lookup.py` to avoid drift.

## Validation Findings and Curator Review

Target envelope-backed extractors return ordinary extraction result schemas.
They do not return field-patch contracts, retry envelopes, or target-specific
correction wrappers. Biological validation belongs to package-scoped validator
bindings, and unresolved validator outcomes are represented as
`ValidationFinding` records on the envelope.

Validators return structured decisions and facts such as resolved values,
resolved objects, missing expected fields, candidates, lookup attempts,
curator-facing messages, and explanations. The supervisor records those results
as findings without asking the extractor to rewrite the target envelope.

Curator-facing changes use review-row and field-edit semantics. A curator may
edit bounded fields, waive or resolve findings when policy allows it, or leave a
finding open for later package or data work. The envelope revision and history
remain the durable record, and review rows are regenerated from the persisted
envelope revision.

## Persistence and History

`write_domain_envelope_checkpoint()` commits the next envelope revision in one
transaction. It verifies the expected revision, stores the full envelope JSON,
regenerates object/finding/projection indexes, appends unseen history events,
and commits.

The indexed tables are read models:

- `DomainEnvelopeModel`: current envelope JSON and revision metadata.
- `DomainEnvelopeObject`: current object index with payload JSON and validation
  state.
- `DomainValidationFinding`: current finding index.
- `DomainEnvelopeProjectionIndex`: provider/domain-pack projections.
- `DomainEnvelopeHistory`: append-only history events by event ID.

Revision checks matter. Workspace, export, and submission requests should carry
the expected envelope revision when they depend on a specific reviewed state.
Stale revision writes or submissions must block instead of silently using newer
data.

## Materialized Review Rows

`DomainPackMetadataReviewRowMaterializer` regenerates review rows from envelope
objects and domain-pack metadata. It returns one `DomainEnvelopeReviewRow` per
non-`metadata_only` object. Display labels, secondary labels, summary fields,
schema refs, model refs, evidence anchors, validation summaries, and projection
keys all come from the envelope and metadata.

The curation workspace still stores projected candidate rows so existing review
session mechanics can assign, accept, reject, and navigate rows. For domain-pack
runs those rows point back to `domain_envelope.objects`, and draft fields record
their source field paths.

Curator field edits use field-path patches against the envelope object payload.
The envelope revision and history remain the durable record; the UI row updates
after projection refresh.

## Export and Submission

Export and submission readiness is computed from domain envelopes at the
expected revision. Readiness blockers can come from:

- missing domain-pack metadata,
- stale envelope revision,
- missing envelope or object,
- non-stable schema/object/field definition states,
- domain-pack export behavior metadata,
- missing required export context,
- required/export-blocking field policy,
- unresolved error/blocker validation findings,
- adapter-owned readiness checks.

Readiness blockers carry `envelope_id`, `object_id`, `field_path`, severity,
status, code, message, provider refs, projection refs, and details. Curator
overrides only unblock when metadata or field policy allows the override. A
reason is required only when that specific policy explicitly asks for one.

Export and direct submission payloads include:

- `domain_envelope_candidates`,
- selected envelope snapshots,
- readiness blockers,
- expected envelope revisions,
- adapter-selected target details.

Submission results append `submitted` history rows with target result history,
external references, warnings, validation errors, and submission state.

## Agent Studio and Flow Builder

Agent Studio exposes domain-envelope metadata through
`domain_envelope_metadata_catalog_by_agent()`. The frontend renders:

- domain-pack display name, status, schema refs, and provider refs,
- source-of-truth notes,
- object definitions and field paths,
- definition state and notes,
- validation attachment counts,
- active/under-development validation state,
- required/export-blocking indicators.

Flow Builder validation attachments are derived from domain-pack metadata.
Defaults are applied to extraction nodes by
`apply_flow_validation_attachment_defaults()`. Active validators are enabled by
default. Under-development validators remain visible metadata and do not carry
required, blocking, or opt-out runtime policy.

Custom validation agents are regular flow steps. Their steering prompts are
stored as normal node configuration and should target envelope objects, field
paths, or curator questions.

## Adding or Updating a Domain Pack

1. Add or update the package-owned `domain_pack.yaml`.
2. Define schema refs, model definitions, object definitions, and field paths.
3. Put provider-specific refs in metadata, not core schema fields.
4. Declare validators and validator bindings in metadata.
5. Mark required/export-blocking fields and opt-out policy intentionally.
6. Add fixture packs with concrete envelope examples.
7. Add or update package conversion/export/submission adapters when needed.
8. Add contract tests under `backend/tests/contract/alliance/domain_packs/` for
   Alliance packs or neutral tests under `backend/tests/unit/lib/domain_packs/`
   for provider-agnostic behavior.
9. Update Agent Studio and flow tests if metadata changes what curators see.

## Grounding Rules

Only make LinkML or live curation DB claims when grounded by code, tests, cached
schema metadata, domain-pack provider refs, or an explicit source-of-truth
check. If a doc needs a claim that is not exposed by implemented code/tests and
source access is unavailable, omit it or block the work rather than guessing.
