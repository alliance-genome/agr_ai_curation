# Testing Strategy Master Plan

Date: 2026-02-26  
Branch context: `agent-rework` (pre-merge hardening)  
Status: Active (single source of truth)  
Supersedes: `TEST_OVERHAUL_PLAN.md`, `TESTING_TODO.md`

## 1) Why this plan exists

Recent releases have had high regression cost, especially in flow execution and curator-facing behavior.  
This plan prioritizes catching breakage before curator discovery, with explicit emphasis on:

1. Flow safety and regression prevention.
2. Extractor/runtime stability under ongoing agent rework.
3. Real-provider integration confidence (OpenAI + Groq).
4. Coverage visibility and enforceable CI gates.

## 2) Current state snapshot (verified 2026-02-26)

1. Test inventory:
   - ~116 test files under `backend/tests/`
   - ~1292 test functions
2. CI reality:
   - Unit job now runs coverage with published artifacts (`.coverage`, `coverage.xml`, `htmlcov`) via shared runner script.
   - Current backend unit coverage baseline is ~`56.86%` (`844` passing unit tests in latest local docker run).
   - PR CI does not run full integration/contract suites; only persistence integration runs.
   - Frontend tests now run in PR CI but are temporarily non-blocking due legacy failures (`113` failing tests across `13` files on latest local run).
3. Drift/debt indicators:
   - Stale ignore paths in CI/compose reference missing test files.
   - `TESTING_TODO.md` references missing `docs/developer/TEST_HEALTH_REPORT.md`.
   - Flow API endpoints exist with little/no direct test coverage.
   - Existing chat contract/integration tests still patch outdated `generate_chat_response` path.

## 3) Quality goals and hard gates

## Release safety goals
1. No flow-breaking regressions reach curators.
2. No silent provider fallback reaches production.
3. Every extractor policy change includes regression tests.

## CI gate goals (target)
1. Backend PR gates:
   - Unit tests + coverage report.
   - Persistence integration tests.
   - Contract tests (selected stable subset initially).
2. Frontend PR gates:
   - Build + test (coverage report optional in first phase, required later).
3. Nightly/scheduled gates:
   - Live LLM integration (OpenAI + Groq).

## Coverage goals
1. Re-enable backend coverage in PR CI immediately.
2. Keep threshold practical while expanding tests:
   - Phase 0 threshold floor: 50% (implemented to avoid deadlock while baseline is raised)
   - Phase 1 threshold floor: 60%
   - Phase 2 threshold floor: 70%
   - Phase 3 threshold floor: 80% (restores intended baseline)

## 4) Priority risk areas

## P0 (must address before merge)
1. Flow API CRUD/ownership/deletion behavior.
2. `/api/chat/execute-flow` SSE contract and cancellation behavior.
3. Batch flow outcomes when no `FILE_READY` is produced.
4. Startup validation coverage (`test_main_startup.py`) currently ignored in CI.

## P1
1. Extractor auth/token branches (PDFX `cognito_client_credentials` path).
2. Provider health endpoint contract + model/provider drift behavior.
3. Stale ignored tests and placeholder contract tests.

## P2
1. Broader end-to-end curator workflow scenarios across MOD overlays.
2. Cost-controlled live model matrix expansion.

## 5) Test architecture model

## Unit (fast, mandatory PR)
1. Pure business logic and adapter behaviors.
2. No external network, no real DB.
3. Strict mocks for I/O boundaries.

## Contract (stable API/SSE expectations)
1. Endpoint-level request/response/event contracts.
2. Validates payload shapes and status semantics.
3. No brittle assumptions about internal call names.

## Integration (system with local dependencies)
1. Real app + local services (Weaviate/test DB when needed).
2. Covers flow execution, batch orchestration, auth-sensitive paths.

## Live LLM integration (scheduled/manual)
1. Real provider calls to OpenAI + Groq.
2. Verifies runtime/provider behavior, tool-calling, streaming, and no-fallback policy.
3. Separate from PR-required pipeline due cost and flake risk.

## 6) Phased execution plan

## Phase 0: CI and plan hygiene (Day 0-2)
1. Create this master plan and treat it as source of truth.
2. Remove stale ignore entries from:
   - `.github/workflows/test.yml`
   - `docker-compose.test.yml`
3. Re-enable backend coverage in CI unit job (remove `--no-cov`).
4. Add frontend test job (not just build).
5. Add CI check: fail if ignored file path does not exist.

Exit criteria:
1. CI runs with accurate ignore list only.
2. Coverage artifacts are published.
3. Frontend tests run on PRs.

