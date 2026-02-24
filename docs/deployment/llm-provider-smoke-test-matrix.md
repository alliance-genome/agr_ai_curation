# LLM Provider Smoke Test Matrix

Last updated: 2026-02-24

## Purpose

Validate that config-defined LLM providers and models work end-to-end in real runtime conditions with no silent fallback behavior.

## Environments

- **Local:** Frontend at `http://localhost:3002`, backend at `http://localhost:8000` (via docker compose)
- **Staging:** Organization staging URL and environment

## Preconditions

- All services are up and healthy (`docker compose ps` shows healthy status).
- `config/providers.yaml` and `config/models.yaml` are in sync (every model references a valid provider).
- API keys are configured for each provider under test (`OPENAI_API_KEY`, `GROQ_API_KEY`, etc.).
- `LLM_PROVIDER_STRICT_MODE=true` is recommended for smoke testing.

## Automated Smoke Script

Run the automated local preflight:

```bash
make smoke-llm-local
```

This runs `scripts/testing/llm_provider_smoke_local.sh`, which:

1. Waits for the backend to become healthy (`/health`).
2. Checks `GET /api/admin/health/llm-providers` for structural errors.
3. Checks `GET /api/agent-studio/models` for model list availability.
4. Parses the provider health response to verify zero structural errors.

Evidence is written to `file_outputs/temp/llm_provider_smoke_local_<timestamp>.json`.

The automated script covers test cases `BASE_HEALTH`, `A1`, `A1B`, and `A1_STRUCTURAL` below. All other test cases in this matrix are manual.

## Evidence Template

Record each manual test case with:

- Timestamp (UTC)
- Environment (`local` or `staging`)
- Provider ID
- Model ID
- Test case ID
- Result (`pass` or `fail`)
- Notes and error snippet (if fail)

---

## Test Matrix

### A. Provider Contract and Health (Automated + Manual)

| ID | Test | Steps | Pass Criteria | Automated |
|---|---|---|---|---|
| `BASE_HEALTH` | Backend health | `GET /health` returns 200. | HTTP 200. | Yes |
| `A1` | Provider health endpoint | `GET /api/admin/health/llm-providers` | HTTP 200, `errors` is empty, all mapped providers show `readiness: ready`. | Yes |
| `A1B` | Model list endpoint | `GET /api/agent-studio/models` | HTTP 200, response contains expected models. | Yes |
| `A1_STRUCTURAL` | Provider health body analysis | Parse `A1` response body. | `errors` array is empty (no structural contract violations). | Yes |
| `A2` | Provider/model drift detection | Temporarily introduce an invalid model-to-provider reference in a local branch. | Startup validation or the health endpoint reports a clear error. | No |

### B. Runtime Path (Manual, Per Provider/Model)

Run each scenario for every curator-visible model defined in `models.yaml`.

| ID | Test | Steps | Pass Criteria |
|---|---|---|---|
| `B1` | Agent Studio chat | Open Agent Studio, select the model, run a short prompt. | Successful response with no fallback or provider mismatch in logs. |
| `B2` | Tool-calling path | Use an agent or flow that invokes at least one tool call. | Tool call executes, response returns, no provider adapter errors. |
| `B3` | Flow execution | Run a small curation flow end-to-end with the model. | Flow completes and output is produced. |
| `B4` | Parallel tool call policy | For a provider with `parallel_tool_calls: false`, run a multi-tool agent. | Tools execute sequentially without error. For `parallel_tool_calls: true`, parallel execution works. |

### C. Reasoning and Temperature Behavior (Manual)

| ID | Test | Steps | Pass Criteria |
|---|---|---|---|
| `C1` | Reasoning-capable model | Run a reasoning model (e.g., `gpt-5.2`) with low, medium, and high reasoning levels. | Accepted levels work. Unsupported values are rejected or normalized. |
| `C2` | Non-reasoning model | Attempt to set reasoning on a non-reasoning model (e.g., `gpt-5.2-mini`). | Runtime does not crash. The setting is ignored or blocked. |

### D. Failure Path -- No Fallback (Manual)

| ID | Test | Steps | Pass Criteria |
|---|---|---|---|
| `D1` | Missing API key | Unset the provider API key in the local environment and restart. | Startup fails with an explicit missing env var error (strict mode) or health endpoint reports `missing_api_key` readiness. |
| `D2` | Invalid base URL | Set an invalid provider base URL and attempt a request. | Request fails with a provider-specific error. No silent reroute to another provider. |
| `D3` | Unknown model ID | Force an invalid model selection request via the API. | Explicit validation error. No fallback model is used. |

---

## Sign-Off Criteria

- All `A` tests pass in both local and staging environments.
- All `B` tests pass for every curator-visible model.
- `C` behavior matches the model definitions in `models.yaml`.
- `D` failures are explicit and expected (no fallback observed).
