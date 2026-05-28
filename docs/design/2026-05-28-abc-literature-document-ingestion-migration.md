# ABC Literature Document Ingestion Migration

Date: 2026-05-28

Status: design assessment

Owner context: AI Curation / ABC Literature / PDFX

## Executive Summary

AI Curation should move document ingestion toward ABC Literature as the canonical paper and file source instead of treating the local Documents page as the owner of PDF extraction. The current local path should remain available during rollout, but the new default should be:

1. Resolve a paper by PMID or ABC reference curie first; add PMCID/DOI only after their Literature cross-reference contracts are verified with fixtures.
2. Ask ABC Literature whether converted Markdown already exists.
3. If converted Markdown exists, download that file and ingest it directly into AI Curation.
4. If conversion is needed, request conversion through ABC Literature and poll the Literature conversion lifecycle.
5. For uploaded PDFs, compute MD5 first and check ABC Literature before storing or extracting anything locally.
6. Store source/provenance metadata in AI Curation so curators can see the ABC reference, source file, converted file, and conversion state.

This is larger than a tiny PDFX progress patch, but it aligns better with the Jira direction and avoids building more local-only document behavior that we expect to replace. A reasonable first vertical slice is one to two focused implementation days if we keep scope to PMID/reference import of already-converted non-TEI main Markdown or newly Literature-converted main Markdown, and defer full PDF viewer parity, supplement import, image manifests, PMCID/DOI guarantees, and ambiguous MD5 matching.

## Recommendation

Build a Literature-backed ingestion path behind a feature flag and keep direct AI Curation-to-PDFX upload as a legacy fallback until the new path is stable.

Recommended rollout:

1. Add a backend `LiteratureClient` plus fake-service tests.
2. Add SQL provenance columns and mirror the most important provenance into Weaviate document metadata.
3. Add a markdown-ingestion adapter that reuses the existing chunking, hierarchy, embedding, and storage pipeline without calling PDFX.
4. Add identifier import first: PMID/ABC curie to non-TEI converted Markdown to AI Curation document.
5. Add upload-by-MD5 next: PDF upload to ABC lookup, existing conversion reuse, then ABC upload/conversion only when a reference is known.
6. Replace the Documents page upload UX with a paper import control plus a retained PDF upload path that explains whether it matched ABC, needs reference selection, or was sent to ABC for conversion.

Do not require any breaking PDFX API changes for this migration. The Literature service already provides the conversion lifecycle API that AI Curation should consume.

## Evidence Gathered

### Local repositories

Repositories inspected:

- AI Curation: `/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation`
- ABC Literature Service fresh clone: `/tmp/ai-curation-pdfx-migration-review-agr_literature_service`
- ABC Literature UI: `/home/ctabone/programming/claude_code/analysis/alliance/agr_literature_ui`
- ABC document parser package: `/home/ctabone/programming/claude_code/analysis/alliance/agr_abc_document_parsers`

The AI Curation repo is currently on `main` and ahead of `origin/main` by one local commit from the prior PDFX queue-reporting work. This plan file is additive.

### Jira/KANBAN findings

The Jira tickets already describe the intended migration, and they agree with the direction Chris described.

High-signal AI Curation tickets:

- `KANBAN-1238`: Epic for integrating AI Curation with ABC Literature as the document source.
- `KANBAN-1239`: Add PMID and ABC Literature identifier import API.
- `KANBAN-1240`: Update Documents UI for PMID and ABC Literature paper import.
- `KANBAN-1241`: Display Literature provenance on AI Curation documents.
- `KANBAN-1242`: Add fake ABC Literature service tests.
- `KANBAN-1243`: Define import lifecycle, polling, retry, and status mapping.
- `KANBAN-1244`: Add SQL/Weaviate provenance schema migration.
- `KANBAN-1245`: Define PDF artifact strategy for ABC Literature imports and viewer/download behavior.
- `KANBAN-1246`: Configure Literature integration base URLs, auth, feature flag, and rollback.
- `KANBAN-1040`: Verify ABC Literature OpenAPI, auth, checksum lookup contract.
- `KANBAN-1041`: Integrate PDF uploads with ABC Literature existence/conversion flow.
- `KANBAN-1042`: Ingest ABC Literature converted Markdown into AI Curation Weaviate documents.
- `KANBAN-1063`: Add backend ABC Literature service client/config/auth/health.
- `KANBAN-1064`: Confirm canonical converted Markdown retrieval contract.
- `KANBAN-1065`: Verify ABC Literature to PDFX handoff.

Related Blue Team / Valerio context:

- `SCRUM-6122`: Classifier Markdown downloader had rejected global converted rows where `referencefile_mods` contained `mod_abbreviation: null`. AI Curation must treat null/global rows as usable.
- `SCRUM-6125`: Decommission legacy GROBID service because PDFX now handles pdf2md conversions.
- `SCRUM-6136`: Historical data artifact around no eligible main PDF for target MOD. Not an ongoing AI Curation blocker, but it reinforces that Literature conversion status can mean "no source for this MOD".
- `SCRUM-6101`: Remove TEI from Literature file-management display. AI Curation should prefer `converted_merged_*` Markdown and not build a TEI-first path.
- `SCRUM-5868`: Ongoing Markdown schema refinement.
- `KANBAN-1327`: Continue processing when one extraction method fails. AI Curation should report per-file failures and consume main Markdown when available rather than treating supplement failure as total failure.
- `KANBAN-1336`: Improve AI Curation guardrails for extraction failures and empty-result ambiguity.

Important Jira implication:

The migration should not be framed as "AI Curation uploads a PDF to PDFX differently." It should be framed as "AI Curation asks Literature for paper/file/conversion state, and Literature owns PDFX handoff."

### AWS/PDFX runtime findings

Checked with AWS profile `ctabone` on 2026-05-28.

Current PDFX-related EC2 instances tagged `Project=pdfx` in `us-east-1`:

| Instance | State | Type | Name | Owner | Notes |
| --- | --- | --- | --- | --- | --- |
| `i-0105504873df917c0` | running | `g5.2xlarge` | `pdf-benchmark-pedro` | `pedro-assis-sgd` | Current active PDFX GPU instance, launched 2026-05-28 16:12:31 UTC |
| `i-0fa5709214958e7ee` | stopped | `g5.2xlarge` | `pdfx-backend-test` | none | Stopped test instance |

Live PDFX health at the time of this assessment:

```json
{
  "proxy": "ok",
  "status": "healthy",
  "ec2": "ready",
  "queue_depth": 0,
  "queue_durable": true,
  "active_jobs": 0,
  "active_backend_jobs": 0,
  "gpu_healthy": true,
  "gpu_status": "ok",
  "gpu_workers": 1,
  "gpu_redis": "ok",
  "gpu_grobid": "ok"
}
```

Operational implication:

The local "GPU is spinning up" confusion was a reporting mismatch, not proof that PDFX was hung. The deeper migration should rely on Literature conversion states (`running`, `converted`, `failed`, `no_sources`) rather than exposing AI Curation's direct PDFX worker lifecycle as the curator-facing truth.

## ABC Literature API Contracts

OpenAPI was downloaded from both production and stage:

- Production: `https://literature-rest.alliancegenome.org/openapi.json`
- Stage: `https://literature-rest-stage.alliancegenome.org/openapi.json`

Both currently report title `Alliance Literature Service` and version `0.1.0`.

### Reference lookup endpoints

Relevant reference endpoints:

| Purpose | Endpoint | Notes |
| --- | --- | --- |
| PMID lookup | `GET /reference/external_lookup/{external_curie}` | Accepts `PMID:123`, `PubMed:123`, etc. Current implementation only supports PMID-like prefixes. |
| Cross-reference lookup | `GET /reference/by_cross_reference/{curie_or_cross_reference_id}` | Resolves a cross-reference to a reference entity. Candidate for DOI/PMCID if indexed as cross-references. |
| Reference show | `GET /reference/{curie_or_reference_id}` | Fetches the reference by ABC curie or database ID. |
| Add PMID | `POST /reference/add/` | Body: `pubmed_id`, `mod_mca`, optional `mod_curie`. Returns a reference curie string. |

`ReferenceSchemaAddPmid` requires:

```json
{
  "pubmed_id": "39671436",
  "mod_mca": "WB",
  "mod_curie": ""
}
```

Open question:

The docs and tickets mention PMCID and DOI lookup, but the current `external_lookup` implementation only accepts PMID-like prefixes. PMCID/DOI may work through `by_cross_reference` if the cross-references exist. We should verify this with Valerio before promising PMCID/DOI in the first release.

### Reference search endpoint

ABC Literature has a full reference search endpoint that AI Curation can use for a "type title/author/year and select paper" workflow:

`POST /search/references/`

The endpoint is backed by Elasticsearch and is already used by the ABC Literature UI. It supports:

- free-text query across all text fields
- field-scoped search via `query_fields`
  - `All`
  - `Citation`
  - `Title`
  - `Abstract`
  - `Author`
  - `ORCID`
  - `Keyword`
  - `Curie`
  - `Xref`
- `author_filter`
- date filters:
  - `date_published`
  - `date_pubmed_modified`
  - `date_pubmed_arrive`
  - `date_created`
- pagination through `page` and `size_result_count`
- `partial_match`
- facets and negated facets
- sorting, including published-date ordering
- result highlights for title, abstract, keywords, citation, authors, and ORCID

The response includes:

- `hits`
- `aggregations`
- `return_count`

Each hit includes:

- `curie`
- `citation`
- `title`
- `date_published`
- `date_published_start`
- `date_published_end`
- `date_created`
- `abstract`
- `cross_references`
- `workflow_tags`
- `mod_reference_types`
- `language`
- `authors`
- `highlight`

This should become the preferred interactive discovery path in AI Curation:

1. User searches by title, author, year/date range, PMID/PMCID/DOI/cross-reference, or keywords.
2. UI displays a compact selectable result list.
3. User selects one or more papers.
4. AI Curation imports by the selected `curie` using the same Literature import flow.

Open implementation detail:

The search response does not directly include converted-file state. After selection, AI Curation should call `show_all/{curie}` and/or `conversion_request/{curie}` to determine whether converted Markdown already exists.

### Referencefile endpoints

Relevant referencefile endpoints:

| Purpose | Endpoint | Notes |
| --- | --- | --- |
| MD5 lookup | `GET /reference/referencefile/by_md5/{md5sum}` | Returns all referencefiles with that MD5, plus converted Markdown rows derived from each source. |
| File listing | `GET /reference/referencefile/show_all/{curie_or_reference_id}` | Lists all files attached to a reference. |
| Conversion request/poll | `GET /reference/referencefile/conversion_request/{curie_or_reference_id}?wait=false&overwrite_tei_md=false` | Starts conversion if needed, or reports current state. Re-call same URL to poll. |
| Download file | `GET /reference/referencefile/download_file/{referencefile_id}` | Downloads source PDF, converted Markdown, nXML, etc. |
| Upload file | `POST /reference/referencefile/file_upload/` | Multipart upload plus metadata query params or metadata file. |

`POST /reference/referencefile/file_upload/` metadata parameters:

- `reference_curie`
- `display_name`
- `file_class`
- `file_publication_status`
- `file_extension`
- `pdf_type`
- `is_annotation`
- `mod_abbreviation`
- `upload_if_already_converted`

Important upload response gap:

`POST /reference/referencefile/file_upload/` currently returns only the string `success`. The CRUD layer computes the MD5 and may create or reuse a `referencefile`, but the router does not return `referencefile_id`, MD5, or created/reused status. AI Curation therefore cannot populate `source_pdf_referencefile_id` directly from the upload response.

Required handling:

- After a successful upload, call `GET /reference/referencefile/by_md5/{md5sum}` and/or `GET /reference/referencefile/show_all/{reference_curie}` to reconcile the source `referencefile_id`.
- If this becomes noisy or ambiguous, ask Blue Team for an additive Literature response schema with `referencefile_id`, `md5sum`, `reference_curie`, and created/reused status.
- Do not design the AI Curation upload path as if `file_upload` already returns structured IDs.

For a normal PDF upload to ABC Literature, the likely metadata is:

- `file_class=main` or `supplement`
- `file_publication_status=final`
- `file_extension=pdf`
- `pdf_type=pdf`
- `is_annotation=false`
- `mod_abbreviation=<curator MOD/access>`
- `upload_if_already_converted=false` by default

### MD5 lookup schema

`GET /reference/referencefile/by_md5/{md5sum}` returns a list of `ReferencefileByMd5MatchSchema` entries:

- `referencefile_id`
- `reference_id`
- `reference_curie`
- `display_name`
- `file_class`
- `file_publication_status`
- `file_extension`
- `pdf_type`
- `md5sum`
- `is_annotation`
- `open_access`
- `copyright_license_name`
- `referencefile_mods`
- `converted_referencefiles`

`converted_referencefiles` entries include:

- `referencefile_id`
- `display_name`
- `file_class`
- `file_extension`

Important behavior from source:

- Source `main` maps to `converted_merged_main`.
- Source `supplement` maps to `converted_merged_supplement`.
- Source `nXML` maps to `converted_merged_main`.
- Derived rows are matched using display-name suffixes: `_merged`, `_grobid`, `_docling`, `_marker`, `_tei`, `_nxml`.
- A single MD5 can return multiple referencefiles if the same content is attached to multiple references.

### Conversion lifecycle schema

`GET /reference/referencefile/conversion_request/{curie_or_reference_id}` returns `ConversionStatusResponseSchema`.

Status values:

- `converted`: every convertible source has a converted Markdown row.
- `running`: a conversion job is active. HTTP 202.
- `failed`: the most recent conversion attempt failed. HTTP 200.
- `no_sources`: the reference has nothing convertible. HTTP 200.

Fields:

- `reference_curie`
- `status`
- `job_id`
- `error_message`
- `started_at`
- `completed_at`
- `converted_classes`
- `per_file_progress`
- `per_mod_status`

`per_file_progress` entries include:

- `source`: display name, file class, referencefile ID
- `converted`: expected or actual converted file info
- `figures`: extracted figure referencefiles, if present
- `status`: `pending`, `success`, or `failed`
- `error`

`per_mod_status` entries include:

