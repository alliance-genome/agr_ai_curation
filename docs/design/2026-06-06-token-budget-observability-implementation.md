# Token Budget Observability Implementation Plan

Date: 2026-06-06
Status: design for implementation

## Scope

This plan closes the remaining token JSON audit items that are narrow enough to
implement as one observability and payload-budgeting slice:

- Provider-call telemetry guardrails for large runtime inputs.
- Conversation context reporting for durable chat sessions.
- Batch aggregate token and character summaries by document and flow step.
- TraceReview/model-live context visibility that separates provider-bound
  context from observability-only payloads.
- Evidence capture from real chat/flow runs so any later validator prompt
  payload compaction is based on measured JSON paths rather than speculation.

This plan intentionally does not implement full standard-chat history
compaction or Agent Studio tool-loop compaction. Those are larger runtime
behavior changes and should be tracked as separate implementation tickets.
This slice does include report-only provider-bound telemetry at those surfaces
so the later compaction tickets have concrete size evidence.

This plan also intentionally defers scalar `selected_inputs` value truncation.
The current audit evidence shows real wins from duplicate removal, repeated
lists/results, and observability-only payload separation, but it does not yet
show large scalar validator input values as a recurring top offender. Scalar
compaction should be considered only after this plan captures real Incus
chat/flow traces and proves a field is large, non-identity, and recoverable
through an explicit full-value lookup path.

## Existing Ground Truth

The codebase already has the first compaction pass:

- `backend/src/lib/domain_packs/validator_dispatch.py`
  - `validator_request_payload_for_agent()` removes `target.input_values`,
    `input_selectors`, and full `evidence[]`.
  - `run_package_scoped_validator_agent()` and
    `run_package_scoped_validator_agent_batch()` serialize that compact payload
    for provider calls and log `payload_bytes`.
  - `_validator_batch_summary()` emits request and validator identity metadata,
    but no aggregate payload character/token summary yet.
- `backend/src/lib/openai_agents/supervisor_context_tools.py`
  - `inspect_chat_traces()` exposes TraceReview summary, conversation,
    diagnostic report, tool calls, costs, duplicates, and payload inventory.
  - Main-chat payload inventory is allowlisted with `include_values=False`.
  - Diagnostic report calls force `include_raw_args=False` and
    `include_raw_outputs=False`.
- `backend/src/lib/agent_studio/tools.py`
  - `get_trace_payloads()` already supports summary inventory and
    `include_values=False`.
  - `get_trace_payload()` supports explicit exact payload chunk retrieval.
- `trace_review/backend/src/api/claude.py`
  - TraceReview has `langfuse_payloads`, `langfuse_payload`,
    `diagnostic_report`, costs, duplicates, and token metadata for responses.
- `trace_review/backend/src/analyzers/extraction_timeline.py`
  - Existing `size_summary` calculates exchange character totals, estimated
    exchange tokens, threshold counts, and largest payload contributors.
- `backend/src/lib/chat_history_repository.py`
  - Durable chat sessions/messages keep `content`, `payload_json`, `trace_id`,
    `role`, `message_type`, and `chat_kind`, which are enough for a
    conversation context report.
- `backend/src/api/chat_sessions.py`
  - Existing session endpoints are the natural API home for a read-only
    context report.
- `backend/src/api/batch.py` and `backend/src/lib/flows/executor.py`
  - Existing batch and flow execution paths are the natural places to attach
    aggregate size summaries.

LSP/code survey used for this plan:

```bash
scripts/utilities/agent_lsp.py --root . status
scripts/utilities/agent_lsp.py --root . symbols backend/src/lib/domain_packs/validator_dispatch.py
scripts/utilities/agent_lsp.py --root . symbols backend/src/lib/openai_agents/supervisor_context_tools.py
scripts/utilities/agent_lsp.py --root . symbols backend/src/api/agent_studio.py
scripts/utilities/agent_lsp.py --root . symbols backend/src/lib/agent_studio/tools.py
scripts/utilities/agent_lsp.py --root . symbols trace_review/backend/src/api/claude.py
scripts/utilities/agent_lsp.py --root . symbols backend/src/lib/chat_history_repository.py
```

## Design Principles

1. Provider-bound context and observability payloads are different things.
   Runtime prompts may carry summaries, refs, and small field slices.
   TraceReview and durable stores may retain large exact payloads for explicit
   debugging, but those payloads must not become model-live context by default.

