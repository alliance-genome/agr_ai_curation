# Flow Output Projection and Context Lookup

Date: 2026-06-06
Status: Design and implementation tracking; revised after read-only `gpt-5.5` high review
Scope: follow-up lookup support for review sessions/files, then deterministic agent-guided CSV/TSV/JSON/chat output from flow artifacts

Implementation note: the "Current Code Map" section below records the
pre-implementation baseline that motivated this design. Branch changes should
be evaluated against the goals, non-goals, and acceptance criteria later in this
document.

## Problem

Two related gaps showed up after the flow-memory and terminal-output work:

1. Flow memory now records useful refs, but `inspect_curation_context` only knows how to inspect extraction-result-shaped context. The memory text says the supervisor can use review session and file refs, but the tool cannot actually resolve those refs yet.
2. TSV flow output is deterministic because the executor bypasses the terminal formatter model and saves rows from completed structured artifacts. CSV, JSON, and chat output still go through a formatter agent with only a compact step query, so those terminal steps are not reliably artifact-driven.

The desired end state is bigger than just "make CSV match TSV." Curators should be able to say things like:

- "Download this as CSV, but call the first column Gene Symbol."
- "Skip the evidence quote column."
- "Only include validated rows."
- "Put FlyBase IDs before symbols."
- "For JSON, group the objects by adapter."
- "Show me a chat table with just gene symbol, ID, and status."

The model can guide those choices, but the runtime should own the data extraction, filtering, serialization, file save, and chat rendering.

## Current Code Map

LSP/source pass checked:

- `backend/src/lib/openai_agents/supervisor_context_tools.py`
  - `inspect_curation_context` accepts `scope`, `detail`, `extraction_result_id`, `trace_id`, `flow_run_id`, `adapter_keys`, `object_ref`, `field_path`, `limit`, and `cursor`.
  - It supports extraction scopes only: `current_chat`, `current_turn`, `current_document`, `flow_run`, and `extraction_result`.
  - It already has the right bounded response helpers: `_tool_response`, `_bounded_json`, `_offset_page`, object/evidence/validation/field detail helpers.
- `backend/src/lib/openai_agents/agents/supervisor_agent.py`
  - Wraps `inspect_curation_context` as a supervisor tool. The wrapper signature and tool description must grow with the backend helper.
  - Also exposes `export_to_file`, which directly accepts model-authored JSON data for ordinary chat exports. That is separate from flow terminal output.
- `backend/src/api/chat_execute_flow.py`
  - `_build_flow_memory_assistant_message` emits extraction result refs, review session refs, and file refs.
  - It currently tells the supervisor to use `inspect_curation_context` with "review session, or file refs above," which is aspirational until lookup support lands.
- `backend/src/lib/curation_workspace/session_queries.py`
  - `get_session_detail` and `get_session_workspace` load review sessions by ID.
  - These helpers return the session summary/detail, candidates, evidence anchor projections, validation summary projections, action log, and submission history.
  - The read endpoint currently does not pass `current_user_id`, so the supervisor lookup design should add its own authorization check rather than copy the current detail endpoint behavior blindly.
- `backend/src/api/files.py`
  - `get_file_metadata` and `download_file` already enforce `FileOutput.curator_id == current_user`.
  - `list_session_files` lists user-owned files for a session and rejects sessions containing files from other curators.
- `backend/src/lib/flows/executor.py`
  - `_build_flow_artifact_tsv_rows` and `_try_save_tsv_formatter_flow_output` are the deterministic TSV path.
  - `_wrap_with_step_order` calls that TSV path first. If it does not apply, it invokes the specialist formatter with `json.dumps({"query": resolved_query})`.
  - That fallback is why CSV/JSON/chat-output terminal steps are still partly prompt-memory-driven instead of artifact-driven.
