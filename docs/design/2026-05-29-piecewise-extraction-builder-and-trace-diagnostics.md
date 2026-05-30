# Piecewise Extraction Builder And Trace Diagnostics

Date: 2026-05-29

Companion documents:

- [`2026-05-29-gene-expression-linkml-extraction-failure-notes.md`](./2026-05-29-gene-expression-linkml-extraction-failure-notes.md)
- [`2026-05-29-ontology-resolution-reference-research.md`](./2026-05-29-ontology-resolution-reference-research.md)

## Summary

The gene-expression extractor now has the right ingredients: verified evidence
tools, field-scoped ontology/CV resolver tools, domain-pack validators, and
TraceReview. The failure is at the final handoff. We are asking the model to
assemble a large `GeneExpressionEnvelope` in one final structured response after
many retrieval, evidence, and ontology operations. The model can do the domain
work and still place evidence IDs, resolver provenance, references, or payload
fields in the wrong envelope location.

The next design should move final envelope assembly out of the model and into
backend code:

1. Keep the model as the reader, selector, and caller of small strict tools.
2. Add domain-pack builder tools that stage small JSON fragments during the run.
3. Let backend code validate, store, patch, and finalize staged candidates.
4. Materialize `GeneExpressionEnvelope` deterministically from builder state.
5. Upgrade TraceReview so a single trace ID produces a clear timeline of model
   turns, reasoning summaries when available, tool calls, tool arguments, tool
   outputs, staged candidates, validation failures, and finalization decisions.

## Why Change

The latest live run proved the resolver workflow can find useful evidence and
terms, but the final output still failed.

Observed run:

- Flow run: `a69eed93-eb0b-40dc-b274-a4c6d1b30708`
- Trace: `86d04d956d368e1380063423d4988136`
- Evidence recorded: `evidence-67598e5688f123c8`

The extractor searched the paper, read the relevant chunk, recorded verified
evidence, called ontology resolver tools, listed evidence, and attached evidence.
Then it generated text containing JSON rather than a valid structured SDK result.
The recovered JSON had these contract breaks:

- `curatable_objects[0].evidence_record_ids` was empty even though
  `metadata.evidence_records[]` contained the recorded evidence.
- Resolver provenance appeared under object metadata instead of top-level
  `metadata.provenance.helper_selections[]`.
- `relation.name` was null despite resolver activity.
- Reference fields included placeholder `PMID:12345678`.
- The evidence gate correctly rejected the output.

The failure mode is not simply "bad retrieval" or "bad ontology lookup." It is a
late assembly failure after useful intermediate work.

## OpenAI Guidance

OpenAI's Structured Outputs guidance says structured outputs are intended to
make model responses adhere to a supplied JSON Schema, while JSON mode only
ensures parseable JSON and does not guarantee schema adherence:

- https://developers.openai.com/api/docs/guides/structured-outputs
- https://developers.openai.com/api/docs/guides/function-calling

The function-calling guidance recommends strict schemas for function tools. In
strict mode, object schemas need `additionalProperties: false`, all properties
listed as required, and nullable unions for optional fields:

- https://developers.openai.com/api/docs/guides/function-calling#strict-mode

The reasoning-model guidance is relevant for diagnostics:

- Raw reasoning tokens are not exposed through the API.
- Reasoning summaries can be requested with `reasoning.summary`, when supported
  by the selected model.
- Reasoning summaries are opt-in. If we want them in TraceReview, the backend
  must request them and persist them as first-class diagnostic events.
- Tool-heavy reasoning workflows should preserve reasoning and function-call
  items across turns when manually managing Responses API state.

Source:

- https://developers.openai.com/api/docs/guides/reasoning

Local schema measurement for `GeneExpressionEnvelope`:

```text
schema size: approximately 20 KB
object properties: 95
object definitions: 20
arrays: 22
anyOf count: 55
raw schema depth: 7
```

