# Supervisor Curation Context Lookup Design

Date: 2026-06-06

Status: runtime slice implemented and sub-agent-reviewed on `codex/supervisor-context-lookup-design`

Related:

- `docs/design/2026-06-06-token-json-audit.md`
- `backend/src/lib/openai_agents/agents/supervisor_agent.py`
- `backend/src/lib/openai_agents/streaming_tools.py`
- `backend/src/api/chat_common.py`
- `backend/src/lib/chat_history_repository.py`
- `backend/src/lib/curation_workspace/curation_prep_service.py`
- `backend/src/lib/agent_studio/tools.py`
- `docs/design/2026-06-06-flow-guided-supervisor-simplification.md`

## Problem

The supervisor should not carry full specialist JSON in its model context just to answer a curator. Curator review and final validation already need the full canonical payload, but that is a different consumer from the supervisor's conversational synthesis.

The missing capability is a bounded lookup path. Today, if the supervisor receives only a compact specialist handoff and later needs details, it has no general-purpose tool to retrieve persisted extraction details. It also has no main-chat tool to inspect the trace IDs associated with earlier turns in the same chat session. That absence makes raw JSON handoffs tempting, and raw JSON handoffs are exactly what can inflate second-turn chat context.

## Current Architecture Split

### Supervisor-visible channel

`supervisor_agent._create_streaming_tool()` wraps each specialist as a tool and returns only the string produced by `run_specialist_with_events()`.

That string is what the supervisor sees in its current model turn. If the supervisor uses it in the final assistant answer, that text is also what durable chat history replays into later turns.

Relevant current paths:

- `backend/src/lib/openai_agents/agents/supervisor_agent.py::_create_streaming_tool`
- `backend/src/lib/openai_agents/streaming_tools.py::run_specialist_with_events`
- `backend/src/lib/openai_agents/streaming_tools.py::_reduce_specialist_output_for_supervisor`
- `backend/src/api/chat_common.py::_build_context_messages_from_durable_messages`

### Canonical review and validation channel

Canonical extraction payloads do not need to travel through the supervisor answer. Builder and domain-envelope paths emit an internal extraction-result event containing `internal.canonical_payload`. Chat persistence consumes only that internal event to build persisted extraction results.

Relevant current paths:

- `backend/src/lib/openai_agents/extraction_builder_workspace.py::build_internal_extraction_result_event`
- `backend/src/api/chat_common.py::_build_extraction_candidate_from_tool_event`
- `backend/src/api/chat_common.py::_persist_extraction_candidates`
- `backend/src/lib/curation_workspace/extraction_results.py::persist_extraction_results`
- `backend/src/lib/curation_workspace/curation_prep_service.py::run_curation_prep`
- `backend/src/lib/curation_workspace/curation_prep_service.py::_domain_envelope_from_extraction_result`

This means curator review can keep using full structured data while the supervisor receives a compact answer. The two surfaces should remain intentionally separate.

### Main-chat trace channel

Main front-window chat already records a Langfuse trace ID on assistant messages. `ChatMessage.trace_id` is persisted with each assistant turn, and feedback transcript capture serializes message-level `trace_id` values. However, the normal supervisor prompt only receives user/assistant text from durable history. It does not receive a structured trace inventory, and it has no built-in TraceReview tool.

Relevant current paths:

- `backend/src/models/sql/chat_message.py::ChatMessage.trace_id`
- `backend/src/lib/chat_history_repository.py::ChatMessageRecord`
- `backend/src/lib/chat_history_repository.py::append_message`
- `backend/src/api/chat_common.py::_persist_completed_chat_stream_turn`
- `backend/src/api/chat_execute_flow.py::_persist_execute_flow_runtime_identifiers`
- `backend/src/api/chat_execute_flow.py::_persist_completed_execute_flow_turn`
- `backend/src/lib/feedback/transcript.py::_serialize_message`

Agent Studio already has TraceReview client wrappers in `backend/src/lib/agent_studio/tools.py`. Those wrappers are useful implementation precedent, but the product surface here is the main front-window chat supervisor. This design should not require curators to open Agent Studio, copy trace IDs, or include an Agent Studio transcript in the supervisor prompt.