2. Compaction should be schema-aware and explicit. Do not truncate arbitrary
   JSON strings without leaving ref metadata, omitted field paths, approximate
   sizes, and restoration guidance.

3. Canonical domain contracts stay intact. Validator internals may keep full
   `DomainValidationRequest` data for materialization and audit. Only the
   provider-bound render shape is compacted.

4. The first implementation should be reporting and guardrails, not silent
   behavior changes. Warnings and audit events should expose risk before later
   tickets enforce hard limits.

5. Telemetry must cover every provider boundary named by the audit, even where
   this slice does not change compaction behavior.

6. Core platform code remains project-agnostic. Domain-specific policy for
   "this selected input is safe to summarize" belongs in domain pack metadata
   or generic field-policy config, not hard-coded Alliance names in core
   services.

## Shared Runtime Size Utility

Add a small shared module for deterministic runtime-size summaries. Suggested
starting location:

- `backend/src/lib/runtime_payload_budget.py`

Target helpers:

```python
@dataclass(frozen=True)
class RuntimePayloadSize:
    json_chars: int
    estimated_tokens: int
    threshold: str | None

def estimate_tokens_from_chars(chars: int) -> int:
    return max(1, math.ceil(chars / 4))

def json_size(value: Any) -> RuntimePayloadSize:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return RuntimePayloadSize(
        json_chars=len(text),
        estimated_tokens=estimate_tokens_from_chars(len(text)),
        threshold=classify_threshold(estimate_tokens_from_chars(len(text))),
    )

def summarize_text(value: str, *, max_chars: int) -> dict[str, Any]:
    return {
        "preview": value[:max_chars],
        "original_chars": len(value),
        "omitted_chars": max(0, len(value) - max_chars),
        "truncated": len(value) > max_chars,
    }
```

Thresholds should default to estimated token warnings at `100000`, `250000`,
and `1000000`, matching the audit language. Make thresholds configurable via
environment variables only if existing config patterns make that cheap.

Use the helper in backend runtime code first. TraceReview can keep its current
`trace_review/backend/src/utils/token_budget.py`; later cleanup can share a
package-level utility if the duplication becomes painful.

## P1: Deferred Scalar `selected_inputs` Compaction

Current state:

- `validator_request_payload_for_agent()` keeps full `selected_inputs`.
- The current audit samples and PDF trial corpus do not show large scalar
  strings inside `selected_inputs` as a real top offender.
- Long fields such as evidence quotes and identity-resolution notes may still
  become a future risk, but they need real examples before the model-visible
  JSON structure is altered.
- `_VALIDATOR_DEDUPE_CONTEXT_INPUT_FIELDS` already treats some context fields as
  non-identity for dedupe, so the codebase already distinguishes identity from
  context.

Policy for this implementation slice:

- Do not truncate scalar values inside `selected_inputs`.
- Preserve unknown scalar fields exactly.
- If a scalar selected-input value exceeds a reporting threshold, emit
  telemetry with the field path, character size, validator binding, trace/session
  refs, and whether an explicit full-value retrieval path exists.
- It is acceptable to cap/paginate repeated inventories and result lists when
  the response includes counts, omitted-item metadata, and a tool/ref path for
  full retrieval.
- Any future scalar compaction must be policy-approved, field-specific, backed
  by real logs, and paired with an agent-visible full-value lookup option.

Deferred implementation sketch, not part of this slice:

1. Add explicit selected-input policy before truncating values. The first pass
   should default unknown long fields to exact transmission plus telemetry
   warning, not silent truncation. Domain packs or validator bindings can opt
   fields into summary/preview behavior.

Recommended report-only metadata shape for this slice:

```yaml
selected_input_size_warnings:
  - field_path: selected_inputs.evidence_quote
    original_chars: 8420
    strategy: exact_with_size_warning
    model_visible_value: exact
    full_value_lookup:
      tool: get_validator_selected_input
      arguments:
        trace_id: "..."
        request_id: "..."
        field_path: selected_inputs.evidence_quote
```

Core runtime should treat this as generic provider-context telemetry. It must
not hard-code Alliance field names in `validator_dispatch.py`, and it must not
convert exact scalar values into previews in this implementation slice.

2. If later evidence justifies scalar compaction, extend
   `validator_request_payload_for_agent()` so `selected_inputs` is rendered
   through a dedicated policy helper, for example:

```python
def compact_validator_selected_inputs(
    selected_inputs: Mapping[str, Any],
    *,
    max_string_chars: int = 1200,
    max_list_items: int = 20,
    max_mapping_items: int = 40,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ...
```