- `mod_abbreviation`
- `pending_main_count`
- `pending_supplement_count`
- `main_converted`
- `all_converted`

Important conversion behavior from source:

- If everything is already converted, the endpoint returns `converted` without starting work.
- If only nXML is pending, conversion runs synchronously and can return `converted` immediately.
- If PDF conversion is needed, the default `wait=false` starts a background job and returns HTTP 202 `running`.
- Poll by calling the same `conversion_request` URL again.
- `converted_classes` can contain `converted_merged_main` while supplement conversion is still running or failed.
- `overwrite_tei_md=true` ignores and later removes legacy TEI-derived Markdown rows with `_tei` suffix.
- Permission logic treats open-access references as readable by anyone and allows MOD/global rows where `referencefile_mods.mod` is null.
- Conversion job manager state is in-process. The endpoint also synthesizes success entries from the DB, so callers should be robust to process restart and re-query `show_all`.

Important TEI cache trap:

Literature counts `_tei` rows as cached converted Markdown unless `overwrite_tei_md=true`. If a reference has only TEI-derived Markdown, `conversion_request` can report `converted` forever while AI Curation's preferred non-TEI selection finds no acceptable artifact.

Required handling:

- First release should treat "only TEI-derived Markdown exists" as an explicit unsupported/needs-conversion state, not as a generic failure.
- AI Curation should not set `overwrite_tei_md=true` by default because that mutates Literature state by replacing legacy TEI-derived rows.
- If Blue Team approves it, add a controlled admin/config path that calls `conversion_request(..., overwrite_tei_md=true)` for TEI-only references.
- If Blue Team does not approve automatic overwrite, the UI should report that ABC Literature only has legacy TEI-derived Markdown and cannot import the paper under the current policy.

## Current AI Curation Architecture

### Upload endpoint

The current PDF upload endpoint is:

`POST /api/weaviate/documents/upload`

Implementation:

- `backend/src/api/documents.py`
- Route: `upload_document_endpoint`
- It validates/authenticates, then delegates to `upload_intake_service.intake_upload(...)`.

### Intake service

Implementation:

- `backend/src/lib/pdf_jobs/upload_intake_service.py`
- Main method: `UploadIntakeService.intake_upload(...)`

Current behavior:

- Validates the upload filename as PDF.
- Saves the PDF under user-scoped local storage.
- Computes a raw checksum from the upload handler.
- Creates a scoped hash with `sha256(f"{db_user.id}:{raw_checksum}")`.
- Checks for duplicate local uploads by same user using scoped hash or raw checksum.
- Creates a Weaviate document.
- Creates a SQL `PDFDocument` row.
- Creates a durable PDF processing job.
- Dispatches the background upload execution.

This is local-first and user-scoped. It does not check ABC Literature MD5 before storing the PDF.

### Execution service

Implementation:

- `backend/src/lib/pdf_jobs/upload_execution_service.py`
- Main methods:
  - `dispatch_upload_execution(...)`
  - `execute_upload(...)`

Current behavior:

- Tracks job progress.
- Creates `DocumentPipelineOrchestrator`.
- Calls `orchestrator.process_pdf_document(...)`.
- Marks the durable job completed, failed, or cancelled.

### Pipeline orchestrator

Implementation:

- `backend/src/lib/pipeline/orchestrator.py`
- Main method: `DocumentPipelineOrchestrator.process_pdf_document(...)`

Current behavior:

1. Optional PDF validation.
2. PDF parsing through `parse_pdf_document(...)` from `pdfx_parser.py`.
3. Saves `pdfx_json_path` and `processed_json_path` into SQL.
4. Chunks parsed elements.
5. Resolves hierarchy metadata.
6. Embeds and stores chunks in Weaviate.
7. Updates statuses.

The hard dependency on PDFX is concentrated in the parse stage:

```python
from .pdfx_parser import parse_pdf_document
parse_result = await parse_pdf_document(...)
```

This is the best place to split the pipeline into:

- `process_pdf_document(...)`: legacy/direct PDFX path.
- `process_markdown_document(...)`: Literature converted Markdown path.

### PDFX parser and markdown adapter

Implementation:

- `backend/src/lib/pipeline/pdfx_parser.py`

Important existing helpers:

- `PDFXParser.parse_pdf_document(...)`
- `markdown_to_pipeline_elements(markdown: str) -> list[dict]`
- `_save_pdfx_json(...)`
- `_save_processed_json(...)`

Current PDFX parser already downloads merged Markdown from PDFX and then converts Markdown to pipeline element dictionaries. That means the Literature path does not need to invent a brand-new chunking model for the first release. It can:

1. Download converted Markdown from ABC Literature.
2. Feed it through a shared markdown-to-elements function.
3. Save processed JSON.
4. Skip `pdfx_json_path` or save a Literature import receipt JSON instead.
5. Continue through the existing chunking, hierarchy, embedding, and Weaviate storage stages.

Follow-up cleanup:

`markdown_to_pipeline_elements` should move out of `pdfx_parser.py` into a neutral module such as `backend/src/lib/pipeline/markdown_elements.py`, so Literature ingestion does not import a PDFX-specific module just to parse Markdown.

### SQL document metadata

Current model:

- `backend/src/models/sql/pdf_document.py`

Current fields:

- local filename/path/hash/size/page count
- upload timestamp/last accessed
- processing status/error/timestamps
- `pdfx_json_path`
- `processed_json_path`
- `hierarchy_metadata`
- `user_id`

The model does not have source system, ABC reference curie, referencefile IDs, PMID, DOI, MD5 provenance, or viewer capability flags.

### Weaviate document metadata

Current model:

- `backend/src/models/document.py`
- `DocumentMetadata`

Current fields:

- `page_count`
- `author`
- `title`
- `checksum`
- `document_type`
- `last_processed_stage`

The Weaviate metadata should receive a small provenance mirror so search results and document lists can identify Literature-sourced documents without always joining SQL.

### Frontend upload/document flow

Current frontend upload adapter:

- `frontend/src/features/documents/pdfUploadFlow.ts`
- `uploadPdfDocument(file)` posts to `/api/weaviate/documents/upload`.
- `waitForDocumentProcessing(documentId)` streams `/api/weaviate/documents/{document_id}/progress/stream`, with polling fallback.
- `loadDocumentForChat(documentId)` loads the document into the chat/PDF viewer context.

Current PDF viewer drag/drop:

- `frontend/src/components/pdfViewer/usePdfViewerUpload.ts`
- Uploads one dropped PDF.
- Waits for processing.
- Loads the result into chat.

Current Documents page:

- `frontend/src/pages/weaviate/DocumentsPage.tsx`
- Lists documents.
- Shows PDF jobs.
- Refreshes document list and job state.

Frontend impact:

The import UI should be added to Documents page first, then the PDF viewer drag/drop upload can be routed through the same Literature-aware backend after the backend contract is stable.

## Proposed Target Architecture

### Backend components

Add these backend pieces:

1. `LiteratureClient`
   - Typed async wrapper around ABC Literature REST.
   - Handles base URL, auth, timeouts, token caching, retries, and structured errors.
   - Methods:
     - `lookup_external_curie(curie)`
     - `lookup_cross_reference(curie_or_id)`
     - `search_references(query, query_fields, filters, pagination)`
     - `show_reference(curie_or_id)`
     - `add_pubmed_reference(pubmed_id, mod_mca, mod_curie)`
     - `lookup_referencefile_by_md5(md5)`
     - `show_referencefiles(reference_curie)`
     - `request_conversion(reference_curie, wait=False, overwrite_tei_md=False)`
     - `download_referencefile(referencefile_id)`
     - `upload_referencefile(...)`

2. `LiteratureImportService`
   - Owns AI Curation import workflow.
   - Resolves identifiers and references.
   - Selects canonical converted Markdown.
   - Starts and polls conversion.
   - Downloads converted Markdown.
   - Creates/updates AI Curation SQL/Weaviate records with provenance.
   - Dispatches the markdown ingestion pipeline.

3. `MarkdownDocumentIngestionService` or orchestrator method
   - Accepts converted Markdown plus provenance.
   - Converts Markdown to pipeline elements.
   - Saves `processed_json_path`.
   - Runs chunking, hierarchy, embedding, and storage.
   - Does not call PDFX.

4. `DocumentImportJob` handling
   - Short-term: reuse `pdf_processing_jobs` because the UI already understands job state, SSE, cancellation, and status panels.
   - Longer-term: rename/generalize to `document_processing_jobs` or add a `job_type` field (`pdf_upload`, `literature_import`).

5. ABC Markdown validation
   - The current AI Curation `markdown_to_pipeline_elements` helper is a simple line-oriented parser.
   - The ABC parser repo provides `read_markdown()` and `validate_markdown()` for ABC-format Markdown.
   - First implementation should decide explicitly:
     - preferred: validate downloaded ABC Markdown with `agr_abc_document_parsers.validate_markdown()` before ingestion, then convert to pipeline elements;
     - acceptable spike fallback: keep lossy markdown-to-elements parsing, but record that structured ABC Markdown semantics are not yet preserved.
   - Do not let this remain implicit, because extracted evidence quality may depend on section/table/list fidelity.

### Backend endpoints

Proposed endpoints:

| Endpoint | Purpose |
| --- | --- |
| `POST /api/weaviate/documents/import/literature/search` | Search ABC Literature references for the import picker. |
| `POST /api/weaviate/documents/import/literature` | Import one or more identifiers/reference curies. |
| `POST /api/weaviate/documents/import/literature/upload` | Upload a PDF using Literature-first MD5/file_upload flow. |
| `GET /api/weaviate/documents/import/literature/{job_id}` | Optional direct import-job detail if existing PDF job endpoints are not enough. |
| `GET /api/weaviate/documents/literature/lookup` | Optional preflight lookup for UI validation/preview. |
| `GET /api/weaviate/documents/literature/health` | Auth/config/connectivity health for admin diagnostics. |

Minimal first release can use:

- `POST /api/weaviate/documents/import/literature/search`
- `POST /api/weaviate/documents/import/literature`
- existing document status/progress endpoints
- existing PDF jobs panel, with labels generalized in the UI

Job/status compatibility note:

The current AI Curation pipeline is not stage-name agnostic everywhere. `ProcessingStage` is a fixed enum, and durable PDF job status values are constrained to the existing terminal/running states. The new Literature stages in this document are product-level names, not a statement that the current DB/API can already store those literal values.

Implementation choices:

- Short-term: keep durable `status` values to existing values (`pending`, `running`, `completed`, `failed`, `cancelled`) and map Literature progress into allowed `current_stage`/message values, or add a structured `details` payload for finer Literature state.
- If literal stages such as `literature_lookup` or `literature_conversion` are desired, first update `ProcessingStage`, durable job schemas, frontend stage mapping, SSE/polling contracts, and tests.
- Do not add `timeout` as a durable job status unless the SQL/API enum permits it. Represent timeout as `failed` with a retryable timeout error or migrate the status contract explicitly.

### Configuration

Add environment variables:

- `LITERATURE_INTEGRATION_ENABLED`
- `LITERATURE_API_BASE_URL`
- `LITERATURE_AUTH_MODE`
- `LITERATURE_BEARER_TOKEN`
- `LITERATURE_COGNITO_TOKEN_URL`
- `LITERATURE_COGNITO_CLIENT_ID`
- `LITERATURE_COGNITO_CLIENT_SECRET`
- `LITERATURE_COGNITO_SCOPE`
- `LITERATURE_REQUEST_TIMEOUT_SECONDS`
- `LITERATURE_CONVERSION_POLL_INTERVAL_SECONDS`
- `LITERATURE_CONVERSION_TIMEOUT_SECONDS`
- `LITERATURE_IMPORT_BATCH_LIMIT`
- `LITERATURE_IMPORT_ALLOW_CREATE_REFERENCE`
- `LITERATURE_IMPORT_DEFAULT_MOD_MCA`
- `LITERATURE_IMPORT_DEFAULT_MOD_CURIE`
- `LITERATURE_IMPORT_OVERWRITE_TEI_MD`
- `DOCUMENT_IMPORT_USE_LITERATURE_FOR_PDF_UPLOADS`
- `DOCUMENT_IMPORT_LEGACY_DIRECT_PDFX_ENABLED`

The auth mode should mirror the existing PDF extraction auth pattern:

- `none` for local fake service or explicitly public endpoints.
- `static_bearer` for local testing with a supplied token.
- `cognito_client_credentials` for service-to-service access.

Startup/health validation should say:

- Literature disabled.
- Literature base URL missing.
- Auth missing/misconfigured.
- OpenAPI/reachable but unauthorized.
- Basic read-only lookup/download endpoints reachable.

Health check guardrail:

Do not call `conversion_request` from routine startup or health endpoints. Despite being a GET endpoint, it can start conversion jobs when sources are pending. Conversion smoke checks belong in Phase 0 fixtures or manual stage validation with known test references.

### Provenance schema

Add nullable columns to `pdf_documents`:

- `source_system`: e.g. `local_pdf_upload`, `abc_literature`
- `source_reference_curie`
- `source_reference_id`
- `source_referencefile_id`
- `source_converted_referencefile_id`
- `source_pdf_referencefile_id`
- `source_pmid`
- `source_pmcid`
- `source_doi`
- `source_md5`
- `source_file_class`
- `source_file_extension`
- `source_conversion_status`
- `source_import_status`
- `source_imported_at`
- `source_payload_path`
- `source_markdown_path`
- `viewer_mode`: e.g. `local_pdf`, `literature_pdf_proxy`, `text_only`

Existing PDF-shaped contracts that must be handled in Phase 2:

- `backend/src/models/sql/pdf_document.py` currently requires `file_path`, `file_hash`, positive `file_size`, and positive `page_count`.
- `backend/src/models/document.py` currently requires `DocumentMetadata.page_count > 0`.
- `backend/src/lib/weaviate_client/documents.py` creates Weaviate documents through a PDF-shaped `PDFDocument` model.
- `backend/src/api/pdf_viewer.py` and download routes assume local PDF files under upload storage.

Phase 2 must update these contracts before text-only Literature imports are enabled. This is not a later polish item.

Keep existing fields:

- `file_path`: for local uploaded PDF when present.
- `pdfx_json_path`: legacy/direct PDFX metadata.
- `processed_json_path`: still useful for both paths.

