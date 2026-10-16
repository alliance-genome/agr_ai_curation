# AGENTS.md

This file is the shared instruction and policy source for humans, implementation
agents, and PR reviewers working in `agr_ai_curation`. Keep repository policy
here and use the linked guides for detailed contracts; CI review prompts should
load these instructions rather than maintain a separate policy copy.

## 1) System Boundaries

- App type: full-stack AI curation platform.
- Backend: `backend/` (FastAPI, config-driven agents, tool runtime).
- Frontend: `frontend/` (React/TypeScript UI).
- Runtime config and agent definitions: `config/`.
- Persistent stores and services are orchestrated through the
  `docker-compose*.yml` files.
- Docker-first layout:
  - `docker-compose.yml` for local development stacks
  - `docker-compose.test.yml` for isolated test runs
  - `docker-compose.production.yml` for standalone/production-style deploys

Private deployment and workstation orchestration are maintained outside this
public application repository. Application changes must remain usable from a
fresh clone without access to private operations tooling.

## 2) Authoritative Docs

- Repository knowledge index: `docs/README.md`
- Developer docs index: `docs/developer/README.md`
- Curator docs index: `docs/curator/README.md`
- Development doctrine: `docs/developer/guides/DEVELOPMENT_DOCTRINE.md`
- Config system of record: `config/README.md`
- Test strategy and known scope: `docs/developer/TEST_STRATEGY.md`

## 3) Test and Validation Commands

Prefer `backend-unit-tests` for day-to-day backend changes. Use
`backend-contract-tests`, `backend-integration-tests`,
`backend-persistence-tests`, or `backend-tests` when acceptance criteria
need broader coverage.

For isolated backend test runs:

- Backend unit tests:
  `bash scripts/testing/docker-test-compose.sh run --rm backend-unit-tests`
- Backend contract tests:
  `bash scripts/testing/docker-test-compose.sh run --rm backend-contract-tests`
- Specific backend test:
  `bash scripts/testing/docker-test-compose.sh run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/path/to/test.py -v --tb=short"`

For a local development checkout with the main app stack running:

- Backend unit tests: `docker compose exec backend pytest tests/unit/ -v`
- Backend contract tests: `docker compose exec backend pytest tests/contract/ -q`
- Focused frontend tests:
  `docker compose exec frontend npm run test -- --run src/path/to/File.test.tsx`
- Frontend build: `docker compose exec frontend npm run build`

Host-side frontend validation:

- Focused frontend tests:
  `cd frontend && npm ci && npm run test -- --run src/path/to/File.test.tsx`
- Scoped TypeScript guard:
  `cd frontend && npm run type-check:changed -- --base origin/main`

Use focused tests for local implementation and review. The blocking GitHub
`Frontend Tests` job is the authoritative clean-checkout broad suite; run
`npm run test:stable` locally only when documented cross-cutting risk requires
it, not as routine pre-PR validation.

`type-check:changed` runs the full TypeScript compiler but fails only on
changed frontend TypeScript files or unscoped/config-level errors. If it reports
`FRONTEND_TYPECHECK_STATUS=baseline_only`, record the baseline debt and do not
treat it as a change-local failure.

Other useful validation:

- Syntax-only Python:
  `PYTHONPYCACHEPREFIX=/tmp/agr-ai-curation-pycache python3 -m py_compile backend/src/path/to/file.py`
- LLM provider smoke: `./scripts/testing/llm_provider_smoke_local.sh`
- Agent PR gate: `./scripts/testing/agent_pr_gate.sh`

Real extraction and release-evidence runs require a healthy local stack,
backend-to-Langfuse reachability, durable Langfuse dependencies, configured
read-only curation/literature access, and intact Compose DNS. Follow
`docs/developer/DEV_RELEASE_SMOKE_STRATEGY.md` and the release workflow for
the target environment.

OpenAI Responses websocket transport is the default
(`OPENAI_RESPONSES_WEBSOCKET_ENABLED=true`). A websocket handshake failure is
a transport/provider issue; disabling websocket is acceptable only as a narrow
diagnostic workaround, not as a permanent configuration change.

## 4) Agent Semantic Navigation Tools

Use `rg` first for broad discovery. When the remaining uncertainty is about
symbol ownership, definitions, references, imports/exports, or shared API blast
radius, use `scripts/utilities/agent_lsp.py` before editing or final review.

Common commands:

