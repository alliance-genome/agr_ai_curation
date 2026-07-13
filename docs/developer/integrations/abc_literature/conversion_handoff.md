# ABC Literature Conversion Handoff

Status: ALL-359 closeout note for existing ABC Literature paper conversion.

This note defines the boundary between AI Curation, ABC Literature, and PDFX for
Literature-backed imports. It supersedes the earlier blanket read-only language:
AI Curation must not upload unknown PDFs into ABC Literature, but it may request
provider-side conversion for an existing, authorized ABC reference discovered by
PDF MD5 or identifier lookup.

## Responsibility Boundary

ABC Literature owns reference-file conversion for ABC-backed papers. AI
Curation owns local document import, PDF retention/viewing, converted-Markdown
validation, chunking, embeddings, Weaviate storage, durable job state, and
curator-facing progress.

Normal import flows must not call:

- `POST /reference/add/`
- `POST /reference/referencefile/file_upload/`
- merge, bulk upload, or other ABC Literature reference/referencefile mutation
  paths

For an existing authorized ABC source/reference match, AI Curation may call:

```text
GET /reference/referencefile/conversion_request/{curie_or_reference_id}?wait=false&overwrite_tei_md=false
```

The call is provider-side and reference-wide. A PDF MD5 match proves the curator
is dealing with a known source file, but ABC may satisfy the main text from nXML
or another reference-level source rather than from the matched PDF bytes.

## Upload Outcomes

| Upload outcome | AI Curation behavior |
| --- | --- |
| No ABC MD5 match | Keep ordinary local PDF processing available. |
| One authorized ABC MD5 match with canonical Markdown | Retain the PDF for viewer/download, import provider Markdown, and persist ABC provenance. |
| One authorized ABC MD5 match without canonical Markdown | Request/poll ABC conversion, then import provider Markdown once main text is available. Do not silently run local PDFX for the known ABC paper. |
| Multiple accessible ABC source matches | Stop as ambiguous and ask for a more specific identifier/import path. |
| Source match inaccessible | Stop as access denied. Do not retry through a service-token bypass. |

This preserves the useful old behavior for genuinely unknown PDFs while avoiding
repeated AI Curation-local extraction for papers ABC already knows about.

## Identifier Outcomes

Identifier import follows the same provider conversion boundary after
resolution:

- Resolve-only is a dry run. It must not request conversion, download bytes,
  create local documents, or enqueue jobs.
- Resolve-and-import may request conversion only after the reference/artifact
  has been resolved and source-PDF-derived access has been applied.
- If canonical Markdown already exists, import it directly and do not request
  conversion.

## Conversion Response Mapping

ABC exposes provider conversion state through the `conversion_request` response:

| ABC signal | AI Curation interpretation |
| --- | --- |
| `status=running` without main text | Keep the local durable job running in provider-conversion stage. |
| `status=running` with `converted_merged_main`, `per_file_progress[].converted.file_class=converted_merged_main`, or `per_mod_status[].main_converted=true` | Re-list reference files and import the canonical main Markdown if available. |
| `status=converted` with canonical main Markdown | Re-list/download/import provider Markdown. |
| `status=converted` but no canonical main Markdown | Fail locally with a clear missing-main-text message. |
| `status=failed` | Fail locally with sanitized ABC error/progress details. |
| `status=no_sources` | Fail locally as no convertible provider source files. |

The ABC adapter owns interpretation of all three main-text readiness signals,
including per-MOD `main_converted=true`. After a ready signal, reusable upload
and import services re-list artifacts and ask the provider to identify and rank
its canonical main Markdown; they do not inspect ABC file-class names.

Artifact availability is a separate normalized contract. Statusless converted
ABC rows are normalized to `AVAILABLE` at the adapter boundary. An explicit
unrecognized status is normalized to `UNKNOWN` and is not import-ready. All
reusable consumers require `AVAILABLE`; they must not reinterpret `UNKNOWN` as
available.

The ABC `job_id` is display/correlation metadata only. AI Curation persists its
own local job state and recovers by re-querying ABC by reference/source
provenance rather than treating the ABC job ID as durable.

## nXML And TEI Rules

AI Curation should prefer existing `_nxml` `converted_merged_main` Markdown when
ABC exposes it. It is the canonical main text even when the curator uploaded a
PDF, because the PDF MD5 match is the source/provenance anchor and the text
artifact can be reference-level.

`overwrite_tei_md` must remain `false` in this wave. That means AI Curation does
not ask ABC to ignore/delete legacy `_tei` rows. It does not prevent ABC from
creating a newer `_nxml` row when nXML is present. If only TEI-derived Markdown
exists and no non-TEI main Markdown can be obtained without overwrite, surface a
legacy-TEI-only/missing-canonical-text state for product and Blue Team follow-up
rather than overwriting ABC rows.

## Provider-Neutral Shape

Core AI Curation code should depend on document-source concepts, not ABC
endpoint names:

- `DocumentSourceProvider.request_conversion(reference, wait=False)` is an
  optional provider capability.
- `SourceConversionResult` carries provider-neutral status values:
  `converted`, `running`, `failed`, and `no_sources`.
- Provider hooks own conversion readiness, canonical-main identification, and
  main-text ranking. Reusable upload/import code applies only the normalized
  `AVAILABLE` requirement and provider-declared hooks.
- Provider metadata such as `converted_classes`, `per_file_progress`, and
  `per_mod_status` may be sanitized into local job metadata for UI/debugging.
- Providers without conversion support can return ready text, PDF-only/source
  artifacts, or no match according to their own capability.

ABC-specific names such as `referencefile_id`, `converted_merged_main`,
`conversion_request`, `_nxml`, and `_tei` stay in the ABC adapter/docs/tests or
sanitized optional provenance.

## Timeout, Retry, And Cancellation

Timeout, polling, retry, and stale recovery are defined in
`import_lifecycle.md`. The important handoff constraints are:

- poll using the request-scoped curator bearer token;
- do not persist or return bearer credentials;
- use `wait=false` in production import flows;
- keep polling bounded by `DOCUMENT_SOURCE_IMPORT_TIMEOUT_SECONDS`;
- stop local work on cancellation when observed;
- do not attempt to cancel ABC conversion, because ABC exposes no cancellation
  contract for this endpoint.

## Validation Contract

Closure for ALL-359 is based on code, docs, unit/fake-provider coverage, and the
existing READY-path smoke evidence. A final live conversion-needed trial is
deferred until the dev-publish smoke, because it requires a safe ABC fixture that
can be allowed to convert without creating unknown Literature references.

Required local coverage:

- ABC client calls `conversion_request` with `wait=false` and
  `overwrite_tei_md=false`.
- Provider converts ABC payloads into `SourceConversionResult`.
- Upload checksum selection requests conversion for known authorized ABC source
  matches without canonical Markdown when a curator token exists.
- Upload checksum selection does not request conversion before curator-token
  availability is known.
- Identifier resolve-only does not request conversion.
- Identifier import can dispatch provider-conversion polling instead of local
  PDFX when the provider reports conversion running.
- Known ABC source-only cases do not silently fall back to local PDFX.

Live/dev-publish evidence should add a safe conversion-needed fixture when one
exists, but that evidence is not required to establish the implementation
contract.
