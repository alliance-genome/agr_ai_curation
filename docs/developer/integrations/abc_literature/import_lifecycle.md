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

## Upload Checksum Preflight

When `DOCUMENT_SOURCE_PROVIDER=abc_literature` and
`DOCUMENT_SOURCE_IMPORT_ENABLED=true`, uploaded PDFs use their MD5 checksum as a
provider preflight before any durable provider-backed import work is queued.

| Provider decision | HTTP/API behavior | Local job behavior |
| --- | --- | --- |
| `no_match` | Continue ordinary local PDF processing for the uploaded file. | Normal local PDF job. |
| `no_source_artifact` | Stop with `document_source_no_source_artifact`. | No provider job. |
| `access_denied` | Stop with `document_source_access_denied`. | No provider job. |
| `ambiguous_match` | Stop with `document_source_ambiguous_match`. | No provider job. |
| `ready` with converted Markdown | Create document/job and dispatch provider-Markdown ingestion. | `pending` -> `running` -> terminal. |
| `conversion_running` for ABC with authorized source | Create document/job and dispatch provider-conversion polling. | `pending` -> `running` provider conversion. |
| `conversion_failed` | Stop with `document_source_conversion_failed`. | No provider job. |
| `no_converted_text` | Stop with `document_source_no_converted_text`. | No provider job. |

Known ABC papers without usable converted Markdown do not fall back to local
PDFX extraction. Unknown/no-MD5-match PDFs keep the ordinary local PDF path.

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
GET /reference/referencefile/conversion_request/{curie_or_reference_id}?wait=false&overwrite_tei_md=false
```

only for an existing authorized ABC match. It must not call Literature upload,
reference creation, or TEI-overwrite paths.

ABC conversion response fields are stored under PDF job
`metadata.document_source` after JSON-safe sanitization:

- `conversion_status`
- `conversion_job_id`
- `converted_classes`
- `per_file_progress`
- `per_mod_status`
- `conversion_error_message`

| ABC status/signal | Local mapping |
| --- | --- |
| `running` without main text | Job `running`, stage `provider_conversion`, progress around 20%, message `ABC Literature conversion running` or pending-file count. |
| `running` with `converted_merged_main`, `per_file_progress[].converted.file_class=converted_merged_main`, or `per_mod_status[].main_converted=true` | Re-list artifacts with `show_all`; if one canonical non-TEI Markdown artifact is available, continue to provider-Markdown ingestion. |
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

## Timeouts, Polling, Retry, And Staleness

Configured knobs:

- `DOCUMENT_SOURCE_POLL_INTERVAL_SECONDS`: provider-conversion poll interval.
- `DOCUMENT_SOURCE_IMPORT_TIMEOUT_SECONDS`: wall-clock timeout for one
  provider conversion/import job.
- `DOCUMENT_SOURCE_REQUEST_TIMEOUT_SECONDS`: individual provider HTTP timeout.
- `PDF_JOB_STALE_TIMEOUT_SECONDS`: stale durable-job reconciliation timeout
  when set; otherwise PDF extraction timeout-derived fallback applies.

Retry policy:

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

## Cancellation

Cancellation is local to AI Curation. It stops local polling/download/ingestion
when observed and marks the local job cancelled.

AI Curation does not attempt to cancel ABC Literature conversion work. Even when
AI Curation initiated `conversion_request`, the ABC endpoint does not provide a
cancel contract for this flow.

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
