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

- `GET /reference/referencefile/conversion_request/{curie_or_reference_id}?wait=false&overwrite_tei_md=true`

Important constraints:

- `conversion_request` is reference-wide, not source-file-scoped. A PDF MD5
  match is the access/provenance anchor, but ABC may convert nXML, main PDFs,
  and eligible supplements for the reference.
- AI Curation sends `overwrite_tei_md=true` (agreed with the Blue Team on
  2026-10-02, SCRUM-6624) so references whose only main text is legacy
  TEI-derived Markdown are converted from their main source. ABC deletes the
  `_tei` rows after a successful replacement.
- Existing `_nxml` `converted_merged_main` Markdown is preferred/canonical main
  text and should be used when available, even when the curator uploaded a PDF.
- If nXML exists but `_nxml` Markdown does not, ABC may create it.
- AI Curation should proceed once authorized non-TEI
  `converted_merged_main` Markdown exists; supplement conversion may continue
  or fail without blocking the main chat/document import.
- ABC conversion job IDs/progress are not durable enough to be AI Curation's
  source of truth. Persist local import state and recover by re-querying ABC by
  reference.
- No direct local PDFX fallback for known ABC papers that are waiting on
  provider conversion unless a later ticket explicitly scopes a fallback.
- A curator-uploaded PDF already saved by AI Curation may use local PDFX when
  the selected canonical Markdown download is authoritatively denied. This is
  an explicit source-selection branch after provider conversion, not a pending
  conversion fallback; it must keep the same document/job and record the
  provider-text import as failed.

## Access And Provenance

Converted main Markdown inherits access from its source/main PDF. Historical
converted rows can have null/global MOD metadata, so UI and backend code must
not infer public access from the converted row alone. Converted supplements
keep their own rule and are allowed only when their original's rule also
allows the curator (below).

### File lineage and per-file access (KANBAN-1976 / ALL-1460, P2)

`GET /reference/referencefile/show_all/{curie}` returns one flat row per file.
It never nests converted rows under their original; instead each
`converted_merged_*` row carries `source`, the original ABC resolved it from
(`referencefile_crud._find_source_for_derived`), or `null` when ABC cannot
resolve it. The adapter reads these fields only:

- A `converted_merged_supplement` row is linked to its original through
  `source.referencefile_id` (`parent_artifact_id`) and keeps its own access
  rule, exactly as ABC's `download_file` applies it to that row (license, then
  its own `referencefile_mods`, no-MOD grant included). The original's rule
  stays on the original. A reader must allow the converted text only when both
  rules allow the curator, checked separately (P6 does this); one precomputed
  intersected policy is not the same thing. ABC's sync only adds MOD links to
  converted rows, so after sync the two usually agree; before sync the
  converted row can be narrower.
- A `converted_merged_supplement` row with a `null` source is never listed: no
  original means no access rule to apply. A row whose `source` names a file
  missing from the same listing (an inconsistent ABC answer, since ABC builds
  `source` from that list) is left out too; the paper's other files are still
  listed, a WARNING log names the raw file ids and the reference, and one
  Sentry warning (`document_source` / `converted_source_not_listed`) carries
  only hashed ids.
- Every row whose class is not `converted_*` or `embedding` (the originals,
  plus a few classes ABC derives without that prefix: `tei`, `figure`,
  `thumbnail`, `inline_image`, `bw_duplicate`) gets ABC's own `download_file`
  rule, which ABC applies to every class: open-access license, or a no-MOD
  `referencefile_mods` row (any caller with some MOD access), or the caller's
  MOD among the rows. A license whose `open_access` is unset refuses everyone.
  Before P2 the no-MOD grant applied only to PDFs.
- Word and Excel supplements (`file_class` `supplement` with extension `xlsx`,
  `xlsm`, `docx` or `docm`, the formats ABC converts in-process) map to the
  core role `SOURCE_SUPPLEMENT`. Supplementary PDFs stay `SOURCE_PDF`. Large
  data files (`htp_supplement`) and other extensions keep role `UNKNOWN`.
- Figure-metadata sidecars must have the metadata class matching the source's
  class (`converted_main_figure_metadata` for a main PDF). Names from the
  source's figure rows, or `{source}_image_`, are preferred; when none match,
  every sidecar of that class is kept, as before, so a paper whose main text
  came from another main PDF keeps that text's sidecars. A main paper without
  main-figure metadata no longer gets supplement sidecars.

Persist compact provenance on AI Curation documents:

- source provider/reference IDs
- source PDF/referencefile ID
- converted Markdown referencefile ID
- source MD5 and external IDs when known
- source access scope/MODs
- viewer mode

