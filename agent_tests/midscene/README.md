# Midscene Curator-Agent Smoke Pilot

This directory is an isolated, on-demand Midscene Test Runner pilot for the
Docker development stack on the same trusted host. It complements `scripts/testing/dev_release_smoke.py`
by exercising curator-visible UI actions and then proving the resulting state
through deterministic APIs. This includes a designated dev server when the
harness runs on that server and targets its loopback proxy; remote shared-dev
URLs, production, CI, and release gating remain outside this pilot.

Midscene Test Runner 1.x is Beta. The package pins `@midscene/test` and
`@midscene/web` 1.12.2 plus Playwright 1.62.1. The current locked Midscene
dependency graph has npm audit advisories in transitive packages. The harness
therefore accepts only the four committed YAML files, runs against a local app,
uses a read-only Codex sandbox for model calls, and must not process untrusted
YAML or be promoted to CI without a fresh dependency/security review.

## Install

From the repository root:

```bash
cd agent_tests/midscene
npm ci
npx playwright install chromium
```

The default model path uses the Codex subscription authenticated for the exact
OS account running the harness and requires `codex login status` to report a
valid login. It never falls back to an OpenAI API key:

```text
MIDSCENE_MODEL_BASE_URL=codex://app-server
MIDSCENE_MODEL_NAME=gpt-5.6-sol
MIDSCENE_MODEL_FAMILY=gpt-5
MIDSCENE_MODEL_REASONING_ENABLED=true
MIDSCENE_MODEL_REASONING_EFFORT=low
MIDSCENE_MODEL_TEMPERATURE=1
```

The explicit temperature avoids Midscene 1.12.2's package default of `0`,
which GPT-5.6 Sol rejects; `1` is the model's supported API default.
The wrapper also clears inherited `MIDSCENE_PLANNING_MODEL_*` and
`MIDSCENE_INSIGHT_MODEL_*` overrides so every intent uses this one validated
provider/model slot and the verdict describes the provider that actually ran.

Export `TESTING_API_KEY` from the local stack configuration, then run:

```bash
scripts/testing/agent_ui_smoke.sh --offline
scripts/testing/agent_ui_smoke.sh --preflight-only
scripts/testing/agent_ui_smoke.sh
```

The strict preflight verifies authenticated app access, end-to-end backend
file-output writeability, PDF worker readiness, Chromium launch, Codex login,
model availability, and the configured reasoning effort. The OpenAI path uses
an authenticated model-metadata lookup; it does not send an inference request.

The explicit direct-billing alternative is:

```bash
export OPENAI_API_KEY=... # obtain this from the approved secret source
scripts/testing/agent_ui_smoke.sh --provider openai --case create --cost-warning-usd 5
```