- `packages/alliance/agents/{csv_formatter,json_formatter,tsv_formatter,chat_output}/`
  - CSV/TSV/JSON formatter prompts instruct the model to call `save_*_file` with `data_json`.
  - `chat_output` has no tools.
  - The flow implementation should avoid relying on those save tools for terminal flow output; the runtime should apply a projection plan and save/render deterministically.

## Design Goals

- Make review-session and file refs first-class lookup targets for follow-up chat.
- Keep all lookup responses bounded, paginated, and authorized.
- Make CSV, TSV, JSON, and chat-output terminal steps deterministic over completed flow artifacts.
- Let the model guide output shape through a validated manipulation API: choose row sources, columns/keys, labels, order, filters, sort order, grouping, and a small set of safe derived fields.
- Keep terminal output agents from directly writing raw CSV/TSV/JSON payloads in flow mode.
- Preserve current TSV behavior as a compatibility baseline unless a projection plan explicitly asks for a richer row source.
- Avoid hardcoded model names in runtime code. New agent/planner behavior should use agent config/provider defaults.

## Non-Goals

- No arbitrary Python, SQL, JavaScript, spreadsheet formulas, regex engines, or model-authored executable transformations.
- No unbounded file-content reads through `inspect_curation_context`.
- No model-generated file contents for flow terminal outputs.
- No refactor of ordinary supervisor `export_to_file` unless a later ticket chooses to move chat exports onto the projection service too.
- No arbitrary grouping expressions or free-form transformation code. Grouping and derived fields must still be expressed through validated plan specs.

## Part 1: Review Session and File Lookup

### API Shape

Extend `inspect_curation_context` rather than create a parallel tool:

```python
async def inspect_curation_context(
    *,
    scope: str = "current_chat",
    detail: str = "inventory",
    extraction_result_id: str | None = None,
    trace_id: str | None = None,
    flow_run_id: str | None = None,
    review_session_id: str | None = None,
    file_id: str | None = None,
    adapter_keys: list[str] | None = None,
    object_ref: str | None = None,
    field_path: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> str:
```

New scopes:

- `review_session`: requires `review_session_id`.
- `file`: requires `file_id`.
- `session_files`: requires `review_session_id` or defaults to the active main chat session ID for ordinary file outputs.

Existing scopes and details remain compatible.

### Review Session Details

For `scope="review_session"`:

- `detail="inventory"`: compact session ref, status, adapter key, document id/name, `flow_run_id`, candidate counts, validation state, timestamps, and available detail types.
- `detail="summary"`: inventory fields plus extraction result refs, current candidate ref, tags, notes preview, warning count, submission status, and file refs if any user-owned files are attached to the same review session.
- `detail="candidates"` or `detail="objects"`: paginated compact candidates. Include candidate id, envelope id, object id, object type, status, adapter key, selected scalar fields, and counts for evidence/validation findings. Use `object_ref` to match candidate id, envelope id, object id, pending ref id, or object type.
- `detail="evidence"`: paginated evidence anchor projections or compact evidence records associated with matching candidates.
- `detail="validation_findings"`: paginated validation summary projections/findings associated with matching candidates.
- `detail="field"`: bounded field lookup by `field_path`, scoped to the session summary or selected candidate. If `object_ref` is present, resolve against candidate/object first.

Return shape should mirror the current tool:

```json
{
  "status": "ok",
  "scope": "review_session",
  "detail": "candidates",
  "refs": [{"review_session_id": "...", "flow_run_id": "..."}],
  "results": [...],
  "total_count": 12,
  "truncated": true,
  "next_cursor": "5"
}
```

### File Details

For `scope="file"`:

- `detail="metadata"` or `detail="inventory"`: `FileOutputResponse`-style metadata plus agent name, model, trace id, session id, file type, size, created/download timestamps, and download URL.
- `detail="schema"`:
  - CSV/TSV: delimiter, headers, row count estimate when cheap, first N row previews.
  - JSON: top-level type, object keys, array item count when cheap, item key union for first N objects.