Raw Literature payloads, if retained for debugging, belong in storage paths or
debug/download surfaces, not the main document list or Weaviate metadata.

### File listing and per-file checks (KANBAN-1976 / ALL-1460, P6)

`GET /weaviate/document-sources/files?identifier=...` (before import) and
`GET /weaviate/documents/{document_id}/source-files` (an imported paper, owner only) list a
paper's ABC files for the signed-in curator. Backend only; the response holds codes and
numbers, never wording (`src/lib/document_sources/file_listing.py`).

- The reference is always resolved first; the listing refuses a bare identifier, because the
  licence step of ABC's access rule applies only to a resolved reference.
- The main paper is one entry, chosen as an import chooses it (`decide_reference_import`); its
  converted text is not listed separately and is not checked here.
- Supplements: published (`file_publication_status == "final"`) `supplement` and
  `htp_supplement` rows. Status `ready` (a published `converted_merged_supplement` names it as
  its `source`), `no_converted_text`, `large_data_file` (`htp_supplement`) or
  `type_not_supported`. Other converter variants are never read.
- Access: a supplement is shown only when the curator passes both the original's rule and the
  converted row's own rule, checked separately. Files the curator cannot open appear only in
  `not_shown_count` (ABC's own `show_all` lists every file's metadata to any signed-in caller,
  so a bare count shows less). A converted row whose source is missing from ABC's listing is
  left out by the adapter and is not counted.
- Copies with the same MD5 collapse to the copy the curator can open, the lowest provider file
  id (numeric order) among several; the others are counted in `duplicate_copies_count`.
- ABC can hold more than one `final` converted text for one original: a legacy `_tei` text
  next to the `_merged` one, both `converted_merged_supplement`. ABC treats `_merged` as
  canonical (it deletes `_tei` rows when it re-converts), so the adapter ranks `_merged` first
  (`supplement_text_artifact_sort_key`, keyed on ABC's own name suffix). Within one rank the
  highest provider file id the curator can read is listed and read.
- An imported paper is listed by its stored reference CURIE, and the resolved reference id must
  equal the stored one (`paper_reference_mismatch`; `paper_reference_incomplete` when either is
  missing). The numeric ABC reference id is never looked up: ABC reads a bare number as a PMID.
- File role an import would give: `main` for the paper's main PDF; `supplement` for a
  supplementary PDF (`SOURCE_PDF`) or another supplement (`SOURCE_SUPPLEMENT`); any other role
  is a named error.
- Checks R1 to R7 (`file_assessment.py`) run on the downloaded converted text of each `ready`
  supplement not checked before: at most `DOCUMENT_FILE_DOWNLOAD_CONCURRENCY` downloads at once
  across the backend process (default 4), shared with imports; never past
  `DOCUMENT_FILE_MAX_CONVERTED_TEXT_BYTES`; at most `DOCUMENT_FILE_LISTING_CHECK_LIMIT` files
  per listing (default 20, the per-paper file limit), the rest `check_limit_reached`. Each file
  is checked and released before its download slot is freed. A converted text without an MD5 is
  not downloaded (`text_without_checksum`). `too_large` is stored like any refusal; a failed
  download (`text_unavailable`, `text_missing`, `text_access_denied`) is tried once per listing
  and not stored. There is no per-user rate limit: the repository has no such mechanism. R7 (one
  top-level title) applies to converted Word and Excel files. R3 is a note only
  (`DOCUMENT_FILE_FEW_CHARACTERS_NOTE_BELOW`). Results are stored in `document_file_assessments`
  by the converted text's provider file id, MD5 and a check version that includes every
  threshold and the token encoding, and reused only after the access checks. A refusal found
  while listing sends no Sentry event. The listing does not clean or parse text.
- Each entry is named by the original's provider file id (an import writes it to
  `source_provider_source_file_id`; "in this paper" matches it). It carries the original's MD5
  (`md5`), the converted text's MD5 (`converted_md5`, which an import sends back to detect a
  change since listing), the core `format`, the `category` code and the provider's
  `single_title_required` profile flag.
- Shared with the import (P7): `list_files_for_identifier` / `list_files_for_paper`
  (`file_listing_service.py`), `converted_text_access_allowed` (both rules, separately),
  `download_converted_text` (one file, the process-wide limit and the size cap, bytes handed
  to a callback while the slot is held; failure codes `too_large`, `text_unavailable`,
  `text_missing`, `text_access_denied`), `assess_converted_file` with
  `FileCheckReason`, and `stored_assessment` / `store_assessment`.