For Literature-only imports with no local PDF:

- Either make `file_path`, `file_hash`, `file_size`, and `page_count` nullable/relaxed in a migration, or create placeholder values. Prefer relaxing the model over lying with placeholders.
- If relaxation is too much for the first slice, store the downloaded Markdown as the file artifact and set `document_type=abc_literature_markdown`, but do not pretend it is a PDF.
- Update PDF viewer/download contracts so `viewer_mode=text_only` documents return a clear "PDF unavailable" response instead of a broken local-file path.

Weaviate document metadata mirror:

- `source_system`
- `source_reference_curie`
- `source_referencefile_id`
- `source_converted_referencefile_id`
- `source_pmid`
- `source_doi`
- `source_md5`
- `viewer_mode`

Do not store large Literature API payloads directly in Weaviate metadata. Save full payloads to storage and store paths/IDs.

## Proposed Flows

### Flow A: Identifier import

Inputs:

- One or more identifiers: PMID, PMCID, DOI, ABC curie.
- Optional MOD context for adding references or upload ownership.

Steps:

1. Normalize identifiers.
   - `39671436` -> `PMID:39671436` if numeric and configured as PMID default.
   - `PMID39671436` -> `PMID:39671436`.
   - `AGRKB:...` remains ABC curie.
   - `PMCID:...` and DOI are attempted through cross-reference lookup unless Valerio confirms a better endpoint.

2. Resolve reference.
   - For PMID: `GET /reference/external_lookup/PMID:{id}`.
   - For ABC curie: `GET /reference/{curie}`.
   - For PMCID/DOI/cross-reference: `GET /reference/by_cross_reference/{value}` if indexed.
   - If missing and policy allows creation: `POST /reference/add/`.

3. Fetch file list.
   - `GET /reference/referencefile/show_all/{reference_curie}`.

4. Select converted Markdown.
   - Prefer `converted_merged_main` with `file_extension=md` and `file_publication_status=final`.
   - Accept global/null MOD rows as usable.
   - Prefer source order:
     1. nXML-derived `_nxml` converted main.
     2. PDFX merged `_merged` converted main.
     3. Other converted main rows only if explicitly configured.
   - Do not use TEI-derived `_tei` as canonical unless the rollout explicitly enables legacy fallback.

5. If no converted main Markdown exists:
   - Call `conversion_request/{reference_curie}?wait=false&overwrite_tei_md=<flag>`.
   - If `running`, update AI Curation job state from `per_file_progress` and poll.
   - If `converted` and `converted_merged_main` is present, continue.
   - If `failed` but `converted_classes` contains `converted_merged_main`, allow partial-main import and surface supplement failures as warnings.
   - If `no_sources`, fail the import with a specific curator-facing message.

6. Download converted Markdown.
   - `GET /reference/referencefile/download_file/{converted_referencefile_id}`.

7. Create or reuse AI Curation document.
   - Deduplicate by `(user_id, source_system, source_reference_curie, source_converted_referencefile_id)`.
   - Optionally dedupe by `source_md5` when known.

8. Ingest Markdown.
   - Convert to pipeline elements.
   - Save `source_payload_path`, `source_markdown_path`, and `processed_json_path`.
   - Chunk, resolve hierarchy, embed, store in Weaviate.

9. Return document/job result.

### Flow A2: Search and select import

Inputs:

- Search text.
- Optional field scope: all fields, title, author, citation, abstract, keyword, ORCID, curie, cross-reference.
- Optional date/year filter.
- Optional facet filters if we decide to expose them.

Steps:

1. AI Curation calls `POST /search/references/` through `LiteratureClient.search_references(...)`.
2. UI shows returned `hits` with title, authors, date, citation, curie, cross-references, and highlights.
3. User selects one or more references.
4. AI Curation imports selected papers by `curie` using Flow A from the file-list/conversion step onward.
5. If search returns an Elasticsearch/reindexing error, show that the Literature search index may be rebuilding and allow direct PMID/curie entry as fallback.

Recommended first UI fields:

- Search text.
- Field selector: All, Title, Author, Xref.
- Optional published-year range after the basic path works.

Do not trigger conversion from search results. Selection should be a separate explicit action.

### Flow B: PDF upload through ABC Literature

Inputs:

- PDF file.
- Optional PMID/PMCID/DOI/ABC curie.
- Optional file classification: main vs supplement. Default to `main` for single upload.

Steps:

1. Validate file type and size.
2. Compute raw MD5 from uploaded bytes before local PDFX work.
3. Call `GET /reference/referencefile/by_md5/{md5}`.

If MD5 matches exactly one source with converted Markdown:

4. Select converted main Markdown from `converted_referencefiles`.
5. Download Markdown.
6. Create AI Curation document with provenance.
7. Ingest Markdown.

If MD5 matches exactly one source without converted Markdown:

4. Call `conversion_request/{reference_curie}`.
5. Poll until converted, failed, or no_sources.
6. Download and ingest converted main Markdown when available.

If MD5 matches multiple references:

4. If user supplied a reference identifier and one match belongs to it, select that match.
5. Otherwise return an ambiguity response requiring curator selection.
6. Do not guess based on title/filename alone.

If MD5 has no match:

4. Require a known reference before uploading to ABC Literature.
5. If the user supplied a PMID/reference, resolve or create the reference.
6. Upload the file to Literature:
   - `POST /reference/referencefile/file_upload/`
   - Include metadata listed above.
7. Reconcile the uploaded source file ID by calling `by_md5/{md5}` and/or `show_all/{reference_curie}` because `file_upload` returns only `success`.
8. Call `conversion_request/{reference_curie}`.
9. Poll and ingest converted Markdown when ready.

Important policy:

Pure PDF upload with no identifier and no MD5 match cannot safely become an ABC Literature reference without more metadata. The UI should ask the curator for a PMID/reference or offer a temporary legacy direct-PDFX path if enabled.

Upload-to-Literature caveats:

- Literature can reject uploads for references outside the selected MOD corpus.
- Literature can reject a final main PDF when text is already converted unless `upload_if_already_converted=true`.
- The ABC UI bulk-upload path currently uses `upload_if_already_converted=True`, while the per-file upload path only sends it after user confirmation.
- AI Curation should not silently overwrite or add redundant source PDFs when converted main text already exists. It should prefer reusing the existing converted Markdown and only upload the PDF when the curator is intentionally adding the file to Literature.
- For a reference that exists but already has converted main text from another source file, first-release behavior should be "reuse converted Markdown; do not upload new source PDF" unless Blue Team asks for archival upload.

### Flow C: Existing direct local PDFX upload

Keep as fallback during rollout:

- Existing `/documents/upload` endpoint can stay direct-PDFX when `DOCUMENT_IMPORT_USE_LITERATURE_FOR_PDF_UPLOADS=false`.
- When the feature flag is true, route upload through Flow B.
- Add a clear fallback setting for local development or Literature outage:
  - `DOCUMENT_IMPORT_LEGACY_DIRECT_PDFX_ENABLED=true`

Curator-facing language should distinguish:

- "Found converted paper in ABC Literature."
- "ABC Literature is converting this paper."
- "ABC Literature has no convertible source."
- "This PDF is not known to ABC Literature. Add a PMID/reference to save and convert it there."
- "Using legacy local PDF extraction fallback."