- `detail="preview"`: bounded content preview.
  - CSV/TSV: first N rows parsed via `csv` with the right delimiter.
  - JSON: bounded parsed JSON using `_bounded_json`.
  - Raw text fallback only for known safe text file types.
- `detail="field"`: JSON field path lookup for JSON files only, with bounded output.

The lookup should never return a full file. The download endpoint remains the full-file path.

### Authorization

Review session lookup should be stricter and explicit:

- Require active `get_current_user_id()`.
- Allow when `CurationReviewSession.created_by_id == user_id`.
- Allow when `CurationReviewSession.assigned_curator_id == user_id`.
- Allow legacy/unassigned sessions only when at least one linked extraction result belongs to the active user/chat context: candidate -> extraction result with `user_id == current_user_id` and either `origin_session_id == get_current_session_id()` or matching authorized `flow_run_id`.
- Return `unauthorized_context` rather than leaking whether a hidden review session exists when the user has no linkage.

The implementation should add a concrete helper before calling `get_session_detail` or `get_session_workspace`, because those helpers currently load by ID without user scoping:

```python
def authorize_review_session_for_context(
    db: Session,
    *,
    review_session_id: str | UUID,
    user_id: str,
    current_chat_session_id: str | None,
    flow_run_id: str | None = None,
) -> CurationReviewSession | None:
    ...
```

The helper should:

1. Normalize the session UUID.
2. Load the `CurationReviewSession` row with only the relationships needed for authorization.
3. Return it if `created_by_id` or `assigned_curator_id` matches `user_id`.
4. Otherwise, check for a linked `CurationCandidate -> CurationExtractionResultRecord` where `ExtractionResultRecord.user_id == user_id` and either `origin_session_id == current_chat_session_id` or `flow_run_id == flow_run_id`.
5. Return `None` for both missing and unauthorized sessions at the tool-response layer, unless a separate debug-only path is explicitly added later.

That same helper should be considered for the curation workspace read endpoint as a follow-up; otherwise the tool will be safer than the API endpoint it summarizes.

File lookup should mirror `backend/src/api/files.py`:

- Require `FileOutput.curator_id == current_user_id`.
- Check `file_path` resolves under `FileOutputStorageService.base_path` before previewing.
- For `session_files`, preserve the current `list_session_files` mixed-curator policy unless we intentionally change that API too: reject a session containing files owned by another curator instead of silently filtering to user-owned rows. If a `review_session_id` is supplied, also require review-session lookup authorization.

### Flow Memory Wording

After lookup support lands, update `_build_flow_memory_assistant_message` to be specific:

```text
Use inspect_curation_context with flow_run_id/extraction_result_id for extraction details,
review_session_id for review workspace details, or file_id for bounded file metadata/previews.
```

This turns the existing "review session or file refs" promise into a true contract.

### Part 1 Tests

- `inspect_curation_context(scope="review_session", detail="inventory")` returns a bounded authorized session ref.
- Unauthorized review session is rejected without session details.
- `detail="candidates"` paginates and resolves `object_ref`.
- `detail="evidence"` and `detail="validation_findings"` return compact records/projections.
- `detail="field"` requires `field_path` and bounds long values.
- `scope="file", detail="metadata"` enforces `curator_id`.
- `scope="file", detail="schema"` parses CSV/TSV headers and JSON top-level shape.
- `scope="file", detail="preview"` refuses path traversal and limits rows/bytes.
- Supervisor wrapper exposes `review_session_id` and `file_id`.
- Flow memory test asserts the wording matches supported lookup args.

## Part 2: Deterministic Output Projection

### Core Idea

Add a projection service that converts completed flow artifacts into deterministic table/JSON/chat output. The model no longer writes `data_json`. It writes or finalizes a validated projection plan.

Proposed module:

```text
backend/src/lib/flows/output_projection.py
```

Responsibilities:

- Build a canonical `FlowOutputArtifactBundle` from `execution_state["completed_steps"]`.
- Discover available row sources and field refs.
- Validate a `FlowOutputProjectionPlan`.
- Apply the plan deterministically.
- Save CSV/TSV/JSON or render chat output.

