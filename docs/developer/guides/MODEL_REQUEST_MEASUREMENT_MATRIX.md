# Model Request Measurement Matrix

Every model request the backend sends is measured at the provider boundary and
checked against known provider field limits before it is sent (ALL-1279 /
KANBAN-1836). This page maps each runtime entry path to its measurement point
and the test that proves it. The regression guard
`backend/tests/unit/lib/openai_agents/test_model_request_measurement_coverage_guard.py`
fails when a new path bypasses measurement or a new OpenAI client construction
site is not listed here.

## Why

On Sep 22 2026 the final `ask_chat_output_specialist` step of a gene-expression
flow sent 1,422,809 characters of `instructions`; OpenAI accepts at most
1,048,576 and rejected the request. The enclosing flow's
`provider_context_preflight` logged 4,339 characters because that preflight
measures application-assembled input only (no instructions, no tool schemas,
no later-turn history), and streamed specialists had no equivalent.

## Measurement points

There are two measurement points and no per-call-site instrumentation.

1. **Agents SDK model resolution.**
   `backend/src/lib/openai_agents/model_request_measurement.py`
   `install_model_request_measurement()` wraps the SDK's per-turn model
   resolution (`agents.run_internal.run_loop.get_model`, pinned SDK 0.17.4).
   Every `Runner.run`, `Runner.run_streamed` and `Runner.run_sync` turn resolves
   its model there, whatever the `RunConfig`, so each turn's model is wrapped in
   `MeasuredModel`. It measures the arguments handed to the provider adapter
   (`get_response` / `stream_response`) before the adapter sends anything, then
   attaches provider-reported usage from the response. Installed by
   `backend/main.py` `create_app()` before Sentry initializes, and at import by
   `lib/openai_agents/runner.py` and `lib/openai_agents/streaming_tools.py`
   (and transitively by every module that imports the runner). The wrapper is
   installed on `turn_preparation.get_model`; `run_loop.get_model` is rebound
   only while it is still the SDK function, so a foreign wrapper such as
   Sentry's OpenAI Agents integration (which resolves
   `turn_preparation.get_model` at call time) reaches it in either install
   order without recursion.
2. **Direct provider clients.** Code that calls an OpenAI client without the
   Agents SDK passes the bound request method to
   `call_measured_direct_request(surface=..., provider=..., api=..., kwargs=..., call=...)`,
   which measures the exact request keyword arguments, enforces limits, sends,
   and records usage.

## Runtime entry path matrix

