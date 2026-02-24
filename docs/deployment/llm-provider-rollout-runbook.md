# LLM Provider Rollout Runbook

Last updated: 2026-02-24

## Scope

Safe rollout and rollback process for changes to:

- `config/providers.yaml` -- provider definitions (driver, API key env vars, base URLs, capability flags)
- `config/models.yaml` -- model catalog (model-to-provider mappings, reasoning/temperature support, curator visibility)
- Runtime provider/model validation behavior in `backend/src/lib/config/provider_validation.py`

## Change Types

- Add, update, or remove a provider entry
- Add, update, or remove a model mapping
- Change a provider's `supports.parallel_tool_calls` flag

## Pre-Deploy Checklist

1. **Config review complete:**
   - `providers.yaml` has exactly one provider with `default_for_runner: true` (currently: `openai`).
   - Every model's `provider` field in `models.yaml` references an existing provider ID in `providers.yaml`.
   - Each `litellm` driver provider has a `litellm_prefix` defined.
   - `supports.parallel_tool_calls` is explicitly set for each provider.

2. **CI and tests pass:**
   - Unit tests pass, including provider contract validation tests.
   - No unexpected failures in Agent Studio or provider-related test suites.

3. **Secrets and connectivity:**
   - Required API key env vars are prepared in the target environment (e.g., `OPENAI_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`).
   - Env var names match the `api_key_env` values in `providers.yaml`.
   - If a provider uses a non-default base URL, the corresponding `base_url_env` is set or `default_base_url` is correct.

4. **Strict mode:**
   - `LLM_PROVIDER_STRICT_MODE` defaults to `true`. In strict mode, a missing API key for any provider that is referenced by a model causes a startup validation error. Set to `false` only if intentionally deploying with unused providers unconfigured.

## Deployment Steps

1. Deploy config and code changes to the target environment.
2. Restart the backend service to apply provider/model config (`docker compose restart backend` or full redeploy).
3. Monitor startup logs for provider validation output. The backend runs `validate_and_cache_provider_runtime_contracts()` at startup and will fail fast if strict mode catches errors.
4. Check the diagnostics endpoint:
   - `GET /api/admin/health/llm-providers`
   - Verify `status` is `healthy`, `errors` is empty, and all expected providers show `readiness: ready`.
5. Run smoke checks:
   - `make smoke-llm-local` for automated health endpoint verification.
   - Manually verify: one chat call per critical model, one tool-calling scenario, one flow execution.

## Post-Deploy Verification

1. **Diagnostics healthy:**
   - No provider/model contract errors in the `/api/admin/health/llm-providers` response.
   - Expected provider and model counts match `summary.provider_count` and `summary.model_count`.

2. **Product checks:**
   - Agent Studio model selector renders the correct models (curator-visible models only).
   - A curator can execute at least one workflow per primary model.

3. **Logs:**
   - No sustained increase in provider runtime errors.
   - No unexpected model substitution or fallback behavior.

## Rollback Triggers

- Startup provider validation fails unexpectedly.
- A critical model fails execution for curator workflows.
- Widespread tool-calling or flow execution regression after the change.

## Rollback Procedure

1. Revert `providers.yaml` and/or `models.yaml` to the last known good revision.
2. Revert related backend code only if required by the change set.
3. Redeploy and restart the backend service.
4. Re-run `make smoke-llm-local` and verify `GET /api/admin/health/llm-providers`.
5. Announce rollback completion and impact summary.

## Incident Triage Quick Guide

| Symptom | Check |
|---|---|
| Unknown provider/model contract error | Verify model `provider` references match provider IDs in `providers.yaml`. Check for typos. |
| Missing API key / runtime readiness failure | Verify the env var named in `api_key_env` is set and non-empty in the runtime environment. |
| Provider request failures (timeouts, auth errors) | Verify `base_url_env` or `default_base_url` is correct. Validate API credentials and network egress. |
| Tool-calling regression on one provider | Check `supports.parallel_tool_calls` in `providers.yaml` for that provider. |

## Communication Template

When announcing a provider/model change:

- Change summary (what providers/models were added, updated, or removed)
- Affected providers and models
- Deployment window
- Validation status (health endpoint result and smoke check outcome)
- Rollback status (if triggered)