The existing `_try_save_tsv_formatter_flow_output` becomes a compatibility wrapper over the projection service, then eventually disappears.

### Full-Scope Boundary

The implementation should go big enough that curator-directed export shaping is a real capability, not just deterministic defaults:

- TSV: preserve the existing artifact-summary rows as the default compatibility path, but allow the same projection machinery as CSV/JSON/chat when a curator asks for object-, evidence-, or validation-level rows.
- CSV/TSV/JSON/chat: support artifact rows, object rows, evidence rows, and validation-finding rows when those row sources are present in canonical artifacts.
- All formats: support deterministic default output plus column/key rename, omit, reorder, filtering, sorting, grouping, and safe derived fields.
- Implementation can still be sequenced internally for testability, but the design target and acceptance criteria include the full manipulation toolset.

This fixes the deterministic handoff problem and gives curators the export-shaping behavior they actually want, while still keeping model-written code and model-written file contents out of the runtime.

### Artifact Bundle

The bundle should preserve enough structure for useful exports without giving the model the whole payload:

```python
class FlowOutputArtifactBundle(BaseModel):
    flow_name: str
    flow_run_id: str | None
    document_id: str | None
    artifacts: list[FlowOutputArtifact]
    field_catalog: list[FlowOutputField]
    default_row_source: Literal["artifact", "object", "evidence", "validation_finding"]
```

Each artifact includes:

- step number, agent id/name, adapter key
- extraction result id when already persisted
- envelope/domain ids from canonical payload
- object count/candidate count
- compact output preview
- bounded structured payload pointers for runtime use

### Artifact Normalization

Completed flow steps currently expose an `ExtractionEnvelopeCandidate`, plus preview/evidence/validation metadata captured by the executor. The projection service must normalize shapes before exposing row sources:

- `domain_envelope`: payload is a mapping with `envelope_id`, `domain_pack_id`, and `objects` as a list. Object rows are supported for this shape. Evidence and validation row sources are supported when records/findings can be collected deterministically from the envelope, object, or executor-captured metadata.
- `legacy_extraction_envelope`: payload has `curatable_objects`, `items`, `raw_mentions`, `exclusions`, or `ambiguities` plus `run_summary`. The normalizer must provide explicit, tested mappings for object/evidence/validation row sources where the shape is understood; otherwise it should classify the unsupported sub-shape and fall back to artifact rows with a clear warning.
- `non_structured`: no projection rows except the artifact summary.

The normalizer should never infer object semantics from arbitrary nested JSON. It should classify the shape, expose the reason in the field catalog, and fall back to artifact rows when a requested row source is unavailable.

Field refs should be stable strings, for example:

- `artifact.step`
- `artifact.agent_id`
- `artifact.agent_name`
- `artifact.adapter_key`
- `envelope.domain_pack_id`
- `envelope.envelope_id`
- `object.object_type`
- `object.status`
- `object.payload.symbol`
- `object.payload.primary_external_id`
- `evidence.quote`
- `evidence.verified_quote`
- `evidence.source`
- `validation.finding_id`
- `validation.status`
- `validation.severity`
- `validation.message`

The field catalog should include labels, value type, source row type, count of non-empty values in the preview window, and a few example values.

### Row Sources

Support these deterministic row sources:

- `artifact`: one row per completed structured artifact. This preserves today's TSV behavior.
- `object`: one row per curatable object inside canonical domain-envelope payloads or explicitly mapped legacy payloads. This is the natural default for curator data exports when objects exist.
- `evidence`: one row per evidence record/anchor.
- `validation_finding`: one row per validation finding.

Defaulting rule for discussion:

- Preserve current TSV terminal default as `artifact`.
- For CSV/JSON/chat-output, default to `object` when supported objects exist, otherwise `artifact`.
- If the curator or flow step asks for a specific shape, the projection plan wins.