Status:
1. Completed:
   - Shared backend unit CI runner added: `backend/tests/unit/run_ci_unit_tests.sh`.
   - Shared ignore list added: `backend/tests/unit/.ci-ignore-paths`.
   - Stale ignore paths removed from workflow/compose.
   - Coverage artifacts published by workflow (`backend-unit-coverage`).
   - Frontend test job added and set non-blocking pending stabilization.
2. Open follow-up:
   - Raise backend coverage floor from 50% as Phase 1 tests land.
   - Stabilize frontend suite, then switch frontend test job to blocking.

## Phase 1: Flow regression shield (Day 2-7)
Add or repair tests for:
1. `/api/flows` CRUD + ownership + soft delete.
2. Duplicate-name behavior with active vs deleted flows.
3. `flow_definition` JSON persistence/update behavior.
4. `/api/chat/execute-flow` SSE flattening + cancellation.
5. Graph order correctness (`entry_node_id` + edges traversal).
6. `DOMAIN_WARNING` emission for unavailable doc-required steps.
7. Batch failure when no `FILE_READY`.

Candidate tests:
1. `test_flows_crud_enforces_ownership_and_soft_delete`
2. `test_update_flow_persists_flow_definition_jsonb`
3. `test_create_flow_duplicate_name_conflict_active_only`
4. `test_execute_flow_endpoint_streams_flattened_events`
5. `test_execute_flow_endpoint_cancel_stops_stream`
6. `test_execute_flow_respects_entry_and_edges_order`
7. `test_execute_flow_emits_domain_warning_for_skipped_doc_required_step`
8. `test_batch_processor_marks_failed_when_no_file_ready`

Exit criteria:
1. Flow API and flow runtime high-risk paths covered.
2. Known stale flow tests updated to current schema expectations.

Status:
1. Completed in this tranche:
   - Added flow ownership + soft-delete unit coverage:
     - `backend/tests/unit/api/test_flows_api.py`
   - Added `/api/chat/execute-flow` stream flattening + cancellation tests:
     - `backend/tests/unit/api/test_chat_execute_flow_endpoint.py`
   - Added batch no-`FILE_READY` failure enforcement and tests:
     - `backend/src/lib/batch/processor.py`
     - `backend/tests/unit/lib/batch/test_processor.py`
   - Fixed double-counted batch failures in `process_batch_task` fallback path.
   - Hardened FILE_READY handling:
     - no broadcast before ownership validation
     - malformed details ignored safely
     - missing `file_id` treated as invalid output
   - Added `/api/chat/stop` ownership tests and security hardening:
     - owner-gated cancellation checks (`local + Redis owner key`)
     - reject unknown-owner cancellation when stream is active
     - cross-worker session ownership check during stream registration
2. Remaining Phase 1 backlog:
   - Add flow graph traversal/order assertions (`entry_node_id` + edges strict ordering).
   - Add explicit `DOMAIN_WARNING` endpoint-level contract assertions.
   - Expand contract coverage for flow endpoints and stream ownership collision scenarios.

## Phase 2: Extractor and provider runtime hardening (Week 2)
1. Add tests for PDFX auth token fetch/cache/error branches.
2. Add contract tests for `/api/admin/health/llm-providers`.
3. Un-ignore or split `test_main_startup.py` so fail-fast startup checks run in CI.
4. Replace outdated chat integration patches referencing removed internals.

Exit criteria:
1. Extractor auth paths and provider health contracts are covered.
2. Startup validation is part of PR gate path.