3. Preserve scalar identity fields exactly when they are short:

- CURIEs, IDs, symbols, enum values, taxon IDs, data-provider abbreviations.
- Short labels/names.
- Field names used in `expected_result_fields`.

4. Only after policy approval, compact policy-approved long context-like values
   into structured summaries:

```json
{
  "selected_inputs": {
    "identifier": "AGR:0001",
    "evidence_quote": {
      "kind": "text_summary",
      "preview": "First 1200 chars...",
      "original_chars": 8420,
      "omitted_chars": 7220,
      "truncated": true
    },
    "identity_resolution_notes": {
      "kind": "list_summary",
      "item_count": 73,
      "items": ["first compact item", "second compact item"],
      "omitted_items": 71
    }
  },
  "runtime_compaction": {
    "omitted_fields": [
      "input_selectors",
      "target.input_values",
      "evidence",
      "selected_inputs.evidence_quote.tail"
    ],
    "selected_input_summaries": [
      {
        "field_path": "selected_inputs.evidence_quote",
        "original_chars": 8420,
        "retained_chars": 1200,
        "strategy": "text_preview",
        "model_visible_value": "compacted_after_policy_approval",
        "full_value_lookup": {
          "tool": "get_validator_selected_input",
          "arguments": {
            "trace_id": "...",
            "request_id": "...",
            "field_path": "selected_inputs.evidence_quote"
          }
        }
      }
    ],
    "input_values_source": "selected_inputs",
    "canonical_identity_restored_by": "finalize_validator_result"
  }
}
```

5. Do not add a fallback prompt path that tells agents to read
   `target.input_values`. The canonical provider-visible request context remains
   `selected_inputs`.

6. Keep the result identity restore path unchanged. `_validated_result_from_agent_output()`,
   `_validated_results_from_agent_batch_output()`, `_finalize_validator_result()`,
   and `_validator_result_target_matches_request_identity()` should continue to
   normalize accepted results against the server-side request.

7. If scalar compaction is approved later, add tests in
   `backend/tests/unit/lib/domain_packs/test_validator_dispatch.py`:

- Policy-approved long `selected_inputs.evidence_quote` compacts with metadata
  and an explicit full-value lookup option.
- Short identity fields remain exact.
- Unknown long `selected_inputs` fields remain exact and emit/report size risk.
- Batch payload uses the same compaction helper for every request.
- `DomainValidationRequest` internals still retain full values for
  materialization.
- Result finalization still restores the full `request.target`.
- Evidence-heavy validator fixtures still produce equivalent validation results
  when policy-approved compaction is enabled.

Open design choice:

- After real Incus run artifacts exist, decide whether this section remains
  report-only or becomes a field-policy implementation. The default answer
  should remain "no scalar truncation" unless the evidence says otherwise.

## P2: Provider-Call Telemetry Guardrails

Current state:

- Validator runs log `payload_bytes`, but not estimated tokens or thresholds.
- TraceReview diagnostic reports calculate large payload exchange estimates
  after the fact.
- Agent Studio already surfaces `token_info` from TraceReview tools, but backend
  provider calls do not consistently emit preflight size events.

Implementation:

1. Add a small preflight reporter, likely in `backend/src/lib/runtime_payload_budget.py`:

```python
def provider_context_preflight(
    *,
    surface: str,
    operation: str,
    provider: str | None,
    model: str | None,
    payload: Any,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ...
```

2. The reporter returns a compact dict and logs at `INFO` below threshold,
   `WARNING` at or above threshold:

```json
{
  "event": "provider_context_preflight",
  "surface": "validator",
  "operation": "domain_validator_batch",
  "provider": "openai",
  "model": "gpt-5.5",
  "json_chars": 31538,
  "estimated_tokens": 7885,
  "threshold": null,
  "metadata": {
    "validator_binding_id": "...",
    "request_count": 8,
    "flow_run_id": "..."
  }
}
```

3. Wire the reporter into every provider boundary named by the audit:

- `run_package_scoped_validator_agent()`
- `run_package_scoped_validator_agent_batch()`
- `_run_custom_flow_validator_agent()` in `backend/src/lib/flows/executor.py`
  for replaced/supplemental flow validators that do not pass through package
  validator dispatch.
- Standard chat supervisor/provider input assembly in `backend/src/api/chat_common.py`,
  `backend/src/api/chat_stream.py`, and the shared OpenAI runner boundary.
