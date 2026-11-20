# Agent Observability Playbook

This playbook defines the default `observe -> diagnose -> verify` loop for agent-executed work.

## Core Loop

1. Observe runtime state and service health.
2. Collect deterministic evidence (logs, health payloads, smoke checks, traces).
3. Diagnose one failure mode at a time.
4. Apply a minimal fix.
5. Re-run targeted validation and compare evidence.

## Baseline Commands

```bash
docker compose ps
curl http://localhost:8000/health
curl http://localhost:8000/api/admin/health/llm-providers
curl -fsS http://localhost:3100/ready
./scripts/testing/llm_provider_smoke_local.sh
./scripts/utilities/collect_agent_evidence.sh
```

`/api/admin/health/llm-providers` and `/api/admin/health/packages` require
login. A local backend in dev mode (`DEV_MODE=true`) signs requests in without
credentials. Elsewhere, send the testing API key, for example
`curl -H "X-API-Key: $TESTING_API_KEY" ...`.

`collect_agent_evidence.sh` sends `TESTING_API_KEY` for its own provider-report
request when it is set. That does not make the whole capture work outside dev
mode: the `llm_provider_smoke_local.sh` step it runs next sends only
`SMOKE_BEARER_TOKEN` as a Bearer token, which the backend login does not
accept, so its login-required checks fail and the capture ends as partial. This
was already true before these routes required login (the smoke step's
`/api/agent-studio/models` check needed login). Use dev mode for a complete
capture.

## Canonical Investigations

### 1) Backend startup/runtime failure

- Signals:
  - `/health` fails or returns non-200.
  - backend logs show import/config/runtime exception.
- Commands:
  - `docker compose logs -f backend`
  - `docker compose ps`
  - `./scripts/utilities/collect_agent_evidence.sh`
- Verify:
  - backend service healthy and stable over repeated `/health` checks.

### 2) LLM provider health failure

- Signals:
  - `/api/admin/health/llm-providers` returns `errors`.
  - smoke script fails.
- Commands:
  - `./scripts/testing/llm_provider_smoke_local.sh`
  - inspect `file_outputs/temp/llm_provider_smoke_local_*.json`
  - `docker compose logs --tail=300 backend`
- Verify:
  - smoke result is pass with empty provider error list.

### 3) Trace/flow behavior regression

- Signals:
  - flow completes with wrong behavior, retries, or tool misuse.
- Commands:
  - follow `trace_review/README.md`
  - inspect trace APIs in `docs/developer/traces/TRACE_REVIEW_API.md`
  - collect evidence bundle for corresponding run timestamp.
- Verify:
  - trace path shows expected tool chain and no unexpected detours/errors.

### 4) Frontend interaction mismatch

- Signals:
  - UI behavior does not match expected flow despite healthy backend.
- Commands:
  - `docker compose logs --tail=200 frontend`
  - backend + frontend health checks
  - evidence bundle collection
- Verify:
  - target interaction succeeds end-to-end with no frontend runtime errors.

## Evidence Standard for PR/Issue Handoff

Include:

1. Exact command(s) run.
2. Pass/fail status for each validation.
3. Pointer to evidence artifacts (`file_outputs/...`).
4. Remaining risk statement when checks are partial.