This is under current Structured Outputs hard limits, but it is still too large
and nested for reliable one-shot model-authored assembly after a long tool loop.

## Current Touch Points

LSP and repo inspection found the main symbols and files this design touches.

### Backend agent runtime

- [streaming_tools.py](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/backend/src/lib/openai_agents/streaming_tools.py)
  - `_apply_relaxed_output_schema_if_needed`
  - `_pop_matching_pending_tool_call`
  - `SpecialistToolCall`
  - `SpecialistActivity`
  - `add_specialist_event`
  - `run_specialist_with_events`
- [runner.py](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/backend/src/lib/openai_agents/runner.py)
  - streaming `TOOL_START` / `TOOL_COMPLETE` / `AGENT_THINKING`
  - evidence gate after `final_output`
  - `RUN_ERROR` emission for missing evidence records

The current domain-envelope path explicitly relaxes SDK schema conversion:

```python
runtime_agent.output_type = AgentOutputSchema(
    output_type,
    strict_json_schema=False,
)
```

That was likely added because the envelope schemas are not fully strict-schema
friendly. The staged-builder design avoids relying on a strict full envelope as
the model's final output.

### Evidence workspace

- [evidence_workspace.py](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/backend/src/lib/openai_agents/tools/evidence_workspace.py)
  - `set_active_evidence_records`
  - `reset_active_evidence_records`
  - `create_list_recorded_evidence_tool`
  - `create_get_recorded_evidence_tool`
  - `create_attach_evidence_to_object_tool`
  - `create_detach_evidence_from_object_tool`
  - `create_discard_recorded_evidence_tool`
  - `create_update_recorded_evidence_metadata_tool`

The builder should reuse the active evidence workspace rather than inventing a
parallel evidence registry.

### Alliance ontology resolver tools

- [agr_curation.py](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/packages/alliance/python/src/agr_ai_curation_alliance/tools/agr_curation.py)
  - `search_domain_field_terms`
  - `inspect_ontology_term`
  - `resolve_domain_field_term`

These remain the only path for final controlled selector justification. Builder
tools should accept resolver outputs or resolver selection references, not raw
memory-based controlled terms.

### Gene-expression config and conversion

- [agent.yaml](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/packages/alliance/agents/gene_expression/agent.yaml)
- [prompt.yaml](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/packages/alliance/agents/gene_expression/prompt.yaml)
- [domain_pack.yaml](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/packages/alliance/domain_packs/gene_expression/domain_pack.yaml)
- [conversion.py](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/packages/alliance/python/src/agr_ai_curation_alliance/domain_packs/gene_expression/conversion.py)

The prompt should stop asking the model to author the full
`GeneExpressionEnvelope`. The conversion layer should remain authoritative for
domain-pack validation and final normalized payload semantics.

### TraceReview

- [trace_extractor.py](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/trace_review/backend/src/services/trace_extractor.py)
  - `OBSERVATION_FIELDS`
  - `TraceExtractor.get_observations`
  - `TraceExtractor.extract_complete_trace`
- [tool_calls.py](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/trace_review/backend/src/analyzers/tool_calls.py)
  - `ToolResultParser`
  - `ToolCallAnalyzer._extract_tool_outputs`
  - `ToolCallAnalyzer._extract_function_calls_from_generation`
  - `ToolCallAnalyzer.extract_tool_calls`
- [traces.py](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/trace_review/backend/src/api/traces.py)
  - `_build_trace_cache_data`
  - `_tool_call_summary`
  - `_compact_trace_bundle`
  - `analyze_trace`
  - trace export and view endpoints
- [claude.py](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/trace_review/backend/src/api/claude.py)
  - `_ensure_trace_analyzed`
  - `/summary`
  - `/tool_calls/summary`
  - `/tool_calls`
  - `/conversation`
  - `/views/{view_name}`

