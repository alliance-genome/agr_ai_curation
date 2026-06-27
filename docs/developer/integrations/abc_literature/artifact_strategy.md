# ABC Literature Artifact Strategy

Status: implementation decision for ALL-366 / ALL-359.

See `api_contract_verification.md` for the current OpenAPI hostnames,
endpoint contract notes and required live fixture checks. Some earlier
read-only wording in that file is superseded by the ALL-359 conversion
clarification below.

See `converted_text_contract.md` for the canonical converted Markdown
selection, access, download-byte, and TEI/nXML fallback rules.

See `import_lifecycle.md` for the durable local job lifecycle, ABC conversion
status mapping, timeout, cancellation, and curator-facing message contract.

See `conversion_handoff.md` for the ALL-359 conversion boundary between AI
Curation, ABC Literature, and local PDFX.

AI Curation's first ABC Literature integration uses Literature as the source
paper system, but AI Curation still performs local Markdown validation,
chunking, hierarchy resolution, embedding, and Weaviate storage. Centralized
ABC embedding parquet/catalog consumption is intentionally out of scope until
that service is ready.

## First-Cut Behavior

- Identifier/search import resolves references through ABC Literature.
- AI Curation downloads authorized `converted_merged_main` Markdown and stores
  it as a first-class source artifact.
- The local pipeline ingests the converted Markdown into existing AI Curation
  document/chunk storage.
- Imported records keep a PDF-backed viewer path (`viewer_mode=local_pdf` for
  retained uploads today, or a future provider PDF proxy/cache mode) so the
  chat viewer can always show the PDF.
- Existing local PDF upload remains available during this manual integration
  phase.
- If an uploaded PDF has no ABC MD5 match, AI Curation may continue through the
  local PDF processing path.
- If an uploaded PDF has an authorized ABC MD5 match but no usable converted
  Markdown, AI Curation should request provider-side ABC conversion instead of
  silently running local PDF extraction for that known ABC paper.

## Viewer And Downloads

For ABC-backed imports:

- Evidence search and extraction operate from locally stored chunks derived
  from ABC converted Markdown when available.
- PDF viewing/downloading remains available from the retained upload or an
  authorized provider PDF cache/proxy.
- Download/info surfaces may offer the PDF, converted Markdown, processed JSON,
  and compact provenance.

For a future `viewer_mode=provider_pdf_proxy` path:

- Source PDF access must be checked using the logged-in curator's token or an
  equivalent source-PDF-derived access scope before bytes are served.
- Any cached provider PDF must be tagged with the source access scope and served
  only to authorized curators.

## Provider Conversion Boundary

AI Curation must not create or upload Literature records in normal import flows:

- No `POST /reference/add/`.
- No `POST /reference/referencefile/file_upload/`.

For an existing, authorized ABC reference discovered through MD5 or identifier
lookup, AI Curation may call:

- `GET /reference/referencefile/conversion_request/{curie_or_reference_id}?wait=false&overwrite_tei_md=false`

Important constraints:

- `conversion_request` is reference-wide, not source-file-scoped. A PDF MD5
  match is the access/provenance anchor, but ABC may convert nXML, main PDFs,
  and eligible supplements for the reference.
- `overwrite_tei_md` must remain `false` in this wave. Do not ask ABC to ignore
  and delete legacy TEI-derived Markdown rows.
- Existing `_nxml` `converted_merged_main` Markdown is preferred/canonical main
  text and should be used when available, even when the curator uploaded a PDF.
- If nXML exists but `_nxml` Markdown does not, ABC may create it without
  overwriting TEI rows.
- AI Curation should proceed once authorized `converted_merged_main` Markdown
  exists; supplement conversion may continue or fail without blocking the main
  chat/document import.
- ABC conversion job IDs/progress are not durable enough to be AI Curation's
  source of truth. Persist local import state and recover by re-querying ABC by
  reference.
- No direct local PDFX fallback for known ABC papers that are waiting on
  provider conversion unless a later ticket explicitly scopes a fallback.

## Access And Provenance

Converted Markdown inherits access from its source/main PDF. Historical
converted rows can have null/global MOD metadata, so UI and backend code must
not infer public access from the converted row alone.

Persist compact provenance on AI Curation documents:

- source provider/reference IDs
- source PDF/referencefile ID
- converted Markdown referencefile ID
- source MD5 and external IDs when known
- source access scope/MODs
- viewer mode

Raw Literature payloads, if retained for debugging, belong in storage paths or
debug/download surfaces, not the main document list or Weaviate metadata.