- `scripts/utilities/agent_lsp.py --root . status`
- `scripts/utilities/agent_lsp.py symbols path/to/file.py`
- `scripts/utilities/agent_lsp.py definition path/to/file.py 120 17`
- `scripts/utilities/agent_lsp.py references path/to/file.py 120 17`
- `scripts/utilities/agent_lsp.py --timeout 30 diagnostics --changed`
- `scripts/utilities/agent_lsp.py diagnostics backend/src/path.py frontend/src/path.tsx`

Positions are 1-based by default. Add `--zero-based` only for raw editor/LSP
coordinates. Diagnostics are navigation and review aids, not replacements for
required Docker tests. TypeScript queries automatically use the nearest nested
`tsconfig.json`; `warm` prepares every discovered TypeScript project from its
own `package-lock.json`.

## 5) Dangerous Areas

- Secrets: never commit `.env`, API keys, passwords, tokens, or credential
  files.
- Migrations and persistence: review data-integrity changes carefully.
- Tool policy and agent config: changes under `config/` can alter runtime
  behavior broadly.
- CI path-selection files:
  - `backend/tests/unit/.ci-ignore-paths`
  - `backend/tests/contract/.core-test-paths`
  Treat changes to these files as high risk and justify them in PR notes.

## 6) Expected Change Workflow

1. Sync the branch and inspect the changed scope.
2. Reproduce the problem or define expected behavior first.
3. Make minimal, scoped changes. Default to forward-only development: remove
   fallbacks, compatibility paths, and legacy branches instead of extending
   them; use explicit migrations for persistence changes.
4. Run targeted validation and tests.
5. Update documentation when behavior or process changes.
6. Keep one workpad-style progress trail and clear acceptance criteria for
   autonomous agent work.
7. Surface every operational limit through environment configuration as
   described below.

## 7) Operational Limits Must Be Surfaced to `.env`

Every operational limit must be environment-configurable with its current value
as the default and documented in `.env.example`. Never introduce a bare
hardcoded operational limit.

Operational limits include agent/validator turn limits, tool-call budgets, batch
sizes, parallelism caps, list/page/section caps, result caps, retry counts,
timeouts, size/preview thresholds, and feature kill switches. Pure internal
plumbing waits, such as a short queue poll, are exempt.

For backend code, add a getter in
`backend/src/lib/openai_agents/config.py` using the existing environment
helpers. Isolated package code under
`packages/alliance/python/src/agr_ai_curation_alliance/` cannot import the
backend config module; read the same environment variable directly there so one
setting tunes both processes.

Document each setting under the `# Operational limits` section of
`.env.example`, including what it controls, why someone might change it, and
its default. Adding a numeric or boolean operational limit without both the
environment getter and documentation is a review-blocking defect.

## 8) LLM Judgment and Structural Enforcement

When a task delegates checking, validation, interpretation, or review to an LLM,
the LLM owns the semantic judgment. Do not add a second programmatic judge that
recomputes, overrides, or vetoes that judgment through substring matching,
regular expressions, keyword lists, similarity thresholds, or hardcoded
scientific/domain-policy rules. Calling such logic a "validator", "guardrail",
"sanity check", or "finalizer" does not make it acceptable.

Code may enforce structural and operational contracts: required fields, data
types, parseable formats, allowed schema values, existing record references,
ownership/authorization, and safe persistence. These checks must not decide
whether evidence supports a claim, two descriptions mean the same thing, an
annotation is scientifically justified, or an LLM's interpretation is correct.
For example, checking that an evidence record exists is structural; requiring
the LLM's explanation to repeat an exact phrase from that record as a condition
of scientific acceptance is semantic and prohibited.

Put domain policy and evidence-assessment instructions in the LLM's task, and
preserve its explanation and uncertainty. The LLM may return unresolved or
needs-review judgments. Do not silently convert them to success, or convert an
LLM-supported judgment to failure using a separate coded semantic policy.

The LLM decides whether it needs tools to make its judgment. Do not require a
lookup solely as proof that a validator ran, and never fabricate a lookup audit
for a scientific assessment. A task that explicitly requires database verification
still needs real supporting records; copied identifiers and provenance must match
those records. Report missing or contradictory output as a structural problem for
bounded correction. If correction cannot finish, preserve the LLM's judgment,
explanation, and valid values, disclose the incomplete output, and prevent it from
being finalized as complete. Do not substitute a new scientific verdict.