### Flow execution channel

Flow execution is conceptually a pre-authored main-chat supervisor run: the
curator supplies the task and step order ahead of time, and the supervisor calls
the configured step tools in order. The flow runtime should reuse the same
compact-handoff and lookup philosophy, but it should not support prompt-level
step-output chaining.

The current code has a separate dataflow layer (`input_source`, `custom_input`,
`output_key` template variables, and hidden flow memory JSON). The follow-up flow
plan removes those model-live prompt paths while preserving completed structured
artifacts for final aggregation, curation prep/handoff, evidence export,
persisted review data, and lookup tools.

Where possible, that follow-up should reuse main-chat code rather than rebuild
flow-specific equivalents: `run_agent_streamed`, streaming specialist wrappers,
current-turn curation context registration, compact handoff reducers,
`inspect_curation_context`, `inspect_chat_traces`, validator compact payload
rendering, extraction-result persistence/materialization, and durable
transcript/context-budget helpers.

The flow follow-up must also verify TraceReview parity. A real VM-backed flow run
against `backend/tests/fixtures/sample_fly_publication.pdf` should produce a
flow `trace_id` that can be inspected through the same TraceReview summary,
conversation, tool-call, cost, duplicate, payload-inventory, and extraction
diagnostic surfaces used for normal chat traces.

## Design Principle

Curation and structured extraction specialists should always complete one of these contracts:

1. **Accepted structured finalization.** The specialist calls its finalization tool with the canonical result. The runtime validates it, stores the accepted payload, and creates a compact supervisor handoff from the accepted payload.
2. **Accepted builder finalization.** The specialist stages candidates and calls the builder/materializer finalizer. The runtime stores the canonical envelope and creates a compact supervisor handoff from the finalized envelope.
3. **Explicit failure.** If finalization is missing, rejected, or over the attempt limit, the runtime raises a specialist error. It should not pass malformed raw JSON to the supervisor.

The supervisor-facing handoff is an answer and reference summary, not the canonical object graph.

Plain-text or otherwise unstructured helper agents can still return ordinary text. The key rule is narrower: once a tool/agent declares a structured curation contract, the runtime should either accept and summarize that contract or fail explicitly.

## Proposed Curation Lookup Tool

Add a supervisor built-in tool named `inspect_curation_context`.

Purpose:

- Let the supervisor retrieve compact details from current-turn or persisted extraction results.
- Keep normal specialist handoffs small.
- Avoid replaying full JSON through durable assistant text.
- Give the supervisor a way to answer follow-up questions without recalling every previous result in context.

### Tool Inputs

Proposed parameters:

| Field | Type | Purpose |
| --- | --- | --- |
| `scope` | enum | `current_turn`, `current_chat`, `current_document`, `flow_run`, or `extraction_result` |
| `detail` | enum | `inventory`, `summary`, `objects`, `evidence`, `validation_findings`, or `field` |
| `extraction_result_id` | optional string | Required when `scope=extraction_result` |
| `trace_id` | optional string | Filters current or persisted results by trace |
| `flow_run_id` | optional string | Filters flow-persisted results |
| `adapter_keys` | optional list | Narrows to gene, allele, phenotype, etc. |
| `object_ref` | optional string | Object id, pending ref id, or object type for object-level detail |
| `field_path` | optional string | JSON field path when `detail=field` |
| `limit` | int | Default 5, capped server-side |
| `cursor` | optional string | Cursor for paged result slices |

This should be separate from trace inspection. Curation context answers "what canonical extraction/validation data do we have for review?" Trace inspection answers "what happened during a previous model/tool run, and why?"

Implementation note: `current_chat`, `current_document`, and `flow_run` can share the existing extraction-result listing ownership model. Direct `extraction_result_id` and `trace_id` filters require new repository helpers and likely indexes, because current extraction-result listing is primarily scoped by user/session/document/flow/source filters.

### Tool Output

The tool should return bounded JSON text:

```json
{
  "status": "ok",
  "scope": "current_chat",
  "detail": "summary",
  "summary": "2 persisted gene extraction results are available for this document.",
  "refs": [
    {
      "extraction_result_id": "uuid",
      "trace_id": "trace",
      "adapter_key": "gene",
      "agent_key": "gene_extractor",
      "builder_run_id": "builder-run",
      "envelope_id": "extraction-result:uuid"
    }
  ],
  "results": [
    {
      "extraction_result_id": "uuid",
      "adapter_key": "gene",
      "candidate_count": 1,
      "object_count": 1,
      "validation_counts": {
        "resolved": 1
      },
      "objects": [
        {
          "object_type": "gene_mention_evidence",
          "pending_ref_id": "gene-mention-1",
          "status": "validated",
          "fields": {
            "mention": "crb",
            "primary_external_id": "FB:FBgn0259685",
            "gene_symbol": "crb",
            "taxon": "NCBITaxon:7227"
          }
        }
      ]
    }
  ],
  "truncated": false,
  "next_cursor": null
}
```

Rules:

- Return summaries and selected scalar fields by default.
- Include references every time so the supervisor can ask for a narrower follow-up.
- Never return entire canonical payloads by default.
- For `detail=field`, return only the requested JSON path or a bounded slice.
- For evidence and validation findings, cap counts and text length, include cursors, and expose exact IDs.

## Proposed Trace-Aware Main Chat Tool

Add a second supervisor built-in tool named `inspect_chat_traces`.

Purpose:

- Let the main chat supervisor answer curator questions like "why did you do this?", "what did you search?", "why did you ignore X?", or "what happened in the previous answer?"
- Give the supervisor a session trace inventory without injecting all prior TraceReview/Langfuse payloads into prompt context.
- Reuse the safe subset of existing TraceReview/Agent Studio client behavior while keeping raw payload retrieval out of the default path.
- Avoid requiring curators to copy trace IDs from feedback reports or developer tools.

### Trace Inventory Source

The tool should derive trace candidates from durable main-chat messages:

- list messages for `get_current_session_id()` and `get_current_user_id()`;
- collect ordinary assistant messages with non-empty `trace_id`;
- collect execute-flow user/runtime rows and durable `role="flow"` transcript rows with non-empty `trace_id`;
- include adjacent user question, assistant response preview, turn id, message id, created time, and active document id when available;
- optionally include traces from `extraction_results.trace_id` for the same session/document when a persisted extraction result exists but an assistant message trace is missing.

The supervisor prompt does not need this whole inventory every turn. It needs the tool. When the curator asks "the previous thing" or "why did you do that?", the supervisor can call `inspect_chat_traces(detail="inventory", limit=5)` and select the most recent or best matching trace.

Implementation notes:

- Trace IDs are not free-form lookup keys. Before any TraceReview call, the tool must resolve the requested `trace_id` from the current authorized chat/session/document inventory and reject IDs outside that inventory.
- P1 should inventory by bounded session timeline scan. `chat_messages.trace_id` currently exists on all chat rows, but the visible index name in the SQL model is scoped for Agent Studio. If main-chat trace inventory or trace-id filtering becomes frequent, add or verify an index for `chat_kind = 'assistant' AND trace_id IS NOT NULL` or for the session/user/created-at path used by the inventory query.

### Trace Tool Inputs

Proposed parameters:

| Field | Type | Purpose |
| --- | --- | --- |
| `detail` | enum | `inventory`, `conversation`, `summary`, `diagnostic_report`, `tool_calls`, `costs`, `duplicates`, or `payload_inventory` |
| `trace_id` | optional string | Required for trace-specific details except `inventory` |
| `turn_ref` | optional string | Message id, turn id, or ordinal such as `previous` |
| `query` | optional string | Text used to rank session traces by nearby user/assistant previews |
| `tool_name` | optional string | Filter diagnostic/tool-call views |
| `event_type` | optional string | Filter diagnostic timeline events |
| `candidate_id` | optional string | Filter extraction diagnostics by candidate |
| `include_sibling_traces` | bool | Include traces from the same feedback/session context when supported |
| `limit` | int | Default 5 or 20 depending on detail, capped server-side |
| `cursor` | optional string | Cursor/offset for paged outputs |