TraceReview currently reads Langfuse observations and can extract some
OpenAI-style function calls. In the latest local run it returned zero tool calls
for the nested specialist trace even though backend logs showed more than 90
internal tool events. That means our trace analyzer is missing one or more
surfaces used by the current Agents SDK / backend streaming path.

## Proposed Runtime Architecture

### Core idea

Add a run-scoped `ExtractionBuilderWorkspace` that receives small structured
tool calls and owns final envelope construction.

The model should call tools like:

- `stage_gene_expression_observation`
- `patch_gene_expression_observation`
- `discard_gene_expression_observation`
- `list_staged_gene_expression_observations`
- `finalize_gene_expression_extraction`

Each tool has a small strict schema. Each call validates immediately and returns
a compact state update. The backend stores all staged candidates, validation
issues, evidence links, and resolver provenance in the workspace.

At the end, `finalize_gene_expression_extraction` materializes the
`GeneExpressionEnvelope` using backend code, not model-authored JSON.

This design is intentionally a builder intermediary, not a fallback ladder. The
model performs interpretation and selection; backend code performs assembly,
validation, and persistence. If staging or finalization fails, the run should
fail loudly with traceable validation errors rather than silently salvaging a
model-authored envelope.

### Canonical output handoff

Finalization needs an explicit adapter into the existing runner/chat/persistence
path. It is not enough for the finalization tool to return a successful tool
message while the runner continues to validate `result.final_output`.

`finalize_gene_expression_extraction` should:

1. Store a run-scoped canonical payload in the builder workspace.
2. Emit a structured internal event that chat collection and persistence can
   consume.
3. Make the runner's evidence gate and domain-pack validation operate on that
   canonical payload.
4. Treat the model's final text or structured response as an acknowledgement,
   not as the authoritative extraction result.

The handoff adapter should cover the current surfaces that inspect final output:

- runner evidence gating after `final_output`
- specialist `INTERNAL_EXTRACTION_RESULT` handling
- chat event collection
- persistence/export of extraction results

The adapter should be idempotent. A duplicate finalize call with the same
candidate set should return the existing finalized result. A duplicate finalize
call with different candidate membership should fail with a clear validation
error unless the prior finalization has been explicitly reset by the run owner.

No fallback behavior:

- Do not salvage JSON from final assistant text.
- Do not accept a model-authored full `GeneExpressionEnvelope` as a backup path.
- Do not infer controlled values from model memory if resolver provenance is
  missing.
- Do not carry forward unresolved or placeholder references.
- Do not downgrade validation errors into warnings during finalization.

### Builder state

The workspace should track:

- `builder_run_id`
- `document_id`
- `domain_pack_id`
- `agent_id`
- `candidate_id`
- `pending_ref_id`
- staged payload fields
- evidence IDs copied from the active evidence workspace
- evidence attachments by object and field path
- resolver selections copied from `resolve_domain_field_term`
- field-level validation errors
- domain-pack validation errors
- candidate lifecycle: `draft`, `valid`, `needs_patch`, `discarded`, `finalized`

Builder state should be serializable into trace/debug events without requiring
secret or credential access.

Lifecycle should mirror the evidence workspace's run-scoped behavior:

- initialize once per extraction run
- reset on run teardown
- reject mutations after successful finalize
- preserve enough state after validation failure for patch tools to repair it
- handle provider/tool-call retry by making create/update operations idempotent
  against caller-supplied `pending_ref_id` or `candidate_id`
- mark cancellation/abort distinctly from validation failure
- serialize parallel tool mutations through the workspace rather than relying on
  model call order

### Tool contracts

All builder tools should be authored as strict-schema tools:

- every object uses `additionalProperties: false`
- every property is listed in `required`
- optional values are represented as nullable required fields
- patch operations enumerate allowed fields rather than accepting arbitrary
  `Dict[str, Any]`
- lists have bounded item schemas and conservative size limits
- the generated OpenAI Responses tool schema is tested before rollout

#### `stage_gene_expression_observation`