- Flow supervisor and specialist calls in `backend/src/lib/flows/executor.py`.
- Agent Studio initial Anthropic call in `backend/src/api/agent_studio.py`.
- Agent Studio per-loop continuation calls after tool results are appended to
  `current_messages`.
- Batch execution summaries via validator batch summary events.

4. Do not block provider calls in this slice. The guardrail is report-only.
   Later enforcement can add per-surface max budgets.

5. Emit the preflight summary into the active trace/runtime stream whenever a
   trace context exists. Logging alone is not enough because TraceReview reads
   Langfuse/runtime events, not application log files.

Recommended event shape:

```json
{
  "type": "PROVIDER_CONTEXT_PREFLIGHT",
  "surface": "agent_studio",
  "operation": "tool_loop_continuation",
  "model_live": true,
  "trace_id": "...",
  "provider": "anthropic",
  "model": "claude-sonnet-...",
  "payload_summary": {
    "json_chars": 42000,
    "estimated_tokens": 10500,
    "threshold": null
  },
  "refs": {
    "session_id": "...",
    "turn_id": "...",
    "flow_run_id": null,
    "batch_id": null,
    "document_id": null
  }
}
```

Suggested transport:

- For main chat, flow, and specialist paths, reuse the existing event-stream
  collection mechanisms where available, such as
  `src.lib.openai_agents.streaming_tools.add_specialist_event()` or the flow
  runtime event path. Also attach trace metadata/span information where the
  OpenAI runner already has Langfuse trace context.
- For Agent Studio, persist the compact preflight event in the durable
  Agent Studio assistant/user debug payload and emit it as an SSE/audit event
  if that does not alter user-facing behavior.
- For batch/background paths without active trace context, include
  `batch_id`, `document_id`, and provider-call ordinal in emitted audit/runtime
  events. TraceReview should report "preflight unavailable" rather than
  guessing for historical traces with no events.

6. Tests:

- Unit test threshold classification.
- Unit test validator single and batch preflight metadata.
- Use `caplog` to assert warnings at configured high sizes.
- Unit tests for Agent Studio initial and tool-loop preflight summaries.
- Unit tests or integration tests for standard chat/flow preflight summaries.

## P3: Conversation Context Report Endpoint

Goal:

Give developers and TraceReview a read-only way to answer "what would this
chat replay, and how large is it?" without sending the transcript to a model.

Suggested endpoint:

```text
GET /api/chat/sessions/{session_id}/context-report?chat_kind=assistant_chat
```

Natural implementation home:

- `backend/src/api/chat_sessions.py`
- `backend/src/lib/chat_history_repository.py`
- Possible helper module: `backend/src/lib/chat_context_report.py`

Target response:

```json
{
  "session_id": "abc",
  "chat_kind": "assistant",
  "message_count": 12,
  "visible_content_chars": 18342,
  "payload_json_chars": 42710,
  "hidden_flow_memory_chars": 932,
  "flow_memory_message_count": 2,
  "trace_ids": ["..."],
  "messages": [
    {
      "role": "user",
      "message_type": "user",
      "content_chars": 520,
      "payload_json_chars": 0,
      "trace_id": null,
      "model_live": true
    },
    {
      "role": "flow",
      "message_type": "execute_flow_transcript",
      "content_chars": 684,
      "payload_json_chars": 18000,
      "trace_id": "...",
      "model_live": true,
      "model_live_source": "_assistant_message",
      "payload_json_model_live": false,
      "replay_content_chars": 684
    }
  ],
  "estimated_replay_tokens": 4800,
  "threshold": null
}
```

Important constraints:

- Report ownership must match existing chat session authorization rules.
- Do not return raw `payload_json` in this endpoint.
- Include per-message sizes and classification, not payload bodies.
- Use valid repository chat kinds: `assistant_chat` and `agent_studio`.
- Classify `role="flow"` transcript rows as model-live when
  `extract_flow_assistant_message()` contributes replay text. Keep
  `payload_json_model_live=false` so the report distinguishes the replayed
  assistant summary from observability-only flow transcript payload JSON.
- Include `chat_kind=agent_studio` support if it is cheap, but do not make
  Agent Studio compaction part of this ticket.

Tests:

- Add unit/integration coverage near existing chat session tests.
- Verify raw payload bodies are absent.
- Verify flow memory counts and estimated tokens.
- Verify unauthorized session returns 404/403 matching existing behavior.

## P4: Batch Aggregate Token Summaries

Goal:

Batch runs should expose aggregate size/cost risk by document and by flow step,
especially around validator request payloads.

Current state:

- `_validator_batch_summary()` returns request counts and IDs.
- `_emit_validator_batch_event()` emits `validator_batch_start` and
  `validator_batch_complete`.
- Batch and flow code already surface runtime events for audit.

Implementation:

1. Extend validator batch summaries:

```json
{
  "validator_binding_id": "...",
  "batch_family": "...",
  "request_count": 8,
  "request_ids": ["..."],
  "payload_summary": {
    "request_payload_json_chars": 42000,
    "request_payload_estimated_tokens": 10500,
    "largest_request_json_chars": 12000,
    "compacted_selected_input_count": 3,
    "omitted_evidence_count": 8
  }
}
```

2. For flow-attached package validators and custom validation attachments,
   include:

- `flow_id`
- `flow_run_id`
- `step_id`
- `step_output_key`
- `document_id` when available

3. For batch document processing, aggregate by `batch_id + document_id +
   session_id`. Do not assume one distinct `flow_run_id` per document unless a
   future batch execution change creates one. Current batch execution may pass
   `flow_run_id=batch_id` for every document.

```json
{
  "batch_id": "...",
  "documents": [
    {
      "document_id": "...",
      "session_id": "...",
      "total_provider_payload_json_chars": 100000,
      "total_provider_payload_estimated_tokens": 25000,
      "steps": [
        {
          "step_id": "validator_1",
          "operation": "domain_validator_batch",
          "request_count": 8,
          "payload_json_chars": 42000,
          "estimated_tokens": 10500
        }
      ]
    }
  ]
}
```

4. First implementation should be stream/audit-event only. Current batch SQL
   models do not expose a generic payload-summary JSON field. Do not imply an
   API persistence guarantee unless this ticket explicitly adds a migration,
   such as `batch_documents.payload_summary` JSONB or a separate batch
   observability table.

5. Tests:

- Unit test `_validator_batch_summary()` includes aggregate sizes.
- Flow executor tests assert package validator batches and custom
  `_run_custom_flow_validator_agent()` paths carry step/run metadata.
- Batch runtime test or fixture-level test asserts stream-level document
  aggregation keyed by `batch_id + document_id + session_id`.

## P5: TraceReview Model-Live Context Visibility

Goal:

TraceReview should make it easy to distinguish:

- payloads actually sent to model/provider calls;
- payloads persisted only for observability/debugging;
- exact payload chunks that require explicit retrieval.

TraceReview is close today: payload inventory, exact payload lookup, costs,
duplicates, and diagnostics all exist. The missing layer is a first-class
model-live view that classifies provider-bound inputs and output continuations.

Implementation plan:

1. Backend emits provider context preflight events before provider calls and
   makes those events visible to TraceReview through runtime/Langfuse event
   capture. These events should include compact metadata only, never full prompt
   bodies:

```json
{
  "type": "PROVIDER_CONTEXT_PREFLIGHT",
  "surface": "validator",
  "operation": "domain_validator_batch",
  "model_live": true,
  "payload_summary": {
    "json_chars": 42000,
    "estimated_tokens": 10500,
    "threshold": null,
    "largest_paths": [
      {
        "path": "requests[3].selected_inputs.evidence_quote",
        "json_chars": 12000,
        "compacted": true
      }
    ]
  },
  "refs": {
    "trace_id": "...",
    "flow_run_id": "...",
    "batch_id": "...",
    "request_ids": ["..."]
  }
}
```

2. TraceReview adds an endpoint:

```text
GET /api/claude/traces/{trace_id}/model_live_context?source=local
```

Target response:

```json
{
  "trace_id": "...",
  "model_live_context": {
    "provider_call_count": 7,
    "total_input_json_chars": 123000,
    "total_estimated_input_tokens": 30750,
    "threshold_counts": {
      "100000": 1,
      "250000": 0,
      "1000000": 0
    },
    "calls": [
      {
        "ordinal": 1,
        "surface": "validator",
        "operation": "domain_validator_batch",
        "provider": "openai",
        "model": "gpt-5.5",
        "input_json_chars": 42000,
        "estimated_input_tokens": 10500,
        "payload_refs": ["observation:...:input"],
        "observability_only_refs": []
      }
    ]
  },
  "observability_payloads": {
    "payload_inventory_available": true,
    "exact_payload_requires_explicit_lookup": true
  },
  "token_info": {
    "estimated_tokens": 900,
    "within_budget": true
  }
}
```

