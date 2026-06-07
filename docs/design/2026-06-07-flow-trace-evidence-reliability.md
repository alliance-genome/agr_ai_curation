# Flow, Trace, and Evidence Reliability Follow-up

Date: 2026-06-07

## TL;DR

The tunnel-backed smoke run gave us enough evidence to split the token-budget work from a more urgent reliability issue: flow extraction can finish as `completed` with no persisted extraction result, no adapter key, and no evidence records, even after the builder tools report a verified evidence record.

There are two separate empty-state problems to fix. First, the runtime evidence registry stayed empty, which means the internal builder payload, candidate construction, or evidence extraction failed before final persistence. Second, final flow persistence accepted an empty candidate/result set as success. `_persist_flow_extraction_candidates()` returns `[]` when there are no candidates, and `_persist_flow_extraction_candidates_or_build_error()` currently returns success for that empty result. Together, those let a flow end with `FLOW_FINISHED.status=completed`, `total_evidence_records=0`, and no extraction-result refs.

The next implementation should be diagnostics-first and fail-closed for steps that are expected to produce curation extraction output. We do not yet know whether the break was internal event emission, event lookup, adapter resolution, candidate construction, evidence extraction, or persistence. The fix should record that handoff audit in the flow output, then emit a specific `FLOW_ERROR` instead of reporting completion when a required extraction candidate/evidence/result is missing. The expected-output signal must be explicit so ordinary non-curation flows do not fail just because they have no extraction candidates.

## Evidence Sources

Raw run artifacts are intentionally ignored under:

- `docs/design/token-budget-evidence-2026-06-06/validation-rerun-fresh-pdf-20260607T000552Z/`
- `docs/design/token-budget-evidence-2026-06-06/validation-rerun-with-tunnels-20260607T0008Z/`

The fresh PDF run summary is:

- `dev_release_smoke_20260607T001858Z.json`
- flow trace prefix: `3b62237...`
- streaming chat trace prefix: `d1840f23...`

Key observation from the smoke JSON:

```text
Flow finished without persisted evidence records:
status=completed
failure_reason=None
total_evidence_records=0
step_evidence_counts={"1": 0}
adapter_keys=[]
extraction_result_refs=[]
review_session_ids=[]
```

Key observation from backend logs:

```text
record_evidence result status=verified
evidence record id present
stage_gene_mention_evidence complete
finalize_gene_extraction complete
generated text instead of structured output
```

The successful non-flow chats identified `crb/crumbs` and `ninaE/Rh1`, with `crb` and `ninaE` validated by FlyBase identifiers. The flow run still failed the smoke because the persisted flow evidence contract was empty.

## What This Means

This is not primarily evidence that we should truncate JSON scalar values. The observed provider contexts were small:

- standard chat context payloads: 246-459 JSON characters
- validator payloads: 1879-2267 JSON characters
- flow context payload: 848 JSON characters

The immediate issue is reliability and observability around handoff boundaries:

- builder tools can create meaningful internal state;
- supervisor-facing final text can be a compact summary;
- flow persistence expects a structured extraction envelope candidate;
- the system can lose the structured payload or reject it without making the final flow status fail.

This does not call for a blind LLM retry. In the failed run, the expensive extraction work already happened: `record_evidence`, `stage_gene_mention_evidence`, and `finalize_gene_extraction` all completed. Retrying the specialist could consume more tokens and duplicate evidence while still leaving the backend handoff gap unfixed. A bounded retry/recovery may be useful later, but first the runtime must identify the failed handoff point and stop reporting success.

## Flow Code Path

The specialist wrapper in [backend/src/lib/flows/executor.py](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/backend/src/lib/flows/executor.py:2384) captures an internal extraction-event cursor before invoking the specialist tool.

After the specialist returns, it asks `_internal_extraction_tool_output_since(...)` for the canonical internal extraction payload. If no internal payload is found, it falls back to the specialist result text.