| Runtime entry path | Code path | Measurement point | Transport | Test |
| --- | --- | --- | --- | --- |
| Standard chat supervisor, every turn | `api/chat*` -> `runner.run_agent_streamed` -> `Runner.run_streamed` | SDK resolution -> `MeasuredModel.stream_response` | WebSocket (default) or HTTP | `test_daniela_instructions_blocked_before_provider_streamed`, `test_streamed_usage_comes_from_terminal_response` |
| Flow supervisor | `flows/executor.execute_flow` -> `run_agent_streamed` | SDK resolution | WebSocket or HTTP | same as above |
| Streamed specialists (incl. flow chat-output and formatter steps) | `streaming_tools.run_specialist_with_events` -> `Runner.run_streamed` (and its structured-finalization retry run) | SDK resolution | WebSocket or HTTP | `test_daniela_instructions_blocked_before_provider_streamed`, `test_importing_specialist_or_studio_runtime_alone_installs_measurement` |
| Supervisor-invoked specialists (`as_tool` wrappers) | `agents/supervisor_agent.py` -> streaming tool -> `Runner.run_streamed` | SDK resolution | WebSocket or HTTP | same as above |
| Non-streamed specialists and domain validators | `domain_packs/validator_dispatch` -> `run_agent_sync_with_owned_openai_resources` -> `Runner.run` / `Runner.run_sync` | SDK resolution -> `MeasuredModel.get_response` | WebSocket or HTTP | `test_daniela_1_42m_instructions_blocked_before_provider_nonstreamed`, `test_sdk_retry_attempts_are_each_measured` |
| Flow validators | `flows/executor` validator step -> streaming tool | SDK resolution | WebSocket or HTTP | covered by the streamed specialist path |
| One-shot helpers: hierarchy resolution, figure-locator resolution, guardrails | `pipeline/hierarchy_resolution.py`, `pipeline/figure_locator_resolution.py`, `openai_agents/guardrails.py` -> `run_agent_with_owned_openai_resources` -> `Runner.run` | SDK resolution | WebSocket or HTTP | `test_one_shot_helper_runner_entry_is_measured` |
| Agent Studio authoring chat | `agent_studio/openai_runtime.stream_agent_studio_run` -> `Runner.run_streamed` (and `Runner.run`) | SDK resolution; deferred tool definitions sized separately | WebSocket or HTTP | `test_agent_studio_run_measures_visible_and_deferred_tools`, `test_output_schema_visible_and_deferred_tool_definitions_are_separate` |
| Later tool-loop turns | SDK run loop resolves the model again each turn | a new `MeasuredModel` per turn | any | `test_every_tool_loop_turn_is_measured_with_single_large_tool_result`, `test_accumulated_small_tool_results_counted_and_warned` |
| SDK-managed retries | `get_response_with_retry` / `stream_response_with_retry` re-invoke the wrapped model | per-turn attempt counter (`attempt`) | any | `test_sdk_retry_attempts_are_each_measured`, `test_blocked_request_is_not_retried_by_sdk_retry_policy` |
| Transport-internal retries | OpenAI HTTP client `max_retries` (including `OPENAI_COMPATIBLE_HTTP_MAX_RETRIES`) and WebSocket reconnect-before-first-event retries resend the same payload inside one SDK attempt | covered by that attempt's record; not counted separately | HTTP / WebSocket | not separately observable |
| OpenAI-compatible providers (Gemini, Groq, OpenRouter) | `lib/openai_agents/config.py` `get_model_for_agent` builds provider-bound SDK models | SDK resolution; provider-aware limits | HTTP (chat completions) | `test_compatible_provider_is_measured_not_blocked_by_openai_field_limit` |
| Standard-chat context compaction | SDK `OpenAIResponsesCompactionSession` -> `client.responses.compact` on `SafeAsyncOpenAI` in `lib/openai_agents/runner.py` | `SafeAsyncOpenAI._wrap_responses_compact` -> `call_measured_direct_request` | HTTP | `test_safe_client_compaction_request_is_measured` |
| Abstract extraction | `lib/openai_agents/prompt_utils.py` `_extract_abstract_with_llm` (`chat.completions`) | `call_measured_direct_request` | HTTP | `test_direct_chat_completion_measured_with_usage` |
| Benchmark adjudication (admin harness) | `lib/benchmarks/adjudication.py` `execute_direct_openai_adjudication` (`responses.parse`) | `call_measured_direct_request` | HTTP | `test_direct_responses_request_blocked_before_call` |

Embedding requests are not model-context requests and are out of scope.

## OpenAI client construction sites

The guard compares these with the code. A new site must be measured and added.

| Module | How its requests are measured |
| --- | --- |
| `lib/openai_agents/runner.py` (`SafeAsyncOpenAI`, default and owned clients) | SDK models resolved per turn; `responses.compact` wrapped |
| `lib/openai_agents/config.py` (`get_model_for_agent`, compatible providers) | SDK models resolved per turn |
| `lib/openai_agents/prompt_utils.py` | `call_measured_direct_request` |
| `lib/benchmarks/adjudication.py` | `call_measured_direct_request` |

## What one measurement record contains

Each SDK-level request attempt (and each direct-client call) produces one record: a `model_request_measurement` INFO
log line (numeric sizes and small labels only; `extra.model_request_measurement`
holds the full record) and a `runtime.model_request_measurement` extraction
trace event, which is mirrored to Langfuse as an EVENT observation.

