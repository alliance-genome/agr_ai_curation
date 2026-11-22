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

## Durable meaning and re-ingestion

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
from continuing. A new ingestion of a paper requires a new conversation; an old selection
cannot silently acquire a new source meaning.

A successful chunk read, whether by `read_chunk` or as a passage of `read_section`
or `read_subsection`, stores its exact full-chunk hash, canonical UUID, and
issued span definitions. A read from a span offset adds only newly issued ordinal coverage, preserving
full-chunk ordinals and avoiding repeated copies of the definition. A PostgreSQL
transaction lock serializes concurrent issuance. Evidence selection requires a
previously issued window, checks the current full chunk and canonical span
identity, and preserves input order while deduplicating selections. Existing
workspace evidence updates are staged until the final generation check succeeds.
Changed source or segmentation fails explicitly instead of rebinding an ordinal.
These checks verify identity and exact source, not scientific sufficiency.

## Readable files

A paper holds one or more files (`pdf_document_files`), and each file owns the
piece range `chunk_index_start..chunk_index_end` of the paper's `chunk_index`
numbers. Every document read keeps to the run's readable files
(`src.lib.document_files.readable`, `ReadableFiles`): the paper's `ready` files,
or only its ready main file when the run's `ReadingScope` is `MAIN_FILE_ONLY`.
Batch runs set `MAIN_FILE_ONLY` (`lib/batch/processor.py`); chat and
single-paper flows read every ready file. Files being added (`pending`), failed
files and removed files are never readable.

- Each document tool call resolves the readable files inside the transaction
  that binds its source generation, and exposes them as
  `current_readable_files()`. The call fails if a file it could read stops being
  readable before it ends; a file that becomes ready during the call is harmless.
- Every read function in `weaviate_client/chunks.py` (and the runtime facade
  `agr_ai_curation_runtime.weaviate_chunks`) takes `readable=` and filters on the
  readable ranges: search, index reads, existing-index checks, neighbours
  (clamped to the piece's own file), section reads, the document outline and the
  chunks API. With no readable file they return nothing without a query.
- `read_chunk` on an index in no readable file (negative, past the end, or in a
  gap left by a pending or removed file) returns `invalid_result_cursor` with
  the readable ranges, for example "this document has chunks 0 to 41" for a
  one-file paper and "0 to 41 (paper.pdf), 42 to 60 (Table S1.xlsx)" for
  several. A missing piece inside a readable range is still a terminal
  `DocumentReadError`.
- A piece loaded by its storage id is checked against the ranges before use:
  `record_evidence`'s internal span-ID path treats it as a source failure, and
  history projection marks it `source_unavailable`.
- The curation review's evidence resolution fetch
  (`fetch_document_chunks_for_resolution`) reads the ready files only, whatever
  the run's scope.
- A ready file with no recorded range (the paper-files fill step has not run)
  is a named `FileRangeMissingError`; tools and history projection end with a
  `DocumentReadError`. `fill_existing --verify` must be clean before traffic.
- The document-context cache records the readable files each outline was built
  from; a run with other readable files builds its own.

## Section names are exact, within one file

`read_section(section_name, file=None)` and
`read_subsection(parent_section, subsection, file=None)` read the pieces whose
stored `parentSection` (and `subsection`) equal the requested names exactly
(Q9): "Methods" does not read "Materials and Methods". The search index tokenizes
these names by word, so its `equal` filter only narrows the read; the stored
name is then compared exactly. `file` is a file name as the outline shows it
(`ReadableFiles.labelled`: a second file with the same display name is shown as
"name (2)", skipping any suffix another file really has). It is optional when
the section is in one readable file. A request the model can correct returns
`section: null` with a summary and an `error_code`, and the run continues:

- `section_not_found`: every exact section name (or the section's subsection
  names), file by file, as stored;
- `section_ambiguous`: the files holding the section, asking for `file`;
- `unknown_file`: the readable file names.

The outline in agent prompts (`prompt_utils.format_hierarchy_for_prompt`) lists
each readable file in tab order ("File n of m: name (role, kind, k chunks, chunks
a to b)") with its sections and subsections by exact stored name.
`get_document_sections_hierarchical` returns `files` beside the whole-paper
`sections`. The outline is built at the start of a run: a file that becomes
ready during the run is readable from the next tool call (its pieces appear in
search results, and `read_section` with its name and the file works), but the
outline the model was given does not list it.

While the paper has files being added (its `pending` file rows, counted by
`readable_files_in_session` within the run's scope), the outline adds "Files are
still being added to this paper; they cannot be read yet." The count is taken for
every run and never cached with the outline. A dead add's pending rows count
until the reconciler marks them `failed` (the stale-job rule), then the line stops.

## Selections that have not been read

If `record_evidence` selects a chunk or span window that has not been issued in
the authenticated source scope, it returns `status: needs_source_read`,
`error_code: unissued_document_selection`, `saved: false`, and the rejected local
selection. The agent must explicitly read the relevant passage with `read_chunk`,
`read_section` or `read_subsection` and submit the selections again within its existing turn budget. The application
does not fetch the missing selection, replay the tool, or save partial evidence.
Mixed issued/unissued selections cannot alter an existing evidence record.

A selection the scope has read and issued, whose `chunk_index` is no longer part of a
readable file (its file was removed or is no longer ready since the read), returns `status:
needs_source_read`, `error_code: unreadable_document_selection`, `saved: false`,
the selection, and the readable ranges. Nothing is looked up or saved. Like an
unissued selection, it is unsuccessful in tool telemetry without a Sentry event.
The issued check runs first: a selection never read gets the unissued answer
above whatever its piece number, so one-file papers answer exactly as before.

This is a source-read precondition, not evidence that retrieval failed. The
rejection remains unsuccessful in tool telemetry without generating a new Sentry
operational error. TraceReview preserves the status, selection and read guidance.
Authorization, generation changes, missing stored chunks, hash/span mismatches,
history failures and dependency errors remain terminal `DocumentReadError`s.
The final source-generation check still runs before returning the rejection.

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