Do not include a raw `include_payloads=true` default. Payload inventory is useful because it returns payload IDs, sizes, and previews. Exact payload retrieval should be a later guarded mode, or an explicit admin/developer-only extension, because it can reintroduce the token blowups this design is trying to prevent.

### Trace Tool Output

For `detail=inventory`, return bounded session trace candidates:

```json
{
  "status": "ok",
  "detail": "inventory",
  "session_id": "chat-session-id",
  "summary": "3 assistant turns in this chat have trace IDs.",
  "traces": [
    {
      "ordinal": 3,
      "trace_id": "f3095edf162452743a4b31cafdf9801e",
      "turn_id": "turn-uuid",
      "message_id": "message-uuid",
      "created_at": "2026-06-06T13:21:00Z",
      "user_question_preview": "Can you extract the focal gene?",
      "assistant_answer_preview": "The focal gene is crb...",
      "document_id": "document-uuid"
    }
  ],
  "truncated": false,
  "next_cursor": null
}
```

For trace-specific details, return compact TraceReview-derived summaries:

```json
{
  "status": "ok",
  "detail": "diagnostic_report",
  "trace_id": "f3095edf162452743a4b31cafdf9801e",
  "summary": {
    "tool_call_count": 18,
    "reasoning_summary_status": "present",
    "validation_failure_count": 0,
    "finalization_count": 1
  },
  "reasoning_summaries": [
    "The gene extractor focused on crb because the quoted Results passage directly supported the central phenotype."
  ],
  "timeline": [
    {
      "sequence": 12,
      "event_type": "TOOL_COMPLETE",
      "tool_name": "finalize_gene_extraction",
      "output_preview": "status=complete; finalized_count=1"
    }
  ],
  "refs": {
    "trace_id": "f3095edf162452743a4b31cafdf9801e",
    "payload_inventory_available": true
  },
  "truncated": false
}
```

### Safe TraceReview Modes

Initial main-chat supervisor modes should map to bounded TraceReview endpoints through a narrow adapter:

- `inventory`: local durable chat/extraction-result query, no TraceReview call.
- `conversation`: TraceReview conversation endpoint or local durable message preview.
- `summary`: TraceReview summary / trace summary view.
- `diagnostic_report`: extraction diagnostic report with raw args/outputs disabled.
- `tool_calls`: tool-call summary/page, capped.
- `costs`: token/cost rollup.
- `duplicates`: duplicate payload report.
- `payload_inventory`: payload IDs, sizes, hashes, and previews only.

Modes to avoid in P1:

- full trace export;
- full reconstruction with payload values;
- exact payload chunks;
- raw tool args/outputs by default.

Those are valuable for developers and Agent Studio, but the main chat supervisor should reach them only through a later explicit, capped, audited path.

Do not expose the Agent Studio tool set directly to the main chat supervisor. Extract or reuse only the low-level TraceReview client behavior behind an allowlist that enforces `include_values=false`, `include_raw_args=false`, and `include_raw_outputs=false`; blocks exact payload retrieval; and applies wrapper-side caps to conversation, timeline, reasoning, and tool-call arrays.

## Same-Turn Versus Later-Turn Lookup

There are two timing cases.

### Same turn

During a specialist call, the internal extraction event exists before the supervisor finishes, but database-backed extraction rows are later-turn state. In the streaming completion path, extraction rows are persisted after the model turn finishes and before the assistant message row is committed; both are too late for same-turn supervisor inspection.

So `inspect_curation_context(scope="current_turn")` must read an in-memory/run-context registry populated when `build_internal_extraction_result_event()` is emitted. The registry should store compact refs plus canonical payloads for lookup slicing. This registry is not durable; it only helps the supervisor answer the current turn without receiving full JSON.

Suggested reference keys:

- `trace_id`
- `tool_name`
- `agent_key`
- `builder_run_id`
- `builder_candidate_ids`
- `envelope_id`
- `domain_pack_id`