## Status Mapping

Map Literature conversion status to AI Curation job stages:

| Literature state | AI Curation stage | Progress | Message |
| --- | --- | --- | --- |
| lookup starting | `literature_lookup` | 5 | Looking up paper in ABC Literature |
| reference found | `literature_lookup` | 15 | Found ABC Literature reference |
| MD5 matched | `literature_lookup` | 20 | Found matching file in ABC Literature |
| upload to Literature | `literature_upload` | 25 | Saving PDF in ABC Literature |
| `running` | `literature_conversion` | 30 to 55 | ABC Literature is converting this paper |
| per-file pending | `literature_conversion` | 35 | Waiting for PDFX conversion |
| per-file success | `literature_conversion` | 50 | Converted Markdown is available |
| `converted` | `literature_download` | 60 | Downloading converted Markdown |
| `failed` with main converted | `literature_download` plus warning | 60 | Main text converted; some files failed |
| `failed` with no main converted | `failed` | 100 | ABC Literature conversion failed |
| `no_sources` | `failed` | 100 | ABC Literature has no convertible source |
| markdown parsed | `chunking` | 65 | Preparing document chunks |
| chunks created | `embedding` | 75 | Generating embeddings |
| stored | `storing` | 90 | Storing document |
| complete | `completed` | 100 | Document ready |

Cancellation:

- AI Curation can stop polling and mark its import job cancelled.
- Literature conversion currently has no cancel endpoint in the reviewed contract. Do not imply cancellation stops ABC/PDFX work unless a Literature cancel endpoint is added later.

Timeout:

- If Literature remains `running` past AI Curation timeout, mark the local job as `timeout` or `failed` with a retryable message.
- A retry should call `conversion_request` again and then `show_all`; it should not re-upload the file by default.

## Converted Markdown Selection Rules

Canonical first-release selection:

1. `file_class == "converted_merged_main"`
2. `file_extension == "md"`
3. `file_publication_status == "final"`
4. Accessible by open access, caller MOD, developer/all access, or global/null MOD association.
5. Prefer non-TEI-derived rows.
6. Prefer `_nxml` over `_merged` only if both are present and Valerio confirms nXML-derived Markdown is preferred.
7. If multiple candidates remain, choose newest only if all belong to the same source, otherwise require deterministic selection logic or curator choice.

Supplement policy:

- First release: ingest main converted Markdown only.
- Later: optionally append supplement converted Markdown as separate documents or child sections if curation workflows need it.
- Supplement conversion failures should not block main import.

TEI policy:

- Do not use `tei` as canonical display/import artifact.
- `overwrite_tei_md=false` initially to avoid mutating Literature data unexpectedly.
- Add an admin/config-only option for `overwrite_tei_md=true` after Valerio confirms desired behavior.

## Artifact And Viewer Strategy

There are three different artifacts:

1. Source PDF in ABC Literature.
2. Converted Markdown in ABC Literature.
3. AI Curation processed JSON/chunks.

First release:

- For identifier import, ingest text-only converted Markdown.
- Set `viewer_mode=text_only` unless we also download/proxy the source PDF.
- Evidence search and extraction should work from text chunks.
- PDF coordinate highlighting should be disabled or clearly unavailable for text-only imports.
- Download dialog should offer processed JSON and provenance, not a fake local PDF.

For user-uploaded PDFs:

- Keep local uploaded PDF only when the curator actually uploaded it through AI Curation.
- Set `viewer_mode=local_pdf` for those records, so the existing PDF viewer still works.
- Store ABC provenance if the upload matched or was saved to Literature.

Later options:

- Add `viewer_mode=literature_pdf_proxy` and proxy `download_file/{source_pdf_referencefile_id}` through AI Curation.
- Add image/figure manifest support after KANBAN-1228 is stable.
- Add page/text coordinate mapping only if ABC/PDFX exposes reliable page anchors in Markdown or a sidecar format.

## Frontend Design

### Documents page

Add a paper discovery/import control above or beside the current upload affordance:

- Search mode lets curators type title, author, citation text, year/date filters, PMID/PMCID/DOI/cross-reference, or ABC curie.
- Search results come from ABC Literature `POST /search/references/`.
- Identifier mode accepts comma-separated identifiers.
- Supports up to `LITERATURE_IMPORT_BATCH_LIMIT` identifiers, default 10.
- Shows one row per requested paper:
  - Identifier
  - ABC reference curie
  - PMID/DOI when known
  - State
  - Message
  - Result document link
  - Retry/action button

States:

- looking up
- found
- already converted
- conversion queued/running
- downloading
- ingesting
- ready
- failed
- needs reference selection
- needs PMID/reference

Do not make the Documents page a marketing or explanatory page. It should stay a work surface for managing documents.

### Upload UX

Current upload flow posts directly to `/api/weaviate/documents/upload`. Under the feature flag:

- Upload starts with MD5 lookup.
- If found in Literature, say it matched ABC Literature and skip local PDFX.
- If conversion is running, show Literature conversion progress.
- If not found and no reference is supplied, ask for PMID/reference or offer legacy extraction if enabled.
- If not found and reference is supplied, upload to Literature and convert there.

### Document list/provenance display

Update document list/details/download dialog to show:

- Source: Local upload or ABC Literature.
- ABC reference curie.
- PMID/DOI if available.
- Source file ID and converted file ID where useful.
- Viewer availability: PDF available, Literature PDF, or text only.

The UI should not surface raw implementation terms like "conversion_request" to curators.

## Other Client Compatibility

ABC Literature UI already uses:

- `POST /reference/referencefile/file_upload/`
- `GET /reference/referencefile/show_all/{curie}`
- `GET /reference/referencefile/download_file/{referencefile_id}`
- Converted file display classes:
  - `converted_merged_main`
  - `converted_grobid_main`
  - `converted_docling_main`
  - `converted_marker_main`
  - `converted_merged_supplement`
  - `converted_grobid_supplement`
  - `converted_docling_supplement`
  - `converted_marker_supplement`
- Access checks that treat `referencefile_mods.mod_abbreviation === null` as accessible/global.

This migration should not break ABC Literature UI because it consumes existing endpoints and does not require changing their shape.

If PDFX itself changes:

- Keep changes additive.
- Do not remove or rename existing fields.
- Keep old status values working.
- Add new fields like queue position, active run count, or worker state as optional data.
- Coordinate with ABC Literature Service before requiring any new PDFX status semantics.

If Literature API changes:

- Add optional fields to existing response schemas rather than changing required fields.
- Version or feature-detect new behavior if AI Curation depends on it.
- Open a matching ABC Literature UI PR only if UI behavior needs to adapt.

## Implementation Plan

### Phase 0: Contract smoke and fixtures

Goal:

Verify the exact Literature API behavior in stage/prod and capture fixtures.

Work:

- Add a small local script or test helper to call:
  - `search/references`
  - `external_lookup`
  - `by_cross_reference`
  - `show_all`
  - `by_md5`
  - `conversion_request` only with explicit known test references, because it can start work
  - `download_file`