Purpose: create a draft candidate from one paper-supported expression
observation.

Representative input:

```json
{
  "pending_ref_id": "gene-expression-annotation-pef-1",
  "evidence_record_ids": ["evidence-67598e5688f123c8"],
  "where_expressed_statement": "PEF-1::GFP expression in the cilium",
  "subject": {
    "source_phrase": "PEF-1::GFP",
    "gene_symbol": "pef-1",
    "primary_external_id": "WB:..."
  },
  "reference": {
    "source_phrase": "PMID 39550471",
    "reference_id": "PMID:39550471"
  },
  "controlled_fields": [
    {
      "field_path": "relation.name",
      "resolver_call_id": "call_...",
      "selected_value": "is_expressed_in"
    }
  ]
}
```

The exact schema should be smaller and stricter than the final envelope. It
should not expose raw `metadata.provenance` layout to the model.

The builder should require `resolver_call_id` rather than model-authored
resolver objects. The builder copies the validated resolver output from the
tool-call ledger into a canonical helper-selection object. That copied
provenance must include the fields required by the gene-expression converter:

- `field_path`
- `authority`
- `lookup_status`
- `source_phrase`
- `term_source`
- `selected_value`
- resolver tool/call provenance

The model should never be asked to manually place these fields into
`metadata.provenance.helper_selections[]`.

#### `patch_gene_expression_observation`

Purpose: update only known fields on an existing candidate. The tool returns a
before/after diff plus remaining validation issues.

This prevents the current pattern where the model re-records evidence or
re-emits a whole object to fix one field.

Representative strict patch input should be field-oriented rather than a free
map:

```json
{
  "candidate_id": "gex-candidate-1",
  "pending_ref_id": "gene-expression-annotation-pef-1",
  "updates": [
    {
      "field_path": "reference.reference_id",
      "string_value": "PMID:39550471",
      "resolver_call_id": null,
      "evidence_record_ids": null
    }
  ]
}
```

Allowed `field_path` values should be enumerated in code for the initial
gene-expression implementation. Free-form object patching should not be exposed
to the model.

#### `finalize_gene_expression_extraction`

Purpose: request backend materialization of all retained builder candidates.

Input can be very small:

```json
{
  "candidate_ids": ["gex-candidate-1"],
  "allow_incomplete": false
}
```

Output should include:

- `status`
- `finalized_candidate_count`
- `envelope_summary`
- `validation_errors`
- `evidence_record_ids`
- `resolver_selection_count`
- `domain_envelope_preview`

The actual `GeneExpressionEnvelope` is generated by backend code and handed to
the existing persistence/export/validator path.

The canonical handoff type should be the existing domain extraction result shape
that persistence already expects, with the generated `GeneExpressionEnvelope`
stored as its domain payload. The materializer, not the model, owns:

- default values
- data provider values
- generated experiment IDs
- reference normalization and placeholder rejection
- envelope metadata
- copying evidence records into both object-level references and top-level
  metadata where the current converter expects them

## Provenance Rules

The builder should enforce the rules we want the model to follow:

- Evidence records must already exist in the active evidence workspace.
- Staged `evidence_record_ids[]` must reference retained records.
- Finalized objects must have non-empty evidence IDs.
- Controlled selectors must come from `resolve_domain_field_term`.
- `search_domain_field_terms` and `inspect_ontology_term` can support selection
  but cannot justify final values alone.
- Resolver selections are copied by code into
  `metadata.provenance.helper_selections[]`.
- The model cannot manually author provenance layout.
- References must come from document metadata, curation DB/reference lookup, or
  explicit evidence-backed paper identifiers. Placeholder PMIDs are rejected.

## Prompt Changes

The gene-expression prompt should become a tool-loop prompt:

- Read the paper.
- Record evidence for retained curatable observations.
- Resolve controlled selectors.
- Stage one observation candidate per curatable finding.
- Patch until the builder reports no blocking validation errors.
- Call `finalize_gene_expression_extraction`.
- Return a short final acknowledgement or minimal final structured status.