| Field | Meaning |
| --- | --- |
| `measurement_scope` | `model_request` (this record) versus `application_payload` (the older `provider_context_preflight`, which sizes application-assembled data before the SDK adds instructions, tool schemas and history) |
| `measurement_basis`, `transport_payload_observed` | `agents_sdk_model_call` measures the adapter arguments; the final wire frame (HTTP body or WebSocket frame) is built inside the SDK and is not separately observed. `direct_client_kwargs` measures the exact client request |
| `outbound.instructions` | characters and UTF-8 bytes of the `instructions` field (exact) |
| `outbound.input` | input/history size, items by role, `tool_calls`, `tool_results` (count, total, largest with tool name), reasoning items, `loaded_deferred_tool_definitions` (from `tool_search_output` items: `count` is loaded function/custom definitions with namespace members flattened, `namespaces` the namespaces returned, `definition_chars` their definition sizes, `names` the loaded application tool names, `chars` the full items); `tool_calls.tool_search_calls` counts hosted searches |
| `outbound.tools` | `initially_visible` and `deferred` function tool definitions (name, description, parameters schema), hosted tool types, `namespace_headers` (count and size of the namespace name/description headers the model sees for deferred tools), handoffs. Hosted tools other than hosted MCP are sized by type and name only; their provider-side definitions are not observable |
| `tool_surface` | run-level record from the shared tool-surface compiler (ALL-1280), cumulative through this response: `runtime`, `agent_key`, `mode` (`deferred`, `eager_policy`, `eager_provider_unsupported`), `fingerprint`, eager/deferred/namespace counts and chars, `declared_names`, `deferred_names`, `searches`, `loaded_names`, `called_names`, `loaded_not_called`. Qualified wire names (`namespace.tool`) are normalized to application tool names |
| `outbound.output_schema` | structured output schema size, or null for plain text |
| `model_visible` | instructions + input + initially visible tools + handoffs + output schema, with `estimated_tokens` (characters / 4, labelled `estimate_basis`) |
| `deferred_tools` | definitions transported for hosted tool search; `loaded_status=provider_managed` because the provider decides what it loads during the response |
| `provider_managed` | components this process cannot observe: `previous_response_history`, `stored_conversation_history`, `prompt_template`, `hosted_tool_search_loaded_definitions_this_response` |
| `provider_usage` | `status=reported` with provider input, cached input, output and reasoning tokens, or `not_reported` (never zero-filled), `not_sent` for a blocked request |
| `outcome` | `completed`, `incomplete`, `failed`, `provider_error`, `cancelled`, `consumer_closed`, `stream_closed_without_terminal_event`, `blocked_before_send` |
| correlation | `trace_id`, `session_id`, cost-context `run_id` / `workflow_id` / `job_id` / `node_id` / `activity` / `document_id`, `agent_name` / `agent_id` / `agent_role`, `attempt`, SDK `sdk_trace_id` / `sdk_span_id`, `provider_response_id`, `provider_request_id` |

Cost is not recorded here. It stays on the SDK response/generation span
(`observability/cost_tracing.py`), which TraceReview counts once per provider
response id. The measurement event is an EVENT observation, never a GENERATION,
so it cannot add a second cost; `provider_response_id` joins the two.

## Limits and warnings

| Setting | Default | Effect |
| --- | --- | --- |
| `OPENAI_INSTRUCTIONS_MAX_CHARS` | 1048576 | Blocks a native OpenAI Responses request whose `instructions` exceed the limit, before sending. Other providers and fields are measured, not blocked. |
| `MODEL_REQUEST_WARNING_ESTIMATED_TOKENS` | 250000 | Warning only (log + `warnings` in the record). |
| `MODEL_REQUEST_TOOL_RESULT_WARNING_CHARS` | 200000 | Warning only, for one tool result. |

No application token or cost cap blocks scientific work. A blocked request
raises `ModelRequestBlockedError` (a `PayloadContractViolation`, category
`provider_request_blocked`, component `openai_responses.instructions`) naming
the agent, measured size, limit, setting and trace/run references. The SDK
retry policy cannot resend it. Callers report the step as failed with that
message; nothing is trimmed and saved application data is untouched.

## Sentry

A blocked request is reported once through
`report_payload_contract_violation` with fingerprint
`[payload_contract, provider_request_blocked, openai_responses.instructions]`,
compact size/limit/setting context and hashed trace/session/flow tags. Repeats
for the same agent and component in the same trace or run are logged, not
captured again, and `before_send` drops events that re-report an exception
already captured (`raise ... from`, `exc_info`, or the exception passed as a
log argument; implicit `__context__` chains are distinct failures and are not
dropped). Size
warnings and normal paging do not create Sentry events. If Sentry is
unavailable, the structured `payload contract violation` log line and the
original error remain.

When Sentry's optional OpenAI Agents integration is enabled, it wraps the
`MeasuredModel` returned by resolution, so its response-model span attribute
(set from `_fetch_response`) is not populated; its client spans still record.

Alert delivery is a separate release-validation step: a returned event id
proves local capture only. Verify ingestion and delivery through the existing
production alert rule (see `SENTRY_OBSERVABILITY.md`) with one controlled event.
