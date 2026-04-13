# Dev Release Smoke Strategy

Date: 2026-04-13  
Status: Implemented on dev, still pending manual/browser sign-off and release-tag work  
Audience: release owners, backend/frontend maintainers, future handoff sessions  
Primary script target: `scripts/testing/dev_release_smoke.py`
Execution cadence: implement one slice at a time, validate it, run a GPT-5.4
xhigh code-review pass, then move to the next slice

## 1) Why this document exists

This document is the durable source of truth for the dev-release smoke effort.
It exists so that a context refresh, handoff, or interrupted session does not
lose:

1. What we already learned from the `v0.5.x` dev stabilization work.
2. Which API surfaces are genuinely release-critical.
3. Which smoke checks are already covered elsewhere in tests.
4. Which gaps still exist in the planned deep smoke harness.
5. What "good enough to release from dev" should mean going forward.

This is intentionally more verbose than a normal task note. It is meant to be
useful even if read cold by a new session with no surrounding context.

## 2) Background and motivation

Recent dev validation surfaced several important realities:

1. Standard unit, contract, and integration suites were green on dev.
2. Dev still had a broken `OPENAI_API_KEY`, which caused real OpenAI requests to
   fail with `401 invalid_api_key`.
3. Dev also had broken PDF extraction Cognito credentials, which caused token
   fetch failures (`400 invalid_client`).
4. Those failures were not caught by the current default release gate because
   our ordinary automated suites mostly validate configuration shape, mocked
   behavior, or fake-key flows rather than real deployed credentials and the
   real deployed backend API path.

That means we need a true deployed-backend smoke layer on dev before any
production rollout. The goal is not just "run a couple of endpoints"; the goal
is to catch the exact kinds of failures that matter to curators:

1. Can a real paper be uploaded and processed?
2. Can the system answer a real question against that paper?
3. Can a real flow be created and executed?
4. Can batch processing complete with downloadable artifacts?
5. Can the review-and-curate bridge see the resulting evidence?

## 3) Current implementation state

As of this document:

1. `scripts/testing/dev_release_smoke.py` now covers the full intended
   release-critical API path on dev:
   - health and auth preflight
   - PDFX readiness
   - upload + artifacts
   - non-streaming + streaming chat
   - curation workspace bootstrap
   - custom agent + flow + evidence export
   - batch execution + ZIP download
2. The retrieval stack is now using application-level Amazon Bedrock Cohere
   Rerank 3.5 after Weaviate retrieval rather than the broken Weaviate-native
   rerank path.
3. The latest full passing evidence file on dev is:
   `/tmp/agr_ai_curation_dev_release_smoke/dev_release_smoke_20260413T122856Z.json`
4. The runbook-grade automated validation now completed successfully on dev:
   - backend unit: `2272 passed`
   - backend contract: `122 passed, 130 skipped`
   - backend integration: `173 passed, 23 skipped`
5. Frontend validation is green from the repo workspace:
   - `79` test files, `732` tests passed
   - build passed via alternate output directory
6. The main remaining gap is no longer smoke implementation. It is release-prep
   finish work:
   - manual/browser smoke on dev
   - release version/tag decision
   - final runbook / release-note alignment

This document now serves as both the implementation record and the durable
reference for why the smoke looks the way it does.

## 3.1) Current slice update: Bedrock rerank, flow persistence, stale-document cleanup, and chat hardening

The latest completed slice changed both infrastructure assumptions and smoke
behavior in important ways.

### What changed

1. We stopped relying on Weaviate's built-in rerank path for live retrieval.
2. The backend now reranks retrieved chunks in-app with Amazon Bedrock Cohere
   Rerank 3.5.
3. Dev EC2 validation proved the backend is using the instance role rather than
   legacy static AWS keys for the Bedrock rerank call path.
4. The smoke harness was tightened so it no longer false-passes responses that
   contain curator-facing failure text or SSE error events such as
   `SPECIALIST_ERROR`.
5. Local Docker defaults were adjusted so reranking is no longer implicitly on
   for fresh local setups without AWS credentials; dev/prod now need explicit
   `RERANK_PROVIDER=bedrock_cohere` configuration.
6. Bedrock rerank failure/empty-result handling was hardened so the app
   preserves baseline retrieval ordering instead of turning a reranker outage
   into a full search outage.
