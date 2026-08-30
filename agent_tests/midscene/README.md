# Midscene Curator-Agent Smoke Pilot

This directory is an isolated, on-demand Midscene Test Runner pilot for the
local Docker development stack. It complements `scripts/testing/dev_release_smoke.py`
by exercising curator-visible UI actions and then proving the resulting state
through deterministic APIs. It is not a CI job, a shared-dev test, or a release
gate.

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

The default model path uses the current Codex subscription and requires
`codex login status` to report a valid login. It never falls back to an
OpenAI API key:

```text
MIDSCENE_MODEL_BASE_URL=codex://app-server
MIDSCENE_MODEL_NAME=gpt-5.6-sol
MIDSCENE_MODEL_FAMILY=gpt-5
MIDSCENE_MODEL_REASONING_ENABLED=true
MIDSCENE_MODEL_REASONING_EFFORT=low
```

Export `TESTING_API_KEY` from the local stack configuration, then run:

```bash
scripts/testing/agent_ui_smoke.sh --offline
scripts/testing/agent_ui_smoke.sh --preflight-only
scripts/testing/agent_ui_smoke.sh
```

The strict preflight verifies authenticated app access, end-to-end backend
file-output writeability, PDF worker readiness, Chromium launch, Codex login,
model availability, and the configured reasoning effort.

The explicit direct-billing alternative is:

```bash
OPENAI_API_KEY=... scripts/testing/agent_ui_smoke.sh --provider openai
```

No provider fallback exists. Cookie authentication is retained for testing a
local cookie-auth stack; the application URL remains loopback-only:

```bash
CURATOR_COOKIE='name=value' \
  scripts/testing/agent_ui_smoke.sh --app-auth cookie --url http://127.0.0.1:3002
```

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

Teardown drains in-flight response capture before deleting resources; a bounded
drain timeout is itself a cleanup failure. Run IDs are normalized to the
backend-stable filename form and length, and generated-file deletion requires
the captured UUID and exact filename plus the run-prefix boundary.

## Pilot status

This suite is an on-demand dev-server smoke. Each invocation writes its own
verdict and sanitized evidence; it does not aggregate a reliability ledger or
gate commits and pull requests on a multi-run quota. Run IDs cannot reuse an
existing evidence directory. Keep it out of CI and release automation unless
the project explicitly promotes it later.
