# Flow, Trace, and Evidence Reliability Follow-up

Date: 2026-06-07

## TL;DR

The tunnel-backed smoke run gave us enough evidence to split the token-budget work from a more urgent reliability issue: flow extraction can finish as `completed` with no persisted extraction result, no adapter key, and no evidence records, even after the builder tools report a verified evidence record.

The most concrete code hole is in flow persistence. `_persist_flow_extraction_candidates()` returns `[]` when there are no candidates, and `_persist_flow_extraction_candidates_or_build_error()` currently returns success for that empty result. That lets a flow end with `FLOW_FINISHED.status=completed` and `total_evidence_records=0`.

The next implementation should make this self-diagnosing and fail closed: if an extraction flow runs a curation extraction step but builds no candidate, extracts no evidence, or persists no extraction result, emit a specific `FLOW_ERROR` with the reason bucket instead of reporting completion.

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

Those already appear in `FLOW_STEP_TIMING`, which means we can expose the failure reason without adding a new tracing subsystem.

## Persistence Hole

The final persistence path in [backend/src/lib/flows/executor.py](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/backend/src/lib/flows/executor.py:3671) calls `_persist_flow_extraction_candidates_or_build_error(...)` when extraction was not already persisted.

The problem is lower down:

- `_collect_completed_step_candidates(...)` returns only steps with an `ExtractionEnvelopeCandidate`.
- `_persist_flow_extraction_candidates(...)` returns `[]` if `candidates` is empty or `document_id` is missing.
- `_persist_flow_extraction_candidates_or_build_error(...)` returns `True, None, None, persisted_records` even when `persisted_records == []`.

So an extraction flow can run every required step and still finish as successful if all candidate construction failed silently.

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

### 3. Custom Agent Adapter Metadata May Not Be Present At The Flow Node

Confidence: medium.

The smoke creates a custom agent from `template_source=gene_extractor`. Creation inherits the parent template's model, tools, output schema, category, and records `template_source=gene_extractor` in [backend/src/lib/agent_studio/custom_agent_service.py](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/backend/src/lib/agent_studio/custom_agent_service.py:399).

Catalog metadata has a curation inheritance path in [backend/src/lib/agent_studio/catalog_service.py](/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation/backend/src/lib/agent_studio/catalog_service.py:1644): if a DB custom agent has no direct config definition, operates on a document, and points to a launchable curation template, it can inherit the template's curation metadata.

The final flow event still had `adapter_keys=[]`. That does not prove metadata inheritance failed, but it means adapter metadata was not represented in any persisted candidate.

Fix:

- Add a smoke-level assertion that the custom agent catalog entry contains `curation.adapter_key` before flow execution.
- Add a backend test that a custom agent from `gene_extractor` resolves curation metadata through `get_agent_metadata(...)`.
- If a flow node is a curation extraction node and no adapter key resolves, fail before specialist execution or emit a typed `FLOW_ERROR`.

### 4. TraceReview 404 Was A Trace-Ingest Failure, Not Proof Of No Trace

Confidence: medium.

The fresh trace IDs were not retrievable through local or remote TraceReview. Backend logs showed OTLP exporter `401 Unauthorized`, so the likely failure was Langfuse ingest/auth. TraceReview then had nothing to read.

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

### P0: Fail Closed On Empty Flow Evidence

Update `_persist_flow_extraction_candidates_or_build_error(...)` to know whether a curation extraction candidate was expected. It should return failure when:

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
  - no candidate for curation extraction step -> `FLOW_ERROR`
  - candidate list empty -> persistence helper returns failure
  - candidates present but persisted records empty -> failure
  - builder internal payload -> candidate/evidence persisted

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
- flow run persists at least one extraction result;
- `FLOW_FINISHED.total_evidence_records > 0`;
- `adapter_keys` includes the gene curation adapter;
- TraceReview can retrieve the run trace;
- raw evidence artifacts remain ignored, with only summarized design artifacts committed.

## Open Questions

- Was `INTERNAL_EXTRACTION_RESULT` absent, or present but invisible to the flow cursor lookup?
- Did the smoke custom agent catalog metadata include `curation.adapter_key` immediately before flow execution?
- Do the local backend and TraceReview containers share the same Langfuse project credentials in the Incus stack?
- Should flow output consider a builder tool sequence successful only after both extraction-result persistence and review-session materialization succeed?
