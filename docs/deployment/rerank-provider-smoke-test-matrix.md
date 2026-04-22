# Rerank Provider Smoke Test Matrix

Last updated: 2026-04-18

## Purpose

Validate the backend post-retrieval rerank provider modes end to end with no
silent fallback behavior.

Current runtime sources of truth:

- `config/connections.yaml` for when the local reranker service becomes
  required.
- `scripts/install/lib/templates/env.standalone` for standalone operator env
  defaults.
- `docker-compose.yml` and `docker-compose.production.yml` for backend env
  wiring and the optional `local-reranker` Compose profile.

## Provider Selection Matrix

| `RERANK_PROVIDER` | Backend behavior | Required env keys | Local reranker service required? |
|---|---|---|---|
| `bedrock_cohere` | Rerank retrieved chunks with Amazon Bedrock Cohere Rerank. This remains the standalone default path. | `RERANK_PROVIDER`, `BEDROCK_RERANK_MODEL_ARN` | No |
| `local_transformers` | Rerank retrieved chunks with the local `reranker-transformers` service. | `RERANK_PROVIDER`, `RERANKER_URL` | Yes |
| `none` | Skip post-retrieval reranking and preserve retrieval order. | `RERANK_PROVIDER` | No |

Notes:

- Reranking is a backend post-retrieval step. It is not a primary Weaviate
  query-time rerank workflow.
- Existing standalone installs that already use
  `scripts/install/lib/templates/env.standalone` keep the Bedrock-backed path
  by default because that template still sets `RERANK_PROVIDER=bedrock_cohere`.
- `RERANKER_URL` may stay on its default internal service URL unless you are
  pointing `local_transformers` at a different host.
- The local smoke script validates the backend's effective `RERANKER_URL`.
  It resolves that target from an exported `RERANKER_URL`, then the local
  backend env file, then the default `http://reranker-transformers:8080`.

## Environments

- **Local:** Frontend at `http://localhost:3002`, backend at
  `http://localhost:8000` via `docker compose`
- **Standalone / production-style:** `docker-compose.production.yml` plus the
  optional `local-reranker` profile when `RERANK_PROVIDER=local_transformers`

## Preconditions

- The backend stack can start cleanly from `docker compose`.
- When testing `bedrock_cohere`, valid AWS credentials and
  `BEDROCK_RERANK_MODEL_ARN` are available to the backend runtime.
- When testing `local_transformers`, the optional Compose profile
  `local-reranker` is available and `RERANKER_URL` resolves from the backend
  container.
- Use the current runtime files above as the source of truth if docs ever drift.

## Automated Smoke Script

Run the automated local smoke:

```bash
./scripts/testing/rerank_provider_smoke_local.sh
```

Optional base URL override:

```bash
./scripts/testing/rerank_provider_smoke_local.sh http://localhost:18000
```

This script:

1. Restarts the local backend for each provider mode:
   `bedrock_cohere`, `local_transformers`, and `none`.
2. Starts the `local-reranker` Compose profile only for `local_transformers`.
3. Verifies backend startup with `GET /health`.
4. Verifies the reranker requirement contract with
   `GET /api/admin/health/connections`.
5. For `local_transformers`, verifies that the effective reranker URL matches
   the configured target resolved from the exported `RERANKER_URL`, then the
   local backend env file, then the default
   `http://reranker-transformers:8080`.
6. Runs a real `rerank_chunks(...)` probe inside the backend container to prove:
   - `bedrock_cohere` reorders the candidate list
   - `local_transformers` reorders the candidate list
   - `none` preserves the original retrieval order

Evidence is written to
`file_outputs/temp/rerank_provider_smoke_local_<timestamp>.json`.

## Evidence Template

Record each manual test case with:

- Timestamp (UTC)
- Environment (`local` or `standalone`)
- Provider mode
- Relevant env keys used
- Test case ID
- Result (`pass` or `fail`)
- Notes and error snippet (if fail)

---

## Test Matrix

### A. Provider Selection and Startup (Automated + Manual)

| ID | Test | Steps | Pass Criteria | Automated |
|---|---|---|---|---|
| `BEDROCK_COHERE_HEALTH` | Bedrock startup | Set `RERANK_PROVIDER=bedrock_cohere`, keep the local reranker service stopped, start backend. | `/health` returns 200. | Yes |
| `BEDROCK_COHERE_SERVICE_MODE` | Bedrock service contract | Check `/api/admin/health/connections`. | `reranker.required` is `false`. | Yes |
| `LOCAL_TRANSFORMERS_HEALTH` | Local reranker startup | Set `RERANK_PROVIDER=local_transformers`, start the `local-reranker` profile, start backend. | `/health` returns 200. | Yes |
| `LOCAL_TRANSFORMERS_SERVICE_MODE` | Local reranker service contract | Check `/api/admin/health/connections`. | `reranker.required` is `true` and healthy. | Yes |
| `LOCAL_TRANSFORMERS_TARGET_URL` | Local reranker configured endpoint | Check `/api/admin/health/connections`. | The backend reports `reranker.url` as the configured `RERANKER_URL` value. Default: `http://reranker-transformers:8080`. | Yes |
| `NONE_HEALTH` | No-rerank startup | Set `RERANK_PROVIDER=none`, keep the local reranker service stopped, start backend. | `/health` returns 200. | Yes |
| `NONE_SERVICE_MODE` | No-rerank service contract | Check `/api/admin/health/connections`. | `reranker.required` is `false`. | Yes |

### B. Rerank Behavior (Automated + Manual)

| ID | Test | Steps | Pass Criteria | Automated |
|---|---|---|---|---|
| `BEDROCK_COHERE_RERANK_BEHAVIOR` | Bedrock reorders results | Run the automated smoke probe or manually exercise `rerank_chunks(...)` against the backend runtime. | The Bedrock provider moves the most relevant chunk to the top of the ranked output. | Yes |
| `LOCAL_TRANSFORMERS_RERANK_BEHAVIOR` | Local reranker reorders results | Run the automated smoke probe or manually exercise `rerank_chunks(...)` against the backend runtime. | The local transformers provider moves the most relevant chunk to the top of the ranked output. | Yes |
| `NONE_RERANK_BEHAVIOR` | No-rerank preserves order | Run the automated smoke probe or manually exercise `rerank_chunks(...)` against the backend runtime. | Output order exactly matches retrieval order. | Yes |

### C. Manual Operator Checks

| ID | Test | Steps | Pass Criteria |
|---|---|---|---|
| `C1` | Standalone Bedrock default | Start a standalone install from `env.standalone` without changing rerank vars. | Bedrock-backed reranking remains active without requiring the local reranker service. |
| `C2` | Standalone local reranker profile | Set `RERANK_PROVIDER=local_transformers`, keep `RERANKER_URL` aligned with the target reranker service, and start with the `local-reranker` profile when using the bundled container. | Backend starts cleanly and the configured reranker endpoint becomes the required dependency. |
| `C3` | Disabled rerank mode | Set `RERANK_PROVIDER=none` and restart backend. | Retrieval order is preserved and the local reranker service is not needed. |

---

## Sign-Off Criteria

- Automated local smoke passes for all three provider modes.
- Operators can identify the correct env keys for each mode without consulting
  outdated Bedrock-only guidance.
- Standalone docs make it clear that Bedrock remains the default path and that
  the local reranker service is required only for `local_transformers`.