- Capture non-secret JSON fixtures for:
  - already converted main Markdown
  - running conversion
  - failed conversion
  - no sources
  - MD5 match with converted referencefile
  - MD5 match with multiple references
- Confirm auth mode needed for stage/prod.
- Confirm PMCID and DOI lookup path with Valerio.
- Confirm preferred converted Markdown source order (`_nxml` vs `_merged`).

Exit criteria:

- We can download converted Markdown for at least one known reference.
- We know whether PMCID/DOI can be supported in the first release.
- We know whether `download_file` returns `application/octet-stream`, `text/markdown`, or only blob bytes.

### Phase 1: Literature client

Goal:

Create the backend integration boundary.

Files likely touched:

- New `backend/src/lib/literature/client.py`
- New `backend/src/lib/literature/models.py`
- New `backend/src/lib/literature/errors.py`
- Maybe `backend/src/api/admin/connections.py` or a new health endpoint
- Tests under `backend/tests/unit/lib/literature/`

Work:

- Implement async HTTP client with timeouts.
- Implement auth header builder.
- Parse response schemas into typed models or typed dictionaries.
- Preserve raw payload for diagnostics without logging secrets.
- Add fake-service tests from KANBAN-1242.

Exit criteria:

- Unit tests cover lookup, MD5 match, conversion polling, download, upload, auth failure, 404, 422, timeout.

### Phase 2: Provenance schema

Goal:

Persist ABC Literature source details.

Files likely touched:

- `backend/src/models/sql/pdf_document.py`
- New Alembic migration under `backend/alembic/versions/`
- `backend/src/models/document.py`
- `backend/src/lib/weaviate_client/documents.py`
- `backend/src/api/pdf_viewer.py`
- document download/info endpoints in `backend/src/api/documents.py`
- Contract tests for document list/detail/download metadata

Work:

- Add nullable provenance columns.
- Relax PDF-only constraints if text-only Literature imports are allowed.
- Update document creation models so text-only Literature documents are first-class records, not fake PDFs.
- Update PDF viewer/download behavior so text-only records return clear unavailable responses for PDF-specific requests.
- Add indexes:
  - `(user_id, source_system, source_reference_curie)`
  - `(user_id, source_system, source_converted_referencefile_id)`
  - maybe `(source_md5)`
- Mirror small provenance fields into Weaviate document metadata.

Exit criteria:

- Existing local PDF upload still passes tests.
- A synthetic Literature document can be stored and listed with provenance.

### Phase 3: Markdown ingestion path

Goal:

Ingest ABC converted Markdown without PDFX.

Files likely touched:

- `backend/src/lib/pipeline/orchestrator.py`
- `backend/src/lib/pipeline/pdfx_parser.py`
- New `backend/src/lib/pipeline/markdown_elements.py`
- New `backend/src/lib/pipeline/markdown_ingestion.py` or method on orchestrator
- `backend/tests/unit/pipeline/`

Work:

- Move `markdown_to_pipeline_elements` to a neutral module.
- Decide whether to validate downloaded Markdown with `agr_abc_document_parsers.validate_markdown()` before ingestion.
- If validation is not included in the first implementation, document the accepted lossiness and add fixtures that show the fallback parser preserves enough headings/tables/lists for evidence search.
- Add `process_markdown_document(...)`.
- Save source payload/Markdown if paths are configured.
- Save processed JSON.
- Reuse chunking/hierarchy/embedding/storage.

Exit criteria:

- Unit test ingests sample ABC Markdown into chunks without PDFX.
- Existing PDFX parser tests still pass.

### Phase 4: Identifier import service and API

Goal:

Implement `POST /api/weaviate/documents/import/literature`.

Files likely touched:

- `backend/src/api/documents.py` or new `backend/src/api/document_imports.py`
- `backend/src/lib/pdf_jobs/service.py` or new document job service
- New `backend/src/lib/documents/literature_import_service.py`
- `backend/tests/unit/api/`
- `backend/tests/contract/`

Work:

- Accept batch identifiers, max 10 by default.
- Resolve references.
- Select or trigger converted Markdown.
- Create a durable job per paper or batch item.
- Stream progress through existing status/SSE mechanisms.
- Deduplicate per user and source converted referencefile.

Exit criteria:

- Given a fake already-converted Literature reference, API returns a document/job and the document becomes searchable.
- Given a fake running conversion, API reports running and completes after polling.
- Given no sources, API returns a specific failure.

### Phase 5: Documents page import UI

Goal:

Expose identifier import to curators.

Files likely touched:

- `frontend/src/pages/weaviate/DocumentsPage.tsx`
- New component under `frontend/src/components/weaviate/`
- `frontend/src/services/weaviate.ts`
- `frontend/src/features/documents/`
- Tests under frontend unit/integration suites

Work:

- Add identifier input and import table.
- Reuse existing job polling/SSE where possible.
- Show per-paper progress and partial failures.
- Show provenance in document list/details.

Exit criteria:

- Curator can paste a PMID, import it, see progress, and open resulting document.

### Phase 6: PDF upload through ABC

Goal:

Route PDF uploads through MD5 lookup and ABC save/convert when appropriate.

Files likely touched:

- `backend/src/lib/pdf_jobs/upload_intake_service.py`
- New upload adapter or branch in `LiteratureImportService`
- `frontend/src/features/documents/pdfUploadFlow.ts`
- `frontend/src/components/pdfViewer/usePdfViewerUpload.ts`
- Upload and duplicate tests

Work:

- Add a hash-first upload adapter rather than reusing the current intake choreography unchanged.
- Spool/hash the upload stream enough to compute MD5, then seek/reset for later upload or local storage.
- Compute MD5 before local PDF storage, SQL document creation, Weaviate document creation, or direct PDFX.
- Call `by_md5`.
- Handle exact match, multiple matches, no match.
- For no match, require or collect reference context.
- Upload to ABC Literature when allowed.
- Reconcile source `referencefile_id` after upload because `file_upload` currently returns only `success`.
- Fall back to direct PDFX only when configured.

Exit criteria:

- Same-PDF upload that already exists in ABC imports converted Markdown without calling direct PDFX.
- Unknown PDF asks for reference context or uses configured legacy fallback.

### Phase 7: Rollout and cleanup

Goal:

Make Literature import the default and reduce direct PDFX UI prominence.

Work:

- Enable feature flag in sandbox/stage.
- Add dashboard/logging around import outcomes.
- Update curator/developer docs.
- Confirm ABC Literature/Blue Team are comfortable with traffic and statuses.
- Decide whether direct local PDFX remains hidden admin fallback or is removed.

Exit criteria:

- Curators can import by PMID/reference and upload known PDFs through ABC.
- Existing direct PDFX path is no longer the primary user journey.

## Minimal Vertical Slice

If we want something testable quickly, implement this subset:

Scope:

- Identifier import only.
- One identifier at a time or batch max 10.
- PMID and ABC curie support.
- Optional search/select picker backed by `POST /search/references/`, importing selected `curie` values through the same path.
- Main `converted_merged_main` Markdown only.
- Fake Literature service tests.
- Text-only viewer mode.
- No PDF upload changes yet.
- No PMCID/DOI guarantee yet.
- No supplement ingestion.
- No Literature file upload yet.

Why this slice:

- Avoids ambiguous MD5 matching and reference-creation policy.
- Avoids local PDF artifact/viewer decisions.
- Exercises the most important path: ABC Literature already owns the paper and converted Markdown.
- Proves the markdown ingestion split from PDFX.
- Avoids upload-to-ABC policy questions around MOD corpus membership, already-converted source files, and upload return IDs.

Expected touched areas:

- Literature client
- Provenance columns
- Markdown ingestion path
- New import endpoint
- Documents page import control
- Document list provenance

Mandatory preconditions:

- Phase 0 confirms a non-TEI converted main Markdown fixture can be downloaded from Literature.
- Phase 2 handles text-only document contracts or the vertical slice stores a real Markdown artifact without pretending it is a PDF.
- TEI-only references are explicitly reported as unsupported or conversion-required under the current policy.

## Testing Plan

Backend unit tests:

- Literature client:
  - lookup success/failure
  - MD5 match no/multiple/exact matches
  - conversion statuses
  - download bytes
  - upload metadata
  - auth/token caching
  - timeout/retryable errors
- Literature import service:
  - already converted
  - running then converted
  - failed no main
  - failed with main converted
  - no sources
  - duplicate document
  - global/null MOD access
- Markdown ingestion:
  - ABC Markdown to elements
  - no usable elements
  - sections/tables/lists
  - processed JSON path saved

Backend contract tests:

- Import endpoint request/response.
- Progress/status endpoint for Literature stages.
- Document list/detail includes provenance.
- Download info behaves correctly for text-only Literature imports.

Frontend tests:

- Identifier input validation and batch limit.
- Per-paper progress rows.
- Partial failure display.
- Document list provenance.
- Upload flow messaging for Literature match/no match.

Integration/live smoke:

- Stage Literature known PMID with existing converted main Markdown.
- Stage Literature known reference requiring conversion.
- Stage Literature MD5 match for known PDF.
- Unauthorized/missing Literature auth.
- Literature timeout/retry.

Do not make live PDFX conversion a default PR test. Keep it manual or stage-smoke because it is slow, expensive, and dependent on GPU worker availability.

## Logging And Observability

Backend logs should include structured fields:

- `operation`: `literature_lookup`, `literature_md5_lookup`, `literature_conversion_poll`, `literature_download`, `literature_import`
- `document_id`
- `job_id`
- `user_id` or safe internal user key
- `identifier`
- `reference_curie`
- `source_referencefile_id`
- `converted_referencefile_id`
- `conversion_status`
- `duration_ms`
- `retry_count`
- `http_status`
- `error_type`

Do not log:

- bearer tokens
- client secrets
- full PDF contents
- full Markdown content

Useful metrics:

- imports by source (`abc_literature`, `local_pdf_upload`)
- conversion statuses
- Literature API latency
- import duration
- direct PDFX fallback count
- MD5 exact/multiple/no match counts
- duplicate imports avoided

## Risks And Open Questions

### PMCID/DOI support

Current `external_lookup` code supports PMID-like prefixes only. PMCID and DOI likely require `by_cross_reference`, but we need confirmation that Literature stores those cross-references consistently.

Decision needed:

- First release supports PMID and ABC curie only, or
- First release includes PMCID/DOI with verified fixture coverage.

### Upload with no reference

ABC Literature `file_upload` requires `reference_curie`. A PDF with no MD5 match and no identifier cannot be attached safely.

Decision needed:

- Require PMID/reference for new upload-to-ABC.
- Keep legacy local PDFX fallback for no-reference uploads.
- Or add a Literature-side workflow for unassigned uploaded PDFs, if Blue Team wants that.

### Multiple MD5 matches

The MD5 endpoint can return multiple references. AI Curation must not guess in ambiguous cases.

Decision needed:

- If user supplied reference context, pick the matching reference.
- Otherwise require curator selection in UI.

### Text-only viewer

Identifier import may have converted Markdown without a local PDF. Existing AI Curation UI assumes PDF download/viewer in a few places.

Decision needed:

- Text-only mode in first release.
- Literature PDF proxy later.
- Or download/cache source PDF for every import.

Recommendation:

Start text-only for identifier import. Add proxy/cache only when evidence navigation needs it.

### SQL constraints

`pdf_documents` currently requires `file_path`, `file_hash`, positive `file_size`, and positive `page_count`. Literature-only text imports do not naturally satisfy those fields.

Decision needed:

- Relax the table and rename/generalize later, or
- Store downloaded Markdown as the artifact and set meaningful text artifact values.

Recommendation:

Relax/generalize enough now to avoid fake PDF metadata. A future rename from `pdf_documents` to `documents` can be a separate cleanup.

### Conversion manager durability

Literature conversion job state is in-process, while converted rows are durable in the Literature DB/S3. AI Curation polling should recover by re-checking `conversion_request` and `show_all`, not by assuming a `job_id` will always be enough after service restart.

### Permissions

AI Curation service credentials need enough Literature access to:

- read reference files
- download converted Markdown
- upload PDFs if enabled
- trigger conversion

But it should not need broad write/admin permissions beyond file upload/reference add unless explicitly required.

### ABC embeddings overlap

Blue Team has ABC embeddings work in flight. This AI Curation migration should not wait for it, but future architecture may reuse ABC embeddings instead of re-embedding documents in AI Curation. Store provenance now so those systems can converge later.

## Questions For Valerio / Blue Team

1. Is `GET /reference/by_cross_reference/{value}` the intended path for PMCID and DOI, or should AI Curation only promise PMID/ABC curie first?
2. What `mod_mca` and `mod_curie` should AI Curation send to `POST /reference/add/` when a PMID is not yet in Literature?
3. For converted main Markdown selection, should `_nxml` beat `_merged`, or should `converted_merged_main` newest/final be enough?
4. Should AI Curation ever pass `overwrite_tei_md=true`, or should that remain a Literature admin/manual action?
5. For no-MD5-match upload, should AI Curation require a reference first, or is there an ABC workflow for unattached PDFs?
6. Is service-to-service Cognito client credentials the intended auth mechanism for AI Curation to call Literature?
7. Should AI Curation download/cache source PDFs for identifier imports, or is text-only import acceptable for the first release?
8. Are supplement Markdown files needed for AI extraction in 0.7, or can main text be the first release target?
9. Should partial conversion with main success and supplement failure be surfaced as "ready with warnings"?
10. Are there rate limits or queue expectations for AI Curation-triggered conversion requests?

## Final Assessment

This migration makes sense and is probably the better next move than polishing the current local uploader. The existing AI Curation pipeline can be split cleanly because PDFX already hands the app Markdown before chunking. ABC Literature already has the missing contracts: MD5 lookup, reference lookup, converted file listing, conversion request/polling, and file download/upload.

The main work is not PDF extraction. It is product and data-contract work:

- how users identify papers,
- how we handle unknown PDFs,
- how provenance is stored,
- how text-only imports behave in a PDF-oriented UI,
- and how we report Literature conversion states faithfully.

The highest-confidence first slice is PMID/ABC-curie import to converted main Markdown, with provenance and text-only viewer mode. That gives curators the new model quickly, avoids the ambiguous upload cases, and gives us a real end-to-end path to validate before changing the PDF upload default.