### Later turns

After the assistant turn is saved, `inspect_curation_context(scope="current_chat" | "current_document")` should query persisted `extraction_results`. This path is already what `prepare_for_curation` uses, so the lookup tool should share the same ownership and filtering rules.

Suggested filters:

- `origin_session_id=get_current_session_id()`
- `user_id=get_current_user_id()`
- `document_id=_current_chat_document_id(user_id)` when a document is loaded
- `source_kind=CHAT` for ordinary chat
- `source_kind=FLOW` plus `flow_run_id` for flow results

### Trace lookup timing

Trace IDs are available earlier than persisted extraction result IDs. During a streaming run, the current trace ID is known through runtime context, but the current assistant message row is not saved until the stream completes. Therefore:

- same-turn supervisor inspection can use the current trace ID from context for the active run and any in-turn specialist registry refs;
- later-turn inspection can use persisted `chat_messages.trace_id`;
- feedback and developer reports can still use feedback `trace_ids` and stored trace snapshots, but the main chat supervisor should not depend on feedback submission.

## Supervisor Handoff Contract

Specialist tool output should be compact and deterministic:

```text
Gene extraction finalized for the current paper.
Reference: trace_id=f309..., tool=ask_gene_extractor_specialist, builder_run_id=builder-..., domain_pack_id=gene.
Objects: 1 gene_mention_evidence.
Validated values: mention=crb; primary_external_id=FB:FBgn0259685; gene_symbol=crb; taxon=NCBITaxon:7227.
Validation: resolved=1.
Use inspect_curation_context for evidence quotes, validation finding details, or full field slices.
```

The handoff should include enough for the supervisor's normal answer:

- What completed
- Whether it succeeded, was unresolved, or failed
- Core resolved values
- Counts and warning statuses
- Stable refs for lookup

The handoff should not include:

- Full `objects[]`
- Full `validation_findings[]`
- Full evidence quote arrays
- Full lookup attempts
- Full TraceReview/Langfuse payloads
- Raw JSON emitted only because a shape recognizer could not classify the result

For trace-aware follow-up, normal assistant answers should include or preserve compact trace references only when useful. The supervisor does not need to expose trace IDs to curators in every answer, but durable message metadata should keep them queryable.

## Prompt Updates

Supervisor prompt additions:

- Treat specialist handoffs as compact summaries of canonical runtime results.
- Do not ask a specialist to repeat full JSON solely to inspect details.
- Use `inspect_curation_context` when the curator asks for specific evidence, validator details, prior extracted objects, or exact field values not present in the compact handoff.
- Use `inspect_chat_traces` when the curator asks why the system did something, what tools/searches were used, what happened in a previous answer, why a value was selected or omitted, or whether a previous trace had errors.
- Start trace-aware investigation with `inventory`, then request the narrowest trace detail needed. Do not request payload inventory unless summary/diagnostics are insufficient.
- For curation prep, rely on `prepare_for_curation`; it consumes persisted extraction results, not the supervisor's prose.

Specialist prompt additions should stay minimal. Specialists should focus on finalization, not on teaching the supervisor how to retrieve data. Runtime should own the handoff.

## Implementation Plan

### P0: Document and guard the contract

- Keep this document linked from the token audit.
- Add tests asserting curation prep/review uses persisted extraction results even when the supervisor-facing specialist output is compact.
- Add tests asserting malformed structured output does not become supervisor-visible raw JSON.

### P1: Add lookup tool and in-turn registry

- Add a context-local curation context registry in the streaming runtime.
- Register canonical payload refs when internal extraction result events are emitted.
- Add `inspect_curation_context` to supervisor built-ins.
- Implement inventory and summary details first.
- Support `current_turn`, `current_chat`, `current_document`, and `extraction_result` scopes.
- Cap returned objects, fields, evidence, and validation findings.

### P1b: Add main-chat trace awareness