It should not say "Return JSON only, matching `GeneExpressionEnvelope`" for the
main extraction path.

## Output Schema Strategy

Short term:

- Keep a minimal final acknowledgement/status shape only if the runner requires
  a final model output.
- For gene expression, the builder-finalized payload is the only authoritative
  extraction output.
- Treat any model-authored final envelope as an error or ignored diagnostic
  artifact. It must not be parsed, repaired, saved, or used as fallback.

Medium term:

- Add a small strict `BuilderFinalizationStatus` output type if the runner
  requires structured final output.
- Remove the relaxed full-envelope path for domains migrated to builder tools.
- Keep full domain-envelope models as backend schemas and persistence/export
  contracts, not as model-authored final response schemas.

## Trace Diagnostics Design

TraceReview should answer a debugging question in one call:

> Given a trace ID, what exactly did the model do?

The desired diagnostic report should include:

- trace metadata: trace ID, session ID, model, timestamps, duration, tokens,
  output status, error status
- ordered model events: generation, reasoning summary, tool call, tool output,
  final text/structured output
- reasoning summary text when available; never claim raw reasoning is available
- each tool call:
  - index
  - call ID
  - parent observation ID
  - tool name
  - parsed arguments
  - raw arguments
  - output status
  - parsed output summary
  - raw output preview
  - duration
  - matching confidence between call and output
- extraction-specific events:
  - evidence records created
  - evidence attachments
  - resolver searches
  - resolver inspections
  - resolver final selections
  - staged candidates
  - patches
  - validation failures
  - finalization result
- final output analysis:
  - whether final output was a minimal acknowledgement or an unexpected
    model-authored payload
  - schema validation errors
  - missing evidence refs
  - placeholder values
  - misplaced resolver provenance
  - evidence reference report
- direct pointers:
  - which tool call produced the evidence
  - which resolver call justified each controlled field
  - which validation gate failed

### Why current TraceReview missed the live run

The current `ToolCallAnalyzer` mostly expects:

- `function_call` items in Langfuse `GENERATION.output` or `GENERATION.input`
- `function_call_output` items in a later observation input
- old `TOOL` observations

The backend streaming path also emits rich SSE/specialist events through
`add_specialist_event`, including:

- `TOOL_START`
- `TOOL_COMPLETE`
- `SPECIALIST_ERROR`
- `SPECIALIST_SUMMARY`
- internal extraction result events

Those events are visible in app logs/SSE but are not guaranteed to be first-class
Langfuse observations in the shape TraceReview currently parses. The staged
builder should write diagnostic events in one canonical shape that TraceReview
can parse from either Langfuse metadata/output or stored feedback artifacts.

Reasoning summaries are a hard diagnostic requirement for OpenAI
reasoning-model extraction runs. The backend should request the most detailed
supported summary setting available for the configured model, capture the
`reasoning.summary` output item, and write it through the durable trace-event
writer. When a provider or model cannot provide summaries, TraceReview must show
that explicitly and still display the observable decision trail: assistant
messages, tool calls, tool arguments, tool outputs, validation errors, and
finalization events.

### Proposed TraceReview additions

Add a new analyzer:

```text
trace_review/backend/src/analyzers/extraction_timeline.py
```

Add API surfaces:

```text
GET /api/traces/{trace_id}/views/extraction_timeline
GET /api/claude/traces/{trace_id}/extraction_timeline
GET /api/claude/traces/{trace_id}/diagnostic_report
```

The `diagnostic_report` endpoint should be token-aware and concise by default,
with optional drill-down parameters:

```text
?include_raw_args=true
?include_raw_outputs=false
?tool_name=resolve_domain_field_term
?event_type=validation_error
?candidate_id=gex-candidate-1
```

TraceReview should parse these sources in priority order:

1. Structured backend trace events emitted by the builder.
2. OpenAI/Agents SDK function-call observations.
3. Existing `TOOL` observations.
4. Stored feedback trace artifacts.
5. Backend log excerpts, only when explicitly supplied or available through a
   trusted local/prod log fetch path.

The API should not be trace-ID-only in practice. A trace ID should be enough for
the happy path, but the diagnostic endpoints should also accept:

```text
?source=local|remote|auto
?session_id=...
?feedback_id=...
?include_sibling_traces=true
?refresh=true
```

This matters because production debugging often starts from curator feedback,
session IDs, cached local traces, or nested/sibling traces rather than one clean
Langfuse trace. Cached diagnostic reports should include an analyzer schema
version and should be invalidated when the analyzer version changes or when
`refresh=true` is supplied.

### Backend event contract

Emit stable diagnostic events from the builder and resolver path through a
durable trace-event writer. SSE/log events alone are insufficient because
TraceReview reads Langfuse observations, stored trace artifacts, and local cache
records; ephemeral stream messages may be gone by the time someone debugs a
trace.

The writer should enforce:

- schema version
- monotonically increasing per-run sequence number
- `trace_id`, observation/span ID when available, and `tool_call_id`
- event type and domain pack ID
- redaction of secrets and oversized text
- input/output preview size limits
- lossless pointers to full evidence/resolver/builder objects when those are
  stored elsewhere
- graceful degradation when Langfuse is unavailable, with local run artifacts
  still available to TraceReview

Representative event:

```json
{
  "schema_version": "extraction_trace_event.v1",
  "event_type": "extraction_builder.stage",
  "event_id": "evt-...",
  "sequence": 42,
  "trace_id": "...",
  "observation_id": "...",
  "specialist": "Gene Expression Extractor",
  "domain_pack_id": "agr.alliance.gene_expression",
  "candidate_id": "gex-candidate-1",
  "pending_ref_id": "gene-expression-annotation-pef-1",
  "tool_call_id": "call_...",
  "input_summary": {},
  "output_summary": {},
  "validation": {
    "status": "needs_patch",
    "errors": []
  },
  "timestamp": "..."
}
```

The report should be built from these events rather than fragile string parsing.

Reasoning should be reported with explicit availability status:

- `present`: a model-provided reasoning summary was captured
- `not_requested`: the backend did not request summaries for this run
- `not_supported`: the provider/model does not expose summaries
- `unavailable`: summaries were requested but absent from the trace artifacts

Raw hidden chain-of-thought should never be expected or reported as available.

For OpenAI Responses runs, the trace event should store:

- requested reasoning effort
- requested summary setting
- provider/model support status
- reasoning item IDs
- summary text chunks
- encrypted reasoning item presence when present, without trying to decrypt or
  display hidden reasoning tokens

## Implementation Order

1. **Trace diagnostics foundation**
   - Add durable backend trace-event writer used by resolver, evidence, and
     builder paths.
   - Add extraction timeline analyzer and API view.
   - Teach TraceReview to surface reasoning summaries when present in response
     output items.
   - Add tests with synthetic Langfuse observations for nested Agents SDK tool
     calls, specialist events, and builder events.

2. **Builder workspace foundation**
   - Add backend run-scoped builder context and state model.
   - Reuse the active evidence workspace where possible.
   - Add unit tests for state transitions, evidence ID validation, candidate
     patching, and finalization errors.

3. **Gene-expression builder tools**
   - Add Alliance package tools for staging, patching, listing, discarding, and
     finalizing gene-expression observations.
   - Bind them in `packages/alliance/tools/bindings.yaml`.
   - Add targeted unit tests beside the existing Alliance tool tests.

4. **Domain-pack materializer**
   - Convert builder state into `GeneExpressionEnvelope`.
   - Copy evidence records and resolver selections into canonical locations.
   - Run existing gene-expression conversion/validation.
   - Reject placeholder references and unresolved required fields.

