# Flow Guided-Supervisor Simplification Plan

Date: 2026-06-06

Status: design update before implementation

Related:

- `goal.md`
- `docs/design/2026-06-06-token-json-audit.md`
- `docs/design/2026-06-06-supervisor-curation-context-lookup.md`
- `backend/src/lib/flows/executor.py`
- `backend/src/api/chat_execute_flow.py`
- `backend/src/schemas/flows.py`
- `frontend/src/components/AgentStudio/FlowBuilder/`
- `backend/tests/fixtures/sample_fly_publication.pdf`

## Product Contract

A curation flow should behave like a main-chat supervisor conversation where the
curator has already written the steps.

The supervisor should receive one task, one document context, and an ordered list
of step tools. It should call each configured step once, in order. Individual
steps may have step goals, custom instructions, custom agent definitions, and
validation attachments. Later agent steps should not receive earlier agent
responses as prompt context.

The flow still needs to aggregate outputs at the end. That aggregation should use
canonical flow artifacts, persisted extraction results, review/session/file
services, and bounded lookup handles. It should not use prompt templating that
injects prior specialist prose or full JSON into another agent.

## Current Code Shape

The backend already has the right high-level execution model:

- `execute_flow()` creates a flow supervisor and delegates to
  `run_agent_streamed()`, so flows share the same streaming/tracing substrate as
  normal chat.
- `create_flow_supervisor()` creates a supervisor with only the configured flow
  tools.
- `_wrap_with_step_order()` enforces strict tool order and stores completed step
  artifacts in `execution_state["completed_steps"]`.
- flow extraction candidates are persisted as `CurationExtractionSourceKind.FLOW`
  with `flow_run_id`.
- curation prep and curation handoff already consume completed structured
  artifacts rather than prompt text.

The old dataflow layer is the part to remove:

- `FlowNodeData.input_source` supports `previous_output` and `custom`.
- `FlowNodeData.custom_input` supports `{{variable}}` prompt templates.
- `FlowNodeData.output_key` is documented as a downstream template variable.
- `_build_initial_flow_template_variables()` binds task instructions into a
  template variable.
- `_build_flow_template_variables()` mixes built-ins with stored step outputs.
- `_resolve_flow_step_query()` lets `custom_input` override the supervisor query.
- `_wrap_with_step_order()` stores full `result_text` by `output_key`, making
  prior output available to later prompts.
- `build_supervisor_instructions()` explicitly says to pass relevant context from
  previous steps to subsequent steps.
- the Flow Builder UI and curator docs expose previous-output and custom-variable
  controls.

There is also later-chat replay risk:

- `_build_flow_memory_assistant_message()` writes hidden JSON with specialist
  outputs into the flow assistant memory message.
- `extract_flow_assistant_message()` rehydrates that message from durable flow
  transcript rows.
- `_build_context_messages_from_durable_messages()` replays that assistant
  message into future main-chat turns.

TraceReview parity is also part of this slice. Flow traces should be inspectable
through the same local TraceReview surfaces as chat traces: summary,
conversation, tool calls, costs, duplicates, payload inventory, and extraction
diagnostics. Removing prompt-level step dataflow must not reduce Langfuse or
TraceReview visibility into what happened during a flow.

## Design Decision

Remove inter-step prompt dataflow.

The runtime should not support `previous_output` or `custom_input` as ways to
build later agent prompts. A step tool should derive its model-visible input from:

- the flow task instructions;
- the current run's user query, if present;
- the loaded document context available through document-aware tools;
- the step goal;
- step-local custom instructions;
- runtime metadata such as flow ID, flow run ID, trace ID, and document name.

The supervisor's tool-call `query` argument should not be trusted as the source
of specialist context for flow steps, because the supervisor has already seen
previous tool returns in the same model turn. The wrapper should build the
bounded step query itself and ignore prompt-influencing prior-output content in
the tool-call argument.

## Shared Runtime Reuse

