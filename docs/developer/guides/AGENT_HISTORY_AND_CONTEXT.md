# Exact history and bounded model input

This policy separates stored information from the information repeated to a model.
It does not generate natural-language summaries, rewrite scientific decisions,
infer that a disagreement is unresolved, or remove canonical conversation items.
It applies at runtime to packaged agents, saved custom agents, flow invocations,
batch invocations, validators and authoring assistants.

## The five changes

1. **Document results contain text once.** The package still returns its complete
   structured result, including span text and PDF geometry. The shared backend
   catalog records that original before creating a distinct model response.
   `content` appears once; evidence spans retain their original IDs and absolute
   Unicode character offsets. `content_start` makes offsets usable within a paged
   chunk. Every removed span text is checked against the exact returned slice.
   Page/section/chunk identities and continuation fields remain. Geometry travels
   in `CHUNK_PROVENANCE` events, including for `read_chunk` and `read_subsection`.
   Evidence recording still resolves canonical spans. Source paging budgets are
   conservative and unchanged; this change does not claim that an unread page
   has been read, nor enlarge document reads by discarding source information.

2. **Builder lists are versioned indexes.** All seven builders use the same
   listing and exact-read implementation. An index contains candidate ID,
   revision, status, optional label/type, counts, and `detail_read` arguments.
   Existing generic attribute warnings remain visible (an oversized index row
   supplies an exact detail reference). `find_staged_*` accepts a path such as
   `candidate.staged_fields.attributes`, returning contiguous exact JSON/text
   chunks. Read all chunks to `complete=true`, passing the returned hash.
   `workspace_revision` must be retained across listing pages; a changed
   workspace returns `stale_result_cursor`. A new query can omit the revision.
   Detail hashes also detect changes. Discarded candidates require an explicit
   `include_discarded=true`. Candidate revisions advance on mutations, validation,
   discard and finalization. Old versions remain in the history journal.

3. **A durable journal retains observed inputs and results.** PostgreSQL stores
   immutable event identities and content-addressed canonical JSON payloads.
   Duplicate payloads share a body, but repeated calls keep separate events.
   Events include owner, session/run, invocation, parent invocation, lineage,
   sequence, kind, tool/call identity and transport outcome. `returned` describes
   a tool returning, not whether a biological finding was valid. Original package
   and Studio results are captured before their model projections. Builder
   changed candidate revisions are captured before diagnostic trace reduction. Each
   workspace event links to its predecessor and includes exact changed candidates,
   workspace metadata and the original finalization payload when produced. Earlier
   candidates are not copied again for every unrelated mutation; replaying the
   deltas reconstructs each workspace version. Model input items,
   manifests and final/error outputs are retained too. No source that was never
   read is claimed to be present. Diagnostic JSONL remains a separate redacted,
   bounded observation channel and is not the canonical store.

   A derived search column contains decoded JSON scalar text, including nested
   serialized tool arguments/results, so quotes, line breaks and backslashes
   match literally. It does not replace the canonical JSON or its hash.
   `search_run_history` performs a literal search over this text and
   returns paged identities. `read_run_history` reads complete nested values in
   exact chunks. It never repeats the original tool or mutation. A root chat/flow
   supervisor or authoring assistant can read its own session history; a nested
   agent can read its invocation subtree. Owner/session/run identity comes from
   the server and is checked again when a tool instance is used. User-supplied
   owner IDs or arbitrary payload hashes cannot grant access. Earlier releases
   are not backfilled; storage failures and interrupted runs can leave gaps, so
   an empty search is not proof that an action did not happen.

4. **The SDK receives a separate context view.** The pinned SDK's
   `call_model_input_filter` archives canonical input before creating a copy.
   Only completed older tool-output bodies can be replaced by exact event refs.
   Original requests, instructions, tool-call arguments, opaque reasoning,
   pending calls and recent results are retained. Error/warning records are
   protected; source continuation fields are copied mechanically into refs.
   No model-authored summary or scientific interpretation is introduced.

   The provisional trigger is 60,000 estimated total tokens, target 40,000,
   with four recent tool outputs retained. Instructions, schemas, tools (including
   deferred definitions) and output allowance count toward that estimate.
   An explicit agent/run `max_tokens` supplies the allowance; otherwise the
   provisional allowance is 16,000. These estimates are not provider usage.
   A checkpoint remains stable until another threshold crossing. Two runtime
   read tools are added; specialist domain tools remain scoped as before.

   Failed persistence disables eviction for that invocation. A completed tool is
   not retried because its archive write failed. If the protected/full context
   cannot fit, a typed curator-facing error stops the run. A structured-output
   retry that deliberately has no tools is archived but cannot externalize data:
   it retains the complete input or stops at the same budget. Standard chat's
   between-turn compaction remains separate and retains its canonical chat rows.
   Studio no longer asks the provider to truncate automatically. Explicit
   provider-managed conversation IDs are rejected with this policy enabled,
   because their unseen history cannot be measured or recovered by this layer.
   The ordinary SDK websocket transport remains enabled.

5. **Measurement stays attached to the real request.** Existing request records
   now include `context_view`: policy/checkpoint, before/after component sizes,
   estimated total and output reserve, archived/retained item counts, externalized
   outputs, archive health/write counts/submitted canonical bytes (including synchronous
   builder writes), and retrieval call/output
   overhead. Provider input, cached input, output and reasoning usage remain in
   the existing accounting record. No second cost ledger or fabricated dollar
   saving is introduced. `output_reserve_tokens` remains visible in redacted
   diagnostic traces; credential tokens remain redacted.

## Runtime coverage and persistence

| Entry point | Hook |
| --- | --- |
| Main chat, direct extraction, flow supervisor | Final shared tool-surface compilation and run config |
| Nested specialists, including batch flow steps | Same final compiler and SDK input hook |
| Single/batch/profile validators | Existing compiled surface plus owned runner config |
| Other owned async/sync SDK utility runs | Shared history preparation |
| Studio and benchmark assistant | Authenticated request scope; preserve existing deferred namespaces |
| Tool-less structured retry | Same journal and budget; no unreadable refs are inserted |

The new migration only adds the two history tables and indexes. It does not
rewrite saved prompts, agent revisions, profiles, flows or historical results.
The runtime infrastructure tools are attached after domain authorization. The
saved-agent/flow release inventory must still verify effective tool contracts
and explicitly account for every owner; an additive migration alone is not
release acceptance.

Internal/offline utility runs without an authenticated owner and run ID cannot
create a history authorization scope. They keep their original input and existing
request measurement; `context_view.reason` records why this policy is inactive.
No model is allowed to supply the missing identity.

## Failure and cost checks

Offline regression tests exercise Unicode spans, exact nested field reads,
revision changes between pages, discarded objects, repeated events, multiple
validation results, an early negated finding, restart recovery, owner/session
isolation, and an archive failure after a completed mutation. A real SDK loop
with a deterministic fake model verifies that the canonical returned history
still contains the original output after the provider-facing copy shrinks.
Growth checks verify that new independent candidates add journal bytes linearly
   and that replaying candidate deltas exactly reconstructs the workspace. Search
   checks cover quotes, newlines, backslashes and nested JSON serialization.
   PostgreSQL tests exercise concurrent payload deduplication and durable recovery.
No paid model call is required for these tests.

Release comparison should use actual provider input/cached/output usage and
cost together with retrieval overhead, cache share, checkpoint frequency,
archive write latency/storage growth, successful completion and scientific
correctness. A smaller request alone does not prove cheaper or better curation.
Measure representative production usage and tune the documented `.env` settings;
keep continued calibration tracked separately from this implementation.
