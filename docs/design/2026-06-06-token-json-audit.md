# Token and JSON Context Audit

Date: 2026-06-06

Status: audit complete; implementation recommendations only

Related samples:

- `docs/design/token-json-audit/chat-two-turn-trace-metrics.sample.json`
- `docs/design/token-json-audit/context-paths.sample.json`
- `docs/design/token-json-audit/validator-input-values-duplication.sample.json`

Related follow-up design:

- `docs/design/2026-06-06-supervisor-curation-context-lookup.md`
- `docs/design/2026-06-06-flow-guided-supervisor-simplification.md`

## Question

Are prompts and JSON structures passed optimally between agents? Are agents getting only what they need? Are duplicate structures being passed? Does a second curator question inherit thousands or millions of tokens from prior work? Do flows and batch processing have the same risk?

## Short Answer

The two-turn paper-chat reproduction did not show runaway context from ordinary durable chat history. The second gene-extraction turn was slightly cheaper than the first: 63,757 model tokens versus 66,850. The second turn's first supervisor input contained only the prior user question, the prior visible assistant answer, and the current user question. It did not contain raw TraceReview JSON, raw tool outputs, or the prior trace payload.

The system is not fully optimal yet. The main risk is not normal two-turn chat. The risk is unbounded or full JSON continuation in specialist/validator retries, Agent Studio initial context and tool loops, flow template variables, hidden flow replay messages, and long multi-turn sessions. Exact duplicate payload detection found 0 duplicate groups in both live traces, but there is meaningful semantic duplication inside validator request JSON and repeated validation summaries.

## Live Evidence

Runtime target:

- Backend: `http://192.168.86.44:8000`
- TraceReview: `http://192.168.86.44:8001`
- Paper chat session: `ea76b64d-552d-4e3b-b491-d40311f4df1e`

Trace pair:

| Turn | Trace | Model tokens | Model calls | Tool calls | Diagnostic payload JSON chars | Duplicate groups |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| First gene extraction | `ac089613a87ebe149cf70b38129e9c3d` | 66,850 | 6 | 18 | 294,715 | 0 |
| Second same-session extraction | `f3095edf162452743a4b31cafdf9801e` | 63,757 | 6 | 18 | 261,198 | 0 |

The diagnostic payload numbers are from TraceReview diagnostic report fields `data.summary.payload_exchange_json_chars` / `data.summary.estimated_payload_exchange_tokens`, mirrored by `data.size_summary.exchange_json_chars` / `data.size_summary.estimated_exchange_tokens`.

Persisted durable chat after the second turn was only 4 text rows:

- user first question, no `payload_json`
- assistant first answer, `trace_id=ac089...`, no `payload_json`
- user second question, no `payload_json`
- assistant second answer, `trace_id=f309...`, no `payload_json`

The exact second-turn first supervisor input payload was 725 characters and contained:

- prior user question
- prior assistant answer
- current user question

The final second-turn supervisor synthesis input was 2,433 characters. It contained the same visible chat context plus one reduced domain-envelope specialist summary, not the full extraction envelope.

## Architecture Findings

### Standard Chat

Relevant code:

- `backend/src/api/chat_common.py::_build_context_messages_from_durable_messages`
- `backend/src/lib/chat_transcript.py::list_session_text_exchanges`
- `backend/src/lib/openai_agents/runner.py::_normalize_context_messages`

Standard chat builds context by reading all completed durable user/assistant text exchanges, then appending the current user message. It does not replay raw tool JSON from prior chat turns. This is why the reproduced second turn stayed small at the supervisor boundary.

The weak point is that there is no hard server-side token, character, or exchange cap. A long session with many verbose assistant answers can still grow steadily. Flow turns can also add hidden assistant memory messages into this same transcript path.

### Specialist To Supervisor

Relevant code:

- `backend/src/lib/openai_agents/streaming_tools.py::_reduce_specialist_output_for_supervisor`
- `backend/src/lib/openai_agents/streaming_tools.py::_domain_envelope_supervisor_summary`

Domain-envelope extraction outputs are reduced before returning to the supervisor. In the live second trace, the supervisor saw a compact validated-gene summary instead of the full materialized envelope. This is good and should be preserved.

The caveat is shape recognition. If a structured output is not recognized as a domain envelope and has no dedicated `answer` field, it can still be returned in full to the supervisor.

### Validator Agents