This gives us compatibility and the richer data-output behavior curators actually want.

### Projection Plan

The model's output should be a small plan, not file contents.

```python
class FlowOutputProjectionPlan(BaseModel):
    format: Literal["csv", "tsv", "json", "chat"]
    row_source: Literal["artifact", "object", "evidence", "validation_finding"]
    columns: list[FlowOutputColumnSpec] = []
    filters: list[FlowOutputFilterSpec] = []
    sort: list[FlowOutputSortSpec] = []
    group_by: list[str] = []
    json_shape: Literal["rows", "grouped", "bundle"] = "rows"
    chat_layout: Literal["table", "sections", "bullets"] = "table"
    missing_value: str = ""
    max_rows: int | None = None
```

Column specs:

```python
class FlowOutputColumnSpec(BaseModel):
    key: str
    header: str | None = None
    field_ref: str | None = None
    transform: FlowOutputTransformSpec | None = None
```

Column behavior:

- `key` is the output JSON key / stable internal column id.
- `header` is the CSV/TSV/chat table label; when missing, use the field catalog label.
- `field_ref` must exist in the bundle field catalog unless `transform` is used.
- Omit a column by not including it.
- Reorder columns by plan order.

Allowed transforms:

- `literal`: constant value.
- `first_non_empty`: first non-empty value from a list of field refs.
- `concat`: concatenate field refs/literals with a separator.
- `join_list`: join list values with a separator.
- `count`: count items at a list field.
- `map_value`: map exact input values to labels with a default.
- `boolean_label`: map boolean-ish values to configured labels.

Allowed filters:

- `eq`, `ne`, `in`
- `contains`
- `is_empty`, `is_not_empty`
- `gt`, `gte`, `lt`, `lte` for numeric/date-like scalar values only

No arbitrary expressions. Every field ref must exist in the bundle field catalog or be one of the standard artifact fields.

### Agent-Guided Tool Story

Flow terminal formatters should receive a flow-output projection tool bundle instead of direct save tools in flow mode. Because the existing formatter prompts explicitly instruct agents to call `save_*_file`, the deterministic flow path should not reuse those prompts after removing save tools.

Use one of these safer patterns:

- Bypass the formatter model entirely for default exports.
- For custom manipulation requests, use a dedicated projection-planner prompt or structured output schema that sees only the compact artifact inventory and returns a projection plan.

Tools:

1. `inspect_output_artifacts`
   - Returns row source counts, available fields, default columns, and examples.
   - Bounded and read-only.
2. `preview_output_projection`
   - Accepts a projection plan.
   - Validates field refs/transforms/filters.
   - Returns first N projected rows plus errors/warnings.
3. `finalize_output_projection`
   - Accepts the final plan.
   - Validates again.
   - Returns a runtime-owned projection result/ref.

The expected happy path is one or two calls:

- If default output is enough, no planner call is needed; runtime applies the default plan.
- If the flow step/custom instructions request changes, the agent inspects once and finalizes a plan.
- If the plan has a validation error, allow one correction attempt before failing with a useful message.

The terminal agent should not have `save_csv_file`, `save_tsv_file`, or `save_json_file` in this flow-specific mode. The executor saves files after projection finalization.

### Runtime Flow

For each terminal output step:

1. Build `FlowOutputArtifactBundle` from completed structured artifacts.
2. Resolve terminal format from agent id, output capability, and tool id metadata rather than brittle display names. Include the formatter package ids: `csv_formatter`, `tsv_formatter`, `json_formatter`, and `chat_output_formatter`.
3. Build a default projection plan.
4. If step goal/custom instructions include customization or the default row source is ambiguous, invoke the terminal planner with the projection tools.
5. Validate/apply the final plan in runtime.
6. For CSV/TSV:
   - Runtime calls `_save_csv_impl` or `_save_tsv_impl`.
   - Runtime passes deterministic row objects and explicit `columns`.
