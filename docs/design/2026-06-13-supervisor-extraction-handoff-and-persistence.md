# Supervisor Extraction Handoff and Persistence Plan

Date: 2026-06-13

Status: design plan from production trace diagnosis; not implemented

Related:

- `docs/design/2026-06-06-supervisor-curation-context-lookup.md`
- `docs/design/2026-06-06-flow-output-projection-and-context-lookup.md`
- `docs/design/2026-06-12-32ca758a-generic-pdf-tsv-hotfix-readiness.md`
- `backend/src/lib/openai_agents/streaming_tools.py`
- `backend/src/lib/openai_agents/curation_context_registry.py`
- `backend/src/lib/openai_agents/supervisor_context_tools.py`
- `backend/src/lib/openai_agents/agents/supervisor_agent.py`
- `backend/src/api/chat_stream.py`
- `backend/src/api/chat_common.py`
- `backend/src/lib/domain_packs/materialization.py`
- `frontend/src/utils/auditHelpers.ts`
- `packages/alliance/domain_packs/*/domain_pack.yaml`

## Production Trigger

Production trace `83c3394d24a2127dd45037d8da581131` exposed a confusing supervisor failure mode:

1. The Allele/Variant Extraction specialist staged 16 allele observations, recorded evidence, finalized a builder payload, and ran active validation.
2. One allele validator request timed out at max turns, but the backend intentionally persisted that condition as a non-fatal `validator_error` finding in the validated envelope.
3. The supervisor received only a compact first-few-objects handoff, wanted more detail, and called `inspect_curation_context(scope="current_chat")`.
4. `current_chat` returned `no_context` because it only sees persisted completed chat extraction rows. The extraction had been emitted inside the still-running turn but had not yet been persisted by the outer chat stream.
5. The supervisor retried the allele extractor as a summarizer. That created a fresh empty builder workspace, reported no objects, failed builder finalization, and then the supervisor called General PDF Extraction for an allele task.
6. The user cancelled during the later General PDF Extraction call. Because chat extraction persistence currently happens only after `RUN_FINISHED`, the good first allele extraction was not durable.

The root issue is not allele extraction quality. It is the handoff/persistence contract between builder-backed specialists, the supervisor, and curation-context tools.

## Design Goals

- Replace the current first-five compact handoff with a compact, paged object manifest that gives the supervisor a real view of what was found.
- Let domain-pack YAML designate the object payload fields that are visible in the supervisor's default manifest, reusing `workspace_display` where possible.
- Persist canonical extraction results immediately after builder finalization plus inline validation, before later supervisor activity or user cancellation can lose the extraction.
- Revamp the extraction-context lookup tool so the supervisor is not forced to choose between confusing storage-timing scopes for ordinary follow-up.
- Make follow-up tools obvious: after a specialist returns an extraction result, the supervisor should know it can answer directly, inspect details, export rows, or prepare for curation.
- Steer the chat supervisor toward completion after successful extraction: a non-empty extractor result is normally enough to answer the curator's current request unless the curator asks to iterate, the result is clearly empty, or the specialist explicitly reports that the requested scope was not handled.
- Make a coordinated pass over Chat-with-Claude tool descriptions and prompts so the new result-inspection behavior is explained consistently wherever the supervisor, extractor handoffs, export tooling, curation prep, and trace/debug tools are described.
- Display non-fatal validator warnings as warnings, not hard specialist failures.
- Keep canonical payloads out of model context. The supervisor should see bounded manifests and inspect slices, not raw envelopes.

## Non-Goals

- Do not send full canonical envelopes into the supervisor model context.
- Do not remove bounded inspection/pagination; large extractions still need server-owned slicing.
- Do not make the supervisor responsible for persistence.
- Do not use prose parsing as a recovery path for curation data.
- Do not change validator semantics from "validator error finding is curator-visible" to "throw away the extraction."

## Forward-Only Rules

This fix should be remove-and-replace work, not compatibility layering.

- No compatibility shims for the old supervisor-facing result lookup API.
- No aliasing old `scope=current_chat/current_turn/current_document` model-facing behavior into the new tool.
- No silent fallback from missing domain-pack display metadata to guessed supervisor fields.
- No legacy artifact/prose/items/raw-mention row sources as recovery paths.
- No new prose parsing, answer-table parsing, or artifact-summary interpretation to compensate for missing canonical data.
- If a domain pack cannot declare the fields needed for the default supervisor manifest, fail validation/tests for that pack rather than guessing at runtime.
- If a caller needs old `inspect_curation_context` behavior, update that caller to the new tool contract or split it into an explicitly named non-result tool. Do not keep the confusing scope-first tool available to the chat supervisor.

## Branch, PR, And Review Workflow

Implement this work on a dedicated branch, then open a PR to `main`. Do not work directly on `main` for this implementation pass.

Expected workflow:

1. Create a focused branch from current `main`, for example `hotfix/supervisor-result-handoff`.
2. Implement the slices below incrementally, preserving the `5.5 high` review gates.
3. Push the branch and open a PR against `main` once the implementation is coherent enough for CI and Claude Code review.
4. Iterate on Claude Code PR feedback the same way as prior hotfix work: address actionable comments, push fixes, and re-request/retrigger review until the remaining feedback is either resolved or explicitly documented as out of scope.
5. Keep working until the relevant automated tests/checks pass. Do not merge with known failing relevant tests unless Chris explicitly approves the risk.
6. Merge to `main` only after the PR review loop and tests are solid.

Because this is a behavior-changing hotfix, each review iteration should preserve the forward-only rules above. Do not resolve review feedback by adding compatibility shims, broad fallback parsing, or alternate legacy row sources.

## Key Principle

Once a builder-backed specialist finalizes and inline validation returns a canonical envelope, the result exists. It should receive a stable extraction result id immediately, and every later supervisor operation should be able to refer to that id or to a clear "the result just produced" concept.

"Validated" should not mean "complete only in a transient event list until the outer assistant answer finishes."

For chat UX, "extracted" should usually mean "ready for the supervisor to answer." The supervisor is primarily responsible for choosing the first specialist, preserving the curator's intent, explaining returned results, and asking whether the curator wants another pass. It should not treat every successful extraction as a draft that needs another specialist call before it can speak.

## Grounded Current-State Map

The implementation agent should verify these paths before editing. These are the current pressure points, not abstract suggestions:

- `backend/src/lib/openai_agents/streaming_tools.py`
  - `_DOMAIN_ENVELOPE_SUPERVISOR_FIELD_PRIORITY` and `_DOMAIN_ENVELOPE_SUPERVISOR_FIELD_SKIP` are hardcoded supervisor field-selection policy. The new plan should remove this model-facing fallback policy rather than extend it.
  - `_reduce_specialist_output_for_supervisor()` delegates canonical domain envelopes to `_domain_envelope_supervisor_summary()`.
  - `_domain_envelope_supervisor_summary()` currently shows only the first five objects and a short finding summary.
  - `_domain_envelope_supervisor_payload_fields()` and `_domain_envelope_supervisor_fallback_payload_fields()` currently guess which payload fields are safe to show. This must be replaced by domain-pack-declared field policy.
  - `run_specialist_with_events()` validates builder finalization before emitting `INTERNAL_EXTRACTION_RESULT`. The immediate persistence hook belongs after builder/materializer validation has produced the canonical envelope with non-fatal findings attached, and before the result is reduced for supervisor handoff.
- `backend/src/lib/openai_agents/extraction_builder_workspace.py`
  - `ExtractionBuilderFinalization.summary()` already carries `builder_run_id`, `builder_invocation_id`, `candidate_ids`, and `source_candidate_ids`. Reuse those for idempotency metadata instead of inventing a separate per-event identity.
  - `build_internal_extraction_result_event()` is the authoritative internal event builder for canonical extraction results. The event details should carry the persisted `extraction_result_id`, stable `result_ref`, and persistence status after inline persistence lands.
- `backend/src/api/chat_common.py`
  - `_build_extraction_candidate_from_tool_event()` intentionally accepts only `INTERNAL_EXTRACTION_RESULT` because accepting supervisor-facing `TOOL_COMPLETE` events previously caused duplicate persistence. Keep that lesson: do not add another fallback row source.
  - `_persist_extraction_candidates()` and `_persist_completed_chat_stream_turn()` currently perform the first durable extraction write at completed-turn time. After this change they should only link/update already persisted extraction rows, or disappear from the successful builder-backed chat path.
- `backend/src/api/chat_stream.py`
  - Streaming and non-streaming paths both accumulate extraction candidates and persist only after `RUN_FINISHED`. Cancellation before that block currently loses a good extraction. The new design must make persistence independent of `RUN_FINISHED`.
  - `INTERNAL_EXTRACTION_RESULT` is intentionally suppressed from the client stream. That can remain true; the persisted id/ref should still be available to the supervisor handoff and final linkage path.
- `backend/src/lib/openai_agents/supervisor_context_tools.py`
  - `inspect_curation_context()` currently multiplexes extraction results, review sessions, files, session files, and old storage-timing scopes.
  - `_current_turn_records()` maps transient current-turn events to fake ids such as `current-turn:N`.
  - `_authorized_extraction_results()` implements the old scope-first lookup model.
  - `_objects_detail()`, `_compact_object()`, `_selected_scalar_fields()`, and `_evidence_detail()` contain useful pagination/detail ideas but currently guess scalar fields and can expose evidence-oriented payload. The new `inspect_results` implementation may reuse the safe parts, but it must not retain scope-first model-facing semantics.
- `backend/src/lib/openai_agents/curation_context_registry.py`
  - This is a transient `ContextVar` registry for current-turn internal extraction events. After immediate persistence, it should no longer be part of the chat supervisor's result lookup contract. If kept, keep it as backend trace/debug plumbing only.
- `backend/src/lib/curation_workspace/extraction_results.py`
  - `persist_extraction_result()` and `persist_extraction_results()` currently insert rows and do not return/lookup an idempotent existing row.
  - `_is_extraction_envelope_payload()` still accepts legacy envelope-like keys such as `curatable_objects`, `items`, `raw_mentions`, `exclusions`, and `ambiguities`. The new builder-backed chat path should accept only canonical domain envelopes, not legacy row sources.
- `backend/src/lib/curation_workspace/models.py`
  - `CurationExtractionResultRecord` has no idempotency key, payload hash, or unique constraint today. Immediate persistence needs a migration-level duplicate-prevention mechanism.
- `backend/src/lib/domain_packs/materialization.py`
  - Workspace review display already reads `workspace_display` metadata and has UI-oriented fallbacks. Do not reuse its fallback behavior for supervisor manifests. The supervisor manifest path needs explicit validation that field policy exists.
- `backend/src/lib/openai_agents/agents/supervisor_agent.py`
  - `_INSPECT_CURATION_CONTEXT_TOOL_NAME`, `_SUPERVISOR_BUILTIN_TOOL_NAMES`, the runtime availability note, and the `inspect_curation_context_tool()` wrapper are the places where the supervisor-facing tool surface must be replaced.
- `backend/src/api/chat_execute_flow.py`
  - The flow-output summary text currently still points model behavior at `inspect_curation_context`. Include this in the prompt/tool-language pass.
- `frontend/src/utils/auditHelpers.ts`
  - `getEventSeverity()` currently maps any event type containing `ERROR` to error before checking `details.fatal` or `details.severity`.
  - The `SPECIALIST_ERROR` display label currently says "failed" in cases that can be non-fatal validator warnings.

## Part 1: Paged Object Manifest Handoff

The current supervisor reduction summarizes only the first few objects and first few validation findings. That is too little for extraction tasks whose normal answer is "list what was found."

Replace that with a manifest handoff shaped around object identity:

```text
Extraction result ready: agr.alliance.allele
Result ref: extraction-result:<uuid or current stable ref>
Objects found: 16
Manifest page: 1 of 1, page_size=100

1. AlleleMention allele-mention-lag1-q385: lag-1(q385)
   validated_to: WB:WBVar00241097; symbol=q385; status=resolved
2. AlleleMention allele-mention-lag1-om13: lag-1(om13)
   validated_to: ...
...

Available actions:
- Answer from this manifest when it satisfies the user's request. A non-empty extractor result is usually enough to answer; do not rerun an extractor merely to gain confidence.
- Use inspect_results(result_ref=..., action="objects", cursor=...) for more objects.
- Use inspect_results(result_ref=..., action="evidence", object_ref=...) for evidence.
- Use export_to_file(source_result_ref=..., format=...) only if the user asks for a file/export.
- Ask "Ready to prepare these for curation?" before prepare_for_curation.
```

The manifest is compact because each row is scalar-only:

- data type / object type;
- stable pending ref or object id;
- display label/name/mention;
- associated gene/locus/taxon when cheap and relevant;
- validation status;
- validated CURIE/symbol/provider value when present;
- warning/error code count when unresolved.

It is still complete for normal-sized results. The default page should be 100 objects. If that becomes an implementation constant, surface it through env config and document it in `.env.example` rather than hardcoding it.

### Pagination Rules