Validator calls were the largest paid model inputs in the live traces. In the second trace, validator input payloads grew from 4,144 characters to 31,538 characters across the validator conversation. The provider-reported token totals were higher than these serialized snippets because system prompts, tool schemas, model framing, and SDK content are also counted.

There is semantic duplication in validator request JSON:

- `selected_inputs`
- `target.input_values`

Those carry overlapping content. This may be contractually convenient, but it is not token-optimal. Validator retries and continuations can also resend prior request JSON, reasoning summaries, tool calls, and tool outputs.

Focused follow-up on 2026-06-06 confirmed this is exact duplication by construction. `build_domain_validation_request()` builds `selected_inputs`, then `_validation_target()` copies that same mapping into `ValidationTarget.input_values`. The single-agent validator runner serializes `request.model_dump(mode="json")` directly, and the batch runner serializes every `job.request.model_dump(mode="json")` inside `requests[]`.

Real trace measurements:

| Trace | Validator input payloads checked | `selected_inputs` chars | Normalized model-input chars saved by deleting only `target.input_values` | Approx saved input tokens |
| --- | ---: | ---: | ---: | ---: |
| `ac089613a87ebe149cf70b38129e9c3d` | 4 | 1,328 | 1,528 per validator generation, 6,112 total | ~1,528 total |
| `f3095edf162452743a4b31cafdf9801e` | 4 | 1,145 | 1,343 per validator generation, 5,372 total | ~1,343 total |

The code already treats `target.input_values` as non-identity context: `_validator_result_target_matches_request_identity()` excludes `input_values`, and accepted finalization normalizes the result back to the server-side `request.target`. Most backend consumers use `request.selected_inputs`; materialization patch metadata stores `selected_inputs` and `input_selectors`, not `target.input_values`.

Affected surfaces:

- Package validator prompts: ten Alliance prompts currently say to read from both `selected_inputs` and `target.input_values`; the gene prompt specifically names `target.input_values` as paper context. These should be updated to make `selected_inputs` the canonical validator input context.
- Flow validator attachments: `backend/src/lib/flows/executor.py` sends `validation_request: request.model_dump(mode="json")`, so flow-driven validator handoffs carry the duplicate too.
- Batch validation: package-scoped validator batch payloads include full duplicated request JSON for every request.
- Tests: several unit and contract tests assert `request.target.input_values == request.selected_inputs`, so schema-level removal is a wider contract change than prompt serialization slimming.

Recommended first patch: keep the internal schema for now, but add a validator prompt payload renderer that omits `target.input_values` and uses it in single, batch, and flow validator prompt/tool handoffs. Update package prompts and repair instructions from "copy target exactly" to "copy target identity fields exactly; use `selected_inputs` for request context." Do not preserve fallback wording that tells validators to read `target.input_values` when present; once the provider-bound payload is compacted, `selected_inputs` should be the only prompt-visible validator input context. This keeps request IDs, internal metadata, and materialization stable while removing the duplicate from paid model context.

### TraceReview And Langfuse

TraceReview/Langfuse payloads are intentionally rich. Largest second-turn examples included:

- `metadata.agent_config`: 41,053 chars
- model outputs around 33k-39k chars
- `specialist.summary` event payload: 25,878 chars
- AGR curation lookup tool output: 13,391 chars

This is mostly observability storage, not live chat replay. It is extremely useful for audit and should remain available through paged lookup tools. The critical boundary is to keep this data out of model context unless summarized or explicitly fetched.

### Agent Studio / Chat With Claude

Relevant code:

- `backend/src/api/agent_studio.py::chat_with_opus`
- `backend/src/lib/agent_studio/chat_session.py::prepare_agent_studio_turn`
- `backend/src/lib/agent_studio/prompt_builder.py::format_conversation_context`

Agent Studio differs from standard chat. The live Anthropic call uses `request.messages` supplied by the frontend. It also builds a context-sensitive system prompt. In Agent Workshop, that system prompt can include the workshop draft up to 12,000 characters and the selected group draft up to 6,000 characters. When a selected agent is in context, the base prompt or group rules can also be injected. During a tool loop, the backend appends the assistant tool-use blocks and full `json.dumps(tool_result)` messages to `current_messages` for the next Anthropic call.

Durable storage is more compact than the live loop: persisted tool-call audit records use summaries, not raw full tool results. But the initial Agent Studio call can already be large, and the live tool loop can further accumulate large tool JSON inside one turn. This matters especially now that Agent Studio has powerful TraceReview tools.

### Flows

Relevant code:

- `backend/src/api/chat_execute_flow.py::_build_flow_memory_assistant_message`
- `backend/src/lib/flows/executor.py::execute_flow`
- `backend/src/lib/flows/executor.py::_wrap_with_step_order`

Flow execution passes a single initial user prompt to `run_agent_streamed`. It does not preload the entire durable chat transcript into each flow run. The runtime already behaves like a guided supervisor run with strict tool-order gating.

The unnecessary risk is the old dataflow layer. Step state stores full `output` internally and stores `output_preview` for compact SSE display. If a node declares an `output_key`, the full `result_text` is stored in template variables. Custom flow templates can therefore inject full prior outputs into later steps. The clarified product contract does not need this: flows are ordered extraction/review/export plans, not inter-agent prompt dataflow graphs.

After a flow completes, durable chat stores a hidden assistant memory message for follow-up grounding. It currently has caps:

- visible final output: 2,500 chars
- specialist outputs: first 8, 3,500 chars each before compaction
- hidden JSON: 18,000 chars max, with a current compaction path that should be removed from model-live replay

This is bounded, but many flow turns can still add repeated hidden JSON to later standard chat context. The follow-up plan is to replace replayable hidden flow JSON with compact flow/result/trace/review/file refs and let main-chat lookup tools retrieve details.

### Batch Processing

Relevant code:

- `backend/src/lib/batch/processor.py::process_batch_task`
- `backend/src/lib/batch/processor.py::_execute_flow_for_document`
- `backend/src/lib/batch/validation.py::validate_flow_for_batch`

Batch processing runs documents sequentially. Each document gets a synthetic session id like `batch-<batch_id>-doc-<document_prefix>` and an `execute_flow()` call with one initial prompt. Prior batch documents are not automatically injected into later document contexts.

One caveat: the current synthetic session id uses `document_id[:8]`. That does not create prompt-context leakage in the audited flow path, but using the full document UUID would be safer for trace/session disambiguation and would avoid rare prefix collisions.

Batch compatibility requires a PDF extraction step and a file-output or curation-handoff exit. The cost risk is linear scale across documents and flow steps, not cross-document context accumulation.

## Optimization Gaps

1. Standard chat needs a server-side context budget.
   The current replay-all-text behavior is correct for short sessions but has no hard cap. Add a bounded context builder that keeps recent exchanges, keeps compact trace/session references, and summarizes or drops old turns.

2. Agent Studio needs live context and tool-result compaction.
   Frontend `request.messages`, workshop prompt context, selected prompt context, and tool results should be budgeted before Anthropic calls. Tool results should be passed back to Claude as compact summaries plus lookup handles by default. Full JSON should remain available through explicit paged tools.

3. Validator request JSON should be slimmer.
   Review whether `selected_inputs` and `target.input_values` both need full copies. If both are required for contract clarity, cap long values such as evidence quotes and identity notes before retry/continuation.

   Initial implementation on `codex/supervisor-context-lookup-design`: standalone, batch, and flow-attached validator model inputs now omit duplicate `target.input_values`, `input_selectors`, and full `evidence[]`, replacing evidence with compact `evidence_summary`. The canonical request/result contracts still retain the full data for materialization. Long `selected_inputs` values are intentionally preserved in this slice and remain the next validator-specific token target.

4. Flow template variables should be removed as prompt inputs.
   The current product need is a guided supervisor run where each agent step gets the original task/document context plus step-local instructions. Prior step data should remain in canonical flow artifacts for final aggregation, curation review, evidence export, and lookup tools, not be substituted into later prompts.

5. Hidden flow replay should move toward lookup handles.
   The current 18k cap prevents infinite single-turn payloads, but future standard chat will replay each hidden flow memory message. Store flow run ids, trace ids, file ids, review session ids, extraction result ids, and adapter keys in transcript text, then let agents call lookup tools for details.

   Reuse the main-chat lookup and transcript/context-budget machinery where
   possible. Flow should not grow a separate hidden-memory system if the main
   chat can already replay compact refs and use lookup tools for detail.

6. Observability payloads need a "never live by default" rule.
   TraceReview payload inventories, reconstruction events, and Langfuse payloads should stay paged. Agent Studio prompts should emphasize summary-first, payload-id-second, full-payload-last behavior.

7. Add telemetry guardrails.
   Log per-run counts for `context_messages`, visible chars, Agent Studio system prompt chars, tool-result chars appended to Agent Studio `current_messages`, validator request chars, and flow hidden memory chars. Emit warnings at 100k, 250k, and 1M estimated tokens before a provider call.