Then it calls `build_extraction_envelope_candidate_with_evidence(...)` with:

- `step_result`
- `agent_key=agent_id`
- `adapter_key=curation_adapter_key`
- step metadata

That helper in [backend/src/lib/curation_workspace/extraction_results.py](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/backend/src/lib/curation_workspace/extraction_results.py:533) builds a persistable candidate only if the payload is an extraction envelope and an adapter key can be resolved.

The completed step records:

- `usedInternalExtractionPayload`
- `candidateBuilt`
- `evidenceCount`

Those already appear in `FLOW_STEP_TIMING`, which gives us the right event surface. It does not yet explain the reason: the event needs additional fields for payload presence, candidate rejection, adapter resolution, and evidence-extraction source.

## Empty-State Split

The run exposed two related but distinct empty states:

1. Runtime evidence was empty.
   `FLOW_FINISHED.total_evidence_records` is computed from the in-memory `evidence_registry.records()`. The observed `step_evidence_counts={"1": 0}` means the step did not add extracted evidence to the registry. Final persistence cannot explain that by itself; the failure is earlier in the step path.

2. Persisted extraction results were empty.
   `FLOW_FINISHED.extraction_result_refs` is populated only after extraction-result records are persisted. The observed empty refs mean the flow did not persist a reviewable extraction record.

The implementation should diagnose both states separately:

- internal extraction payload missing or invisible;
- payload present but not an extraction envelope;
- candidate rejected because adapter metadata was missing;
- candidate built but no evidence records extracted;
- candidate/evidence existed but persistence returned no records.

## Missing Diagnostics

The saved artifacts show where the flow ended, but not the exact point where the handoff failed. Specifically, they do not answer:

- did the builder workspace have `builder_finalization` after `finalize_gene_extraction`?
- was `INTERNAL_EXTRACTION_RESULT` emitted?
- did `add_specialist_event(...)` register it in the current-turn context or live/collected event list?
- did `_internal_extraction_tool_output_since(...)` find it for the flow step's tool name?
- if found, did `build_extraction_envelope_candidate_with_evidence(...)` reject it?
- if a candidate was built, did evidence extraction return zero records?
- if candidate/evidence existed, did persistence receive them and return zero records?

The code already has coarse fields in `FLOW_STEP_TIMING`: `usedInternalExtractionPayload`, `candidateBuilt`, and `evidenceCount`. Those are useful but insufficient; they cannot distinguish "internal event never emitted" from "internal event emitted but missed" from "payload found but rejected."

Add an explicit handoff audit event for extraction-capable flow steps:

```json
{
  "type": "FLOW_EXTRACTION_HANDOFF_AUDIT",
  "details": {
    "step": 1,
    "tool_name": "ask_dev_release_smoke_agent_specialist",
    "candidate_expected": true,
    "candidate_expected_from": ["catalog_curation_metadata", "builder_finalize_tool"],
    "builder_finalization_seen": null,
    "internal_event_emitted": null,
    "internal_event_found_by_flow": false,
    "internal_payload_source": null,
    "candidate_built": false,
    "candidate_reject_reason": "internal_payload_missing",
    "adapter_key_resolved": false,
    "evidence_count": 0,
    "persisted_result_count": 0
  }
}
```

The first implementation can use `null` for fields that are not yet observable, but it should make the unknowns visible and preserve the event in smoke artifacts. The acceptance bar is not "guess the culprit"; it is "the next run tells us which actor did not hand off what."

## Persistence Hole

The final persistence path in [backend/src/lib/flows/executor.py](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/backend/src/lib/flows/executor.py:3671) calls `_persist_flow_extraction_candidates_or_build_error(...)` when extraction was not already persisted.

The problem is lower down:

- `_collect_completed_step_candidates(...)` returns only steps with an `ExtractionEnvelopeCandidate`.
- `_persist_flow_extraction_candidates(...)` returns `[]` if `candidates` is empty or `document_id` is missing.
- `_persist_flow_extraction_candidates_or_build_error(...)` returns `True, None, None, persisted_records` even when `persisted_records == []`.