- Include up to 100 objects in the supervisor handoff by default.
- Include `next_cursor` when more objects exist.
- The supervisor should not re-run the specialist to see page 2. It should inspect the same extraction result by ref.
- The manifest should be generated from the canonical envelope, not from prose.
- For very large labels, truncate each label with a clear marker, but preserve refs.
- Do not include evidence quote text in the default manifest. Evidence belongs in a bounded detail lookup by result ref and object ref.

### Data-Type Display Fields

Each domain pack should expose a small display-field policy in YAML. Reuse existing `workspace_display` metadata where possible instead of adding a second display system:

- gene: mention/label, validated gene ID, symbol, taxon/provider;
- allele: mention, associated gene/locus, validated allele ID, allele symbol, taxon/provider;
- disease: label, validated disease CURIE, ontology/source;
- phenotype: phenotype statement/term label, validated ontology CURIE when present;
- generic object: class key, label, source identifier when present;
- evidence association objects: association label plus linked object refs.

The YAML policy should designate fields only. It should not contain supervisor prose, retry instructions, pagination rules, or evidence inclusion behavior. The backend manifest renderer owns the response shape, page size, truncation, validation-status fields, warning counts, and evidence exclusion.

Recommended first pass:

- Use `workspace_display.primary_label_field` or `workspace_display.primary_label_fields` for the manifest label.
- Use `workspace_display.secondary_label_field` for compact secondary context.
- Use `workspace_display.summary_fields` as the ordered set of additional supervisor-visible payload fields.
- Add a `supervisor_manifest` block only if `workspace_display` proves too broad or too UI/workspace-specific for chat handoff needs.

If a stageable domain object lacks a valid display policy, domain-pack validation should fail. Do not guess supervisor-visible fields at runtime.

### Manifest Renderer Placement

Add a small manifest builder that both specialist handoff code and `inspect_results` can call. Prefer a focused helper module over duplicating rendering logic in `streaming_tools.py` and `supervisor_context_tools.py`.

Suggested internal contract:

```python
ExtractionManifestPage = {
    "result_ref": "extraction-result:<uuid>",
    "extraction_result_id": "<uuid>",
    "domain_pack_id": "agr.alliance.allele",
    "adapter_key": "ALLELE",
    "agent_key": "allele_extraction",
    "result_status": "non_empty_extraction_ready" | "empty_extraction",
    "object_count": 16,
    "page": {"cursor": None, "limit": 100, "next_cursor": None},
    "objects": [
        {
            "object_ref": "allele-mention-lag1-q385",
            "object_type": "AlleleMention",
            "status": "resolved",
            "display_label": "lag-1(q385)",
            "secondary_label": "lag-1",
            "fields": [
                {"path": "payload.validated_to", "label": "validated_to", "value": "WB:WBVar00241097"},
                {"path": "payload.symbol", "label": "symbol", "value": "q385"}
            ],
            "validation": {"error_count": 0, "warning_count": 0, "unresolved_count": 0},
            "evidence_count": 2
        }
    ],
    "next_actions": [...]
}
```

The exact Python type can differ, but the behavior should not:

- The manifest builder accepts a canonical domain envelope plus an already persisted result id/ref.
- It never reads prose tool output, answer tables, files, artifacts, or legacy `items/raw_mentions` shapes.
- It resolves object identity from canonical object ids, pending refs, or stable object references in the envelope. Do not invent row numbers as the primary object ref when canonical refs exist.
- It returns structured data first, then renders supervisor-facing text from that structure.
- It includes status/count metadata owned by the backend, not YAML.
- It excludes full payloads, raw envelope JSON, and evidence quote text.
- It should not include `evidence_record_ids` in the default manifest unless there is a strong implementation need. Prefer `evidence_count` plus bounded lookup by `result_ref` and `object_ref`.

### Replace Current Summary Functions

The implementation should remove or rewrite these old supervisor-summary behaviors:

- Replace `_domain_envelope_supervisor_summary()`'s first-five object logic with the shared manifest renderer.
- Remove `_domain_envelope_supervisor_payload_fields()` as the supervisor policy source.
- Remove `_domain_envelope_supervisor_fallback_payload_fields()` from supervisor-visible manifest generation. Runtime guessing is specifically disallowed.
- Keep any minimal/fatal-error summary paths only for true invalid-output or failed-finalization cases, not as a fallback for valid domain envelopes.
- Do not carry forward the hardcoded `_DOMAIN_ENVELOPE_SUPERVISOR_FIELD_PRIORITY` / `_DOMAIN_ENVELOPE_SUPERVISOR_FIELD_SKIP` lists as a second hidden display system.

The manifest may still need backend safety filters such as scalar-only values and length truncation. Those filters are not display-policy fallbacks. They are safety bounds applied after YAML has chosen the fields.

### Domain-Pack YAML Validation Contract

Domain-pack YAML designates only payload fields the supervisor may see in the default manifest. The backend owns shape, pagination, result status, next-action guidance, truncation, validation counts, and evidence exclusion.

Add validation near domain-pack loading/schema validation, not lazily in the chat path. The validation should enforce:

- A display policy must exist for every stageable object type that can appear as a retained/default manifest object. Evidence-only/support objects do not need default manifest rows unless the pack intentionally surfaces them as retained curation objects.
- The policy source is either `metadata.workspace_display` or an explicit `metadata.supervisor_manifest`. If both exist, define deterministic precedence. Recommended: `supervisor_manifest` overrides; when it is absent, `workspace_display` is the declared policy source for the first implementation pass. This is not runtime guessing.
- Allowed YAML-owned keys are field-designation keys only, such as `primary_label_field`, `primary_label_fields`, `secondary_label_field`, and `summary_fields`. Reject unknown keys in `supervisor_manifest`.
- Each field path must be a string, unique within that object policy, syntactically valid, and resolvable against declared object fields or an explicitly allowed computed path.
- Reject evidence text and evidence containers from default manifest fields. Disallow field paths or field definitions that point at `evidence`, `evidence_records`, `evidence_quote`, `verified_quote`, `quote`, `source_quote`, `chunk`, `chunk_id`, or equivalent free-text evidence fields.
- Reject list/object-valued fields unless the renderer has a documented scalar projection for that exact field. The default should be scalar strings/numbers/booleans only.
- Reject a policy that provides no usable label field and no usable summary field.
- Fail domain-pack validation/tests when required display policy is missing. Do not log-and-guess in production.

When adding this validation, include all current Alliance domain packs under `packages/alliance/domain_packs/*/domain_pack.yaml`. Existing `workspace_display` blocks exist for several allele, disease, gene, gene expression, generic, and phenotype objects; inspect each pack and add explicit `supervisor_manifest` only where `workspace_display` is too broad or too UI-specific.

## Part 2: Immediate Persistence After Validation