7. For JSON:
   - Runtime calls `_save_json_impl`.
   - Runtime serializes rows, grouped rows, or a bounded bundle shape.
8. For chat:
   - Runtime renders markdown from projected rows/sections.
   - Runtime emits or synthesizes `CHAT_OUTPUT_READY` directly from the flow executor, preserving the current `details.output`, `details.output_preview`, and `details.output_length` shape. Do not rely on the runner's exact `ask_chat_output_specialist` tool-name check, because flow tool names can be suffixed.
9. Continue existing `FILE_READY`/`CHAT_OUTPUT_READY` and flow-finished behavior.

For file outputs, returning the existing FileInfo JSON shape may still be enough for `FILE_READY`, but the executor tests must prove the deterministic path produces the same event details currently consumed by `chat_execute_flow.py`: `file_id`, `filename`, `format`, `size_bytes`, `mime_type`, `download_url`, and `created_at`.

### Defaults

Default artifact-summary columns should match current TSV compatibility:

```text
step, agent_id, agent_name, adapter_key, domain_pack_id, envelope_id,
object_count, candidate_count, artifact_preview
```

Default object-row columns should be derived from field catalog:

```text
adapter_key, object_type, status, primary label/id fields, evidence_count,
validation_status
```

Evidence and validation columns should appear only when they are explicit field-catalog entries for the chosen row source.

The exact primary label/id priority should be adapter-aware when domain envelope metadata provides it, otherwise use stable generic priority:

```text
symbol, name, label, primary_external_id, external_id, id
```

### Security and Bounds

- Projection plans are data, not code.
- Runtime rejects unknown fields, unknown transforms, disallowed operators, and overlarge outputs.
- CSV/TSV serialization continues through existing file output helpers. Today those helpers/storage paths detect formula-injection risks as warnings; do not claim sanitization unless the implementation adds deterministic escaping or rejection and tests it.
- Preview tools return samples, not full outputs.
- JSON output can be larger than preview but remains produced by runtime from canonical artifacts and existing storage limits.
- Chat rendering should cap rows and include a truncation note when the projected row count exceeds display limits.
- If projection-plan audit metadata is required for the full implementation, first extend `_save_csv_impl`, `_save_tsv_impl`, and `_save_json_impl` with optional `file_metadata`/agent-name inputs and tests. Otherwise, keep audit metadata as a named follow-up rather than silently promising persistence that the helper signatures do not support today.

### Package and Config Impact

There are two file-output tool surfaces:

- `backend/src/lib/openai_agents/tools/file_output_tools.py`
- `packages/alliance/python/src/agr_ai_curation_alliance/tools/file_output_tools.py`

The deterministic flow path should call backend internals from the executor, like the current TSV path, and should not depend on package-local formatter tools. If we later expose projection tools in package metadata, keep the package registry aligned with backend tests.

Agent YAML/prompt changes should be flow-specific where possible:

- Keep ordinary formatter agents usable for non-flow/package contexts.
- In flow terminal execution, override or wrap the formatter's tool bundle with projection tools and runtime save/render behavior.
- Do not hardcode model names in executor code; use existing agent definition resolution and provider defaults.
- Tests should cover the canonical formatter package agent ids (`csv_formatter`, `tsv_formatter`, `json_formatter`, `chat_output_formatter`) as they appear in flow nodes.

## Implementation Sequence

1. Add review-session/file lookup helpers and wire them into `inspect_curation_context`.
2. Update supervisor wrapper args/description and flow-memory wording.
3. Add Part 1 unit tests.
4. Add `output_projection.py` data models, artifact-shape normalizer, and pure projection functions for artifact, object, evidence, and validation-finding rows.
5. Convert `_try_save_tsv_formatter_flow_output` to use the projection service while preserving current TSV tests exactly.
6. Add deterministic CSV and JSON terminal save paths for default plans.
7. Add deterministic chat-output rendering path and direct executor-side `CHAT_OUTPUT_READY` event contract.
8. Add custom projection planning for column/key rename, omit, reorder, filters, sorting, grouping, and safe derived fields.
9. Add flow executor tests for CSV/TSV/JSON/chat default and customized plans.
10. Update docs/config notes for terminal formatter behavior.
11. Keep implementation sequencing internal, but do not mark the feature complete until the full projection-plan surface is implemented and tested.