7. The flow slice was tightened to require:
   - a real `flow_run_id`
   - at least one `FLOW_STEP_EVIDENCE` event
   - a successful `/api/flows/runs/{flow_run_id}/evidence/export?format=json`
     round-trip
8. That stricter flow slice exposed a real backend bug: custom agents cloned
   from extraction templates were losing curation adapter metadata in
   `get_agent_metadata()`, which prevented flow-produced extraction envelopes
   from persisting to `extraction_results`.
9. The backend was fixed so custom agents now inherit curation adapter metadata
   from their template source or group-rules component when available.
10. The flow smoke itself now uses a custom `gene_extractor`-based agent over
    `sample_fly_publication.pdf`, which is a much better fit for proving real
    evidence persistence than the earlier text-only `chat_output` clone.
11. The smoke then exposed a deeper document-lifecycle bug on dev: a stale
    PostgreSQL-only document row for the preferred sample could survive after
    its Weaviate record was gone, making fresh upload attempts fail as
    duplicates while the visible document APIs could not see or remove the old
    row.
12. That stale-record path was fixed by introducing shared
    `cleanup_document_curation_dependencies()` logic and using it from the
    document delete endpoint, Weaviate-backed delete path, and upload phantom
    duplicate cleanup path so document deletion can safely remove dependent
    `extraction_results` records before deleting the SQL row.
13. The smoke also exposed a non-streaming chat bug: `/api/chat` was stopping
    on the first `RUN_FINISHED` event from the streamed runner instead of using
    the final stabilized result.
14. The chat endpoint was hardened to continue consuming runner events and use
    the last `RUN_FINISHED` payload, which brought non-streaming chat behavior
    back in line with the streaming path.
15. The smoke matcher itself was tightened again so curator-facing failure text
    such as `pdf extraction step failed` or `missing verified evidence records`
    counts as a real smoke failure instead of a false pass.
16. A new live regression then exposed that the ordinary UI/default chat path
    was still forcing legacy request-model defaults from `backend/src/api/chat.py`
    (`gpt-4o`, `gpt-4o-mini`, plus hardcoded temperature/reasoning overrides)
    whenever the request omitted explicit model fields.
17. That API-layer override bug was fixed by changing those request-model
    defaults to `None`, so omitted fields now fall through to the configured
    runtime agent defaults instead of silently overriding them.
18. The smoke harness was then updated to stop masking this bug:
    - the default smoke chat question is now
      `What genes are the focus of the publication?`
    - the chat + streaming smoke stages now exercise the runtime-default path
      instead of injecting chat model overrides
    - the shared fly-paper smoke now requires a `crb` / `crumbs` style answer
      for that focus-gene question

### What was validated on dev

1. Targeted unit tests for the Bedrock reranker and Weaviate chunk integration
   passed.
2. Live backend logs on dev showed:
   - `search_document` tool execution
   - `V5: Post-search reranking enabled via provider=bedrock_cohere`
   - `V5: Applying Bedrock reranking ...`
   - `Found credentials from IAM Role: AIcurationEC2SSMRole`
3. The prior Weaviate runtime error
   `searcher: hybrid: extend: unknown capability: rerank`
   did not appear during the live retrieval path once Bedrock reranking was in
   place.
4. After the stale phantom-document cleanup fix landed, the preferred
   curator-style fixture `sample_fly_publication.pdf` could be deleted and
   re-uploaded cleanly on dev through the normal smoke path.
5. The streaming chat stage now requires `CHUNK_PROVENANCE` so it proves
   document-grounded retrieval actually happened, not just that a model
   produced a plausible final answer.
6. The stricter partial smoke now passes on dev for:
   - health and provider preflight
   - user identity verification
   - PDFX readiness and wake
   - upload + processing
   - chunks + download-info + PDFX JSON artifact checks
   - regular chat
   - streaming chat
   - custom agent creation
   - flow execution with persisted evidence
   - evidence export JSON
7. A clean live repro of the broken focus-gene query, after the API-default fix,
   showed:
   - fresh document id `860d4f7d-f5fe-4932-8ae6-0494507f93dc`
   - streaming session id `20745df0-82bd-40f2-bd70-b6912059030a`
   - `RUN_STARTED.model = gpt-5.4`
   - the expected `ask_gene_extractor_specialist` path in backend logs
   - final answers centered on `crb / crumbs`
8. The latest passing partial/dev evidence file is now:
   `/tmp/agr_ai_curation_dev_release_smoke/dev_release_smoke_20260413T105827Z.json`