3. TraceReview implementation should reuse existing pieces:

- `trace_review/backend/src/api/claude.py`
- `trace_review/backend/src/analyzers/langfuse_run_reconstruction.py` or
  adjacent reconstruction analyzer code, depending on where payload refs are
  currently produced.
- `trace_review/backend/src/analyzers/extraction_timeline.py` size summary
  helpers for thresholds and largest payload contributors.
- `trace_review/backend/src/utils/token_budget.py` for response token metadata.

For traces without `PROVIDER_CONTEXT_PREFLIGHT` events, the endpoint should
fall back to Langfuse payload inventory/reconstruction and mark call
classification as inferred or unavailable. It should never pretend historical
traces have precise model-live classification when the preflight event was not
recorded.

4. Backend wrappers should expose the new view safely:

- `backend/src/lib/agent_studio/tools.py`: add `get_trace_model_live_context()`.
- `backend/src/lib/openai_agents/supervisor_context_tools.py`: allow
  `inspect_chat_traces(detail="model_live_context")` only after authorized
  trace inventory, and return bounded summary only.
- `backend/src/api/agent_studio_opus_tools.py`: add the tool definition and
  prompt guidance that model-live context should precede exact payload lookup.

5. Keep exact payload retrieval explicit:

- Main-chat supervisor should not expose `get_trace_payload()`.
- Agent Studio may expose exact payload chunks, but the prompt should continue
  to prefer summary first, payload inventory second, exact chunk last.
- TraceReview `model_live_context` must not include raw prompt text by default.

Tests:

- TraceReview unit test with synthetic Langfuse input/output observations and
  provider preflight events.
- Backend Agent Studio tool wrapper test.
- Supervisor context tool test for authorized `model_live_context`.
- Negative test proving unauthorized trace IDs cannot reach TraceReview.

## P6: Documentation And Validation

Update docs:

- `docs/design/2026-06-06-token-json-audit.md`: add an implementation note that
  this plan is the follow-up for items 3/4/5/6/7.
- `docs/developer/TEST_STRATEGY.md` only if new commands or smoke scripts are
  added.
- Agent Studio system prompt only if adding `get_trace_model_live_context()`.

Recommended validation:

```bash
docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/lib/domain_packs/test_validator_dispatch.py tests/unit/lib/openai_agents/agents/test_supervisor_agent_runtime.py -q"
docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/api/test_chat_sessions.py tests/unit/api/test_agent_studio_trace_tools.py -q"
cd trace_review/backend && python -m pytest tests/test_claude_langfuse_inspection_api.py tests/test_trace_review_diagnostic_report.py -q
docker compose -f docker-compose.test.yml run --rm backend-unit-tests
```

If the implementation touches frontend TraceReview views, also run the
TraceReview frontend tests/build. Otherwise this slice can stay backend-only.

## Acceptance Criteria

- Validator provider-bound payloads preserve scalar `selected_inputs` values
  exactly while reporting large field paths and duplicate/omitted
  provider-bound structures.
- Runtime preflight events/logs report provider-bound JSON chars, estimated
  tokens, and warning thresholds for validator single and batch calls, standard
  chat, flow supervisor/specialist calls, Agent Studio initial calls, and
  Agent Studio tool-loop continuations where those boundaries are available.
- A read-only conversation context report endpoint exposes message counts,
  visible chars, payload-json chars, hidden flow memory chars, trace IDs, and
  estimated replay tokens without raw payload bodies.
- Batch/flow validator execution emits aggregate payload summaries by document
  and flow step where that metadata is available.
- TraceReview exposes a model-live context endpoint that clearly separates
  provider-bound context from observability-only payload inventory.
- A real-run evidence bundle under `docs/design/` captures Incus chat/flow
  traces, logs, context reports, TraceReview model-live summaries, payload
  inventories, and field-path size histograms for at least one sample paper.
- Main-chat supervisor access remains bounded and authorized.
- Exact payload values remain available only through explicit debugging paths,
  not by default in model-live context or main-chat lookup.

## Out Of Scope

- Rewriting standard chat history replay/compaction.
- Rewriting Agent Studio `current_messages` tool-loop compaction.
- Truncating scalar JSON values inside `selected_inputs`.
- Hard-blocking provider calls over a token budget.
- Removing or changing persisted canonical payloads used for review,
  materialization, export, or TraceReview diagnostics.
- Project-specific selected-input policy hard-coded into core runtime.