Today, builder-backed specialists emit an `INTERNAL_EXTRACTION_RESULT` event. The chat stream collects that event and persists extraction candidates only after the entire supervisor turn reaches `RUN_FINISHED`.

Change the contract:

1. Builder/materializer specialist finalizes.
2. Inline validators run and append findings to the envelope.
3. The runtime persists the canonical extraction result immediately and idempotently.
4. The runtime returns a stable extraction result ref in the supervisor handoff.
5. The outer chat turn may later link the assistant message/turn id, but cancellation must not lose the extraction.

This implies the persistence path needs to move earlier than `chat_stream.py`'s final `RUN_FINISHED` block, or needs a new immediate persistence helper callable from the specialist runtime.

### Idempotency

Immediate persistence must be safe under retries and duplicate stream events.

Recommended key material:

- `trace_id`
- `builder_run_id`
- `builder_invocation_id`
- `tool_name`
- `agent_key`
- `adapter_key`
- canonical payload hash

If a matching row already exists, return the existing extraction result id instead of creating another row.

### Turn Linkage

The persisted row can initially have:

- `origin_session_id`
- `trace_id`
- `document_id`
- `user_id`
- `source_kind=CHAT`
- `metadata.turn_id` if available
- `metadata.persistence_phase="inline_validated_extraction"`

When the assistant turn finishes, the chat stream can link/update metadata with final assistant message identifiers if needed. If the turn is cancelled, the result still exists with trace/session provenance and can be inspected/exported.

### Error Policy

- Fatal builder finalization failure: do not persist as a successful extraction.
- Non-fatal validator tool failure: persist the extraction with `validator_error` findings.
- User cancellation after successful validated extraction persistence: keep the persisted extraction.
- User cancellation during an unfinished specialist: do not persist incomplete builder state as an extraction result.

### Precise Persistence Hook

The implementation should put the first durable write inside the specialist runtime, not in the final chat-completion block.

Target behavior in `run_specialist_with_events()`:

1. Canonicalize the specialist output into builder finalization.
2. Run materializer/domain-envelope validator dispatch.
3. Attach non-fatal validator findings to the canonical envelope.
4. Verify the final payload is a strict canonical domain envelope.
5. Persist idempotently.
6. Build the `INTERNAL_EXTRACTION_RESULT` event with persisted identifiers.
7. Render the supervisor manifest with the same persisted `result_ref`.

The helper can live near existing curation workspace persistence code, but it should be callable from `streaming_tools.py` without pulling chat-stream completion logic into the specialist runtime.

Suggested helper responsibilities:

- Accept canonical envelope, document id, adapter key, agent key, tool name, trace/session/user ids, builder finalization summary, and source kind.
- Build the persistence request/record from canonical data only.
- Compute idempotency key and payload hash.
- Insert-or-return-existing in one transaction.
- Return a record/result object that includes `extraction_result_id`, `result_ref`, `created_new`, and enough metadata for the handoff.
- Never parse supervisor-visible text or `TOOL_COMPLETE` content.

### Duplicate Prevention And Migration Details

Immediate persistence must prevent duplicates at the database boundary. In-process "already seen" checks are not enough.

Add a forward migration for `extraction_results`:

- Add `idempotency_key` as a nullable string/text column initially, or non-null for new rows if the migration backfills existing records safely.
- Add `payload_hash` as a string/text column, or store it in metadata only if the unique key includes a separate stable idempotency column.
- Add a unique index on `idempotency_key` for non-null values. If the database supports partial indexes, use a partial unique index so old rows without keys do not block migration.
- Update the ORM model and API schemas enough that persisted result records can expose/debug the key when appropriate. The supervisor tool does not need to show it.

Recommended idempotency key material:

```text
source_kind
origin_session_id
trace_id
tool_name
agent_key
adapter_key
builder_run_id
builder_invocation_id
canonical_payload_hash
```

Rules:

- Hash canonical JSON with stable key ordering and no prose fields.
- If `builder_invocation_id` is present, include it. It already exists in builder finalization summary.
- If a unique insert conflict occurs, reload and return the existing row.
- Do not dedupe by `candidate_count`, `conversation_summary`, row recency, or object count.
- Do not add a final-turn fallback insert for rows that failed inline persistence. If inline persistence fails after validation, report a fatal backend/tool failure and do not claim the extraction is ready.
- Do not use legacy `_is_extraction_envelope_payload()` acceptance of `items`, `raw_mentions`, or prose-derived artifacts for the new inline builder-backed path. If old helpers must remain for unrelated product surfaces, split or rename the strict canonical-envelope helper so the chat path cannot accidentally call the broad legacy detector.

### Completion Path Removal Steps

After inline persistence lands, update completed-turn handling so it cannot duplicate extraction rows:

- In `chat_stream.py`, when an `INTERNAL_EXTRACTION_RESULT` event appears, read the persisted `extraction_result_id`/`result_ref` from event details instead of building a new candidate for later first insert.
- In `chat_common.py`, make `_persist_completed_chat_stream_turn()` link/update metadata for already persisted extraction rows, such as final assistant message id or final turn id, if that linkage is still useful.
- Retire `_persist_extraction_candidates()` for successful builder-backed chat extraction, or narrow it to non-builder legacy paths that are not model-facing. The preferred endpoint state is no first durable extraction write in the `RUN_FINISHED` block.
- Keep non-streaming and streaming behavior aligned. The non-streaming path should not keep the old final-only persistence while the streaming path uses inline persistence.
- Add tests that cancellation after inline persistence leaves one row, and successful completion after inline persistence still leaves one row.

## Part 3: Replace Scope-First Lookup With One Results Tool

The current scopes are accurate implementation-wise but confusing:

- `current_turn`: transient in-memory results from the still-running assistant response;
- `current_chat`: persisted completed extraction results from the chat;
- `current_document`: persisted extraction results for the loaded document;
- `flow_run`: persisted results from a flow run;
- `extraction_result`: a specific result.

The supervisor should not have to choose between `current_turn` and `current_chat` for ordinary follow-up on a result it just got.

The design goal is not to hide confusing scopes behind aliases. The design goal is to stop exposing storage timing as the model-facing interface. `inspect_curation_context` has grown into one broad tool with many optional arguments and storage concepts. For extraction-result discussion, replace it with one clear read-only tool that is presented as the supervisor's primary way to interact with results.

### New Model-Facing Tool

Add a new tool, tentatively named `inspect_results`.

This should be the tool for read-only interaction with curation extraction results for curator-facing chat answers. The supervisor should not need to know whether a result came from the current in-flight turn, a completed chat turn, or a flow run. It should use either the `result_ref` it just received or `target="latest"`.

Suggested arguments:

- `action`: one of `help`, `list`, `summary`, `objects`, `object`, `evidence`, `validation`, or `field`;
- `result_ref`: explicit extraction result ref from a manifest or previous tool result;
- `target`: `latest`, `this_chat`, `current_document`, `flow_run`, or `all_authorized`, defaulting to `latest` when no `result_ref` is provided;
- `object_ref`: object id or pending ref for `object` and object-scoped `evidence`;
- `field_path`: canonical field path for `field`;
- `adapter_keys`: optional filter for list/summary actions;
- `flow_run_id`: optional flow-run filter;
- `cursor` and `limit`: pagination.

`action="help"` should return a short capability guide and example calls. The manifest handoff should tell the supervisor this tool exists and include the exact result ref to use:

```text
To inspect these results, use inspect_results(result_ref="extraction-result:...", action="objects").
For evidence on one object, use inspect_results(result_ref="...", action="evidence", object_ref="...").
For available result-inspection options, use inspect_results(action="help").
```

The tool response should also teach the next step when useful:

- after `list`, show result refs and suggest `summary` or `objects`;
- after `summary`, show counts and suggest `objects`, `validation`, or `evidence`;
- after `objects`, show object refs and suggest object-scoped `evidence` or `field`;
- after `evidence`, return bounded evidence records and pagination only.

### `inspect_results` Action Semantics

Implement `inspect_results` as a new schema and wrapper. Do not make it a thin alias around `inspect_curation_context(scope=...)`.

All responses should be structured JSON-compatible objects wrapped through the existing tool-response conventions, with these common fields where applicable:

- `status`: `ok`, `not_found`, `invalid_request`, or `error`;
- `action`: the action that ran;
- `result_ref` and `extraction_result_id` when a single result is in scope;
- `next_actions`: short machine-readable or text hints for the supervisor;
- `cursor`, `next_cursor`, `limit`, and `truncated` for paginated results;
- `error_code` and `message` for invalid requests.

Action requirements:

- `help`
  - No result is required.
  - Return allowed actions, required/optional arguments, two or three examples, and boundary reminders: read-only, use result refs, export/prep are separate tools.
  - Include examples for latest result, explicit result ref, object evidence, and list by flow run.
- `list`
  - Return authorized extraction result records only.
  - Sort newest first by `created_at` then stable id.
  - Include `result_ref`, domain/adapter/agent keys, document id or title if authorized, object count, created time, and validation warning/error counts.
  - Respect `target`, `adapter_keys`, `flow_run_id`, `cursor`, and `limit`.
  - Do not include object payloads or evidence text.
- `summary`
  - Resolve `result_ref` if provided; otherwise resolve `target`, defaulting to `latest`.
  - Return counts, domain/adapter/agent identity, result status, validation summary, exclusion/ambiguity counts if available, and first-page manifest metadata.
  - It may include the first manifest page only if bounded by the same renderer/page-size rules.
- `objects`
  - Resolve one result.
  - Return a manifest page using the shared renderer and supplied `cursor`/`limit`.
  - Default `limit` should be the env-configured manifest page size, not the old generic `_MAX_LIST_LIMIT=20`.
  - Include object refs and `next_cursor`.
- `object`
  - Require `object_ref`.
  - Return one bounded object view: display fields, scalar payload fields permitted by YAML policy, validation findings scoped to that object, and evidence count.
  - Do not return the raw envelope or unrestricted payload dump.
- `field`
  - Require `field_path`.
  - Require `object_ref` for object-scoped payload paths unless the field path is explicitly result-level.
  - Return one bounded value, with truncation metadata if long.
  - Reject evidence text paths in this action; direct the supervisor to `evidence`.
- `evidence`
  - Resolve one result.
  - Prefer requiring `object_ref` for evidence text. If object_ref is omitted, return only a bounded evidence inventory/count by object, not all quotes.
  - Return quote/snippet text only through this action, bounded by env-configured count/length limits.
  - Include source/page/section/chunk identifiers when available.
  - Page with `cursor`/`next_cursor`.
- `validation`
  - Resolve one result.
  - Return result-level and optionally object/field-scoped findings.
  - Preserve non-fatal validator dispatch findings as warnings.

Result-ref rules:

- Accept `extraction-result:<uuid>` as the canonical model-facing ref.
- Reject raw UUIDs in the model-facing tool contract. Internal helpers/tests may use UUIDs below the tool boundary, but `inspect_results` should require the prefixed `extraction-result:<uuid>` form so the supervisor has one clear result-ref shape.
- Reject `current-turn:N`, `current_chat`, `current_document`, and other old scope strings as `invalid_request` with a help-oriented message.
- `target="latest"` means the newest authorized extraction result for the current chat/session context, sorted by durable row timestamp. It should not consult the transient current-turn registry.
- `target="flow_run"` requires `flow_run_id` unless a `result_ref` is provided.

Operational limits:

- Surface the default object page size through `backend/src/lib/openai_agents/config.py` and `.env.example` under Operational limits, default `100`.
- Surface evidence lookup count/character limits if they are added or changed. Do not introduce hidden numeric caps.

### Boundaries

`inspect_results` should be read-only and extraction-result focused. Keep these separate:

- `export_to_file`: still creates/downloads files and should only run when the user asks for export/download.
- `prepare_for_curation`: still requires the explicit curator confirmation checkpoint.
- `inspect_chat_traces`: stays as the separate debugging tool for why a previous answer behaved a certain way.
- Review-session and output-file inspection should move to explicitly named tools if the chat supervisor still needs them. Do not expose them through `inspect_curation_context`.

Remove `inspect_curation_context` from the chat supervisor's model-facing tool list when `inspect_results` lands. If review-session or output-file inspection still needs tool support, expose it through explicitly named tools that do not reuse the old scope-first extraction-result interface.

The new tool should satisfy this product contract:

- The manifest includes a result ref.
- The supervisor can inspect that result ref without choosing `current_turn` or `current_chat`.
- The supervisor can call `inspect_results(action="help")` when unsure.
- Earlier saved results remain inspectable.
- Flow-run extraction results remain inspectable through the same result tool when given a `flow_run_id` or result ref.
- The tool returns paginated slices, not raw envelopes.

After immediate persistence lands, `current_turn` should not be a chat-supervisor concept. Same-turn result access should go through the stable result ref or `target="latest"`.

### Removal And Migration Steps For Old Tool Exposure

Remove the old chat-supervisor extraction lookup surface in the same implementation slice that adds `inspect_results`.

Required edits:

- In `backend/src/lib/openai_agents/agents/supervisor_agent.py`:
  - Replace `_INSPECT_CURATION_CONTEXT_TOOL_NAME` with `_INSPECT_RESULTS_TOOL_NAME`.
  - Update `_SUPERVISOR_BUILTIN_TOOL_NAMES`.
  - Remove the `inspect_curation_context_tool()` wrapper from the supervisor tool list.
  - Add an `inspect_results_tool()` wrapper with the new argument schema.
  - Replace runtime note text that says "Use inspect_curation_context..." with `inspect_results` guidance.