So a flow can run every required step and still finish as successful if all candidate construction failed silently. This should become an error only when the step was expected to produce curation extraction output. That expectation can come from catalog curation metadata, flow node metadata, extraction-builder tool usage, or explicit step state captured during specialist execution.

## Likely Root Causes

### 1. Empty Candidate Persistence Is Treated As Success

Confidence: high.

This directly matches the observed final event:

- status `completed`
- no failure reason
- zero evidence
- no persisted extraction refs
- no adapter keys

Fix:

- Treat empty candidates as an error for curation extraction steps.
- Treat empty persisted records as an error when candidates were expected.
- Include reason codes such as `no_extraction_candidates`, `missing_document_id`, or `extraction_persistence_empty_result`.
- Define `candidate_expected` explicitly from node/catalog curation metadata or extraction-specific step state, rather than inferring it from an empty candidate list.

### 2. Builder Finalization Did Not Reach Flow Candidate Building

Confidence: medium-high.

The builder tool path in [backend/src/lib/openai_agents/streaming_tools.py](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/backend/src/lib/openai_agents/streaming_tools.py:5420) emits `INTERNAL_EXTRACTION_RESULT` when `builder_finalization` exists. Flow then tries to consume that internal event after specialist invocation.

The logs show the builder tools completed, but the final step evidence count was zero. That suggests one of these happened:

- `builder_finalization` was never set despite the builder tools completing;
- `INTERNAL_EXTRACTION_RESULT` was emitted but not visible to the flow cursor lookup;
- the internal payload was visible but rejected by candidate construction;
- the adapter key was missing, so the envelope could not become a candidate.

Fix:

- Add a first-class result object from specialist invocation that separates `supervisor_text` from `internal_tool_output`, instead of relying only on a global event-list side channel.
- Add targeted diagnostics to `FLOW_STEP_TIMING`: `internalPayloadFound`, `candidateRejectReason`, `adapterKeyResolved`, and `evidenceExtractedFrom`.
- Add unit coverage around the flow wrapper consuming a builder-finalized payload from a custom curation agent.
- Preserve enough audit state to say whether `builder_finalization` was missing, `INTERNAL_EXTRACTION_RESULT` was missing, the flow cursor missed it, or candidate construction rejected it.

### 3. Custom Agent Adapter Metadata May Not Be Present At The Flow Node

Confidence: medium.

The smoke creates a custom agent from `template_source=gene_extractor`. Creation inherits the parent template's model, tools, output schema, category, and records `template_source=gene_extractor` in [backend/src/lib/agent_studio/custom_agent_service.py](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/backend/src/lib/agent_studio/custom_agent_service.py:399).

Catalog metadata has a curation inheritance path in [backend/src/lib/agent_studio/catalog_service.py](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/backend/src/lib/agent_studio/catalog_service.py:1644): if a DB custom agent has no direct config definition, operates on a document, and points to a launchable curation template, it can inherit the template's curation metadata.

The final flow event still had `adapter_keys=[]`. That does not prove metadata inheritance failed, but it means adapter metadata was not represented in any persisted candidate.

Fix:

- Add a smoke-level assertion that the custom agent catalog entry contains `curation.adapter_key` before flow execution.
- Add a backend test that a custom agent from `gene_extractor` resolves curation metadata through `get_agent_metadata(...)`.
- If a flow node is a curation extraction node and no adapter key resolves, fail before specialist execution or emit a typed `FLOW_ERROR`.

### 4. TraceReview 404 Was Likely A Trace-Ingest Failure, Not Proof Of No Trace

Confidence: medium.

The fresh trace IDs were not retrievable through local or remote TraceReview. Backend logs showed OTLP exporter `401 Unauthorized`, so the likely failure was Langfuse ingest/auth. TraceReview then probably had nothing to read, but a direct ingest/readback preflight is needed before treating that as proven.

