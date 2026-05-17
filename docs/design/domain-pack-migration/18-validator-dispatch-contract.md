# Validator Dispatch Contract

Status: Implemented

This document records the validator-dispatch architecture that shipped for
domain-pack curation. It is both a current contract for future work and a short
migration-history note for the older design language that led here.

## Current Contract

Domain packs declare active validator bindings. Runtime structural checks and
active binding dispatch run after extraction and write validation findings back
into domain envelopes. Under-development bindings are metadata only. Flow Builder
can skip or replace active default validators through explicit validation groups,
but extractor prompts are not responsible for invoking validators directly.

The runtime owns validator scheduling and dispatch:

- Extractors emit evidence-backed `DomainEnvelope` objects, selector inputs,
  optional lookup hints, and ordinary envelope findings.
- `run_domain_envelope_structural_checks()` appends deterministic findings for
  missing required domain-pack fields.
- `dispatch_active_validator_bindings()` matches active bindings, builds
  `DomainValidationRequest` payloads from explicit selectors, runs the
  package-scoped validator agent, and materializes resolved or unresolved
  `DomainValidatorResultBase` results into the envelope.
- Under-development bindings remain visible to Agent Studio and Flow Builder but
  are not executable runtime work.
- Flow validation attachments describe scheduling metadata for extraction nodes.
  They do not move validator execution into extractor prompts.

## Ownership Boundaries

Extractors own source evidence and envelope shape. They should preserve values
that validators need, such as text mentions, taxon hints, unresolved object refs,
and lookup-attempt context. They do not invent final normalized identifiers for
fields covered by active validator bindings, and they do not retry validation by
rewriting their output.

Validator agents own package-scoped biological validation. Their shared output
contract is `DomainValidatorResultBase` in
`backend/src/schemas/domain_validator.py`, which permits only `resolved` and
`unresolved` results for active validator runs. Validator results carry selected
inputs, resolved values or objects, candidates, lookup attempts, missing expected
fields, curator messages, and explanations.

The platform runtime owns orchestration. It performs structural checks, selects
active bindings, resolves selector inputs, dispatches validator agents, writes
findings and materialized objects into the envelope, checkpoints the new
revision, and regenerates review rows.

Curators own review decisions. They may edit configured fields and may waive
open validator findings only when the finding metadata allows curator override.
Export and submission readiness read the current envelope revision and still
block on unresolved active findings unless the waiver or override policy allows
the reviewed state.

## Metadata Contract

Domain-pack metadata is the canonical declaration surface:

- `backend/src/schemas/domain_pack_metadata.py` defines
  `DomainPackActiveValidatorBinding`, `DomainPackUnderDevelopmentValidatorBinding`,
  `DomainPackValidatorAgentRef`, `DomainPackInputSelector`, and
  `DomainPackValidatorCuratorOverride`.
- Active bindings must name a package-scoped `validator_agent`, an `applies_to`
  target, optional `input_fields`, expected result fields, and explicit policy
  flags such as `required`, `blocking`, `allow_opt_out`, and
  `curator_override.allowed`.
- Active bindings cannot set `blocking: true` unless `required: true`.
- Under-development bindings require display and state-explanation metadata, but
  they do not create runtime blockers, automatic scheduling, or replacement
  obligations.
- `DomainPackInputSelector` supports deterministic sources:
  `payload`, `envelope_metadata`, `object_metadata`, `evidence_record`,
  `object_ref`, and `literal`.

`DomainPackValidationRegistry` in
`backend/src/lib/domain_packs/validation_registry.py` normalizes metadata into
active bindings, under-development metadata, field policies, binding matches,
and `ValidationAttachmentOption` records for Agent Studio and Flow Builder.

## Runtime Dispatch

The implemented dispatch path is:

1. Extraction produces and checkpoints a `DomainEnvelope`.
2. `backend/src/lib/curation_workspace/pipeline.py` refreshes validation for
   persisted envelope refs before materializing workspace review rows.
3. `_refresh_domain_envelope_validation_for_ref()` runs
   `run_domain_envelope_structural_checks()` from
   `backend/src/lib/domain_packs/structural_checks.py`.
4. The same refresh path calls `dispatch_active_validator_bindings()` from
   `backend/src/lib/domain_packs/validator_dispatch.py`, passing the structural
   check registry so matching policy is shared.
5. `build_domain_validation_request()` in
   `backend/src/lib/domain_packs/input_selectors.py` resolves binding selectors.
   Selector failures become structured validation findings instead of guessed
   inputs.
6. `run_package_scoped_validator_agent()` dispatches the package-owned validator
   agent through the unified agent runtime.
7. `materialize_validator_results_into_envelope()` in
   `backend/src/lib/domain_packs/materialization.py` applies validator outcomes
   as envelope findings and validated refs.
8. `write_domain_envelope_checkpoint()` persists the updated envelope revision,
   and `materialize_persisted_envelope_review_rows()` regenerates review rows.

This path makes validator dispatch runtime-owned, checkpoint-aware, and
domain-pack-configurable. Extractor prompts may describe which fields validators
own, but they must not require extractor tools or agents to invoke validators as
part of extraction.

## Flow Builder Contract