5. **Prompt and agent config**
   - Update the gene-expression prompt from final-envelope authoring to builder
     tool use.
   - Remove or disable full-envelope model-authored final output for this path.
   - Configure OpenAI reasoning-model runs to request reasoning summaries where
     supported.

6. **Live regression**
   - Re-run known terms and the reference paper.
   - Capture TraceReview diagnostic report for each run.
   - Compare expected evidence, resolver selections, staged candidates, and final
     domain-pack validation output.

## Test Plan

Targeted backend tests:

```bash
docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/lib/openai_agents/tools/test_alliance_agr_curation_vocabulary_helpers.py -v --tb=short"
docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/lib/openai_agents/tools/test_backend_tool_surface_project_agnostic.py -v --tb=short"
```

New tests to add:

- `backend/tests/unit/lib/openai_agents/tools/test_gene_expression_builder_tools.py`
- `backend/tests/unit/lib/openai_agents/test_extraction_builder_workspace.py`
- `backend/tests/unit/lib/openai_agents/test_builder_finalization_handoff.py`
- `backend/tests/unit/lib/openai_agents/test_extraction_trace_event_writer.py`
- `backend/tests/unit/lib/openai_agents/test_reasoning_summary_trace_capture.py`
- `backend/tests/unit/test_gene_expression_prompt_policy.py`
- `trace_review/backend/tests/test_extraction_timeline_analyzer.py`
- `trace_review/backend/tests/test_trace_review_diagnostic_report.py`

Broader checks after implementation:

```bash
docker compose -f docker-compose.test.yml run --rm backend-unit-tests
cd trace_review/backend && python -m pytest tests -v
```

If TraceReview frontend is touched:

```bash
cd trace_review/frontend && npm run test -- --run
cd trace_review/frontend && npm run build
```

## Open Questions

- Should the builder be implemented as a generic backend service with
  domain-pack adapters, or as an Alliance gene-expression service first?
  Recommendation: generic workspace core, Alliance gene-expression adapter first.
- Should `finalize_gene_expression_extraction` return the full envelope to the
  model, or only a summary while backend persistence receives the full envelope?
  Recommendation: return summary to the model, persist/export full envelope in
  backend code.
- Should resolver selections be passed as whole objects or referenced by a
  resolver call ID? Recommendation: require resolver call IDs and implement the
  resolver call ledger in the same change. Do not add a model-authored
  provenance fallback.
- Should raw backend logs be folded into TraceReview? Recommendation: only as an
  optional diagnostic source. The primary path should be structured trace events.

## Acceptance Criteria

- A gene-expression extraction can complete without the model authoring a full
  `GeneExpressionEnvelope`.
- The builder-finalized payload is the canonical payload used by runner
  validation, evidence gating, chat event collection, and persistence.
- Every finalized `GeneExpressionAnnotation` has non-empty verified
  `evidence_record_ids[]`.
- Every controlled selector in a finalized object has provenance from
  `resolve_domain_field_term`.
- Placeholder references such as `PMID:12345678` are rejected before
  finalization.
- TraceReview can produce a diagnostic report for a trace ID that shows the
  ordered model/tool/builder timeline and identifies the failing gate.
- TraceReview can produce the same report from trace ID plus optional
  `source`, `session_id`, or `feedback_id` when the initial trace is nested or
  cached.
- Builder, evidence, resolver, and reasoning-summary events are durable enough
  for TraceReview after the live SSE stream has ended.
- The diagnostic report explicitly distinguishes unavailable raw reasoning from
  available reasoning summaries.
- For OpenAI reasoning-model runs that support summaries, TraceReview displays
  the captured reasoning summary text and the request settings that produced it.
- There is no legacy/salvage fallback path from final assistant text to a saved
  gene-expression envelope.
- Existing domain-pack validators remain the final authority for LinkML-aligned
  output.