Fix:

- Add a local trace-ingest/readback preflight before expensive smoke runs.
- Make the preflight compare backend exporter configuration and TraceReview source configuration by source name and key fingerprint, never by printing secrets.
- Surface OTLP 401 as a readiness/preflight failure, not as a later TraceReview mystery.

### 5. Redis Auth Noise Is A Separate Readiness Problem

Confidence: medium.

The backend repeatedly logged Redis auth failures while checking stream cancellation and active-stream cleanup. [backend/src/lib/redis_client.py](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/backend/src/lib/redis_client.py:20) caches a Redis client from `REDIS_URL` and logs connection/error details.

Fix:

- Add Redis auth/ping to readiness or smoke preflight when streaming/cancel semantics matter.
- Redact Redis URLs in logs.
- On Redis auth failures, clear the cached client and rate-limit repeated errors.
- Prefer deriving `REDIS_URL` from the current `REDIS_AUTH` in local compose, or detect stale overrides.

## Proposed Implementation

### P0: Add A Flow Extraction Handoff Audit

Before adding retry behavior, add deterministic audit state around the existing handoff:

- capture whether the specialist step is expected to produce curation extraction output;
- capture whether the builder/finalizer path produced a finalized builder payload;
- capture whether `INTERNAL_EXTRACTION_RESULT` was emitted and where it was registered;
- capture whether the flow cursor found an internal payload after the specialist call;
- capture candidate rejection reason, adapter key resolution, evidence count, and persisted result count.

Emit this as `FLOW_EXTRACTION_HANDOFF_AUDIT` and include a compact copy of the final audit buckets in any `FLOW_ERROR` and `FLOW_FINISHED` event. The smoke script should preserve this event in its evidence artifact.

### P0: Fail Closed On Empty Flow Evidence

Update `_persist_flow_extraction_candidates_or_build_error(...)` to know whether a curation extraction candidate was expected. `candidate_expected` should be derived from explicit extraction-step state, such as catalog curation metadata, flow node metadata, or extraction-builder tool usage. It should return failure when:

- no candidate was collected for an extraction step;
- candidates existed but no records persisted;
- document ID is required but missing;
- a candidate has no adapter key.

The final `FLOW_FINISHED` event should then contain:

- `status=failed`
- a concrete `failure_reason`
- `step_evidence_counts`
- `candidate_built_counts`
- rejection reason buckets

This should not trigger an automatic specialist retry. First fail closed with audit details. Add bounded recovery later only if the audit shows a deterministic recoverable state, such as "builder finalization exists but event lookup missed it."

### P0: Preserve The Internal Builder Payload Explicitly

Add a typed handoff from specialist execution back to the flow executor:

```text
SpecialistInvocationResult
  supervisor_output: str
  internal_extraction_output: str | None
  internal_payload_found: bool
  internal_event_source: event_list | live_event_list | direct
```

Keep the existing internal event for UI/trace compatibility, but do not make flow persistence depend solely on discovering it from collected events after the fact.

Add one surgical test around this boundary: an `INTERNAL_EXTRACTION_RESULT` emitted from the specialist streaming path must be visible to `_internal_extraction_tool_output_since(...)` and must become the `step_result` used for candidate/evidence construction.

If the audit shows the internal event path is the weak link, prefer direct handoff of the canonical payload over an LLM retry. The specialist already did the work; the backend should not ask it to do the same extraction again just to repair a missed internal event.

### P1: Explain Candidate Rejection

Extend `build_extraction_envelope_candidate_with_evidence(...)` or wrap it with a diagnostic variant that can say:

- `payload_not_json`
- `payload_not_extraction_envelope`
- `missing_agent_key`
- `missing_adapter_key`
- `agent_not_launchable_for_curation`
- `evidence_records_empty`

This should feed `FLOW_STEP_TIMING` and any final `FLOW_ERROR`.

### P1: Validate Custom Curation Agent Metadata Before Running

