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

See `summaries/backend_log_provider_context_preflight.tsv`.

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

## Full-Value Lookup Evidence

TraceReview model-live responses saved under `trace_review/model_live_context_*.json` include:

- `observability_payloads.payload_inventory_available: true`
- `observability_payloads.exact_payload_requires_explicit_lookup: true`
- inventory endpoint: `langfuse_payloads`
- exact payload endpoint: `langfuse_payload`

The folder also includes:

- `trace_review/payload_inventory_*.json` - compact payload inventories without full values.
- `trace_review/exact_payload_f597_validator_preflight_event.json` - exact payload lookup for one validator preflight event.
- `trace_review/exact_payload_f597_largest_event_chunk0.json` - first chunk from a large event payload, demonstrating bounded retrieval.

## Operational Findings

- Initial extraction trace writes failed because the sandbox `extraction_trace_events/` directory was not writable by the backend container. The local run was unblocked with `chmod 777` in the sandbox. This should become an operational follow-up if it recurs.
- AGR curation DB lookups failed with transient connection-refused errors during validator dispatch. The extraction still produced useful gene evidence, but smoke failed when streaming surfaced those validator warnings as error events.
- TraceReview correctly parsed local trace events after the redaction and inferred-sizing fixes and reported mixed explicit/inferred context. Explicit preflight events carry the direct provider-context measurements; inferred Langfuse generation rows are kept separate to avoid overclaiming precision.

## Folder Map

- `dev_release_smoke_*.json` - smoke harness outputs.
- `extraction_trace_events/*.jsonl` - selected extraction trace JSONL files from fresh and comparison runs.
- `backend_logs/filtered_backend_20260606T2323Z.log` - filtered backend logs for the deployed evidence window.
- `context_reports/*.json` - `/api/chat/sessions/{session_id}/context-report` outputs.
- `trace_review/model_live_context_*.json` - TraceReview model-live context summaries.
- `trace_review/payload_inventory_*.json` - TraceReview payload inventories.
- `trace_review/exact_payload_*.json` - exact/chunked payload lookup samples.
- `summaries/*.tsv` - compact tables used for quick review.