When editing existing validation, identify this boundary explicitly. Report
conflicting semantic gates and remove them within the authorized scope; do not
preserve or introduce them merely as "defense in depth". Tests should verify
structural enforcement and faithful handling of LLM judgments, not establish
an additional deterministic oracle for scientific meaning. Any exception must
come from Chris's explicit instruction for that specific semantic rule.

A minimum character count used to reject an LLM-selected abstract is a semantic
quality veto, not a structural check. Making such a veto environment-configurable
does not make it permissible. Exact source-copy, span offset/hash verification,
and document/tenant ownership checks protect provenance and remain mandatory;
they do not assess whether the selected evidence scientifically supports a claim.

## 9) Shared Implementation and Review Policies

These policies apply to implementation and review. Violations relevant to the
changed behavior require correction before merge; establish evidence before
reporting a finding. Keep policy here rather than duplicating it in CI prompts.

### Forward-only changes and explicit recovery

Fallback behavior is BLOCKING by default. Flag:

- Fallback values or defaults that mask missing data (e.g. `value ?? fallbackValue`, `value || defaultValue` where the intent is graceful degradation)
- Backwards-compatibility shims or legacy support code
- Defensive coding patterns that silently handle unexpected states instead of failing clearly
- Try/catch blocks that swallow errors and return default values
- Feature flags or conditional paths that preserve old behavior alongside new behavior

Accept a fallback only when it has verifiable developer approval documented by either a pre-existing
adjacent repository comment that explicitly records developer approval and explains why it is required,
or a linked Linear issue/PR discussion
containing the developer decision. A new implementation comment that merely claims approval is not
evidence. Cite the approval source when accepting a fallback; without verifiable approval, flag it as
BLOCKING.

Explicit failure handling, nullish coalescing (`??`), optional chaining (`?.`), and defaults for fields
whose contract is genuinely optional are not fallbacks. The rule targets alternate behavior that masks
a reachable failure, silently degrades results, guesses missing values, or preserves retired behavior.

### Project-agnostic core boundaries

The reusable platform should be usable by other groups that bring their own agents, tools, schemas,
labels, group identifiers, curation domains, prompts, data sources, and deployment settings.
Flag as BLOCKING any changed reusable core code that embeds Alliance/AGR/MOD-specific assumptions
instead of reading from config, package drop-ins, adapters, runtime manifests, environment settings,
or extension points.

Examples to flag when they appear in core paths:

- Hard-coded Alliance, AGR, MOD, organism, curator-group, or curation-domain concepts
- Fixed Alliance service choices, URLs, API names, database assumptions, feature gates, or labels
- Baked-in default agents, tools, prompts, schemas, sample data, or UI copy that should be supplied
  by a runtime package or deployment config
- Generic interfaces that require Alliance identifiers or package names when an adapter/config value
  should carry that meaning

Do NOT flag legitimate project-specific content that is intentionally scoped to Alliance drop-ins or
instance documentation, including `packages/alliance/`, `alliance_agents/`, `alliance_config/`,
`alliance_tools/`, `scripts/deploy_alliance.sh`, Alliance migration history, tests for those
package-specific paths, and docs/examples that explicitly describe the Alliance instance. The issue is
leakage from those boundaries into reusable platform/core code.

For BLOCKING project-agnostic findings, identify the smallest extraction or interface change that keeps
core reusable while preserving Alliance behavior through config/package boundaries.

### Remove code made obsolete by the change

When a PR changes or removes functionality, ALL code that becomes unreferenced MUST be removed in the same PR. Flag as BLOCKING any instance of:

- Imports that are no longer used after the PR's changes
- Functions, components, hooks, or types that are no longer called/referenced after the PR's changes
- Files that are no longer imported by anything after the PR's changes
- Dead code paths that can no longer be reached
- Commented-out code that was replaced
- Variables assigned but never read
- Test files testing components/functions that were removed in this PR

Use grep/glob to verify whether flagged code is actually still referenced. Do not flag code as dead unless you have confirmed it is unreferenced.

### TraceReview extraction coupling

The `trace_review/` support program parses the Langfuse traces the core program emits. Developers rely
on it to faithfully extract traces, reasoning, agent decisions, tool calls, supervisor routing, and
domain envelopes from curator-feedback sessions. When a PR changes the agent/tool runtime or its
instrumentation, the trace structure can drift away from what TraceReview's analyzers parse, silently
breaking extraction. Treat the diff as TraceReview-affecting when it touches:

- Agent identity/surface: agent names, the `{agent_name}_config` observation contract, or supervisor
  routing (read by `trace_review/backend/src/analyzers/agent_config.py`, `agent_context.py`,
  `supervisor_routing.py`)
- Tools: tool names, tool input/output shapes, or the tool-call record fields `name`/`arguments`/`call_id`
  emitted from `backend/src/lib/openai_agents/streaming_tools.py`, `tools/`, and `tool_call_policy.py`
  (read by `trace_review/backend/src/analyzers/tool_calls.py`, `agent_context.py`, `pdf_citations.py`,
  `document_hierarchy.py`, `token_analysis.py`; some key on literal tool-name strings such as
  `search_document`, `read_section`, `ask_*`)
- Instrumentation: Langfuse span/observation naming, evidence spans, or the trace metadata keys
  `supervisor_agent`, `supervisor_model`, `has_document` assembled in
  `backend/src/lib/openai_agents/runner.py` (the `trace_metadata` dict) and emitted via
  `langfuse_client.py`; related instrumentation lives in `evidence_spans.py`, `evidence_summary.py`,
  `event_types.py`, `audit_labels.py`
- Domain envelope/validation schemas read by `trace_review/backend/src/analyzers/domain_envelopes.py`

Use grep to confirm whether the affected analyzer depends on (breakage) or is missing (new surface)
the changed name, key, or shape before flagging. Flag BOTH of these as BLOCKING, because TraceReview
is core diagnostic tooling that must stay faithful AND complete:

- Breakage: the change would make TraceReview drop, misparse, or mislabel data it currently surfaces
  (renamed/removed tool or agent, changed tool-call field, altered observation/metadata key the
  analyzers read).
- Missing coverage: the change adds a new agent, tool, tool-call/decision type, routing path, or
  diagnostic instrumentation signal that no analyzer extracts yet, so it would be invisible or
  unparsed in trace review. A new extraction surface that ships without the matching `trace_review/`
  wiring is a BLOCKING coverage gap, not a future nicety — diagnostics depend on TraceReview seeing
  everything the agents now do.
In both cases, name the analyzer file that breaks or lacks coverage and the `trace_review/` change
needed (new or extended analyzer, view, tool-name match, metadata key, or envelope schema). Only
downgrade from BLOCKING if the PR already includes the corresponding `trace_review/` changes.

### Sentry observability contract

When the diff changes backend failure handling, caught runtime/background failures, agent/runtime
instrumentation, or Sentry configuration, read `docs/developer/guides/SENTRY_OBSERVABILITY.md` and
verify the changed path preserves that contract. Flag as BLOCKING:

- A handled server failure that can now disappear without the appropriate reporting facade
- Secrets, sensitive database exception data, unbounded curator/document content, curator/document
  content outside the guide's explicit AI-agent content-tier controls, or raw application identifiers
  in fields the guide requires to be hashed entering Sentry
- Duplicate framework and explicit capture of the same handled failure
- Sentry instrumentation displacing canonical Langfuse/TraceReview evidence
- Frontend capture without a dedicated frontend DSN and redaction plan
- A new Sentry-reporting path without focused capture and redaction tests using fake sensitive strings

Do not require Sentry for unrelated changes or ordinary 4xx validation/auth conflicts. Do not convert
every log line into a Sentry event. Preserve the guide's bounded, mechanically scrubbed AI content tiers
and permitted operational trace identifiers. For a BLOCKING finding, cite the changed failure boundary
and the specific guide contract it violates, then recommend the smallest correction that preserves
public HTTP behavior and existing observability.

### Additional implementation and validation requirements


**Code quality**

- Changed code follows established project conventions where deviation creates a concrete defect or integration risk
- No hardcoded secrets or credentials
- Proper error handling and logging

**Security**

- No API keys, tokens, or secrets in code
- Input validation on API endpoints
- Proper authentication checks where needed

**Testing**

- Tests protect materially changed behavior and credible regressions
- Do not demand tests for unchanged framework behavior, trivial accessors, or theoretical combinations without risk evidence

**Docker**

- Dockerfile changes maintain build efficiency
- No removal of pids_limit from Docker configurations (has caused production crashes)

**Dependencies**

- New dependencies are necessary and from trusted sources
- requirements.txt and package.json changes are intentional
