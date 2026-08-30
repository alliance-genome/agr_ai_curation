# Midscene Curator-Agent Smoke Pilot Workpad

## Scope

- Branch: `codex/midscene-curator-agent-smoke`
- Base: clean `origin/main` worktree
- Target: same-host Docker development stack; default `http://localhost:3002`
- Validation URL: loopback proxy `http://127.0.0.1:13004`
- Status: pilot implementation; non-blocking and local-only

## Acceptance criteria

- [x] Exact Midscene 1.12.2 and Playwright 1.62.1 package lock.
- [x] Codex app-server default with low reasoning and no API-key fallback.
- [x] Four serial, parse-validated YAML curator journeys.
- [x] Typed API/polling/graph/evidence/cleanup nodes.
- [x] Salted fixture uploads and `agent-smoke-<run-id>` resource names.
- [x] Sanitized screenshots, API evidence, JSON verdict, and Markdown verdict.
- [x] Offline coverage for configuration precedence, provider selection,
  redaction, API errors, graph assertions, polling bounds, teardown order, and
  cleanup behavior.
- [x] All four cases pass headlessly against the local Docker stack.
- [x] Independent GPT-5.6 Sol high-reasoning rigorous review is clean.
- [x] Isolated commit and PR contain the implementation and review evidence.

## Progress trail

- 2026-08-30: Created a clean worktree from `origin/main`; excluded unrelated
  Sentry hotfix changes from the original checkout.
- 2026-08-30: Verified Midscene Test Runner, Codex app-server, model listing,
  curator UI, flow CRUD, document, durable chat, SSE, and evidence-export seams.
- 2026-08-30: Implemented the locked package, strict configuration, browser
  setup, four journeys, deterministic assertions, cleanup, and per-run verdicts.
- 2026-08-30: Passed typecheck, 34 offline tests, and parse/validation of all
  four YAML files without a browser or model request.
- 2026-08-30: `npm audit --omit=dev` reports advisories in the exact pinned
  Midscene dependency graph (including js-yaml, sharp, uuid, and its bundled
  Puppeteer tooling). Kept the pilot local-only and committed-YAML-only; do not
  promote without updating/reviewing the required pin.
- 2026-08-30: Passed strict loopback preflight for application authentication,
  backend file-output storage, PDF processing, Chromium, Codex app-server model
  availability, and `low` reasoning.
- 2026-08-30: Independent high-reasoning GPT-5.6 Sol review found and drove
  corrections for loopback enforcement, same-origin credential injection,
  generated-file cleanup, provenance linkage, per-run verdict integrity, pending
  response teardown, exact file ownership, run-ID stability, and tag-filter
  verdicts. The same reviewer gave clean final signoff after 34 tests and all
  four YAML validations passed.
- 2026-08-30: Verified all four focused journeys in final form at
  `post-review-focused-create-20260830`, `post-review-focused-edit-v2-20260830`,
  `post-review-focused-upload-20260830`, and
  `post-review-focused-run-v2-20260830`. Preserved one flow replanning-cap flake
  and one edit provider-capacity failure as separate, cleanly torn-down debug
  evidence rather than masking them with whole-case retries.
- 2026-08-30: Passed canonical run `post-review-full-01-20260830` headlessly,
  unfiltered, non-retaining, and with zero whole-case retries. All four cases
  passed; exact generated-file row/storage deletion and every other teardown
  operation were verified clean.
- 2026-08-30: Removed the multi-run reliability ledger and quota at Chris's
  direction. The suite is an on-demand dev-server smoke; each invocation keeps
  its own pass/fail verdict and cleanup evidence without a multi-day PR gate.
- 2026-08-30: Revalidated typecheck, 33 offline tests, all four YAML journeys,
  shell/Python syntax, and diff checks. The independent reviewer gave clean
  final signoff after a fresh-run-directory guard prevented stale evidence reuse.
- 2026-08-30: Final residue audit found zero `agent-smoke-%` file rows, storage
  files, flows, documents, and visible chat sessions. The testing API key was
  absent from all smoke artifacts.
- 2026-08-30: Added request-ID-deduplicated token accounting, versioned GPT-5.6
  Sol API-cost estimates, and an after-run cost warning. Documented safe
  same-host dev-server execution with runner-account Codex device auth or an
  explicitly selected OpenAI key; production remains outside the pilot. The
  expanded offline suite passes 38 tests plus TypeScript and all four YAML
  validations. Changed-file diagnostics are clean; the frontend compiler still
  reports only its unrelated dependency-install baseline.
- 2026-08-30: The same independent high-reasoning reviewer found and verified
  fixes for verdict token-count redaction and false-finite cost bounds when
  usage data is incomplete or unpriced. Final signoff is clean with direct
  OpenAI nested cache-write accounting and explicit unknown-cost states.