9. That latest passing run showed:
   - a grounded non-streaming focus-gene answer against the loaded fly paper
   - a valid streaming `trace_id` plus `CHUNK_PROVENANCE`
   - `chat_stream_model = gpt-5.4`
   - `crb / crumbs` in both non-streaming and streaming previews
   - explicit evidence that the smoke expects `gpt-5.4` for the runtime-default
     supervisor path
   - explicit evidence that the expected runtime-default specialist model is
     `gpt-5.4-nano`, while also recording that this exact specialist model id is
     not yet directly observable via the current SSE contract
   - batch intentionally skipped because that slice is not implemented yet

### Important issues the smoke exposed

1. The tiny fixture `backend/tests/fixtures/live_tiny_chat.pdf` is useful as a
   diagnostic input, but it currently exposes a real document-extraction failure
   path rather than a healthy curator-like chat path. The stricter smoke now
   fails correctly on that input.
2. The preferred sample previously surfaced a real stale-record bug:
   PostgreSQL-only documents missing from Weaviate could still block re-upload
   as duplicates while normal document APIs could not clean them up. That is
   now fixed in the backend rather than handled as a one-off dev cleanup.
3. The stale delete path also revealed dependent-record cleanup gaps:
   `extraction_results` rows could keep a dead document alive through foreign
   key constraints. That is now handled centrally before document deletion.
4. The non-streaming chat path was capable of returning an intermediate
   `RUN_FINISHED` answer, including failure-style text that the streaming path
   would later supersede. That is now fixed by using the last final event from
   the runner.
5. A later rerun exposed one more real bug after the workspace slice landed:
   document cleanup could still lie about success because
   `curation_review_sessions.document_id` kept the PDF row alive even after the
   Weaviate document and files were removed.
6. `cleanup_document_curation_dependencies()` now removes the full
   workspace-owned dependency chain before `pdf_documents` deletion, and the
   full smoke passes again from a clean rerun state.

### 3.2) Current slice update: batch, workspace, and cleanup truthfulness

The later slices are now implemented and validated, and they uncovered one more
important release-safety issue.

### What changed

1. Batch coverage is now real, not planned:
   - second PDF upload
   - batch-compatible flow validation
   - batch creation
   - batch completion polling
   - ZIP download verification
2. Curation-workspace coverage is also real:
   - prep preview
   - bootstrap availability
   - prep execution
   - bootstrap endpoint replay
   - hydrated workspace fetch
3. The first full green rerun exposed that document cleanup was still
   false-green after workspace creation:
   - the delete path removed the Weaviate document
   - the delete path removed filesystem artifacts
   - but PostgreSQL deletion could still fail because
     `curation_review_sessions` referenced the document
4. The cleanup helper was expanded to delete the full workspace dependency
   chain first:
   - action log
   - validation snapshots
   - submissions
   - evidence anchors
   - drafts
   - candidates
   - review sessions
   - extraction results
5. A dedicated regression test was added for this helper.
6. After the GPT-5.4 xhigh review, the helper was tightened again so the final
   delete operations only remove the IDs captured at the start of cleanup
   rather than mixing child cleanup on captured IDs with broader final
   `document_id`-scoped deletes.

### What was validated on dev

1. Targeted cleanup regression tests passed locally.
2. The new cleanup regression test passed on dev.
3. The follow-up partial-state regression test for extraction-results-without-
   sessions also passed on dev.
4. Deleting the previously stuck smoke-user document through the live API
   removed:
   - the `pdf_documents` row
   - `curation_review_sessions` rows
   - `extraction_results` rows
5. The latest full deep-smoke rerun passed on dev:
   - evidence file:
     `/tmp/agr_ai_curation_dev_release_smoke/dev_release_smoke_20260413T122856Z.json`
6. That run produced real batch ZIP output:
   - `001_1a825528182a8b6ded0e1c28b0e59e3b_final_findings_20260413T122659Z.json`
   - `002_42d3548b0e4cd322578149a2eb50adee_study_findings_20260413T122853Z.json`

### Remaining concerns

1. `/api/admin/health/llm-providers` is still formally degraded on dev because
   `GROQ_API_KEY` is unset, even though the real OpenAI runtime path is ready
   and the deep smoke passes.
2. The frontend runtime container on dev is nginx-only, so frontend test/build
   validation still needs to happen from the repo workspace rather than from
   `docker compose exec frontend ...`.
