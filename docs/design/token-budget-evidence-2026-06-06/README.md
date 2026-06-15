# Token Budget Evidence - 2026-06-06

This folder captures real Incus/VM runs used to evaluate token-budget pressure before implementing any P1 scalar truncation. The branch was deployed in `symphony-main` on `c974b53f`, with backend and TraceReview restarted against the main sandbox stack.

## What Ran

- Sample paper: `backend/tests/fixtures/sample_fly_publication.pdf`
- Fresh focused run: `dev_release_smoke_20260606T232851Z.json`
  - Non-streaming chat prompt: `What are the focus genes of this paper?`
  - Streaming chat prompt: `Briefly summarize the paper and name any focus genes mentioned.`
  - Non-streaming chat completed and returned focus genes: `ninaE`, `crb`, `NinaC`, `Eys`.
  - Streaming chat failed the harness because validator dispatch surfaced non-fatal AGR curation DB connection failures as `SPECIALIST_ERROR` events.
- Earlier same-session runs are retained because they show setup issues, flow behavior, and pre-redaction-fix traces.
- Flow measurements in this bundle are comparison evidence from earlier same-session runs, not as clean as the fresh post-fix chat and validator measurements.

## Main Measurements

The raw TSV/JSON/log captures used for these measurements are kept local-only
and ignored by this folder. They should not be committed to the public repo.

- Fresh standard chat model-live context was tiny:
  - `2e80ae...`: 246 JSON chars, about 62 estimated tokens.
  - `f597a3...`: 270 JSON chars, about 68 estimated tokens.
- Fresh validator payloads were also modest:
  - Four non-streaming validator calls ranged from 1,509 to 1,864 JSON chars.
  - Their `selected_inputs.evidence_quote` scalars ranged from 290 to 558 JSON chars.
  - The streaming validator call was 1,831 JSON chars; `selected_inputs.evidence_quote` was 277 JSON chars.
- `selected_input_large_scalar_paths` was empty for every fresh validator preflight event.
- Older pre-fix traces show `estimated_tokens` as `<redacted>` in some extraction events. That is the bug fixed by `c974b53f`; fresh events now preserve numeric token metrics.

## Interpretation

These runs do not support scalar truncation as the next first move. The measured pressure was in repeated/large event payloads and full section/chunk observability records, not in validator scalar fields such as `selected_inputs.evidence_quote`.

For P1, keep scalar truncation deferred until we have traces with genuinely large scalar paths. List/result capping remains plausible, but any future compaction/truncation should preserve a lookup path to the full payload.

## 2026-06-07 Rerun With Validation Tunnels

The raw `validation-rerun-*` directories from this pass are intentionally local-only and gitignored because they contain bulky smoke JSON and backend log excerpts. The useful summary is retained here.

TL;DR:

- Backend readiness was strict and green before the run:
  - curation DB connected and required.
  - literature Elasticsearch connected and required.
  - direct literature DB connected but optional.
- The live literature Elasticsearch package smoke passed before the paper run.
- Non-streaming chat passed on the fresh `sample_fly_publication` upload and identified `crb/crumbs`, `ninaE/Rh1`, and `Eys`.
- Streaming chat passed and produced trace id prefix `d1840f23...`.
- Flow execution reached the builder tools and logged verified evidence activity, but the smoke failed because the final `FLOW_FINISHED` event reported `total_evidence_records: 0`.
- TraceReview/Langfuse did not have the fresh trace ids. Backend logs show OTLP export `401 Unauthorized`, so these rerun traces are currently represented by smoke JSON and backend structured logs rather than TraceReview API payloads.

Fresh provider-context preflight measurements stayed small:

- `standard_chat` preflight events ranged from 246 to 459 JSON chars.
- validator preflight events ranged from 1,879 to 2,267 JSON chars.
- flow streamed-agent context was 848 JSON chars.

This rerun strengthens the original conclusion: there is still not evidence for truncating scalar values inside JSON structures as the next move. The issues exposed by the real run are operational readiness, duplicate/stale document reuse, TraceReview export availability, Redis auth noise, and the flow structured-output/evidence persistence mismatch.

## Full-Value Lookup Evidence

TraceReview model-live responses captured locally under
`trace_review/model_live_context_*.json` included:

- `observability_payloads.payload_inventory_available: true`
- `observability_payloads.exact_payload_requires_explicit_lookup: true`
- inventory endpoint: `langfuse_payloads`
- exact payload endpoint: `langfuse_payload`

The local evidence bundle also included:

- `trace_review/payload_inventory_*.json` - compact payload inventories without full values.
- `trace_review/exact_payload_f597_validator_preflight_event.json` - exact payload lookup for one validator preflight event.
- `trace_review/exact_payload_f597_largest_event_chunk0.json` - first chunk from a large event payload, demonstrating bounded retrieval.

## 2026-06-15 Cross-Surface Compaction Validation

ALL-561 added deterministic unit coverage for the two model-live compaction
surfaces after the standard chat and Agent Studio implementations landed.
These tests are intentionally CI-friendly substitutes for provider smoke until
a coordinator requests a fresh Incus run:

- Standard chat long-session replay:
  `backend/tests/unit/lib/openai_agents/test_chat_compaction_session.py::test_standard_chat_long_session_replay_stays_under_budget_and_keeps_flow_refs`
  seeds an existing compacted projection plus a later flow transcript. It
  proves the replayed model-live items stay below the configured compaction
  threshold, keep `flow_run_id` / lookup-reference text, omit the current
  already-persisted prompt, and never replay the bulky flow `payload_json`.
- Standard chat recall after compaction:
  `backend/tests/unit/lib/openai_agents/test_supervisor_context_recall.py::test_recall_chat_history_search_finds_early_turn_trimmed_from_live_context`
  keeps an early exact phrase out of the compacted/recent live context and
  confirms the supervisor-callable `recall_chat_history` search path retrieves
  that durable text verbatim.
- Agent Studio repeated tool-loop continuation:
  `backend/tests/unit/api/test_agent_studio_context_compaction.py::test_repeated_tool_loop_continuations_stay_compact_and_keep_exact_results`
  drives two consecutive Opus tool-use continuations. The frontend
  `TOOL_RESULT` events retain the full TraceReview inventory and exact payload,
  while each provider continuation receives only a compact
  `compacted_tool_result` with recall handles.
- Agent Studio recall after provider context editing:
  `backend/tests/unit/api/test_agent_studio_context_compaction.py::test_compact_tool_result_recall_hints_fetch_exact_turn_and_trace_payload`
  follows the compact payload's `get_chat_turn` and `get_trace_payload` hints,
  proving both the early durable turn and exact TraceReview payload remain
  retrievable while the compact provider message excludes the raw payload body.
- Observability/model-live boundary:
  existing chat context and runtime preflight tests continue to assert
  `payload_json_model_live=false`, summary-only
  `runtime.provider_context_preflight` trace events, and explicit payload lookup
  instead of default raw-value replay.
- PR validation confirmation:
  PR #474 GitHub checks passed on 2026-06-15, including Agent PR Gate,
  Backend Unit Tests, backend contract/persistence tests, frontend build/tests,
  CodeQL, GitGuardian, and shell regression suites. A focused local Docker run
  of the compaction, recall, runtime payload-budget, and project-agnostic
  guardrail unit tests also passed with 22 tests.

Residual risk: these tests prove deterministic compaction and recall contracts,
but they do not replace a live Incus/provider smoke with fresh Langfuse and
TraceReview artifacts. Run the full stack evidence preflight before any future
live token-budget evidence capture, and keep raw smoke JSON, TraceReview
payloads, and logs in local evidence bundles unless a curated artifact is
explicitly approved for review.

## Operational Findings

- Initial extraction trace writes failed because the sandbox `extraction_trace_events/` directory was not writable by the backend container. The local run was unblocked with `chmod 777` in the sandbox. This should become an operational follow-up if it recurs.
- AGR curation DB lookups failed with transient connection-refused errors during validator dispatch. The extraction still produced useful gene evidence, but smoke failed when streaming surfaced those validator warnings as error events.
- TraceReview correctly parsed local trace events after the redaction and inferred-sizing fixes and reported mixed explicit/inferred context. Explicit preflight events carry the direct provider-context measurements; inferred Langfuse generation rows are kept separate to avoid overclaiming precision.

## Local Evidence Classes

The following artifact classes were used for the analysis but are intentionally
local-only:

- `dev_release_smoke_*.json` smoke harness outputs.
- `extraction_trace_events/*.jsonl` selected extraction trace JSONL files.
- `backend_logs/` filtered backend logs for deployed evidence windows.
- `context_reports/*.json` chat session context-report outputs.
- `trace_review/model_live_context_*.json` TraceReview model-live context summaries.
- `trace_review/payload_inventory_*.json` TraceReview payload inventories.
- `trace_review/exact_payload_*.json` exact/chunked payload lookup samples.
- `summaries/*.tsv` compact tables used for quick review.

Keep those files in a local evidence bundle when rerunning this analysis; commit
only the summarized conclusions unless a specific artifact is deliberately
curated for review.
