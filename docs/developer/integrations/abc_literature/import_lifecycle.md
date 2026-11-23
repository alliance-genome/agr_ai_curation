# ABC Literature Import Lifecycle

Status: ALL-364 closeout note for ABC Literature-backed document imports.

This note maps provider-neutral document-source states and ABC Literature
conversion responses onto AI Curation's durable PDF job lifecycle. The durable
AI Curation job is the source of truth for curator-facing progress; ABC
Literature job IDs are display/correlation metadata only.

See `conversion_handoff.md` for the ALL-359 boundary between ABC-owned
conversion and AI Curation local PDFX behavior.

## Local Job States

AI Curation uses the existing `pdf_processing_jobs` state model:

| Local job status | Meaning for ABC-backed imports |
| --- | --- |
| `pending` | AI Curation accepted the upload/import and queued background work. |
| `running` | AI Curation is checking a provider match, waiting on provider conversion, downloading provider Markdown/PDF, or ingesting converted Markdown. |
| `completed` | AI Curation has imported the selected text, stored chunks/metadata, and retained or cached the PDF for viewer/download. |
| `failed` | AI Curation cannot continue because of access, provider, conversion, download, ingestion, timeout, or configuration failure. |
| `cancel_requested` | The curator requested local job cancellation. |
| `cancelled` | AI Curation local work stopped or stale cancellation was reconciled. |

The local document `processing_status` follows the same terminal semantics:
successful provider import completes the local document; conversion, download,
or ingestion failure marks it failed.

## Papers and Files

Every import or upload creates one paper (`pdf_documents`) and its main file
(`pdf_document_files`, role `main`). Per-file provenance from the provider
(source file, PDF and converted artifact ids, MD5, converted-artifact SHA-256,
file class and extension, artifact and import status, access scope and groups)
is stored on the file; the reference id, CURIE and external ids stay on the
paper. Ingestion is split into a per-file part (the file's status, its piece
range reserved from the paper's `next_chunk_index`, its stored and verified
pieces, and its `text_bytes`) and a paper part that runs once when a new
paper's files have all succeeded (the paper's status and completion time, and
the search-index document's status and summed piece count). See
`docs/developer/guides/PAPER_FILES_MIGRATION.md`.

## Upload Checksum Preflight

When `DOCUMENT_SOURCE_PROVIDER=abc_literature` and
`DOCUMENT_SOURCE_IMPORT_ENABLED=true`, uploaded PDFs use their MD5 checksum as a
provider preflight before any durable provider-backed import work is queued.

| Provider decision | HTTP/API behavior | Local job behavior |
| --- | --- | --- |
| `no_match` | Continue ordinary local PDF processing for the uploaded file. | Normal local PDF job. |
| `local_pdf_required` for a checksum-matched `file_class=supplement` source | Continue ordinary local PDF processing for the exact uploaded file; do not attach reference-main provenance. | Normal local PDF job. |
| `no_source_artifact` | Stop with `document_source_no_source_artifact`. | No provider job. |
| `access_denied` | Stop with `document_source_access_denied`. | No provider job. |
| `ambiguous_match` | Stop with `document_source_ambiguous_match`. | No provider job. |
| `ready` with converted Markdown | Create document/job and dispatch provider-Markdown ingestion. | `pending` -> `running` -> terminal. |
| `conversion_running` for ABC with authorized source | Create document/job and dispatch provider-conversion polling. | `pending` -> `running` provider conversion. |
| `conversion_failed` | Stop with `document_source_conversion_failed`. | No provider job. |
| `no_converted_text` | Stop with `document_source_no_converted_text`. | No provider job. |

Known ABC main papers without usable converted Markdown do not fall back to
local PDFX extraction. Unknown/no-MD5-match PDFs and checksum-matched
supplements keep the ordinary local PDF path.

## Identifier Import Preflight

Identifier import resolves a reference, lists provider artifacts, applies
source-PDF-derived access, then follows the same selected-artifact behavior as
upload checksum imports.

Resolve-only requests are dry runs. They must not call ABC
`conversion_request`, create a local PDF job, download bytes, or ingest content.

Resolve-and-import requests may queue provider conversion only when there is an
existing authorized source/reference match and no canonical converted Markdown
is available yet.

## ABC Conversion Mapping

AI Curation may call:

```text
GET /reference/referencefile/conversion_request/{curie_or_reference_id}?wait=false&overwrite_tei_md=true
```