3. The standard local `npm run build` is blocked by a root-owned
   `frontend/dist/assets` artifact, but build logic itself is fine when using a
   clean out dir.

## 4) High-level goal

Build one deep dev-release smoke harness that can be required in the release
runbooks and that validates the most failure-prone curator-facing behavior on
the deployed dev stack.

The harness should be:

1. Real: it must hit the deployed HTTP API, not only in-process test fixtures.
2. Authenticated: it should use API-key auth and fail closed if that auth path
   is unavailable.
3. Evidence-producing: every run should leave a JSON artifact with enough detail
   to debug what passed and what failed.
4. Clean: it should best-effort clean up uploaded documents, temporary custom
   agents, and temporary flows.
5. Layered: it should support skipping specific stages while debugging, but the
   default release invocation should run the full release-critical path.

## 5) What the smoke must prove

At minimum, the release smoke should prove all of the following on the deployed
dev stack:

1. Backend health endpoints are reachable and healthy enough to serve requests.
2. PDF extraction auth is valid and the worker can wake.
3. A real PDF can be uploaded, processed, and materialized into chunks and
   PDFX-derived artifacts.
4. A loaded-document chat request succeeds using the deployed backend's real LLM
   path.
5. The streaming chat path, not just the non-streaming chat path, behaves
   correctly and exposes a real `trace_id`.
6. A temporary custom agent can be created successfully.
7. A real flow can be created, executed over SSE, and completed without
   `RUN_ERROR` or `SUPERVISOR_ERROR`.
8. A completed flow run produces exportable evidence artifacts.
9. A batch-compatible flow can be validated and run across two documents.
10. Batch outputs can be downloaded and are structurally plausible.
11. The curation workspace bootstrap bridge can see persisted evidence and turn
    it into a review session workspace.

If those steps pass on dev, we have much stronger confidence that the full
release candidate is real, not just syntactically healthy.

## 6) Existing coverage we already have elsewhere

The repo is not starting from zero. Several live/manual tests already exercise
important pieces of this path:

1. `backend/tests/live_integration/test_backend_pdfx_live_pipeline.py`
   - real upload through backend
   - real status polling
   - chunks endpoint check
   - download-info check
   - PDFX JSON download check
2. `backend/tests/live_integration/test_backend_chat_live_pdf_qa.py`
   - real loaded-document chat against the backend
3. `backend/tests/live_integration/test_backend_flow_live_llm.py`
   - real custom agent creation
   - real flow creation
   - real flow execution via `/api/chat/execute-flow`
4. `backend/tests/live_integration/test_backend_batch_live_processing.py`
   - two-document batch processing
   - batch-compatible flow validation
   - ZIP download

That means the dev release smoke should not invent a separate interpretation of
these APIs. It should borrow the working request shapes and success criteria
from those tests wherever possible.

## 7) Gap review: what was missing from the initial deep smoke draft

This section is kept as historical design context. The must-add and
strongly-recommended items below are now implemented in the current smoke.

The first deep smoke draft was already stronger than a trivial script, but it
still missed several release-critical checks.

### 7.1 Must-add gaps

These are required before we should call the smoke "complete":

1. Streaming chat coverage.
   - The frontend primarily depends on `/api/chat/stream`, not only
     `/api/chat`.
   - We should validate real SSE behavior and ensure a `RUN_STARTED` event
     exposes a `trace_id`.
   - This also gives us a path to confirm which model path is actually being
     used.

2. Document artifact verification beyond `download-info`.
   - `download-info` alone is too shallow.
   - We should also verify:
     - `/weaviate/documents/{id}/chunks`
     - `/weaviate/documents/{id}/download/pdfx_json`

3. Flow evidence export.
   - A successful flow execution is not enough.
   - We should capture `flow_run_id` from `FLOW_FINISHED.data` and verify
     `/api/flows/runs/{flow_run_id}/evidence/export?format=json`.

4. Strict API-key auth for release validation.
   - The script currently has a fallback path that can use DEV mode when
     `TESTING_API_KEY` is absent.
   - That is acceptable for local debugging, but not for release sign-off.
   - The release-gate invocation should require `X-API-Key` and fail if the key
     is unavailable.

### 7.2 Strongly recommended gaps

These are not optional in spirit, but they could be staged immediately after the
must-adds if we need to split implementation.

