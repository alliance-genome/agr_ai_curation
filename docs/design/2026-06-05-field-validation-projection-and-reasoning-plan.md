# Field Validation Projection And Reasoning Plan

Date: 2026-06-05

## Why This Exists

Gene expression is currently the best example of the new domain-envelope review
model. Its validator bindings target concrete envelope field paths, validator
results become field-scoped validation findings, and the curation editor can show
field indicators from `validation_summary_projections` without reading legacy
field validation snapshots.

Gene extraction exposed a mismatch in that contract. The validator did resolve
the gene identity, and the resolved values were patched into the envelope
payload, but the validator finding was object-scoped. Because the curation editor
only treats field-scoped validation summaries as evidence for a field indicator,
the gene ID, symbol, and taxon fields stayed visually "AI unconfirmed" even
though the object was validated.

This document captures the research and a forward-only plan to align gene
extraction, allele, disease, phenotype, and future packs with the gene-expression
pattern.

## Current Source Of Truth

The curation field indicator logic is already intentionally strict:

- `frontend/src/features/curation/editor/fieldState.ts` returns `resolved` only
  when a field has one or more field-level validation summaries and every summary
  is resolved or waived.
- `frontend/src/features/curation/editor/CandidateFieldEditor.tsx` filters
  `candidate.validation_summary_projections` by `field_path` and object id.
- The editor does not use `field.validation_result` for the indicator. That is
  good. Falling back to `field.validation_result` would revive legacy state and
  hide projection bugs.

The backend projection pipeline is also clear:

- `backend/src/lib/domain_packs/materialization.py` creates
  `ValidationFinding` records from validator results.
- `_match_field_ref()` only returns a `FieldRef` when the validator match has a
  concrete `field_definition`.
- `project_validation_summary_projections()` groups findings by
  `(object_id, field_path)`.
- Therefore an object-scoped validator result can patch object payload fields,
  but it cannot currently light up those fields in the review UI unless the
  finding also gets field refs.

## Domain Pack Inventory

### Gene Expression: Ideal Pattern

File: `packages/alliance/domain_packs/gene_expression/domain_pack.yaml`

Gene expression active validators mostly target real field paths:

- `subject_gene_validation` targets
  `expression_annotation_subject.primary_external_id` and
  `expression_annotation_subject.gene_symbol`.
- `expression_assay_ontology_validation` targets
  `expression_experiment.expression_assay_used`.
- `expression_stage_ontology_validation` targets `when_expressed_stage_name`
  while materializing the resolved CURIE and name into
  `expression_pattern.when_expressed.developmental_stage_start.*`.
- anatomy, cellular component, relation, data provider, reference, and condition
  validators are all field-addressed.

This is why gene expression can show many resolved field indicators. Each
validator result arrives with enough target-field identity to become field-level
summary projections.

One caveat: some gene-expression fields are materialized mirrors. If the binding
targets a selector field and writes to sibling payload fields, every
curator-visible field that was confirmed should receive a summary projection.
The pack already helps with `materializes_to_field_paths`; the materializer
should make that metadata useful for validation explanations too.

### Gene: Main Mismatch

File: `packages/alliance/domain_packs/gene/domain_pack.yaml`

The active gene binding is object-scoped:

- Binding: `alliance_gene_reference_lookup`
- Applies to: object type `gene_mention_evidence`
- Expected result fields:
  - `curie -> primary_external_id`
  - `symbol -> gene_symbol`
  - `taxon -> taxon`

The object fields also declare validator metadata:

- `primary_external_id` has `validation_result_field: curie`
- `gene_symbol` has `validation_result_field: symbol`
- `taxon` has `validation_result_field: taxon`

That means the metadata knows exactly which fields were materialized by the
validator. The missing piece is projection materialization: the one validator
result should produce field-scoped findings for the three materialized fields,
not just one object-scoped finding.

The fix should not make the frontend read `field.validation_result`. The fix
should make gene validation produce the same kind of field-level summary
projections gene expression produces.

### Allele: Likely Same Class, Slightly Different Shape

File: `packages/alliance/domain_packs/allele/domain_pack.yaml`

Allele is closer to gene expression because the active binding declares a field
path, but the field path is the source mention:

- Binding: `allele_mention_reference_validation`
- Applies to object type `AlleleMention`
- Field path: `mention.text`
- Expected result fields:
  - `curie -> allele.primary_external_id`
  - `symbol -> allele.allele_symbol`
  - `taxon -> allele.taxon`