Before executing a flow node that is intended to produce curation output, verify:

- catalog metadata resolved for the agent;
- `curation.adapter_key` is present;
- required document context is present;
- curation template inheritance is applied for custom agents.

This can run during flow tool construction so failures happen before any model call.

### P1: Add TraceReview/Langfuse Preflight

Add a dev-smoke preflight stage that:

- checks backend trace exporter health;
- emits or locates a tiny known trace;
- confirms TraceReview can retrieve it from the configured source;
- records only source names, status, and key fingerprints.

### P2: Redis Readiness And Logging Cleanup

Add a `redis.ping()` readiness check for local/docker stacks where streaming uses Redis, redact connection URLs, and reset the cached client after authentication failures.

## Tests To Add

- `backend/tests/unit/lib/flows/test_executor.py`
  - extraction handoff audit is emitted for curation-capable steps
  - audit distinguishes internal payload missing, candidate rejected, evidence empty, and persistence empty
  - no candidate for curation extraction step -> `FLOW_ERROR`
  - candidate list empty -> persistence helper returns failure
  - candidates present but persisted records empty -> failure
  - builder internal payload -> candidate/evidence persisted
  - emitted `INTERNAL_EXTRACTION_RESULT` is visible to the flow cursor lookup
  - non-curation flow with no extraction candidates does not fail the extraction-specific gate
  - no blind specialist retry is attempted for backend handoff failures

- `backend/tests/unit/lib/curation_workspace/test_extraction_results.py`
  - domain-envelope builder payload extracts evidence
  - non-envelope summary text returns explicit reject reason through the diagnostic wrapper
  - launchable agent with missing adapter key fails with a useful reason

- `backend/tests/unit/lib/agent_studio/test_catalog_service.py`
  - custom agent created from `gene_extractor` inherits curation metadata

- `backend/tests/unit/lib/test_redis_client.py`
  - Redis URL redaction
  - auth failure clears cached client and logs a bounded diagnostic

- TraceReview smoke/preflight test
  - backend/local Langfuse source mismatch produces a named failure before expensive flow runs

## Validation Plan

Run focused unit tests first:

```bash
docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/lib/flows/test_executor.py -v --tb=short"
docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/lib/curation_workspace/test_extraction_results.py -v --tb=short"
docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/lib/agent_studio/test_catalog_service.py -v --tb=short"
```

Then rerun the Incus smoke with tunnels and trace preflight:

```bash
scripts/testing/dev_release_smoke.py --base-url http://127.0.0.1:8000 --stream --flow --curation-workspace
```

Acceptance criteria:

- curation DB and literature dependencies are ready before the run starts;
- Redis readiness is either green or explicitly skipped for non-streaming tests;
- Langfuse/TraceReview preflight is green before flow execution;
- each extraction-capable flow step emits `FLOW_EXTRACTION_HANDOFF_AUDIT`;
- flow run persists at least one extraction result;
- `FLOW_FINISHED.total_evidence_records > 0`;
- `adapter_keys` includes the gene curation adapter;
- TraceReview can retrieve the run trace;
- raw evidence artifacts remain ignored, with only summarized design artifacts committed.

## Open Questions

- In the failed run, did the builder workspace actually have `builder_finalization`, or did the LLM-facing finalizer tool return success without setting backend finalization state?
- Was `INTERNAL_EXTRACTION_RESULT` absent, or was it emitted but absent from the current flow cursor's live/collected lists?
- Did `build_extraction_envelope_candidate_with_evidence(...)` reject a valid-looking payload because adapter metadata was missing or because the payload shape was not recognized as an extraction envelope?
- Did the smoke custom agent catalog metadata include `curation.adapter_key` immediately before flow execution?
- Do the local backend and TraceReview containers share the same Langfuse project credentials in the Incus stack?
- Should flow output consider a builder tool sequence successful only after both extraction-result persistence and review-session materialization succeed?