1. Curation workspace bootstrap.
   - Check bootstrap availability for a document.
   - Bootstrap a review session from persisted flow/chat evidence.
   - Fetch the resulting workspace with `include_workspace=true`.
   - Confirm at least one candidate/evidence anchor exists.

2. Batch artifact verification beyond ZIP presence.
   - ZIP existence alone is too weak.
   - We should confirm:
     - each completed batch document has a `result_file_path`
     - at least one underlying file artifact can be downloaded successfully

### 7.3 Nice-to-have gaps

These are valuable, but not required for the first mandatory release gate:

1. `/api/admin/health/llm-providers`
   - useful config sanity preflight
   - not a substitute for real auth, but helpful
2. `/api/users/me`
   - useful auth-principal sanity check
3. Batch stream or cancel coverage
4. Feedback pipeline smoke
5. PDF viewer endpoints and fuzzy-match follow-up checks

## 8) Proposed smoke architecture

The smoke should be implemented as one script with distinct stages. The default
invocation should run them all. Debug flags can selectively skip stages.

Recommended stage layout:

### Stage 0: Auth and config preflight

Purpose:

1. Fail early if the release smoke is not using the intended auth path.
2. Catch obvious deployment/config drift before expensive steps run.

Checks:

1. Require `TESTING_API_KEY` or explicit `--api-key`.
2. Call `/health`.
3. Optionally call `/api/admin/health/llm-providers`.
4. Optionally call `/api/users/me`.
5. Verify the resolved current-user principal matches the expected API-key
   identity rather than the synthetic DEV user.

Acceptance:

1. API-key auth is active.
2. Backend is healthy enough to proceed.

### Stage 1: PDF extraction and ingestion

Purpose:

1. Prove the PDF path is actually working end-to-end.

Checks:

1. `GET /weaviate/documents/pdf-extraction-health`
2. `POST /weaviate/documents/pdf-extraction-wake` if needed
3. `POST /weaviate/documents/upload`
4. `GET /weaviate/documents/{id}/status` until terminal
5. `GET /weaviate/documents/{id}/chunks`
6. `GET /weaviate/documents/{id}/download-info`
7. `GET /weaviate/documents/{id}/download/pdfx_json`

Acceptance:

1. Worker becomes available.
2. Processing completes successfully.
3. Chunk count is greater than zero.
4. PDFX JSON is downloadable.
5. Duplicate reuse is disabled by default for release validation so the stage
   proves a fresh upload/processing cycle.

### Stage 2: Loaded-document chat

Purpose:

1. Prove curator-like interaction against a loaded paper works.

Checks:

1. `POST /api/chat/document/load`
2. `POST /api/chat/session`
3. `POST /api/chat`
4. `POST /api/chat/stream`

Acceptance:

1. Loaded document becomes active.
2. Non-streaming chat returns a non-empty answer.
3. Streaming chat yields expected SSE events.
4. Streaming path exposes a real `trace_id`.
5. No failure snippets appear in the answer payload.
6. No SSE event whose type ends in `_ERROR` is present.
7. At least one `CHUNK_PROVENANCE` event is present so document grounding is
   proven, not inferred.

### Stage 3: Custom agent and flow execution

Purpose:

1. Prove Agent Studio and flow execution are viable in the deployed stack.

Checks:

1. `POST /api/agent-studio/custom-agents`
2. `POST /api/flows`
3. `POST /api/chat/execute-flow`
4. `GET /api/flows/runs/{flow_run_id}/evidence/export?format=json`

Acceptance:

1. Temporary custom agent is created successfully.
2. Flow creation succeeds.
3. `execute-flow` emits:
   - `FLOW_STARTED`
   - `RUN_STARTED`
   - `FLOW_FINISHED`
4. No SSE event whose type ends in `_ERROR` is present.
5. `FLOW_FINISHED.data.status == "completed"`.
6. Exported evidence artifact is non-empty and parseable.

### Stage 4: Batch execution

Purpose:

1. Prove multi-document processing and file export still work.

Checks:

1. Upload a second distinct PDF.
2. Poll second document to completion.
3. `POST /api/flows` for batch-compatible flow
4. `GET /api/flows/{flow_id}/validate-batch`
5. `POST /api/batches`
6. `GET /api/batches/{batch_id}` until terminal
7. `GET /api/batches/{batch_id}/download-zip`
8. Optionally download an underlying file result

Acceptance:

1. Batch flow validates successfully.
2. Batch status reaches `completed`.
3. All batch document entries complete successfully.
4. ZIP contains expected files.
5. Each document has a valid result path.

### Stage 5: Curation workspace bootstrap

Purpose:

1. Prove the review-and-curate path can see the output of prior steps.

Checks:

1. `GET /api/curation-workspace/documents/{document_id}/bootstrap-availability`
2. `POST /api/curation-workspace/documents/{document_id}/bootstrap`
3. `GET /api/curation-workspace/sessions/{session_id}?include_workspace=true`

Acceptance:

1. Bootstrap reports eligible when expected.
2. Bootstrap creates or refreshes a session successfully.
3. Workspace payload contains at least one candidate or other expected review
   artifact.

## 9) Proposed endpoint matrix

This table is the practical "what should the smoke hit?" checklist.

| Layer | Endpoint | Why it matters |
|------|----------|----------------|
| Health | `GET /health` | Basic backend readiness |
| Provider preflight | `GET /api/admin/health/llm-providers` | Config sanity before real LLM calls |
| Auth sanity | `GET /api/users/me` | Confirms auth principal resolution |
| PDFX | `GET /weaviate/documents/pdf-extraction-health` | Validates auth and worker state |
| PDFX | `POST /weaviate/documents/pdf-extraction-wake` | Wakes worker if sleeping |
| Upload | `POST /weaviate/documents/upload` | Real ingestion path |
| Processing | `GET /weaviate/documents/{id}/status` | Real status polling |
| Chunks | `GET /weaviate/documents/{id}/chunks` | Confirms usable chunk data |
| Artifact | `GET /weaviate/documents/{id}/download-info` | Confirms stored outputs |
| Artifact | `GET /weaviate/documents/{id}/download/pdfx_json` | Confirms PDFX output availability |
| Chat setup | `POST /api/chat/document/load` | Activates the paper for chat |
| Chat setup | `POST /api/chat/session` | Establishes session path |
| Chat | `POST /api/chat` | Non-streaming question path |
| Chat | `POST /api/chat/stream` | Real frontend chat path |
| Agent Studio | `POST /api/agent-studio/custom-agents` | Real custom agent creation |
| Flows | `POST /api/flows` | Real flow create path |
| Flow execute | `POST /api/chat/execute-flow` | Real SSE flow path |
| Flow evidence | `GET /api/flows/runs/{flow_run_id}/evidence/export` | Curator-facing evidence artifact |
| Batch validation | `GET /api/flows/{flow_id}/validate-batch` | Confirms batch compatibility |
| Batch create | `POST /api/batches` | Starts real batch run |
| Batch status | `GET /api/batches/{batch_id}` | Tracks batch completion |
| Batch ZIP | `GET /api/batches/{batch_id}/download-zip` | Confirms downloadable outputs |
| Workspace | `GET /api/curation-workspace/documents/{document_id}/bootstrap-availability` | Confirms handoff availability |
| Workspace | `POST /api/curation-workspace/documents/{document_id}/bootstrap` | Builds review session |
| Workspace | `GET /api/curation-workspace/sessions/{id}?include_workspace=true` | Verifies review payload |

## 10) Evidence the script should capture

The smoke artifact JSON should record enough to diagnose failures without
rerunning blind.

Recommended evidence fields:

1. Timestamp
2. Base URL
3. Whether API-key auth was used
4. Primary and secondary sample PDF paths
5. Stage-by-stage checks with status code and response summary
6. Primary and secondary document IDs
7. Chat session ID
8. Trace ID from streaming chat
9. Custom agent ID and agent key
10. Flow ID
11. Flow run ID
12. Batch flow ID
13. Batch ID
14. Batch ZIP members
15. Workspace session ID, if bootstrap stage runs
16. Short previews of chat and flow outputs
17. Overall pass/fail plus terminal error string if failed

The evidence file should be treated as a deployment artifact and referenced in
the release notes or deployment notes for that session.

## 11) Cleanup expectations

The script should best-effort clean up temporary artifacts in reverse order:

1. Temporary batch flow
2. Temporary flow
3. Temporary custom agent
4. Loaded document state
5. Uploaded documents created by the smoke

Cleanup failure should be recorded in evidence, but should not erase the
primary error if the actual smoke stage already failed.

## 12) Auth policy for the smoke

Release sign-off should not depend on dev-mode bypass.

Policy recommendation:

1. Debug mode may allow DEV-mode fallback.
2. Release mode must require `TESTING_API_KEY` or explicit `--api-key`.
3. The runbook command used for sign-off should be the strict mode.

This matters because a release check that quietly falls back to DEV mode is too
forgiving and can hide real auth or principal-resolution problems.

## 13) Sample document strategy

Use small, stable fixture PDFs by default to keep runtime and cost reasonable.

Recommended strategy:

1. Prefer the curator-style sample
   `sample_fly_publication.pdf` at repo root when available.
2. Also recognize the shared local testing copy at
   `/home/ctabone/analysis/alliance/ai_curation_new/agr_ai_curation/sample_fly_publication.pdf`
   when running from a different checkout on the same machine.
3. Allow `AGR_SMOKE_SAMPLE_PDF` to override the default primary fixture path
   explicitly for ad hoc runs.
4. Fall back to `backend/tests/fixtures/sample_fly_publication.pdf`.
5. Use `backend/tests/fixtures/micropub-biology-001725.pdf` as the preferred
   second distinct fixture for batch coverage.
6. Keep `backend/tests/fixtures/live_tiny_chat.pdf` as a diagnostic fixture,
   not the primary release-gate default, because it currently exposes a real
   extraction failure path rather than a representative curator workflow.

The goal is not to stress performance; the goal is to prove end-to-end behavior
with predictable inputs.

## 14) Acceptance criteria for calling dev "release-ready"

Before a production rollout is considered, the following should all be true:

1. Unit, contract, integration, frontend test/build checks are green.
2. The deep dev-release smoke passes on the deployed dev stack.
3. The smoke evidence JSON is retained and referenced in the deployment notes.
4. Chris has manually exercised the UI/browser path on dev.
5. Any migration or data-impact review has been completed separately.

If the deep smoke fails, the release candidate should be considered blocked
until the failure is explained and resolved.

## 15) Slice-Based Implementation Plan

To avoid thrash, the smoke should be implemented in explicit slices. Each slice
should leave the script in a usable state, with targeted validation and an
independent code-review pass before moving on.

### Slice 1: Strict auth preflight plus PDF ingestion artifacts

Status: complete on dev

Scope:

1. Require API-key auth for release-gate mode.
2. Add preflight checks that fail early when auth/config is not usable.
3. Complete the PDF ingestion stage with artifact verification.

Expected implementation:

1. Enforce strict mode for release runs:
   - fail if `TESTING_API_KEY` or `--api-key` is missing
2. Keep or add:
   - `/health`
   - optional `/api/admin/health/llm-providers`
   - optional `/api/users/me`
3. Complete document verification:
   - `/weaviate/documents/{id}/chunks`
   - `/weaviate/documents/{id}/download-info`
   - `/weaviate/documents/{id}/download/pdfx_json`
4. Ensure evidence JSON captures auth mode, document IDs, chunk counts, and
   artifact verification results.

Definition of done:

1. The script can prove auth is real and closed-fail for release mode.
2. A real document uploads, processes, produces chunks, and exposes PDFX JSON.
3. Cleanup still works.

### Slice 2: Loaded-document chat including streaming and trace capture

Status: complete on dev

Scope:

1. Finish the curator-facing chat stage, including the real streaming path.

Expected implementation:

1. Keep non-streaming `POST /api/chat` coverage.
2. Add `POST /api/chat/stream`.
3. Parse SSE output and capture:
   - `RUN_STARTED`
   - `RUN_FINISHED`
   - `trace_id`
   - model information where available
4. Record chat answer previews and streaming trace data in evidence JSON.

Definition of done:

1. Non-streaming chat succeeds against the loaded document.
2. Streaming chat succeeds and exposes a real trace identifier.
3. The evidence artifact contains enough detail to debug model-path problems.

### Slice 3: Custom agent, flow execution, and evidence export

Status: complete on dev, with additional stale-document and chat hardening

Scope:

1. Add the Agent Studio and flow runtime path.

Expected implementation:

1. Create a temporary custom agent.
2. Create a temporary flow using that agent.
3. Execute the flow over `/api/chat/execute-flow`.
4. Parse SSE events and assert expected completion semantics.
5. Export flow evidence via
   `/api/flows/runs/{flow_run_id}/evidence/export?format=json`.

Definition of done:

1. Temporary custom agent creation works.
2. Flow creation and execution complete successfully.
3. Evidence export is non-empty and parseable.