The curator-visible validated-reference fields also carry
`validation_result_binding_id` and `validation_result_field` metadata. Depending
on the review row being rendered, the current finding may light up the mention
field while leaving the materialized allele identity fields without field-level
summaries.

Allele should be handled by the same generic result-field fan-out as gene. The
fan-out should not depend on whether the original match was object-scoped or
field-scoped; it should project validation evidence to every expected-result
field that was resolved or explicitly missing.

### Disease: Mostly Aligned

File: `packages/alliance/domain_packs/disease/domain_pack.yaml`

Disease active validators are already field-path driven:

- Disease term lookup targets `disease_annotation_object.curie` and `.name`.
- Relation, condition relation, data provider, subject, evidence code,
  annotation type, genetic sex, qualifier, with/from gene, and experimental
  condition bindings all declare field paths.

Disease therefore broadly matches the gene-expression design. It may still
benefit from result-field fan-out for bindings where one selector validates and
materializes several sibling fields, such as vocabulary term name plus
vocabulary/id, or subject identifier plus subject label/type. That is an
enhancement for consistency, not the same root bug seen in gene.

The disease reference binding remains under development by design because durable
reference identity is not available at chat-extraction time. This document does
not propose changing that.

### Phenotype: Mixed, With Under-Development Identity Work

File: `packages/alliance/domain_packs/phenotype/domain_pack.yaml`

Phenotype has an active phenotype-term ontology validator that applies to
`PhenotypeTerm` at object grain, with expected result fields:

- `curie -> curie`
- `label -> label`

That is the same class of issue as gene, only simpler: the validator resolves
the term object, but field indicators for `curie` and `label` need field-level
summary projections.

The condition relation and experimental condition validators are field-path
driven like gene expression. Subject and reference validators are still under
development, and should be revisited after the projection fan-out exists.

## Why Gene Extraction Showed No Verified Circles

The latest local run showed the gene candidate payloads had validated values.
The legacy field snapshots also had `validated` statuses. But the domain
envelope contained object-level validation findings only:

- `field_path = null`
- `status = resolved`
- `object_type = gene_mention_evidence`

That produces an object-level validation summary, which the editor can show near
object status, but it does not satisfy `validationSummariesForField()`. The
empty circles are therefore a projection-grain bug, not evidence that the
validator failed.

The green box seen during the chat trace is a different surface: an audit-stream
event such as `AGENT_GENERATING` / "Agent reasoning" or a success/processing
event. It is not currently the field-level validator explanation.

## Migration Interpretation And Legacy Cleanup

Gene extraction is not completely unmigrated. It already uses a domain envelope,
has `semantic_source: domain_envelope.objects`, rejects legacy semantic lists in
fixtures, and stages a `gene_mention_evidence` object with a package-owned active
validator binding. The incomplete part is narrower: gene validation still behaves
like an object-grain validated-reference lookup, while the newer curation review
UI expects field-grain validation projections.

So the issue is best described as "gene extraction was migrated to domain
envelopes, but not fully migrated to the gene-expression-style field projection
contract."

There is legacy validation machinery still present:

- `CurationDraftField.validation_result`
- `validation_snapshots.field_results`
- deterministic field-validation status plumbing in
  `backend/src/lib/curation_workspace/validation_runtime.py`
- session/submission services that still read those snapshots for workflow-level
  validation state

That machinery should not be used to make the new field indicators green. The
field indicators should remain driven by `validation_summary_projections`.

Recommended cleanup direction:

- Do not add frontend fallback reads from `field.validation_result`.
- Treat `validation_result_field` and `validation_result_binding_id` metadata as
  migration hints for backend field-finding fan-out, not as UI state.
- After field-level validation projections cover active domain-envelope packs,
  audit whether draft-level `validation_result` needs to stay in the curation
  editor payload at all.
- Keep `validation_snapshots` only where they still serve submission/session
  workflow needs, then plan a separate removal or narrowing pass once submission
  readiness can also rely on envelope findings.
- Remove stale gene fixture patterns where a "tool verified" fixture only marks
  one field or object even though the validator confirms multiple materialized
  fields.

## Recommended Backend Design

Add generic validator-result fan-out during domain-envelope materialization.

When a validator result has `expected_result_fields`, the materializer should
create field-scoped validation findings for each materialized field path that the
binding expected to resolve.

The existing object-level finding can either remain as a compact object summary
or be suppressed when every expected result maps to fields. The safer first
implementation is:

1. Preserve the existing object or source-field finding for traceability.
2. Add field-level child findings for expected-result fields.
3. Mark the child findings with details that identify the source validator
   result so the UI can group duplicate explanations.

Suggested child-finding detail keys:

- `validation_metadata.parent_request_id`
- `validation_metadata.materialized_result_field`
- `validation_metadata.materialized_field_path`
- `validation_metadata.generated_from_expected_result_field = true`
- Existing `validation_request`, `validation_result`, `lookup_attempts`,
  `candidate_matches`, and `curator_message`

Resolution rules:

- If the validator result is `resolved` and the result field has a non-empty
  value, create a resolved field finding.
- If the result is `resolved` but a required expected field is listed in
  `missing_expected_fields`, create an open warning/blocker field finding for
  that field.
- If the result is unresolved or errored and an expected field can be mapped,
  create open field findings so the affected fields show "Needs review".
- If an expected result path cannot be mapped to a declared field, keep the
  existing object-level finding and include a materialization warning.

Field path mapping should reuse the existing materialization helpers:

- `_materialized_field_path()` already maps expected-result paths to declared
  fields.
- `_propagate_materialized_mirror_paths()` already knows about
  `materializes_to_field_paths`; the fan-out should also produce field findings
  for mirror paths when those fields are curator-visible.
- For multivalued fields, preserve the indexed path produced by validator
  fan-out where available, so field summaries can attach to the specific list
  element.

This keeps the frontend simple and keeps domain packs declarative.

## Recommended Domain Pack Cleanup

The fan-out makes the current packs work better without a large YAML rewrite.
Still, each active validator binding should be reviewed against the
gene-expression rule:

Every field the curator sees as AI-confirmed should be reachable from one of:

- the validator binding `applies_to.field_paths`;
- `expected_result_fields` mapping to a declared field;
- field metadata `materializes_to_field_paths` for mirrored values.

Pack-specific work:

- Gene: keep the object-scoped lookup if that is the natural validator grain,
  but require field-level projections for `primary_external_id`, `gene_symbol`,
  and `taxon`.
- Allele: ensure the allele validated-reference fields receive projections from
  `allele_mention_reference_validation`, not only the source mention field.
- Phenotype: ensure `PhenotypeTerm.curie` and `PhenotypeTerm.label` receive
  projections from `phenotype_term_ontology_validator`.
- Disease: verify field-level projections for sibling materialized fields,
  especially subject, vocabulary id/vocabulary, and condition subfields.
- Gene expression: use as the regression oracle; no fallback behavior should be
  introduced that weakens it.

## Validation Reasoning / Green Box Design

The validator result contract already has the right high-level explanation
fields:

- `curator_message`: concise curator-facing result message.
- `explanation`: validator decision explanation.
- `lookup_attempts[].message`: provider or lookup-attempt note.
- `candidates`: ambiguous or alternate matches.
- `resolved_values` / `missing_expected_fields`: structured decision outcome.

Those are persisted in `ValidationFinding.details.validation_result` and are
projected through `DomainEnvelopeValidationFindingProjection.details`.

So the recommended design is not to add a free-form `reasoning` field to every
curation payload object. That would mix validation UI metadata into exportable
domain data and would create another field that curators might think is part of
the annotation.

Instead, add a curation-review UI component that surfaces validation reasoning
from field summary projections.

### Required Design Skills For This UI Addition

Any agent implementing or substantially revising the field-level validation
explanation UI must explicitly trigger and apply these skills before designing
the component:

- `$redesign-existing-projects`
- `$high-end-visual-design`

The skills should be used as a focused design review pass for this existing
curation editor, not as permission to rewrite the app shell or replace the
project's stack. The implementation should first scan the current MUI-based
field editor patterns, then design the validation explanation treatment so it
feels native to the current curation screen while avoiding generic AI UI
patterns.

Skill-specific requirements for this addition:

- Preserve the existing screen architecture and MUI component system.
- Use the available right-side field-row space intentionally; avoid turning every
  validated field into a bulky stacked card.
- Make the success explanation feel premium but quiet: restrained green text,
  tuned opacity, good line-height, and a subtle details affordance.
- Add thoughtful hover, focus, and expanded states for the explanation details.
- Avoid loud gradients, generic card shadows, ornamental blobs, or a landing-page
  aesthetic inside the operational curation interface.
- Keep the implementation responsive: on narrow screens the explanation may wrap
  below the field or collapse behind a details button, but it must not overlap
  the editable value, validation icon, or evidence controls.
- Use only GPU-safe motion (`transform` and `opacity`) for any micro-interaction.
- Verify the final UI visually against dense real curation rows, long ontology
  labels, long CURIEs, and warning/error states.

Suggested UI behavior:

- For a resolved field, show a compact green validation explanation panel or
  expandable row near the field.
