# Trace e4ed920af7204703bf7f4f5fcae62938 curation UI diagnosis

Temporary diagnosis notes for the local Incus main-sandbox run.

## User-reported issues

- Curation review screen: the PAT-3 gene expression annotation does not show the evidence quote near the top of the object review screen.
- Expected behavior: every annotation object should have a clickable evidence quote linked to the PDF location, and clicking it should highlight the source passage.
- Suggested placement: under the under-development / validation banner area, before the editable field list.
- Remove or replace the `Editable fields` heading line in the curation interface.
- Chat conversation: evidence chips/cards are missing for the extraction turn.
- Audit table/chat logs: specialist error and validator/tool lookup error use different colors. Tool lookup errors should use the same generic error styling as specialist errors.
- Literature validation error was present and should remain visible, but styled consistently as an error.
- Check GitHub history before the latest curation-interface PR to see how projected evidence was previously rendered as a chip in the curation review screen.

## Trace and local session facts

- Trace ID: `e4ed920af7204703bf7f4f5fcae62938`
- Document ID: `22af46c3-ccb1-4a08-8827-bfd3531dcf85`
- Local TraceReview base used: `http://192.168.86.44:8901`
- Trace diagnostic summary:
  - durable events: `1074`
  - tool events: `174`
  - validation failures: `16`
  - finalization events: `4`
  - reasoning summary status: `present`
- Curation workspace session found for the document:
  - session ID: `cf07290b-4994-4ab0-9af1-90872e573736`
  - prepared at: `2026-06-05T14:15:56.610698Z`
  - candidate count: `8`
  - workspace evidence projection count: `16`

## Evidence findings so far

- The extractor did record evidence. The missing quote is not because `record_evidence` failed.
- PAT-3 mechanosensory axon candidate:
  - candidate ID: `880cdfdc-5757-428c-aa46-c88c9bb892b8`
  - display label: `pat-3`
  - adapter key: `gene_expression`
  - object ID: `gene-expression-annotation-pat-3-mech-axon`
  - evidence projection count on candidate: `2`
- PAT-3 evidence records:
  - `evidence-f62fee67450d9b81`: Results evidence, page `9`, quote is present.
  - `evidence-91d9a49f3fabb5fd`: Methods evidence, page `1`, quote is present.
- The final gene-expression builder envelope retained all ten evidence record IDs, including the PAT-3 records.
- The local workspace API returns object-level evidence projections for the PAT-3 candidate, but their `field_path` is `null`.
- Draft fields have empty `evidence_anchor_ids`.

## Likely curation-screen diagnosis

- `CandidateFieldEditor` already renders per-field and per-section evidence buttons, but those paths depend on field-level evidence links.
- The current PAT-3 evidence projections are object-level projections (`field_path: null`), so the existing field/section evidence slots do not render them.
- The page builds `envelopeObjectRows` with `evidenceAnchors`, but `CandidateFieldEditor` currently receives only candidate context and does not receive or render the object-level row evidence near the top.
- Next implementation direction: add an object-level evidence quote/chip block near the top of `CandidateFieldEditor`, using `activeCandidate.evidence_anchor_projections` filtered to object-level projections, or pass the `envelopeObjectRows` evidence anchors through deliberately if that is the older pattern.

## Likely chat evidence-chip diagnosis

- Frontend listens for `evidence_summary` events and then renders `EvidenceCard`.
- Trace diagnostic timeline has `evidence_summary` event count `0`.
- Backend specialist stream collected `live_evidence_records`, but builder-finalized specialist runs skip `_emit_specialist_evidence_summary_or_raise`.
- Chat stream fallback evidence reconstruction appears to rely on `TOOL_COMPLETE` events with enough `tool_input` and `tool_output` to call `build_record_evidence_summary_record`.
- Specialist-internal `TOOL_COMPLETE` events include `internal.tool_output` but, from the code inspected, do not include the original `tool_input`; that can prevent fallback evidence summaries from being built.
- Next implementation direction: ensure builder-finalized specialist runs still emit an `evidence_summary` event from `live_evidence_records`, or include enough tool input in specialist-internal events for the existing fallback to work.

## Likely audit color diagnosis

- `frontend/src/utils/auditHelpers.ts` marks event types containing `ERROR` as `error`.
- `TOOL_COMPLETE` events with `success === false`, `error`, or a friendly name containing `failed` currently map to `warning`, not `error`.
- The validator lookup failure is likely a `TOOL_COMPLETE` failure, so it gets warning styling while `SPECIALIST_ERROR` gets error styling.
- Next implementation direction: classify failed `TOOL_COMPLETE` events as `error` when they represent tool/validator lookup errors, or make all `success === false` tool completions use error styling if that matches product intent.

## Validation error observed

- Active validator dispatch completed with unresolved validation and failed because source reference validation could not run.
- Specific reported message: literature reference search unavailable because Elasticsearch configuration is missing or incomplete.
- This should be surfaced as a real validation/tool error, not hidden.

## Next checks