Flows should reuse the main-chat runtime substrate wherever the behavior is the
same. The simplification should reduce flow-only code, not create a second copy
of chat logic.

Reuse or extract:

- `run_agent_streamed()` for the model/stream/trace loop.
- `DocumentContext.fetch()` and `to_agent_kwargs()` for document-aware tools.
- `_create_streaming_tool()` and `run_specialist_with_events()` for specialist
  isolation and audit events.
- compact handoff helpers from `streaming_tools.py`.
- `curation_context_registry` and `INTERNAL_EXTRACTION_RESULT` event handling for
  same-turn canonical refs.
- `inspect_curation_context` and `inspect_chat_traces` for follow-up lookup by
  `flow_run_id`, extraction result ID, and trace ID.
- `validator_request_payload_for_agent()` for all validator model-input payloads.
- extraction-result persistence/materialization helpers used by chat curation
  prep/review.
- durable transcript/context-budget helpers that replay refs instead of hidden
  JSON.
- existing file-output save implementations, fed by structured artifact rows.

If a helper is currently private to chat and flows need the same behavior, move
it into a neutral shared module. Do not fork similar flow-only implementations.

## Preserving Final Output

Removing prompt dataflow does not mean discarding completed step data.

Keep these channels:

- `execution_state["completed_steps"]` for in-run structured artifacts;
- flow-persisted `extraction_results` keyed by `flow_run_id`;
- domain-envelope materialization for curator review;
- evidence registry and evidence export;
- `curation_prep` and `curation_handoff` deterministic artifact consumption;
- TraceReview and Langfuse full observability payloads.

For terminal output agents, prefer deterministic output construction over LLM
parsing of previous step text. If an output step still needs an LLM narrative,
provide a compact artifact summary plus refs, not full specialist output. File
output should be generated from structured rows or persisted extraction results
wherever practical.

The current TSV/file-output shortcut parses rows from the step query. That path
must be replaced or bypassed before prior-output prompt injection is removed.
Batch validation requires file output or curation handoff, so terminal output
acceptance must cover at least one batch-valid path.

## Migration Strategy

Use a forward-only migration instead of runtime compatibility branches.

Saved flow definitions should be normalized so the old prompt-routing fields are
not honored:

- remove `custom_input`;
- remove or neutralize `input_source`;
- keep `output_key` only if it remains useful as a stable step label, file/export
  key, or persistence key;
- update any UI-facing labels so `output_key` is not described as a prompt
  variable.

After the migration, schema validation should reject new definitions that try to
use previous-output prompt routing. Do not keep a fallback branch that silently
continues the old behavior for older rows.

The implementation must prove obsolete keys are gone or rejected. Because
Pydantic ignores unknown fields by default, removing model fields is not enough
unless `extra="forbid"` or an equivalent raw-definition normalizer rejects
`custom_input`, `input_source="previous_output"`, and prompt-variable routing.

## Implementation Plan

### P0: Backend Runtime Contract

- Remove prior-output template helpers from `executor.py`.
- Replace per-step query resolution with a runtime-built flow step query that
  uses the original task/run context and step metadata.
- Change `_wrap_with_step_order()` so completed step output is stored for
  artifacts and final aggregation, not for prompt variables.
- Update `build_supervisor_instructions()` to remove "pass relevant context from
  previous steps" and to state that runtime supplies each step's context.
- Keep strict step order and fail-fast specialist/runtime error behavior.
- Keep validation attachment scheduling and compact validator request rendering.

### P1: Final Aggregation

- Add a bounded artifact-summary builder for terminal output steps.
- Reuse persisted extraction results and completed step candidates for chat/file
  output.
- Surface persisted extraction result IDs from flow persistence so durable flow
  memory and lookup tools can reference exact records.
- Replace query-parsing formatter shortcuts with structured artifact-to-file
  builders before removing prompt-level prior output.
- Keep curation prep and curation handoff deterministic.
- Ensure evidence export remains independent of model-visible prompt text.

### P2: Durable Flow Memory