Status:
1. Completed in this tranche:
   - Added PDFX service auth branch coverage:
     - `backend/tests/unit/api/test_documents_pdfx_auth_headers.py`
   - Added PDFX worker readiness guard coverage:
     - `backend/tests/unit/api/test_documents_pdfx_worker_ready.py`
   - Added PDFX wake endpoint branch coverage (misconfig, transport failures, upstream non-2xx, non-JSON handling):
     - `backend/tests/unit/api/test_documents_pdfx_wake.py`
   - Expanded PDFX health aggregation coverage for worker/deep/proxy precedence and auth-header failure behavior:
     - `backend/tests/unit/api/test_documents_pdf_extraction_health.py`
   - Added status-helper unit coverage for normalization and pipeline-stage mapping/fallback:
     - `backend/tests/unit/api/test_documents_processing_status_helpers.py`
   - Expanded flow execute endpoint error/collision branch coverage:
     - `backend/tests/unit/api/test_chat_execute_flow_endpoint.py`
   - Added flow execution commit-failure cleanup hardening + regression test coverage:
     - `backend/src/api/chat.py`
     - `backend/tests/unit/api/test_chat_execute_flow_endpoint.py`
   - Added auth contract guard for wake endpoint:
     - `backend/tests/contract/test_pdf_extraction_health_auth.py`
   - Added admin connections API unit coverage (status aggregation, init guards, single-service behavior):
     - `backend/tests/unit/api/test_admin_connections_health.py`
   - Added Redis stream/cancel helper unit coverage (ownership, conflicts, graceful degradation, cleanup paths):
     - `backend/tests/unit/lib/test_redis_client.py`
   - Added health/readiness endpoint unit coverage and removed `datetime.utcnow()` deprecation usage:
     - `backend/tests/unit/api/test_health_endpoints.py`
     - `backend/src/api/health.py`
   - Added settings API unit coverage (read/update success/failure validation paths):
     - `backend/tests/unit/api/test_settings_api.py`
   - Added lightweight files API guardrail/helper unit coverage:
     - `backend/tests/unit/api/test_files_api_helpers.py`
   - Added chat stream lifecycle unit coverage and hardened stream cleanup with idempotent response-level background cleanup:
     - `backend/tests/unit/api/test_chat_stream_endpoint.py`
     - `backend/src/api/chat.py`
   - Hardened cross-worker session ownership claiming and cleanup race resistance using stream-scoped ownership tokens:
     - `backend/src/lib/redis_client.py`
     - `backend/src/api/chat.py`
     - `backend/tests/unit/lib/test_redis_client.py`
   - Added manual live PDFX smoke harness with explicit opt-in:
     - `backend/tests/integration/live_pdfx/test_pdfx_live_smoke.py`
     - requires `PDFX_LIVE_ENABLE=1`
   - Un-ignored startup validation path in CI and stabilized tests:
     - `backend/tests/unit/test_main_startup.py`
2. Remaining Phase 2 backlog:
   - Add/expand provider-health contract assertions for `/api/admin/health/llm-providers`.
   - Keep replacing stale chat contract/integration patching of removed internals.

## Phase 3: Live provider integration suite (Week 2-3)
Create `backend/tests/integration/live_llm/` with markers:
1. `live_llm`
2. `provider_openai`
3. `provider_groq`
4. `streaming`
5. `manual_only`

Required env/secrets:
1. `OPENAI_API_KEY`
2. `GROQ_API_KEY`
3. `LLM_PROVIDER_STRICT_MODE=true`
4. Optional: `OPENAI_BASE_URL`, `GROQ_BASE_URL`, `SMOKE_BEARER_TOKEN`

Execution cadence:
1. PR: no paid live calls.
2. Nightly: live OpenAI + Groq happy-path and streaming/tool-call checks.
3. Weekly: live negative-path checks (missing key, invalid URL, unknown model).
4. Pre-release manual: full smoke matrix in `docs/deployment/llm-provider-smoke-test-matrix.md`.

Exit criteria:
1. Nightly live tests stable and actionable.
2. Failures are visible with provider-specific diagnostics.

## Phase 4: Governance and branch protection (Week 3)
1. Define required status checks for merge.
2. Add quarantine table policy (owner + expiry for each excluded test).
3. Add monthly “test health drift” review and stale-ignore cleanup.

Exit criteria:
1. Branch protection aligned with test policy.
2. Exclusions are explicit, temporary, and owned.

## 7) CI design target

## PR workflows
1. `backend-unit-coverage`
2. `backend-persistence`
3. `backend-contract-core`
4. `frontend-build-and-test`

## Scheduled workflows
1. `backend-live-llm-nightly`
2. `backend-live-llm-negative-weekly`

## Manual workflows
1. `provider-smoke-manual` (staging/pre-release)

## 8) Ownership model

1. Core backend testing owner: flow/runtime + provider health contracts.
2. Agent/runtime owner: extractor and prompt-policy regressions.
3. Frontend owner: UI regression + coverage gate.
4. Release owner: live smoke sign-off before production rollout.

## 9) First execution queue (start immediately)

1. Remove stale ignore paths in CI and compose test config.
2. Re-enable backend coverage in CI unit job.
3. Add frontend test job to CI.
4. Implement Phase 1 flow regression tests (top 4 first):
   - `test_flows_crud_enforces_ownership_and_soft_delete`
   - `test_execute_flow_endpoint_streams_flattened_events`
   - `test_execute_flow_endpoint_cancel_stops_stream`
   - `test_batch_processor_marks_failed_when_no_file_ready`

## 10) Definition of done for branch merge readiness

1. Phase 0 complete.
2. Phase 1 core flow regression tests merged and green.
3. No stale ignored test references in CI config.
4. Backend PR coverage gate active with visible report artifact.
5. Manual pre-merge smoke run recorded with results for:
   - flow execution
   - extractor path
   - provider health endpoints