### Slice 4: Batch execution and artifact verification

Status: pending

Scope:

1. Add multi-document batch coverage and stronger output checks.

Expected implementation:

1. Upload a second distinct PDF.
2. Create and validate a batch-compatible flow.
3. Create a batch and wait for terminal completion.
4. Download the batch ZIP.
5. Verify completed document rows include valid result paths.
6. If practical, download at least one underlying file artifact.

Definition of done:

1. Batch run completes successfully for two documents.
2. ZIP output is real and non-empty.
3. At least one underlying batch artifact path is validated.

### Slice 5: Curation workspace bootstrap

Status: pending

Scope:

1. Connect the smoke to the review-and-curate bridge.

Expected implementation:

1. Check bootstrap availability.
2. Bootstrap a review session from prior persisted results.
3. Fetch the resulting workspace payload.
4. Verify candidate or evidence-anchor material is present.

Definition of done:

1. The smoke proves not only that backend processing works, but that the review
   workspace can actually consume the results.

### Slice 6: Docs and release-gate rollout

Status: pending

Scope:

1. Update all human-facing docs to require the finished smoke.

Expected implementation:

1. Update:
   - `scripts/README.md`
   - `docs/developer/TEST_STRATEGY.md`
   - `~/.agr_ai_curation/docs/DEV_ENVIRONMENT_RUNBOOK.md`
   - `~/.agr_ai_curation/docs/DEPLOYMENT_RUNBOOK.md`
2. Ensure the runbooks describe the smoke as the full release-critical gate,
   not a lightweight upload/chat probe.

Definition of done:

1. The release process documents exactly what the smoke covers and how to run
   it.

### Review checkpoint after each slice

After each implementation slice:

1. Run targeted validation for that slice locally and, when appropriate, on the
   deployed dev stack.
2. Run a GPT-5.4 xhigh code-review subagent pass focused on the slice's changed
   files and behaviors.
3. Address any review findings before starting the next slice.

This review cadence is part of the implementation plan, not an optional extra.

## 16) Documentation rollout plan

Once the script matches this plan, the following docs should explicitly point to
the deep version, not a lighter upload-and-chat probe:

1. `scripts/README.md`
2. `docs/developer/TEST_STRATEGY.md`
3. `~/.agr_ai_curation/docs/DEV_ENVIRONMENT_RUNBOOK.md`
4. `~/.agr_ai_curation/docs/DEPLOYMENT_RUNBOOK.md`

Those docs should describe the smoke as:

1. required for dev sign-off
2. evidence-producing
3. covering PDFX, chat, flow, batch, and curation-workspace bootstrap

## 17) Open questions and possible future extensions

These are worth tracking, but should not block the first full smoke rollout:

1. Should the smoke verify `/api/chat/stream` only, or both streaming and
   non-streaming chat?
2. Should we add `/api/pdf-viewer` follow-up checks to prove the review UI can
   fetch its display payload?
3. Should we add a feedback submission smoke against `/api/feedback`?
4. Should the script emit a condensed markdown summary alongside the JSON?
5. Should we split the deep smoke into multiple scripts behind one wrapper, or
   keep one script with skip flags?

Current recommendation:

1. Keep one primary script for now.
2. Allow stage skip flags for debugging.
3. Revisit splitting only if the script becomes unwieldy.

## 18) Recommended next action after reading this document

If you are picking this work up in a fresh session, do this next:

1. Start from the current Bedrock rerank state, not the old Weaviate rerank
   assumption.
2. Treat the deep smoke implementation as complete enough for release-prep
   usage on dev:
   - full latest evidence file:
     `/tmp/agr_ai_curation_dev_release_smoke/dev_release_smoke_20260413T122856Z.json`
   - batch and workspace slices are now implemented
   - cleanup truthfulness for workspace-owned documents is fixed
3. If you need to confirm the retrieval path again, check dev backend logs for:
   - `V5: Applying Bedrock reranking`
   - IAM-role-backed Bedrock credentials
4. The next practical work is release preparation, not more smoke expansion:
   - manual/browser smoke on dev
   - decide how to handle the Groq degraded-provider warning
   - finalize changelog/version/tag for the next patch release
5. If more smoke work is still needed later, continue using the same cadence:
   - targeted validation
   - GPT-5.4 xhigh review pass
   - then only move forward after findings are addressed

That should be enough to resume the work without needing the earlier context.