## Test Plan

Lookup tests:

- Existing `inspect_curation_context` extraction-result tests keep passing.
- New review-session scope tests cover owner, assigned curator, linked flow/chat extraction, unauthorized no-leak, nonexistent no-leak, inventory, candidates, evidence, validation findings, field lookup, and pagination.
- New file scope tests cover metadata, schema, preview, JSON field lookup, ownership, max-byte cap before parsing, malformed JSON, malformed CSV, large files, symlink/path traversal containment, and mixed-curator `session_files` behavior matching the chosen policy.
- Supervisor wrapper signature test includes `review_session_id` and `file_id`.
- Flow-memory wording test checks only supported lookup refs are advertised.

Projection tests:

- Pure unit: artifact row projection exactly matches current TSV row output.
- Pure unit: object row projection extracts scalar fields from canonical domain-envelope `objects` deterministically.
- Pure unit: evidence row projection extracts evidence records/anchors deterministically from supported artifact shapes.
- Pure unit: validation-finding row projection extracts findings deterministically from supported artifact shapes.
- Pure unit: legacy `curatable_objects`/`items` payloads are classified safely and projected through explicitly tested mappings or rejected with a clear unsupported-row-source warning.
- Pure unit: deterministic column order across heterogeneous rows.
- Pure unit: column/key rename, omit, reorder, missing value, filter, sort, grouping, and derived transforms.
- Negative unit: reject unknown field refs, unsupported transforms, unsupported filters, overlarge `max_rows`, unsafe grouping requests, and invalid row sources.
- Pure unit: empty artifact behavior and over-limit row behavior.
- Executor: TSV terminal still saves without model-authored `data_json`.
- Executor: CSV terminal saves from artifacts with explicit columns.
- Executor: JSON terminal saves runtime-generated JSON.
- Executor: chat_output emits runtime-rendered `CHAT_OUTPUT_READY`.
- Executor: deterministic terminal events preserve the current `FILE_READY.details` and `CHAT_OUTPUT_READY.details` shapes.
- Planner: custom instructions such as "skip column X and rename Y", "validated rows only", "group by adapter", and "combine ID plus symbol" produce validated plans and runtime output.
- Regression: formatter agents in ordinary non-flow contexts are not broken.
- Regression: duplicate output formatter steps and actual package agent ids resolve correctly.
- Optional audit test: projection plan summary persists in `FileOutput.file_metadata` only if helper signatures are extended for that.

Validation commands after implementation:

```bash
PYTHONPYCACHEPREFIX=/tmp/agr-ai-curation-pycache python3 -m py_compile backend/src/lib/openai_agents/supervisor_context_tools.py backend/src/lib/flows/output_projection.py backend/src/lib/flows/executor.py
docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/lib/openai_agents/agents/test_supervisor_agent_runtime.py tests/unit/lib/flows/test_tsv_formatter_flow_export.py -v --tb=short"
```

Add the new projection tests to the targeted pytest invocation once the filenames exist.

## Open Questions

1. Should object rows become the default for TSV too, or should TSV keep artifact-summary rows unless the curator asks for object-level output?
2. Should review-session lookup authorization stay stricter than the current read endpoint, or should the API endpoint also gain the same explicit user-link check?
3. How much grouping should full implementation support? `group_by` with one or more exact field refs is the safe baseline; nested custom trees should remain later.
4. Should ordinary supervisor `export_to_file` eventually move to this projection service for follow-up exports from existing refs?
5. Should file preview include CSV/TSV row count by scanning the whole file when the file is small, or always report an estimate to avoid surprise cost?