- Prefer the horizontal space to the right of the editable field when available.
  The field row already has room near the validation slot, and a short green
  explanation there keeps the trust signal visually attached to the value without
  making every validated row taller.
- Keep the always-visible copy terse: one line, green success text, ellipsized if
  needed. Use the tooltip or expandable details affordance for the full validator
  explanation and lookup diagnostics.
- Prefer `curator_message` for the visible first line.
- Expand to show:
  - validator display name;
  - resolved value(s);
  - lookup provider/method;
  - lookup query summary;
  - candidate count / resolved id;
  - `explanation` as high-level rationale.
- For fields validated by the same request, group or deduplicate the explanation
  so `primary_external_id`, `gene_symbol`, and `taxon` can say they came from the
  same successful gene lookup.
- For unresolved/error fields, use the same component shape with warning/error
  styling and the failure classification.

Important wording: this should be called "validation explanation" or
"validation details", not "reasoning", in curator-facing UI. We should not expose
or imply hidden chain-of-thought. The stored `explanation` is a high-level
validator decision summary, which is appropriate to show.

## Why This Helps Weaker Validators

The validator finalization work already makes validators more agentic-friendly by
requiring tool-mediated final output and allowing repair loops. Field-level
projection fan-out helps the next stage:

- A weaker validator can resolve one coherent object-level lookup.
- The backend can deterministically project that decision to the fields it
  materialized.
- The UI can show exactly which fields were confirmed and which fields still need
  curator review.
- Future repair prompts can point at precise failed fields rather than a vague
  object-level warning.

This is a better self-healing boundary than asking each extractor to maintain
legacy field validation snapshots.

## Implementation Plan

1. Add backend fan-out for validator result findings.
   - Extend `materialize_validator_results_into_envelope()` or the helper it
     calls so every validator result can append field-scoped findings for mapped
     `expected_result_fields`.
   - Preserve source request/result details on every generated field finding.
   - Add grouping metadata so duplicate explanations can be collapsed.

2. Add focused tests for projection grain.
   - Gene object-scoped validator result produces field summaries for
     `primary_external_id`, `gene_symbol`, and `taxon`.
   - Phenotype object-scoped term validator result produces field summaries for
     `curie` and `label`.
   - Gene expression still produces its existing field summaries.
   - Object-level findings without mappable expected fields remain object-level.

3. Review active domain packs against the field-projection rule.
   - Gene and phenotype are expected to change behavior via backend fan-out.
   - Allele should be checked with a fixture because the active binding targets
     mention text while expected fields target validated-reference fields.
   - Disease should get regression coverage for sibling materialized fields.

4. Add frontend validation explanation rendering.
   - Build a small helper that extracts a curator-facing explanation from
     `DomainEnvelopeValidationFindingProjection.details`.
   - Render it in `FieldValidationSlot` or a nearby field-detail component.
   - Deduplicate by `request_id` when multiple fields share one validator result.

5. Keep legacy paths out.
   - Do not make field indicators rely on `CurationDraftField.validation_result`.
   - Do not add exportable domain payload fields named `reasoning`.
   - Do not add domain-pack-specific frontend hacks for gene.

## Acceptance Criteria

- Gene extraction review rows show resolved indicators for validator-materialized
  gene ID, symbol, and taxon fields when the gene validator resolves.
- Phenotype term fields can show resolved indicators from object-grain ontology
  validation.
- Allele validated-reference fields receive field-level validation summaries from
  allele lookup results.
- Gene expression behavior does not regress.
- Field indicators remain driven by `validation_summary_projections`.
- Validation explanations can be shown for gene expression and other domains
  using existing finding details.
- No new legacy fallback reads are introduced in the curation editor.

## Open Questions

- Should the original object-level finding remain visible after all expected
  fields are fanned out? Keeping it is safer for traceability, but the UI may
  need grouping to avoid noisy duplicate messages.
- Should field-level child findings be stored as independent envelope findings,
  or should projection code synthesize them at read time? Persisting them makes
  audit and review state durable; synthesizing them reduces stored duplication.
  The current recommendation is to persist them because curator overrides and
  future repair loops need stable targets.
- For allele, should the materialized validated-reference object be the primary
  review row, or should the source mention remain the primary row with resolved
  fields shown inline? That is a review-row design choice, but either way the
  field-level projection mechanism should work.
- How much of `lookup_attempts` should be visible by default? A concise first
  line plus expandable details is probably right.

## Related Existing Note

See also
`docs/design/2026-06-05-agentic-finalization-self-healing-inventory.md` for the
broader inventory of agentic finalization and self-healing opportunities.