- Add `inspect_chat_traces` to main chat supervisor built-ins.
- Implement session trace inventory from durable `chat_messages.trace_id`.
- Reuse or extract a shared TraceReview client from `backend/src/lib/agent_studio/tools.py` for bounded trace summary, conversation, diagnostics, tool-call summary, cost, duplicate, and payload-inventory modes, but expose only an allowlisted main-chat adapter.
- Require every trace-specific request to resolve through the authorized current-session trace inventory before calling TraceReview.
- Keep raw payload retrieval and full trace export out of the initial supervisor tool.
- Add prompt instructions for curator "why did you do that?" questions.
- Add tool telemetry for trace inventory size, TraceReview response size, and truncation.

### P2: Replace heuristic handoff behavior

- Make structured specialist handoff derive from accepted finalizer payload or finalized builder payload.
- Remove broad "looks like a domain envelope" supervisor-output fallback.
- For declared domain-envelope outputs, summarize because the declared schema says it is an envelope.
- For builder/materializer outputs, summarize because builder finalization says it is an envelope.
- For other accepted structured outputs, summarize by schema family, especially domain validators.
- For unstructured specialists, pass plain text through.

### P3: Persisted detail lookup and flow support

- Add field-slice and evidence-slice modes over `extraction_results.payload_json`.
- Add validation-finding summaries for materialized domain envelopes.
- Add `flow_run` scope for flow-persisted extraction records.
- Let `inspect_chat_traces` include flow trace IDs from execute-flow runtime metadata, flow transcript messages, and flow-persisted extraction results when a flow run is part of the chat.
- Consider linking review session IDs when curation prep has already materialized rows.

P1 and P1b are main front-window chat work. Flow supervisor execution and batch processing have separate runtime/tool construction and synthetic session boundaries; they should not be assumed to inherit these tools automatically. The intended flow direction is documented in `docs/design/2026-06-06-flow-guided-supervisor-simplification.md`: remove inter-step prompt dataflow, keep structured artifacts, and expose flow details through `flow_run_id`/trace/result refs.

Runtime acceptance for the next flow slice should use the `$sym-help`
Incus/Symphony loop: create a sample flow from
`backend/tests/fixtures/sample_fly_publication.pdf`, run it in the VM-backed app,
capture `flow_run_id` and `trace_id`, and inspect that trace in local TraceReview
before declaring the implementation complete.

The sample runtime flow should include a batch-valid terminal path, ideally
curation handoff first and file output as a second smoke when practical. Chat
output alone is not enough to prove batch compatibility.

### P4: Context-budget integration

- Teach standard chat context replay to keep compact assistant text and refs, not hidden full payloads.
- Add telemetry for supervisor-visible handoff chars, lookup tool output chars, and persisted payload chars.
- Alert before provider calls when estimated context crosses configured thresholds.

## Tests To Add

- Same-turn lookup: after a mocked specialist emits an internal extraction event, the supervisor lookup tool can return a compact object summary without DB persistence.
- Later-turn lookup: persisted chat extraction results are discoverable by `current_chat` and `current_document`.
- Curation prep independence: compact specialist output still allows `prepare_for_curation` to create review rows from persisted payloads.
- No raw JSON handoff: structured finalization failure raises a specialist error; accepted structured output returns compact handoff.
- Detail caps: `objects`, `evidence`, `validation_findings`, and `field` modes obey limit and cursor caps.
- Flow support: flow-persisted extraction results are discoverable by `flow_run_id` without injecting prior step full output into model context.
- Trace inventory: a session with three assistant turns returns three trace refs with bounded adjacent user/assistant previews.
- Trace diagnostics: `inspect_chat_traces(detail="diagnostic_report")` calls TraceReview with raw args/outputs disabled and returns capped summaries.
- Trace authorization: a trace ID not present in the current authorized session/document inventory is rejected before any TraceReview call.
- Trace safety: payload inventory returns payload refs/previews only; full payload values are not available through the P1 main-chat supervisor tool.

## Open Questions