Flow Builder represents validator policy through validation attachments and
groups:

- `backend/src/lib/flows/validation_attachments.py` builds
  `validation_attachment_options_for_agent()` from domain-pack metadata and
  applies defaults through `apply_flow_validation_attachment_defaults()`.
- `FlowValidationAttachmentSelection` and `FlowValidationAttachmentGroup` in
  `backend/src/schemas/flows.py` persist selected attachments and resolved group
  states: `automatic`, `skipped`, `replaced`, and `supplemental`.
- `validation_schedule_from_node_data()` exposes scheduled validators, skipped
  active defaults, inactive metadata, replacements, and supplemental validators
  as runtime metadata for an extraction node.
- `backend/src/lib/flows/executor.py` includes that schedule in the flow prompt
  and explicitly treats it as metadata, not extractor-owned validator calls.
- `frontend/src/components/AgentStudio/FlowBuilder/NodeEditor.tsx` renders active
  attachments as toggleable only when policy allows it, shows replaced and
  supplemental groups, and keeps under-development rows in the metadata-only
  section.

Active default validators are enabled by default. A flow may disable an active
default only when `allow_opt_out` permits it, or replace/supplement validation
through explicit `validation_attachment` edges to validator agent nodes.

## Agent Studio And Curator Surfaces

Agent Studio surfaces the live contract instead of relying on static prompt
memory:

- `domain_envelope_metadata_catalog_by_agent()` in
  `backend/src/lib/agent_studio/domain_envelope_metadata.py` projects object
  definitions, field paths, source-of-truth notes, and validator capability
  metadata.
- `get_domain_pack_validation_plan()` in
  `backend/src/lib/agent_studio/domain_envelope_tools.py` returns validators,
  validator bindings, validation attachments, active automatic counts,
  under-development metadata counts, and validator-agent IDs for prompt
  inspection.
- `backend/src/api/agent_studio.py` attaches validation metadata to catalog and
  Opus tool responses.
- `frontend/src/components/AgentStudio/DomainEnvelopeMetadataPanel.tsx` renders
  default active counts, blocking and opt-out policy, and clearly separates
  under-development capabilities as planning-only metadata.

Curator review and submission use envelope findings rather than extractor retry
contracts:

- `waive_validation_finding()` in
  `backend/src/lib/curation_workspace/session_mutation_service.py` allows a
  waiver only for open findings whose `validation_metadata` includes
  `curator_override.allowed=true`.
- `backend/src/api/curation_workspace.py` exposes the review waiver endpoint for
  envelope findings.
- `backend/src/lib/curation_workspace/session_submission_service.py` computes
  readiness from envelope findings and blocks unresolved active findings unless
  the reviewed waiver or override policy allows the state.
- `frontend/src/features/curation/workspace/EnvelopeObjectReviewTable.tsx`,
  `frontend/src/features/curation/editor/CandidateFieldEditor.tsx`, and
  `frontend/src/features/curation/submission/SubmissionPreviewDialog.tsx`
  surface finding status, waiver/review state, and submission blockers to
  curators.

## Migration History

Earlier migration notes used language from an implementation plan rather than
from the final runtime. That language described extractor-owned validator
invocation, a separate validation supervision lane, correction-oriented retry
contracts, separate justification states for skipped validators, and future
capability states. Those ideas did not become the current contract.

The shipped design consolidated responsibility into domain-pack metadata,
runtime structural checks, active validator dispatch, flow validation
attachments, and envelope findings. Historical references to the older plan
should be read only as background; new implementation work should follow the
current contract above and the implemented code surfaces named in this document.

## Grounding Checklist For Future Changes

When changing validator dispatch behavior, check these surfaces together:

- Metadata schemas: `backend/src/schemas/domain_pack_metadata.py` and
  `backend/src/schemas/domain_validator.py`.
- Registry and matching: `backend/src/lib/domain_packs/validation_registry.py`.
- Structural findings: `backend/src/lib/domain_packs/structural_checks.py`.
- Selector resolution: `backend/src/lib/domain_packs/input_selectors.py`.
- Active dispatch: `backend/src/lib/domain_packs/validator_dispatch.py`.
- Result materialization: `backend/src/lib/domain_packs/materialization.py`.
- Pipeline checkpoint refresh:
  `backend/src/lib/curation_workspace/pipeline.py`.
- Flow attachments: `backend/src/lib/flows/validation_attachments.py` and
  `backend/src/schemas/flows.py`.
- Agent Studio tools and metadata:
  `backend/src/lib/agent_studio/domain_envelope_tools.py`,
  `backend/src/lib/agent_studio/domain_envelope_metadata.py`, and
  `frontend/src/components/AgentStudio/DomainEnvelopeMetadataPanel.tsx`.
- Flow Builder UI:
  `frontend/src/components/AgentStudio/FlowBuilder/NodeEditor.tsx`.
- Curator waiver and readiness:
  `backend/src/lib/curation_workspace/session_mutation_service.py`,
  `backend/src/lib/curation_workspace/session_submission_service.py`, and
  `frontend/src/features/curation/submission/SubmissionPreviewDialog.tsx`.
- Current developer overview:
  `docs/developer/guides/DOMAIN_ENVELOPES.md`.