## Recommended Implementation Plan

### P0: Prevent Another 6.7M-Token Incident

- Add a shared context-budget utility for message arrays before provider calls.
- Apply it to standard chat durable replay, Agent Studio `request.messages`, and Agent Studio system-prompt context additions.
- Add Agent Studio tool-result compaction for TraceReview and domain-envelope tools.
- Add a preflight metric event when any provider call would exceed a configurable token estimate.

### P1: Make JSON Passing Intentional

- Split tool outputs into `summary`, `refs`, and `full_payload_lookup` forms.
- Remove flow `output_key` prompt-template behavior. Preserve output aggregation through structured flow artifacts and deterministic output/review/export services.
- Slim validator requests or add retry-specific compact validation request rendering.
- Add unit tests for duplicate/semantic duplicate fields in validator payloads.

### P2: Make Auditing Easier

- Add a TraceReview endpoint that reports "model-live context" separately from "observability-only payloads".
- Add a conversation-context report endpoint for a session: message count, visible chars, hidden flow memory chars, and estimated token replay.
- Add batch run aggregate token summaries by document and by flow step.

## Tests To Add

- Standard chat: create 50 prior text exchanges and assert the provider-bound context stays under budget while retaining recent turns and a summary/lookup marker.
- Flow follow-up: create several flow summary rows and assert replay uses refs/counts rather than specialist output JSON.
- Flow terminal output: assert a curation-handoff or file-output flow can finish
  without injecting prior step output into a later prompt/query string.
- Agent Studio: simulate large frontend history, workshop context, selected prompt context, and TraceReview tool results; assert the next Anthropic call receives bounded context and compact results plus payload handles.
- Validator retry: force an empty-output retry and assert retry input has bounded evidence quote and lookup result sizes.
- Batch: run two synthetic documents through a mocked flow and assert document 2 context does not include document 1 outputs.

## Validation Notes

Runtime-validated:

- Standard chat two-turn paper scenario.
- TraceReview summary, costs, diagnostic report, payload inventory, payload lookup, and duplicate scan.
- Durable history endpoint for the reproduced session.

Code-audited:

- Flow execution context and hidden flow memory.
- Batch processor context isolation.
- Agent Studio Anthropic tool-loop context behavior.

Required next runtime validation:

- Run a real VM-backed flow using `backend/tests/fixtures/sample_fly_publication.pdf`.
- Capture the flow `flow_run_id` and `trace_id`.
- Inspect the flow in local TraceReview and compare the core observability
  surfaces against a normal chat trace: summary, conversation, tool-call summary,
  costs, duplicates, payload inventory, and extraction diagnostic report.
- Confirm the flow's model-live context no longer carries full previous step
  output once the guided-supervisor simplification is implemented.

Independent review:

- A requested 5.5/xhigh review agent checked the document against the codebase. Its findings about Agent Studio initial context, flow replay wording, batch session-id caveat, and exact TraceReview metric field paths were integrated into this revision.

Not runtime-validated in this pass:

- A new flow execution and batch run. The sandbox had no saved flows or batches exposed through `/api/flows` and `/api/batches`, and creating a full PDFX-backed batch run would have been higher setup/cost than the additional evidence justified for this audit.

## Bottom Line

The ordinary second chat turn did not inherit raw prior trace JSON and did not reproduce multi-million-token behavior. The system is partly optimized already, especially specialist-to-supervisor reduction for domain envelopes. The remaining risk is concentrated in live tool loops, validator continuations/retries, uncapped long-session replay, full flow template variables, and hidden flow memory replay. The right next move is a shared budget/compaction layer plus lookup-handle based retrieval, not removing the rich TraceReview or validation data that curators and developers need.

## Follow-Up: Supervisor Lookup Tool

The supervisor does not need the full specialist JSON in context for normal answer synthesis, and curator review/final validation does not depend on the supervisor-visible prose. Canonical extraction payloads already travel through internal extraction-result events and persisted `extraction_results.payload_json`; curation prep and review rows consume those persisted payloads.

The missing optimization layer is bounded supervisor lookup. See `docs/design/2026-06-06-supervisor-curation-context-lookup.md` for the proposed main-chat tools: `inspect_curation_context` for canonical extraction/review data and `inspect_chat_traces` for trace-aware "what happened and why?" follow-up. The shared rule is compact handoffs by default, canonical payloads retained for review, and scoped/paged retrieval only when the supervisor truly needs more.