- In `backend/src/lib/openai_agents/supervisor_context_tools.py`:
  - Either implement `inspect_results` in this file or move shared result-inspection code into a clearer helper module.
  - Delete or stop exporting `_current_turn_records()` as a model-facing path.
  - Stop using `_authorized_extraction_results()`'s old storage-timing scopes for chat supervisor calls. If some non-chat API still needs old behavior, keep it behind an explicitly non-supervisor function name.
  - Replace `_compact_object()` / `_selected_scalar_fields()` guessing with the shared manifest renderer for extraction result objects.
  - Make evidence lookup object/ref bounded and explicit.
- In `backend/src/lib/openai_agents/curation_context_registry.py`:
  - Remove supervisor-facing dependencies on `list_current_turn_curation_context()`.
  - Keep the registry only if another backend diagnostic path still needs it, and document that it is not a result source for the model-facing tool.
- In tests:
  - Replace assertions that the supervisor tool list includes `inspect_curation_context` with assertions that it includes `inspect_results`.
  - Add assertions that old scope names and fake current-turn ids are not present in tool descriptions/examples.
- In prompts:
  - Search for `inspect_curation_context`, `current_turn`, `current_chat`, and `scope=` in model-facing text. Replace extraction-result browsing examples with `inspect_results`.
  - Include `backend/src/api/chat_execute_flow.py` in this search; it currently has flow-output guidance tied to the old tool.

### Tool Description Updates

The supervisor-facing description should be explicit:

```text
inspect_results is the read-only tool for looking at curation extraction results
you already have. Use the result_ref from the latest manifest, or target="latest"
for the newest extraction in this chat. Use action="help" to see available result
inspection actions. Use this tool to list objects, inspect one object, fetch
bounded evidence, inspect validation findings, or read a specific field. Do not
use storage-timing concepts such as current_turn/current_chat in ordinary chat.
```

The supervisor prompt should say:

- If the latest specialist manifest answers the curator's request, answer directly.
- Treat a non-empty domain extractor result as the normal stopping point for the current request. Summarize the retained objects, unresolved warnings, and exclusions if available.
- Prefer the manifest and `inspect_results` when the next step is to summarize, list, inspect, or discuss data that has already been extracted.
- Use `inspect_results` for bounded details from the result ref.
- Use export tools only when explicitly requested.
- Ask the curation-prep confirmation question before preparing for curation.
- If an installed domain specialist returned a valid extraction with warnings, keep the extraction and warnings available as stable context for whatever the supervisor chooses to do next.
- If an extractor returns zero retained objects, say that plainly and choose one of the normal recovery paths: ask the curator whether to try a broader search, ask for clarifying scope, or make one better-scoped retry when the missing scope is obvious from the tool output.

### Chat Supervisor Prompt Direction

The chat supervisor prompt should be tightened around this division of labor:

- The supervisor dispatches the initial specialist call and preserves curator intent.
- The specialist performs extraction, evidence capture, and validation.
- The supervisor explains the specialist result to the curator.
- The curator decides whether to broaden, narrow, rerun, export, or prepare for curation.

Recommended prompt language:

```text
When a document extractor returns a non-empty result, treat that as a completed
extraction for the current curator request unless the specialist explicitly says
the requested scope was not handled. Answer from the returned manifest and
validation findings. Do not call another extractor just to double-check,
summarize, or gain confidence.

If the extractor returns zero retained objects, report that directly. Then either
ask the curator whether to try again with broader instructions, ask a clarifying
question, or make one better-scoped retry when the missing search scope is clear.
Do not silently restart extraction loops.
```

The runtime availability note in `supervisor_agent.py` should echo the same rule because it is injected close to the live tool descriptions and can override stale static examples:

```text
EXTRACTION RESULT COMPLETION: When a specialist returns a non-empty extraction
manifest, answer from that manifest unless the user asks to rerun or the manifest
explicitly says the requested scope was not handled. Use inspect_results for
details; do not call extractors again only to summarize existing results.
```

### Chat-With-Claude Prompt And Tool Pass

Because this changes how extraction results move through chat, the implementation should include a focused pass over the model-facing Chat-with-Claude surface:

- `config/agents/supervisor/prompt.yaml`: explain `inspect_results`, answer-from-manifest behavior, empty-result recovery, export boundaries, and curation-prep confirmation.
- `backend/src/lib/openai_agents/agents/supervisor_agent.py`: update runtime availability notes and tool descriptions so live instructions match the static prompt.
- Specialist handoff/reduction text in `backend/src/lib/openai_agents/streaming_tools.py`: include result refs, recommended result-inspection calls, and non-empty/empty completion posture.
- `export_to_file` tool description: make clear that export consumes canonical extraction result refs and is only for explicit export/download requests.
- `prepare_for_curation` tool description: make clear it consumes canonical extraction result refs and still requires curator confirmation.
- `inspect_chat_traces` description: keep it for "why did this happen?" debugging, not normal result browsing.
- Any flow/chat-output prompts that summarize extraction results should refer to canonical result refs and manifests rather than prose/tool artifacts.

The wording should be consistent across these surfaces: extracted objects live in canonical extraction results, `inspect_results` is the read-only result-browsing tool, exports and curation prep use result refs, and evidence is retrieved through bounded lookup rather than the default manifest.

### Extractor Tool Response Direction

The specialist handoff text should also steer the supervisor. The manifest should not only list objects; it should state the completion posture:

- `Result status: non_empty_extraction_ready`
- `Recommended supervisor action: answer_from_manifest`
- `Retry guidance: do not rerun this extractor unless the curator asks to broaden/narrow the search or the manifest says requested scope was not handled`
- `If more detail is needed: inspect this result_ref`

For empty results, the manifest should carry different guidance:

- `Result status: empty_extraction`
- `Recommended supervisor action: report_empty_result`
- `Retry guidance: retry only with a narrower/broader explicit scope, or ask the curator for clarification`

This keeps the supervisor in charge without making it guess whether a specialist result is "finished enough."

## Part 4: Recovery Rules

The supervisor call ledger currently deduplicates exact `(tool_name, normalized_query)` calls. That does not address semantically equivalent retries whose only purpose is to summarize data that already exists. With immediate persistence, richer manifests, and clearer inspection tools, this should be handled as a graceful supervisor behavior rather than a hard block.

Add higher-level extraction-result guidance:

- Once a domain extractor returns a successful extraction result ref for the user request, the handoff should make the answer-from-manifest path obvious.
- If the supervisor needs to summarize, list, reformat, or inspect prior extraction, it should have a direct result-ref inspection path instead of needing a new builder workspace.
- If the latest extraction was non-empty and the new request does not add new curator instructions, the supervisor should usually answer from the manifest or inspect the result.
- If the latest extraction was empty, the supervisor should report that, ask for clarification, or make one clearly different retry when appropriate, rather than silently looping.
- Do not add a special runtime block against General PDF Extraction. Once the supervisor has a complete manifest and stable result ref, normal routing discretion should remain intact.
- If a domain extractor truly fails before finalization, the supervisor may report failure, ask the user whether to retry, or choose another useful tool according to its normal routing judgment.

Implementation note: do not implement this as a ledger-level prohibition against Generic PDF or any other specialist. The behavior change should come from visibility and prompt/tool affordances: non-empty domain results get a useful manifest/ref, empty results get explicit recovery guidance, and result inspection is cheap. Generic PDF remains available when the curator asks for a broader/general extraction or when the supervisor has a materially new reason to call it.

The goal is not to forbid the supervisor from calling another specialist. The goal is to make the existing result sufficiently visible, durable, and inspectable that retrying an extractor purely for summary or confidence is rarely the attractive path.

## Part 5: Frontend/Audit Severity Cleanup

The backend can emit `SPECIALIST_ERROR` with `fatal: false` and `severity: warning` for non-fatal validator dispatch errors. The frontend currently treats any event type containing `ERROR` as error severity.

Fix display semantics:

- If event details contain `fatal: false` or `severity: warning`, render as warning.
- Use wording like "Validator warning" rather than "Specialist failed" for non-fatal validator dispatch findings.
- Reserve hard error styling for fatal specialist failure, missing builder finalization, supervisor errors, or failed tool completions.

This does not change backend validation semantics; it makes the UI match them.

Concrete frontend touchpoints:

- In `frontend/src/utils/auditHelpers.ts`, adjust `getEventSeverity()` so explicit `details.fatal === false` and `details.severity === "warning"` are checked before the broad `type.includes("ERROR")` rule.
- In the `SPECIALIST_ERROR` label/message branch, use warning language when details mark the event non-fatal. For example, "Validator warning" or "`<specialist>` warning" is more accurate than "`<specialist>` failed".
- Preserve fatal styling for missing finalization, fatal specialist output errors, supervisor errors, and failed tool completions.
- Update `frontend/src/test/utils/auditHelpers.test.ts` expectations that currently treat every `SPECIALIST_ERROR` as an error, and add a regression for a validator dispatch warning event.

## Part 6: Tests

### Unit Tests

- Manifest generation lists all objects up to page size, not just first five.
- Manifest includes object type, ref, label, validation status, and resolved CURIE/symbol when present.
- Manifest pagination emits `next_cursor` and never includes raw full envelope JSON.
- Domain YAML display policies determine supervisor-visible payload fields for gene, allele, disease, phenotype, and generic objects.
- Domain YAML validation fails for missing display policy on retained/stageable object types.
- Domain YAML validation fails for unknown field paths, duplicate display field paths, non-string fields, object/list fields without an explicit scalar projection, unknown `supervisor_manifest` keys, and evidence/quote/chunk fields in the default manifest policy.
- Manifest renderer ignores any attempt to include evidence quote text through default display fields.
- Manifest renderer uses `workspace_display` or `supervisor_manifest` and does not call hardcoded field-priority or sorted-scalar fallback logic.
- `inspect_results(result_ref=...)` and `inspect_results(target="latest")` resolve the extraction result just produced without requiring `scope=current_turn`.
- `inspect_results(action="help")` returns concise available actions and example calls.
- `inspect_results(action="list")` returns paginated result refs without object payloads or evidence text.
- `inspect_results(action="objects")` returns page 1 and page 2 of the same result with stable object refs.
- `inspect_results(action="object")`, `field`, `evidence`, and `validation` enforce required arguments and return help-oriented `invalid_request` responses when missing.
- `inspect_results` rejects `current-turn:N`, `current_chat`, `current_turn`, and old `scope=` concepts.
- The old scope-first extraction-result lookup is removed from the chat supervisor tool surface.
- Non-empty extraction manifest includes completion guidance that recommends answering from the manifest.
- Empty extraction manifest includes recovery guidance that recommends reporting emptiness, asking for clarification, or one better-scoped retry.
- Chat-with-Claude prompt/tool descriptions consistently name `inspect_results` as the read-only result-browsing tool and preserve boundaries for export, curation prep, and trace debugging.
- `backend/src/api/chat_execute_flow.py` and other flow-output prompt text no longer direct the supervisor to `inspect_curation_context`.
- Immediate persistence creates one row before final chat completion and returns the same row on duplicate internal events.
- Unique-conflict/idempotency tests reload and return the existing extraction result instead of raising or creating a second row.
- The strict new inline persistence path rejects legacy envelope-like `items/raw_mentions` payloads.
- A non-fatal validator dispatch error produces a persisted extraction with validator findings.
- Missing builder finalization remains fatal and does not persist a successful extraction.
- Supervisor prompt/tool guidance makes the existing manifest/result-ref path the natural route for summarizing prior extraction.
- No guidance or runtime behavior blocks generic PDF use when the supervisor independently chooses it.
- Frontend severity helper renders `SPECIALIST_ERROR` with `fatal: false` or `severity: warning` as warning.

### Integration Tests

- Chat extraction persists immediately after inline validation, before `RUN_FINISHED`.
- Streaming and non-streaming chat paths both use inline persistence and do not keep divergent persistence behavior.
- Cancelling after a validated extraction but before final answer leaves an `extraction_results` row that can be inspected/exported.
- Completing the turn after inline persistence does not create a duplicate extraction row.
- Follow-up question after a successful extraction can inspect/export "those objects" using the persisted ref.
- Follow-up during the same turn, if possible in harness, resolves the produced extraction through `inspect_results(result_ref=...)` or `inspect_results(target="latest")` without requiring `scope=current_turn`.
- Allele trace-shaped fixture: 16 allele observations are returned as a manifest; a later empty summarization attempt cannot erase or replace that extraction.
- Chat supervisor trace-shaped fixture: after a non-empty allele extraction, the next supervisor action is a curator-facing answer or bounded inspect/export action, not a second extractor call whose purpose is summary/confidence.
- Empty extraction trace-shaped fixture: the supervisor reports no retained objects and either asks a clarification question or makes at most one materially different retry.
- Gillian TSV regression: PDF/generic extraction persists immediately and export reads canonical rows, not artifact summaries.

### Production Smoke