- Replace hidden flow context JSON with compact refs and counts.
- Include `flow_run_id`, `trace_id`, file IDs, review session IDs, adapter keys,
  and extraction result IDs.
- Remove specialist output snippets from replayable durable chat context.
- Use `inspect_curation_context` and `inspect_chat_traces` for follow-up detail.

### P3: Schema, UI, And Docs

- Remove `previous_output` and `custom` input-source controls from the Flow
  Builder.
- Remove custom input templates, variable chips, and auto-switching connected
  nodes to `previous_output`.
- Update `FlowNodeData` and frontend `AgentNodeData` types.
- Update curator docs to describe flows as guided supervisor runs.
- Update Agent Studio Claude prompts/tools so flow review no longer recommends
  previous-output wiring.
- Update backend Agent Studio flow tools so `create_flow` no longer writes
  `input_source="previous_output"` and `get_current_flow` no longer reports
  legacy prompt-routing fields.

### P4: Custom Flow Validators

- Give custom flow validators the same compact request payload shape as package
  validators.
- Require structured finalization and repair feedback when a validator is
  expected to produce validation JSON.
- Return explicit failed validation/runtime errors rather than passing malformed
  JSON onward.

### P5: Incus Runtime And TraceReview Validation

- Use the `$sym-help` Incus/Symphony operating model for runtime validation.
- Validate in the `symphony-main` VM-backed app, not only through host unit tests.
- Confirm local TraceReview health before the run with `/health` and
  `/health/langfuse`; use the documented alternate port `18001` if host port
  `8001` is occupied by the Symphony review-port proxy.
- Use the sample FlyBase fixture PDF:
  `backend/tests/fixtures/sample_fly_publication.pdf`.
- Create a small real flow around that PDF, run it from the main flow execution
  path, and capture `flow_run_id`, `trace_id`, persisted extraction result IDs,
  review session IDs, and file IDs as applicable.
- The flow may be created through the UI, existing flow APIs, or a repo helper,
  but the smoke must exercise the curator-facing backend execution path.
- Inspect the resulting flow trace through local TraceReview and verify it has
  the same essential observability as a normal chat trace.
- Confirm TraceReview can show the flow supervisor, specialist calls,
  validator/finalizer activity, materialization or handoff/prep activity, terminal
  output, payload sizes, duplicate payloads, and token/cost accounting.
- Confirm the model-live flow context no longer contains prior step full outputs.
- Confirm canonical payloads remain available through persisted extraction
  results and Langfuse/TraceReview payload lookup.

## Tests

- A two-step flow where step 1 returns a large string and step 2 is called with a
  query containing that string should still invoke step 2 with only the runtime
  step query.
- A flow definition containing `previous_output` or `custom_input` should be
  migrated or rejected under the new contract, not honored.
- Curation prep and curation handoff should still see completed structured
  extraction artifacts.
- Flow memory should serialize refs/counts only and should not contain
  specialist output text.
- Flow completion should include extraction result refs when records were
  persisted.
- Batch execution should keep each document's flow state isolated.
- Batch runtime acceptance should use a batch-valid terminal path: curation
  handoff or file output, not chat output alone.
- Agent Studio backend flow creation/inspection should not recreate or advertise
  legacy previous-output wiring.
- File-output terminal steps should work from structured artifacts rather than a
  prompt/query string containing prior step output.
- Frontend tests should verify that previous-output and variable-template
  controls are gone.
- Runtime smoke should create and run a flow with
  `backend/tests/fixtures/sample_fly_publication.pdf`, then inspect the resulting
  flow trace in TraceReview.
- TraceReview smoke should compare one flow trace and one normal chat trace for
  core observability parity: summary, conversation, tool calls, costs,
  diagnostics, payload inventory, and duplicate report.

## Out Of Scope

- Removing full payloads from Langfuse or TraceReview.
- Removing canonical extraction data from curator review.
- Building true inter-step data dependencies before there is a concrete curator
  use case.
- Keeping compatibility behavior for prompt-level prior-output variables.