Start with one focused case and inspect `model_usage` in `verdict.json` before
running another. The verdict deduplicates Midscene usage by provider request ID,
records input/cached-input/cache-write/output tokens, and estimates direct API
cost using the versioned GPT-5.6 Sol pricing reference embedded in the report.
The direct OpenAI usage shape is read from `prompt_tokens_details`; if cache-write
detail alone is absent, the verdict emits a conservative cost range and applies
the warning threshold to its upper bound. An unknown model or incomplete,
unidentified, or conflicting usage report makes the cost and warning status
explicitly unavailable rather than presenting a misleading finite bound.
For Codex runs, the same number is an API-equivalent estimate—not a subscription
charge. For OpenAI runs, it is an estimate and may differ from the provider's
final invoice. `--cost-warning-usd` is an after-run warning, not a hard cap.
See the official [GPT-5.6 Sol model and pricing reference](https://developers.openai.com/api/docs/models/gpt-5.6-sol).

No provider fallback exists. Cookie authentication is retained for testing a
local cookie-auth stack; the application URL remains loopback-only:

```bash
CURATOR_COOKIE='name=value' \
  scripts/testing/agent_ui_smoke.sh --app-auth cookie --url http://127.0.0.1:3002
```

## Running on a trusted dev host

Install and run the harness directly on the host that runs the Docker
development stack, under a dedicated trusted OS account, and keep the app URL
on loopback. The Codex app server and its authentication are local to that
runner account; they are not supplied by the application containers.

For an interactive or headless host, the preferred setup is:

```bash
codex login --device-auth
codex login status
```

OpenAI's documented fallback is to transfer the existing Codex credential cache
to that account's `~/.codex/auth.json`. If that route is necessary, transfer it
through an approved secret channel, restrict the directory and file to the
runner account, and leave the file writable so refreshed tokens can be saved.
Treat it like a password: never commit it, paste it into tickets/logs, store it
in this repository or `.env`, or bake it into a Docker image. A personal Codex
credential must not become unattended application or production-service
configuration; production enablement needs a separate security and operations
review.
See the official [Codex authentication guide](https://learn.chatgpt.com/docs/auth)
for device-auth and credential-cache behavior.

The direct OpenAI path is operationally simpler for a slow dev-server trial:
inject a dedicated-project API key into the runner environment from the dev
server's approved secret mechanism, select `--provider openai`, and run one case
at a time. The preflight verifies that the selected model exists with a
non-billable metadata request before starting a UI journey.

## Journeys and evidence

The runner executes serially and gives every created document and flow an
`agent-smoke-<run-id>` prefix:

1. Create, connect, and save a Gene Extractor to JSON flow.
2. Open a seeded flow, replace JSON with CSV, rewire, and save.
3. Upload a salted sample publication through Add Literature, load it, and ask
   the curator chat about focus genes.
4. Run a seeded Gene Extractor to JSON flow and verify its evidence export.

Midscene performs only visible curator actions and semantic UI assertions.
Typed Zod-validated nodes handle file selection, setup, polling, persisted graph
checks, durable transcripts, SSE provenance, flow-run evidence, screenshots,
and cleanup.

Artifacts are written under:

```text
file_outputs/temp/agent_ui_smoke/<run-id>/
├── api-evidence/
├── file-exports/
├── inputs/
├── midscene-reports/
├── preflight/
├── screenshots/
├── test-runner/
├── verdict.json
└── verdict.md
```

Secrets and auth headers are never written. API bodies are recursively redacted
and bounded by `AGENT_UI_SMOKE_EVIDENCE_PREVIEW_CHARS`. The browser API key is
added only to requests for the configured application origin. Cleanup clears the
active document, deletes each exact-ID generated file row and storage object
through a loopback/Compose-only helper, then deletes chat sessions, flows, and
documents. It verifies file absence before reporting cleanup clean. A cleanup
failure fails the case. `--retain-resources` is a debugging mode and produces a
partial verdict.

`verdict.json` and `verdict.md` include the runner Git SHA/hostname, per-run
token totals, and the current GPT-5.6 Sol API-cost estimate or conservative
range. Usage objects without a stable request identity, conflicting duplicates,
parse failures, and requests for models without a known pricing table are
surfaced rather than silently priced.
The verdict cannot pass unless every selected canonical case succeeds, at
least one model request is identified, and cleanup is clean.
When `--tag` narrows the runner selection, the verdict evaluates the non-empty
intersection of configured and actually executed canonical cases. A generated
run ID is resolved once per invocation so reports, API evidence, and cleanup
records cannot split across directories during longer preflight checks.

Teardown drains in-flight response capture before deleting resources; a bounded
drain timeout is itself a cleanup failure. Run IDs are normalized to the
backend-stable filename form and length, and generated-file deletion requires
the captured UUID and exact filename plus the run-prefix boundary.

## Pilot status

This suite is an on-demand, same-host dev-server smoke. Each invocation writes its own
verdict and sanitized evidence; it does not aggregate a reliability ledger or
gate commits and pull requests on a multi-run quota. Run IDs cannot reuse an
existing evidence directory. Keep it out of CI and release automation unless
the project explicitly promotes it later.