- Run a paper that produces 10-20 allele/gene objects and verify:
  - supervisor answer lists the object names;
  - extraction row is persisted before final answer completion or survives cancellation after validation;
  - follow-up "show/export those" uses the same extraction result id;
  - audit panel shows non-fatal validator problems as warnings.

## 5.5 High Review Gate Protocol

After each implementation part below, run a focused `gpt-5.5` high review before continuing. The review is not a broad architecture brainstorm; it is a stop/go safety gate for this plan.

Use a prompt shaped like:

```text
Review this implementation slice for the supervisor extraction handoff plan.
Focus on forward-only removal and safety. Confirm:
1. No compatibility shim or alias keeps old scope-first supervisor result lookup alive.
2. No fallback row source accepts prose, artifacts, raw_mentions, generic items, or old current-turn fake ids.
3. No runtime guessing chooses supervisor-visible payload fields when domain-pack YAML is missing.
4. Evidence text is absent from the default manifest and only available through bounded inspect_results evidence lookup.
5. New operational limits are env-configured and documented.
6. Tests cover the new failure modes and duplicate-prevention behavior.
List blocking findings first with file/function references.
```

Gate rules:

- Treat a finding that reintroduces old `inspect_curation_context` model-facing behavior as blocking.
- Treat hidden hardcoded page/evidence/time/count limits as blocking unless they are pure internal plumbing waits.
- Treat any fallback from failed inline persistence to final-turn insertion as blocking.
- Treat Generic PDF hard-blocking as blocking; the desired fix is visibility/context, not a specialist ban.
- Do not proceed to the next slice until blocking findings are fixed or explicitly recorded as out of scope with rationale.

## Implementation Slices

Each slice is forward-only. Do not add compatibility aliases, model-facing shims, silent fallbacks, or legacy row-source recovery paths. After each slice, run the `5.5 high` review gate above before continuing. Address actionable findings before moving to the next slice, or explicitly record why a finding is out of scope.

1. **Manifest renderer**
   - Add canonical envelope -> `ExtractionManifestPage`.
   - Use env-configured page size defaulting to 100.
   - Replace first-five supervisor summary with manifest handoff.
   - Read supervisor-visible payload fields from domain-pack YAML, starting with existing `workspace_display` metadata.
   - Include non-empty versus empty completion/retry guidance in the tool response.
   - Remove runtime guessing for stageable domain objects without display metadata; enforce via domain-pack validation/tests.
   - **Review gate:** `gpt-5.5` high review for manifest shape, YAML field policy, no evidence text in default manifest, no display-field fallbacks, and page-size env documentation.

2. **Immediate persistence**
   - Add idempotent persist helper for validated builder/domain-envelope outputs.
   - Call it after inline validation succeeds or records non-fatal validator findings.
   - Return extraction result ref in the manifest.
   - Remove reliance on final `RUN_FINISHED` persistence as the first durable write for successful extraction results.
   - Add database-level idempotency, payload hashing, conflict reload, and strict canonical-domain-envelope acceptance.
   - **Review gate:** `gpt-5.5` high review for idempotency, cancellation behavior, transaction boundaries, duplicate prevention, and no legacy artifact/prose persistence paths.

3. **`inspect_results` tool**
   - Add one read-only model-facing result inspection tool with clear `action` values and `action="help"`.
   - Make result refs/latest extraction the normal path.
   - Remove the scope-first extraction-result lookup from the chat supervisor tool surface.
   - Update tool descriptions, runtime availability notes, and supervisor prompt with the answer-from-manifest default.
   - Do not alias old `inspect_curation_context` parameters into `inspect_results`.
   - Implement help/list/summary/objects/object/field/evidence/validation actions with explicit invalid-request behavior and pagination.
   - **Review gate:** `gpt-5.5` high review for API clarity, removal of `current_turn/current_chat` model-facing concepts, action/help ergonomics, and no compatibility shim.

4. **Chat-with-Claude prompt/tool pass**
   - Update supervisor prompt, runtime availability notes, specialist handoff text, export tool description, curation-prep tool description, and trace-debug tool description together.
   - Make the language consistent: canonical extraction results are the data source, `inspect_results` is read-only browsing, export/prep are explicit actions, and trace tools are for debugging behavior.
   - Add focused prompt/tool-description regression tests or snapshot assertions where existing harnesses support them.
   - Remove or rewrite stale prompt examples that refer to scope-first lookup, prose artifacts, or extractor reruns for summarization.
   - **Review gate:** `gpt-5.5` high review for Chat-with-Claude prompt/tool consistency, curator-facing behavior, and absence of stale instructions.

5. **Supervisor follow-up guidance**
   - Track successful domain extraction result refs per supervisor turn.
   - Make manifest/result-ref inspection the easy path for summarize/inspect follow-ups.
   - Treat non-empty extractor results as usually sufficient for same-turn summary/confidence requests.
   - Treat empty extractor results as a report-or-clarify/retry decision, not a silent extraction loop.
   - Preserve normal supervisor freedom to call General PDF Extraction when it judges that useful.
   - **Review gate:** `gpt-5.5` high review for supervisor autonomy, no hard blocking of useful specialist calls, and no old retry/lookup behavior sneaking back in.

6. **Audit/UI severity**
   - Respect `details.fatal` and `details.severity`.
   - Add frontend tests.
   - **Review gate:** `gpt-5.5` high review for audit semantics, non-fatal validator warnings, and no misleading specialist-failed UX.

7. **End-to-end regression**
   - Recreate the production allele trace pattern and Gillian TSV workflow on dev.
   - Verify persistence, answer quality, export, and cancellation behavior.
   - **Review gate:** `gpt-5.5` high review for end-to-end readiness: production-trace coverage, Gillian TSV canonical rows, same-turn result browsing, cancellation durability, and no compatibility shims.

## Does This Cover The Discussed Issues?

Yes, this covers the core issues:

- The supervisor sees enough object names to answer directly.
- The supervisor is explicitly steered to treat non-empty extraction as enough to answer, while empty extraction gets a clear report/clarify/retry path.
- Full canonical payloads stay out of model context.
- The data is durable as soon as validated extraction exists.
- The normal lookup path no longer makes the supervisor reason about `current_turn` versus `current_chat`.
- Follow-up questions can refer to stable extraction result ids.
- Retrying an extractor purely as a summarizer becomes unnecessary in the normal path.
- Generic PDF remains available; the successful extraction is simply durable and visible enough that ordinary summarization does not depend on it.
- Non-fatal validator warnings stop looking like hard specialist failures.

One adjacent concern remains separate: validator performance/max-turn behavior for tricky allele cases such as `mod-5(vlc47)`. That should be optimized too, but it should not block preserving and answering from the successful extraction.