- Should same-turn lookup expose only extraction payloads, or also accepted non-extraction validator payloads?
- Should the lookup tool support full payload export at all, or should full payload inspection stay in TraceReview and curator review UI?
- Which reference should be primary in assistant text before a DB row exists: `builder_run_id`, `envelope_id`, or `trace_id + tool_name`?
- Do we want a visible curator-facing "I can inspect more details if needed" sentence, or should that remain only in supervisor instructions?
- Should exact TraceReview payload retrieval ever be exposed to the main chat supervisor, or should it stay in Agent Studio/developer tooling?
- Should trace inventory include all session traces by default, or only traces for assistant turns that are adjacent to the curator's current follow-up?

## Review Status

A requested high-depth sub-agent review checked this design against the codebase. Its review found implementation gaps in shape-based envelope fallback removal, exact extraction-result scoping, flow-attached validator compaction, validator prompt wording, evidence-detail slicing, long-session trace inventory, `turn_ref` handling, and truncation metadata. Those findings were integrated into the runtime slice and covered by focused tests where practical.

## Implementation Status

Implemented in the initial slice:

- `inspect_curation_context` is available as a main-chat supervisor built-in.
- `inspect_chat_traces` is available as a main-chat supervisor built-in.
- Current-turn curation lookup uses a context-local internal extraction-result registry populated from `INTERNAL_EXTRACTION_RESULT` events.
- Later-turn curation lookup reads authorized persisted extraction results by current session, user, document, adapter, trace, or flow scope.
- Trace lookup inventories authorized durable chat rows, including ordinary assistant trace rows and execute-flow transcript trace rows.
- Trace inventory scans a bounded recent durable chat timeline, pages trace candidates with offset cursors, and resolves `turn_ref="previous"` / `latest` from authorized trace refs without treating those words as text-search filters.
- TraceReview calls are gated through authorized session inventory before any trace-specific detail request.
- Main-chat TraceReview access is allowlisted to summary, conversation, diagnostic report, tool-call summary, costs, duplicates, and payload inventory with raw args/outputs and payload values disabled.
- `scope="extraction_result"` requires an explicit `extraction_result_id` instead of paging broad session state.
- Curation context lookup paginates top-level record matches, supports nested cursors for exact-result object/evidence/validation-finding slices, and returns compact evidence records rather than parent payload objects that merely contain an `evidence` key.
- Structured supervisor handoff no longer treats any dict with `domain_pack_id` and `objects` as a validated envelope by shape alone. Declared domain-envelope outputs and accepted builder finalizations summarize compactly; unaccepted envelope-shaped JSON returns a controlled notice instead of a validated-looking summary or raw payload.
- Standalone, batch, and flow-attached validator runtime requests use a compact model-input payload that omits duplicate `target.input_values`, `input_selectors`, and full evidence arrays while keeping the canonical request and finalization contracts unchanged.
- Validator prompts now point agents at `selected_inputs` and compact `evidence_summary` metadata instead of runtime-omitted duplicate fields.
- Flow validation attachment tool names now sanitize non-identifier binding segments before creating runtime tools.

Still intentionally out of scope for this slice:

- Full payload retrieval from main chat.
- Global trace lookup outside the authorized current chat/session/document inventory.
- Dedicated database indexes for direct main-chat trace lookup.
- Flow supervisor access to the new main-chat lookup tools. The current slice supports compact flow-attached validator payloads and main-chat lookup of persisted flow results by `flow_run_id`, but it does not make flow supervisors themselves trace/curation lookup agents. The next flow slice should remove prompt-level previous-output chaining and keep flow details available through structured artifacts and refs.
- Further compaction of long `selected_inputs` values such as very long evidence quotes or identity-resolution notes. This slice removes semantic duplicate fields while preserving validator-required selected inputs.
- Mandatory finalize/repair feedback for custom flow validators that do not already expose a structured output/finalization contract.

## Recommendation

Proceed with compact handoff plus lookup. The supervisor does not need full specialist JSON for normal synthesis, and curator review does not depend on supervisor-visible text. The missing pieces are bounded retrieval tools: one for canonical curation context and one for trace-aware "what happened and why?" investigation in the main chat. Both should expose summaries and refs first, not full payloads.