- Done: inspected Git history before PR `#448`; the older curation review screen used the shared `EvidenceNavigationQuoteCard` from the evidence module.
- Done: added object-level evidence rendering to `CandidateFieldEditor` for `field_path: null` evidence projections.
- Done: replaced the `Editable fields` heading with the active candidate title.
- Done: changed failed `TOOL_COMPLETE` audit events to error severity so validator/tool lookup failures match specialist error styling.
- Done: changed builder-finalized specialist runs to emit `evidence_summary` from recorded live evidence records.
- Done: added regression tests for:
  - object-level evidence projections render near the top of the candidate editor and dispatch PDF navigation on click;
  - `evidence_summary` is emitted for builder-finalized specialist extraction evidence;
  - failed validator/tool lookup completion uses error severity styling.

## Validation run

- `cd frontend && npm run test:symphony -- CandidateFieldEditor.test.tsx auditHelpers.test.ts`: `77 passed`.
- `docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/lib/openai_agents/test_builder_finalization_handoff.py -q"`: `12 passed`.
- `docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/lib/openai_agents/test_tool_event_friendly_name_contract.py -q"`: `23 passed`.
- `docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/api/test_chat_stream_endpoint.py -q"`: `22 passed`.
- `cd frontend && npm run type-check:changed -- --base origin/main`: `FRONTEND_TYPECHECK_STATUS=baseline_only`; changed files passed, existing baseline TypeScript errors remain outside these files.
- `scripts/utilities/agent_lsp.py diagnostics ...`: Ruff passed; frontend changed-file type check passed; Pyright still reports pre-existing `streaming_tools.py` complexity and missing `agents.items` import diagnostics.
- `PYTHONPYCACHEPREFIX=/tmp/agr-ai-curation-pycache python3 -m py_compile backend/src/lib/openai_agents/streaming_tools.py backend/tests/unit/lib/openai_agents/test_builder_finalization_handoff.py`: passed.
- `cd frontend && npm run build`: passed.

## 2026-06-05 follow-up: selector fields showing AI Unconfirmed

- Current main-sandbox logs were captured before any restart at:
  `/home/ctabone/.symphony/diagnostics/agr_ai_curation/main-sandbox/20260605T153241Z`.
- Live workspace session inspected:
  - session ID: `593b7992-3dbb-45a7-8106-711de19685e8`
  - candidate count: `12`
  - inspected PAT-3 candidate ID: `509184dc-1d37-4bf9-8780-89a21c3f1109`
- The validator did resolve the ontology terms:
  - `expression_experiment.expression_assay_used` resolved `MMO:0000686`.
  - `expression_pattern.where_expressed.anatomical_structure` resolved `WBbt:0008431`.
  - `expression_pattern.where_expressed.cellular_component` resolved `GO:0030424`.
- The visible editor fields are scalar selector leaves:
  - `expression_experiment.expression_assay_used.curie`
  - `expression_pattern.where_expressed.anatomical_structure.curie`
  - `expression_pattern.where_expressed.cellular_component.curie`
- The curation editor was matching validation summaries only by exact field path, so the resolved
  parent selector summaries did not attach to the `.curie` rows and those rows showed the empty
  `AI Unconfirmed` icon.
- The backend validation snapshot path had the same exact-path issue, producing `skipped` field
  results and the warning `No envelope validation findings targeted this field...` even when the
  parent selector was resolved.
- Implemented:
  - frontend field path candidates now include `source_field_path`, `materializes_to_field_paths`,
    and selector parent paths for `.curie`/`.name` leaves;
  - backend `domain_envelope_field_validation_results()` now accepts field aliases and uses the
    same selector-parent aliasing;
  - pipeline and explicit candidate validation now pass those aliases and refresh parent selector
    validation findings when a leaf field is validated.
- Follow-up validation run:
  - `cd frontend && npm run test:symphony -- CandidateFieldEditor.test.tsx fieldState.test.ts`: `17 passed`.
  - `docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/lib/curation_workspace/test_domain_envelope_projections.py -q"`: `9 passed`.
  - `docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/lib/curation_workspace/test_pipeline.py -q"`: `16 passed`.
  - `docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/lib/curation_workspace/test_session_service.py -q"`: `70 passed`.
  - `PYTHONPYCACHEPREFIX=/tmp/agr-ai-curation-pycache python3 -m py_compile backend/src/lib/curation_workspace/validation_runtime.py backend/src/lib/curation_workspace/pipeline.py backend/src/lib/curation_workspace/session_validation_service.py`: passed.
  - `cd frontend && npm run type-check:changed -- --base origin/main`: `FRONTEND_TYPECHECK_STATUS=baseline_only`; changed TypeScript files passed, existing unrelated baseline TypeScript errors remain.

## 2026-06-05 follow-up: literature lookup config

- The curation and literature Postgres tunnels were both reachable from the backend container.
- The failing source-reference validator was not a Postgres tunnel failure; the package-owned
  literature reference lookup uses the Alliance literature Elasticsearch/OpenSearch index.
- The current backend container had `ELASTICSEARCH_HOST` present but empty, while
  `ELASTICSEARCH_PORT`, `ELASTICSEARCH_INDEX`, `LITERATURE_DB_URL`, and `CURATION_DB_URL` were
  non-empty.
- The VM private env file has been updated with non-empty `ELASTICSEARCH_HOST`,
  `ELASTICSEARCH_SCHEME=https`, `ELASTICSEARCH_PORT=443`, and
  `ELASTICSEARCH_INDEX=references_index` for the main sandbox runtime.