only for an existing authorized ABC match. `overwrite_tei_md=true` (agreed with
the Blue Team on 2026-10-02, SCRUM-6624) makes ABC convert references whose only
main text is legacy TEI-derived Markdown. It must not call Literature upload or
reference creation paths.

ABC conversion response fields are stored under PDF job
`metadata.document_source` after JSON-safe sanitization:

- `conversion_status`
- `conversion_job_id`
- `converted_classes`
- `per_file_progress`
- `conversion_error_message`

| ABC status/signal | Local mapping |
| --- | --- |
| `running` without main text | Job `running`, stage `provider_conversion`, progress around 20%, message `ABC Literature conversion running` or pending-file count. |
| `running` with a normalized `converted_merged_main` class or `per_file_progress[].converted.file_class=converted_merged_main` | Re-list artifacts with `show_all`; if one canonical non-TEI Markdown artifact is available, continue to provider-Markdown ingestion. The ABC adapter folds its private per-MOD readiness payload into the normalized class signal. |
| `converted` with one canonical non-TEI Markdown artifact | Continue to provider-Markdown ingestion. |
| `converted` but no canonical non-TEI Markdown artifact | Mark failed: `Provider conversion completed without canonical converted Markdown`. |
| `failed` | Mark failed with ABC `error_message` or failed per-file errors. |
| `no_sources` | Mark failed: `Provider conversion found no convertible source files`. |
| Multiple equally preferred canonical Markdown artifacts | Mark failed/ambiguous rather than selecting alphabetically. |

ABC normalizes statusless converted artifacts to `AVAILABLE` while mapping an
explicit unrecognized producer status to `UNKNOWN`. Only normalized
`AVAILABLE` converted Markdown is importable. Checksum, identifier, and upload
execution share this rule and the provider's canonical-main selection hooks;
none of those consumers treats `UNKNOWN` as a ready fallback or embeds
`converted_merged_main` selection logic.

ABC's canonical-main hook also excludes TEI-signaled artifacts before shared
selection. A TEI-only artifact list therefore continues conversion or reports
missing canonical text instead of producing a ready import.

ABC `conversion_job_id` is not durable enough to drive AI Curation recovery.
On restart or replay, AI Curation should re-query by reference and local source
provenance, not by assuming an ABC job ID can be resumed.

## Provider Markdown Ingestion Mapping

Once a converted Markdown artifact is selected:

1. AI Curation downloads the artifact with the request-local curator bearer
   token.
2. It validates and ingests the Markdown through the provider-neutral Markdown
   ingestion path.
3. It updates SQL source-import state and Weaviate document/chunk data.
4. It marks the PDF job completed.

`download_file` authorization failures are terminal for the curator. They must
not be retried with a service-token bypass.

Download failures reach core as provider-neutral errors (all subclasses of
`DocumentSourceError`, so existing handlers still catch them):

| ABC answer | Core error | Meaning |
| --- | --- | --- |
| 403 | `DocumentSourceAccessDenied` | The caller may not read the file. |
| 404 | `DocumentSourceArtifactNotFound` | ABC lists the file but its stored copy is missing; permanent. |
| 502, 503, 504, or a client timeout or transport failure | `DocumentSourceUnavailable` | ABC or its storage could not be reached; may succeed later. |
| Stream longer than the caller's `max_bytes` | `DocumentSourceArtifactTooLarge` (carries the limit) | The download was stopped part way. |
| Any other answer, including 500 | `DocumentSourceError` | Generic download failure. |

The 404 and 502 answers for a missing or unreachable stored file come from ABC
PR 1392 (merged 13 Nov 2026). An ABC deployment without it answers a missing
stored file with an unhandled 500, which stays the generic failure.

`download_artifact(..., max_bytes=N)` streams the raw (identity-encoded) body and
stops once more than `N` bytes have arrived, so an oversized file is never held
in memory. ABC's `download_file` gunzips the stored object before serving it,
so the count is the converted text's own size. The per-file limit for converted
text is `DOCUMENT_FILE_MAX_CONVERTED_TEXT_BYTES` (default 1,000,000). Today's
main-paper imports do not pass a limit; file listing and supplement import
(KANBAN-1976) apply it.

## Timeouts, Polling, Retry, And Staleness

Configured knobs:

- `DOCUMENT_SOURCE_POLL_INTERVAL_SECONDS`: provider-conversion poll interval
  (default 30 seconds). Every poll re-sends ABC's conversion request, which
  answers with the current state; ABC recycles a worker, restarting its running
  conversion, after about 1,000 requests, so the interval bounds how many
  requests one wait sends (at most 121 in an hour at 30 seconds). Minimum 5
  seconds. Start-up refuses settings whose deadline divided by this interval
  exceeds 400 polls in one wait (`DOCUMENT_SOURCE_MAX_POLLS_PER_WAIT`: under
  half of ABC's roughly 1,000 requests per worker, which other callers share),
  with a named error naming both settings; an `.env` still pinning the old 2
  seconds stops the deploy instead of degrading imports.
- `DOCUMENT_SOURCE_IMPORT_TIMEOUT_SECONDS`: deadline for one provider
  conversion/import job of a main paper (default 3,600 seconds), covering the
  wait for ABC's conversion and then downloads, hierarchy and figure
  classification, and vector storage. ABC needs about 5 minutes to start its
  extraction machine and allows itself 30 minutes per file; a median main
  conversion from a cold machine takes about 12 minutes. KANBAN-1976 /
  ALL-1460 (P7w) raised it from 600 seconds, which failed such imports.
- `DOCUMENT_SOURCE_REQUEST_TIMEOUT_SECONDS`: individual provider HTTP timeout.
- `DOCUMENT_SOURCE_DEAD_WAIT_POLLS`: missed polls after which a cancel
  finalizes a provider wait that no task is polling (default 3, minimum 2,
  checked at start-up; see Cancellation).
- `DOCUMENT_FILE_MAX_CONVERTED_TEXT_BYTES`: largest converted text, in
  downloaded bytes, read for one provider file (default 1,000,000).
- `PDF_JOB_STALE_TIMEOUT_SECONDS`: stale durable-job reconciliation timeout
  when set; otherwise PDF extraction timeout-derived fallback applies.

Once vector storage starts, cancellation waits for chunk verification and metadata
writes to finish before propagating the timeout. Executor threads cannot be stopped
by cancelling their await. Successfully written chunks retain their verified counts;
the cancelled operation marks both Weaviate processing and embedding status failed,
and the provider execution service then marks the SQL document and durable job
failed. The job remains active while writes drain, so the normal active-job guards
continue to block deletion and re-embedding. This drain can extend elapsed time past
the configured deadline; it does not convert a timeout into a successful import.

Existing deployment overrides remain authoritative: a deployment whose `.env`
still sets `DOCUMENT_SOURCE_IMPORT_TIMEOUT_SECONDS=600` or
`DOCUMENT_SOURCE_POLL_INTERVAL_SECONDS=2` (both were written uncommented in
`.env.example` before KANBAN-1976) keeps the old values until the release
changes them. Longer deadlines also delay subsequent papers in sequential
import batches: one request's identifiers run one after another.

While a main paper waits, it stays in progress in the Library and cannot be
deleted, imported again or given files (one active job per paper); the jobs
panel shows ABC's progress messages, refreshed at each poll.

Retry policy:

- Re-importing the same identifier returns the existing duplicate document.
  To start a fresh provider import after failure, wait for the job to be terminal,
  delete the failed document through the normal authenticated document API (which
  removes its chunks), then import the identifier again. Do not repair an old
  failed import by only changing its status. This change does not repair historical
  failed documents automatically.
- AI Curation does not do unbounded provider retry loops beyond the local
  polling loop and wall-clock import timeout.
- HTTP/provider/config failures are sanitized and surfaced as provider
  unavailable/misconfigured or job failure.
- A future release/dev trial can collect live conversion-running/failure
  evidence, but it is not required to close the implementation contract.

Stale-job recovery:

- Active stale jobs are reconciled by the existing PDF job service.
- Stale `cancel_requested` becomes `cancelled`.
- Other stale active jobs become `failed` with a stale-inactivity message.
- Every `update_progress` call records activity (`updated_at`), even when the
  provider's answer is unchanged, so a living wait polls itself fresh and is
  never stale.
- One request's imports run one after another, so the jobs behind a long one
  wait `pending`. While a job runs, the execution service records activity on
  the jobs queued behind it in the same request (`touch_pending_jobs`, at the
  poll interval), so the sweep does not fail them before they start.
- A backend restart or deploy ends a wait (the in-process task dies); the job
  stays active until the curator cancels it or the stale sweep fails it. The
  wait is not resumed.

## Cancellation

Cancellation is local to AI Curation. It stops local polling/download/ingestion
when observed (at the next poll, at most one poll interval later) and marks the
local job cancelled.

A job in the polling stage `provider_conversion` whose last recorded activity
is older than `DOCUMENT_SOURCE_DEAD_WAIT_POLLS` (default 3) times the poll
interval plus the provider request timeout (120 seconds at the defaults) has no
task polling it, typically after a backend restart. Cancelling it finalizes it at once: the
job becomes `cancelled`, its paper is reconciled as after any cancelled import
(a new paper becomes failed and can be deleted or imported again; a paper that
was already usable stays usable). Jobs in other stages only get the cancel flag.
If that task was in fact still finishing a poll, it checks before writing and
stops without touching the paper (under the job-row lock for the source-field
write), so nothing lands on a paper imported again meanwhile.

Only the polling stage records activity on every poll; other stages (a local
PDF's extraction, ingestion) record it only when something changes, as before.

AI Curation does not attempt to cancel ABC Literature conversion work. Even when
AI Curation initiated `conversion_request`, the ABC endpoint does not provide a
cancel contract for this flow.

## Classifying An Answer For Chosen Supplements

`classify_conversion_answer` (`backend/src/lib/document_sources/supplement_wait.py`)
says where each chosen supplement stands after one conversion answer and a
fresh file listing. The supplement stage of an import (KANBAN-1976 P7b) uses it.

- `converted`: the listing has converted text for the file.
- `provider_conversion_failed`: ABC's `per_file_progress` has a `failed` entry
  for the file (matched by `source.referencefile_id`, through the adapter's
  `conversion_file_statuses`), even while the job still runs.
- `waiting`: ABC still answers `running`.
- `provider_not_converted`: ABC's answer is final and the file has neither.

Entries ABC merges in from converted rows without a source id are skipped; the
listing decides those files. ABC's free-text `error` and `error_message` are
never read, so causes ABC states only in words (its group rule, its size limits)
are not known. On ABC's inline Office path a failure names no file (the answer
carries the progress of ABC's most recent job), so such a file is
`provider_not_converted`, not `provider_conversion_failed`.

## Curator-Facing Messages

| Situation | Message/error |
| --- | --- |
| Provider config/auth unavailable | `Document-source lookup is unavailable.` or sanitized health/readiness failure. |
| No MD5 match | Continue local PDF processing; no provider error. |
| Source match inaccessible | `No matching source document is accessible to this curator.` |
| Ambiguous match | `Multiple accessible source documents matched this PDF.` |
| No source artifact | `The document source match did not include an importable source PDF.` |
| No converted Markdown | `The source document does not have converted Markdown available.` |
| Conversion running | `ABC Literature conversion running` with pending-file count when available. |
| Conversion completed, locating text | `ABC Literature conversion completed; locating converted Markdown`. |
| Main text ready | `ABC Literature main text is ready; importing converted Markdown`. |
| No convertible sources | `Provider conversion found no convertible source files`. |
| Conversion failed | ABC `error_message`, failed per-file errors, or `ABC Literature conversion failed`. |
| Timeout | `Provider conversion exceeded <seconds> seconds` or `Provider Markdown import exceeded <seconds> seconds`. |
| Duplicate import | Existing duplicate document response with existing document ID/date. |
| Partial identifier batch success | Per-identifier result rows; imported/duplicate/error counts summarize the batch. |

Messages should never say that AI Curation uploaded to Literature, created a
Literature reference, or overwrote TEI Markdown.

## Required Test Coverage

The lifecycle is covered by unit/fake tests rather than live Literature tests:

- checksum no match falls back to local PDF processing;
- accessible READY converted Markdown dispatches provider Markdown ingestion;
- pending ABC conversion dispatches provider conversion and does not dispatch
  local PDF processing;
- ABC source-only/no converted text does not silently run local PDFX;
- resolve-only identifier checks do not request conversion;
- provider conversion running/ready/failure/no-sources/timeout mapping;
- per-MOD-only main-text readiness across checksum, identifier, and upload paths;
- statusless ABC normalization and explicit-`UNKNOWN` rejection;
- non-ABC provider-declared canonical Markdown selection;
- terminal `converted` with no canonical non-TEI Markdown fails clearly;
- ambiguous canonical converted Markdown fails/returns ambiguity;
- `download_file`/provider download failures do not bypass through service auth;
- cancellation and stale-job reconciliation use existing PDF job semantics.
