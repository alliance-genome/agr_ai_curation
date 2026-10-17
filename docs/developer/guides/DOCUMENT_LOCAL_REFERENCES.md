# Document-local references

Model tools identify existing source chunks with `chunk_index` and select evidence
with `selections: [{chunk_index, span_index}]`. Both indexes are strict,
nonnegative integers, zero based in the full document/chunk, never page-relative.
The application binds the user and document. A lookup must return exactly one
chunk in that user's document; duplicate, missing, stale, or invalid references
fail the required document operation.

Weaviate UUIDs and compound canonical span IDs remain storage and provenance
identities. They still power saved evidence, exact offsets/hashes, PDF navigation,
and audit history. See `generate_deterministic_uuid` in `pipeline/store.py` for
the storage-boundary rationale. Numeric references remove the model's need to
copy opaque strings; they do not make an incorrect selection impossible.

## Durable meaning and reprocessing

The canonical `agent_history_events` / `agent_history_payloads` store retains
backend-owned source bindings independently of model context and compaction.
Bindings are scoped by authenticated owner, conversation (or run when no session
exists), and document. Specialist invocations share those source bindings only;
ordinary history access retains its existing lineage restrictions.

The first document read pins the completed SQL processing timestamp. Ingestion
must commit `processing` before replacing Weaviate content and commit a new
completion timestamp afterward. Document tools verify this generation before
and after source I/O. Brief SQL row locks serialize initial generation binding;
no row lock spans asynchronous tool I/O. A failed status write prevents ingestion
from continuing. Reprocessing requires a new conversation; an old selection
cannot silently acquire a new source meaning.

A successful chunk read stores its exact full-chunk hash, canonical UUID, and
issued span definitions. Pages add only newly issued ordinal coverage, preserving
full-chunk ordinals and avoiding repeated copies of the definition. A PostgreSQL
transaction lock serializes concurrent issuance. Evidence selection requires a
previously issued window, checks the current full chunk and canonical span
identity, and preserves input order while deduplicating selections. Existing
workspace evidence updates are staged until the final generation check succeeds.
Changed source or segmentation fails explicitly instead of rebinding an ordinal.
These checks verify identity and exact source, not scientific sufficiency.

## Model and persistence boundaries

Canonical SDK results remain intact for evidence capture and UI provenance.
Before model invocation, a structured projection replaces source identity fields
with local selections. Evidence workspace detail reads and canonical history
recovery use the same projection. Scientific text is never searched/replaced.
Historical evidence whose source is unavailable retains its original quote and
is marked `source_unavailable`; the system does not guess a replacement chunk.
Re-recording historical selections requires reading them in the current scope.

The public schema changes forward only: `read_chunk(chunk_id=...)` and
`record_evidence(span_ids=...)` are invalid requests. Old pending calls must fail
and be restarted with current tools. Original audit payloads remain unchanged;
safely resolvable historical results have a numeric model view. No stored evidence
migration or Weaviate reindex is required.

## Release checks

Before release, audit completed documents for integer, unique, nonnegative stored
chunk indexes and missing chunks. The October 16 production audit covered 950
completed documents and 98,574 chunks with no anomalies. Review any saved custom
agent prompts still instructing UUID/span-ID arguments before using them with the
new tool schema; bundled prompts and Studio guidance use numeric selections.
Do not silently rewrite curator-owned prompts or historical evidence.

Deterministic tests cover the real SDK schema, discovery/read/record provenance,
strict inputs, restart and scope isolation, paging, changed source, cancellation,
model projections, and PostgreSQL concurrent issuance. No paid extraction replay
is needed for those tests. Required read failures follow
[the terminal PDF failure contract](REQUIRED_DOCUMENT_READ_FAILURES.md).
