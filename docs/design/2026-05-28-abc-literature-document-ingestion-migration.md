# ABC Literature Document Ingestion Migration

Date: 2026-05-28

Status: design assessment

Owner context: AI Curation / ABC Literature / PDFX

> Implementation hold: do not start the ABC Literature ingestion cutover until Blue Team confirms or extends the Literature PDF/Markdown access-control contract. AI Curation needs user/group-scoped authorization for read-only search, MD5 lookup, file listing, converted artifact discovery, source/converted downloads, and any status endpoint it consumes.
>
> Read-only rule: AI Curation must never upload, edit, add, or otherwise mutate ABC Literature. The integration may only read existing Literature references/files/artifacts. That excludes `POST /reference/add/`, `POST /reference/referencefile/file_upload/`, `overwrite_tei_md=true`, and any conversion endpoint call that can create jobs or new Literature files.

## Executive Summary

AI Curation should replace its local PDF/PDFX document ingestion path with ABC Literature as the single canonical paper and file source. This is a hard cutover, not parallel operation. The Documents page, PDF viewer drop/import path, backend import endpoint, ingestion pipeline, job reporting, provenance, and tests all move to a read-only Literature-owned model in the same release.

1. Resolve a paper by PMID or ABC reference curie first; support PMCID/DOI only after their Literature cross-reference contracts are verified with fixtures.
2. Ask ABC Literature whether converted Markdown already exists.
3. If converted Markdown exists, download that file and ingest it directly into AI Curation.
4. If converted Markdown does not already exist, stop with an actionable message that the paper must be processed in ABC Literature first.
5. For locally supplied PDFs, compute MD5 transiently and use ABC Literature only as a read-only existence/authorization lookup. If the MD5 is unknown or has no converted Markdown, do not store, upload, extract, or convert it in AI Curation.
6. Store source/provenance metadata in AI Curation so curators can see the ABC reference, source file, converted file, and import state.

The direct AI Curation-to-PDFX path should be removed from product behavior. PDFX remains part of the system only behind ABC Literature workflows that happen outside AI Curation. If a paper or local PDF cannot be resolved to an existing authorized Literature converted Markdown artifact under the rules below, AI Curation should stop with an actionable message instead of doing local extraction or mutating Literature.

## Recommendation

Do a single replacement release that removes local direct-PDFX ingestion and makes every document import provider-backed. The Alliance deployment's required provider is ABC Literature.

Cutover requirements:

1. Add a backend `DocumentSourceProvider` interface, an ABC Literature adapter, plus fake-provider and adapter contract tests.
2. Replace the legacy local upload intake choreography with a read-only provider import service.
3. Add SQL provenance columns and mirror the most important provenance into Weaviate document metadata.
4. Add markdown ingestion that reuses chunking, hierarchy, embedding, and storage without calling PDFX.
5. Add search/select, identifier import, MD5 lookup, read-only file listing, converted Markdown download, and read-only status display in the same release.
6. Replace the Documents page upload UX with a paper discovery/import work surface plus a local-PDF MD5 lookup path that imports only existing authorized Literature artifacts.
7. Remove curator-facing and backend product paths that submit PDFs directly from AI Curation to PDFX or mutate ABC Literature.

Do not require any breaking PDFX API changes for this migration. If AI Curation needs conversion visibility, it should consume a non-mutating Literature status/listing contract. It must not trigger Literature conversion as part of import.

## Organization-Agnostic Boundary

This migration must preserve the same boundary used by the rest of the project: shared runtime code stays organization-agnostic, while Alliance-specific contracts live in package/config-owned adapters.

Cutover shape:

- Core document ingestion should depend on a configured `DocumentSourceProvider` interface, not directly on ABC Literature classes or MOD-specific assumptions.
- The Alliance deployment should configure exactly one required provider for this cutover: `abc_literature`.
- `abc_literature` is the provider adapter that knows ABC endpoint names, `referencefile` semantics, MOD MCA/curie fields, TEI policies, and `converted_merged_main` selection.
- Shared SQL/API/Weaviate models should use provider-neutral field names such as provider key, provider reference ID, provider source file ID, provider converted artifact ID, external IDs, and source payload path.
- Group/MOD context should come from deployment config and authenticated group mapping, not hardcoded `FB`, `WB`, `MGI`, `SGD`, `ZFIN`, `RGD`, or `HGNC` literals in core code.
- If a user belongs to more than one group, the UI must make the read-access context explicit before importing restricted Literature artifacts.
- Generic tests should use a fake non-Alliance document-source provider fixture. Alliance tests should cover the `abc_literature` adapter and real ABC response shapes.

This is not an alternate-path system. The cutover still requires one configured document-source provider. The point is that the required provider is selected by deployment/package config rather than baked into core runtime behavior.

## Evidence Gathered

### Local repositories

Repositories inspected:

- AI Curation: `/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation`
- ABC Literature Service fresh clone: `/tmp/ai-curation-pdfx-migration-review-agr_literature_service`
- ABC Literature UI: `/home/ctabone/programming/claude_code/analysis/alliance/agr_literature_ui`
- ABC document parser package: `/home/ctabone/programming/claude_code/analysis/alliance/agr_abc_document_parsers`

This assessment uses local checkouts plus the reviewed Jira/KANBAN work as implementation evidence.

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
- `KANBAN-1246`: Configure Literature integration base URLs and auth. The replacement plan intentionally drops direct-PDFX behavior.
- `KANBAN-1040`: Verify ABC Literature OpenAPI, auth, checksum lookup contract.
- `KANBAN-1041`: Earlier upload-oriented ticket. Superseded for this design by the read-only rule: keep only MD5 existence lookup/import of existing Literature artifacts.
- `KANBAN-1042`: Ingest ABC Literature converted Markdown into AI Curation Weaviate documents.
- `KANBAN-1063`: Add backend ABC Literature service client/config/auth/health.
- `KANBAN-1064`: Confirm canonical converted Markdown retrieval contract.
- `KANBAN-1065`: Verify ABC Literature to PDFX handoff.

Related Blue Team / Valerio context:

- `SCRUM-6122`: Classifier Markdown downloader had rejected global converted rows where `referencefile_mods` contained `mod_abbreviation: null`. AI Curation must treat null/global rows as usable.
- `SCRUM-6125`: Decommission the retired GROBID service because PDFX now handles pdf2md conversions.
- `SCRUM-6136`: Historical data artifact around no eligible main PDF for target MOD. Not an ongoing AI Curation blocker, but it reinforces that Literature conversion status can mean "no source for this MOD".
- `SCRUM-6101`: Remove TEI from Literature file-management display. AI Curation should prefer `converted_merged_*` Markdown and not build a TEI-first path.
- `SCRUM-5868`: Ongoing Markdown schema refinement.
- `KANBAN-1327`: Continue processing when one extraction method fails. AI Curation should report per-file failures and consume main Markdown when available rather than treating supplement failure as total failure.
- `KANBAN-1336`: Improve AI Curation guardrails for extraction failures and empty-result ambiguity.

Important Jira implication:

The migration should not be framed as "AI Curation uploads a PDF to PDFX differently." It should be framed as "AI Curation reads Literature paper/file/artifact state, and Literature-owned workflows handle PDFX outside AI Curation."

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

The local "GPU is spinning up" confusion was a reporting mismatch, not proof that PDFX was hung. The read-only replacement should avoid exposing AI Curation's direct PDFX worker lifecycle entirely. If Literature eventually exposes non-mutating conversion/status fields, AI Curation can display them as Literature-owned state.

## ABC Literature API Contracts

OpenAPI was downloaded from both production and stage:

- Production: `https://literature-rest.alliancegenome.org/openapi.json`
- Stage: `https://stage-literature-rest.alliancegenome.org/openapi.json`
  (`https://literature-rest-stage.alliancegenome.org/openapi.json` did not
  resolve from the AI Curation workspace on 2026-06-25.)

Both currently report title `Alliance Literature Service` and version `0.1.0`.

### Reference lookup endpoints

Relevant reference endpoints:

| Purpose | Endpoint | Notes |
| --- | --- | --- |
| PMID lookup | `GET /reference/external_lookup/{external_curie}` | Accepts `PMID:123`, `PubMed:123`, etc. Current implementation only supports PMID-like prefixes. |
| Cross-reference lookup | `GET /reference/by_cross_reference/{curie_or_cross_reference_id}` | Resolves a cross-reference to a reference entity. Candidate for DOI/PMCID if indexed as cross-references. |
| Reference show | `GET /reference/{curie_or_reference_id}` | Fetches the reference by ABC curie or database ID. |
| Add PMID | `POST /reference/add/` | Out of scope for AI Curation. This mutates ABC Literature and must not be called by the integration. |

Read-only policy:

- AI Curation may resolve existing references.
- AI Curation must not add missing references to ABC Literature.
- If a PMID/PMCID/DOI/curie is not found, the import stops and tells the curator to add/process the paper in ABC Literature first.

Open question:

The docs and tickets mention PMCID and DOI lookup, but the current `external_lookup` implementation only accepts PMID-like prefixes. PMCID/DOI may work through `by_cross_reference` if the cross-references exist. We should verify this with Valerio before exposing PMCID/DOI search/import in the cutover UI.

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

Read-only implementation detail:

The search response does not directly include converted-file state. After selection, AI Curation should call `show_all/{curie}` to determine whether converted Markdown already exists. It should not call `conversion_request/{curie}` unless Blue Team provides a guaranteed non-mutating status mode.

### Referencefile endpoints

Relevant referencefile endpoints:

| Purpose | Endpoint | Notes |
| --- | --- | --- |
| MD5 lookup | `GET /reference/referencefile/by_md5/{md5sum}` | Returns all referencefiles with that MD5, plus converted Markdown rows derived from each source. |
| File listing | `GET /reference/referencefile/show_all/{curie_or_reference_id}` | Lists all files attached to a reference. |
| Conversion request/poll | `GET /reference/referencefile/conversion_request/{curie_or_reference_id}?wait=false&overwrite_tei_md=false` | Do not call from AI Curation unless Blue Team adds or confirms a non-mutating status-only behavior. The reviewed implementation can start conversion jobs. |
| Download file | `GET /reference/referencefile/download_file/{referencefile_id}` | Downloads source PDF, converted Markdown, nXML, etc. |
| Upload file | `POST /reference/referencefile/file_upload/` | Out of scope for AI Curation. This mutates ABC Literature and must not be called by the integration. |

Mutation boundary:

- `POST /reference/referencefile/file_upload/` is documented here only because it exists in Literature and earlier tickets referenced it.
- The AI Curation cutover must not call it, must not reconcile post-upload IDs, and must not request additive upload response fields.
- A local PDF can only be used to compute an MD5 for read-only lookup. If the MD5 is unknown to Literature, AI Curation must stop.

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
- `overwrite_tei_md=true` ignores and then removes TEI-derived Markdown rows with `_tei` suffix.
- Permission logic treats open-access references as readable by anyone and allows MOD/global rows where `referencefile_mods.mod` is null.
- Conversion job manager state is in-process. The endpoint also synthesizes success entries from the DB, so callers should be robust to process restart and re-query `show_all`.

Read-only conclusion:

- Treat `conversion_request` as mutating unless Blue Team explicitly provides a status-only guarantee.
- The cutover should not start conversion, reconversion, TEI overwrite, or PDFX jobs through Literature.
- Existing converted Markdown should be discovered through `show_all` and/or a new read-only status/listing endpoint if Blue Team adds one.
- If no acceptable converted Markdown exists, AI Curation should report that the paper must be converted in ABC Literature before import.

Important TEI cache trap:

Literature counts `_tei` rows as cached converted Markdown unless `overwrite_tei_md=true`. If a reference has only TEI-derived Markdown, the mutating conversion endpoint can report `converted` while AI Curation's preferred non-TEI selection finds no acceptable artifact.

Required handling:

- The cutover should treat "only TEI-derived Markdown exists" as an explicit unsupported/needs-conversion state, not as a generic failure.
- AI Curation must never set `overwrite_tei_md=true` because that mutates Literature state by replacing TEI-derived rows.
- The UI should report that ABC Literature only has TEI-derived Markdown and cannot import the paper under the current read-only policy.

## Current AI Curation Architecture

### Legacy upload endpoint

The current local PDF upload endpoint is:

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

This is local-first and user-scoped. In the read-only cutover it should be removed from product behavior, not converted into an ABC upload path.

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

- `process_pdf_document(...)`: current direct PDFX path to remove or rewrite.
- `process_markdown_document(...)`: Literature converted Markdown path.

### PDFX parser and markdown adapter

Implementation:

- `backend/src/lib/pipeline/pdfx_parser.py`

Important existing helpers:

- `PDFXParser.parse_pdf_document(...)`
- `markdown_to_pipeline_elements(markdown: str) -> list[dict]`
- `_save_pdfx_json(...)`
- `_save_processed_json(...)`

Current PDFX parser already downloads merged Markdown from PDFX and then converts Markdown to pipeline element dictionaries. That means the Literature replacement does not need to invent a brand-new chunking model. It can:

1. Download converted Markdown from ABC Literature.
2. Feed it through a shared markdown-to-elements function.
3. Save processed JSON.
4. Skip `pdfx_json_path` or save a Literature import receipt JSON instead.
5. Continue through the existing chunking, hierarchy, embedding, and Weaviate storage stages.

Cutover requirement:

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

The model does not have provider key, provider reference IDs, provider artifact IDs, external IDs, MD5 provenance, or viewer capability flags.

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

The Weaviate metadata should receive a small provenance mirror so search results and document lists can identify provider-sourced documents without always joining SQL.

### Frontend upload/document flow

Current frontend upload adapter:

- `frontend/src/features/documents/pdfUploadFlow.ts`
- `uploadPdfDocument(file)` posts to `/api/weaviate/documents/upload`.
- `waitForDocumentProcessing(documentId)` streams `/api/weaviate/documents/{document_id}/progress/stream`, with polling support.
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

The Documents page and PDF viewer drag/drop entry point should both use the same read-only document-source-aware backend in the cutover release. The old local-only upload implementation should be replaced, not kept as a second behavior.

## Proposed Target Architecture

### Backend components

Add these backend pieces:

1. `DocumentSourceProvider` interface plus `ABCLiteratureClient` adapter
   - Provider-neutral interface for search, identifier resolution, MD5 lookup, file listing, converted text download, and optional authorized source artifact download.
   - Typed async wrapper around ABC Literature REST for the Alliance adapter.
   - Handles base URL, auth, timeouts, token caching, retries, and structured errors.
   - ABC adapter methods:
     - `lookup_external_curie(curie)`
     - `lookup_cross_reference(curie_or_id)`
     - `search_references(query, query_fields, filters, pagination)`
     - `show_reference(curie_or_id)`
     - `lookup_referencefile_by_md5(md5)`
     - `show_referencefiles(reference_curie)`
     - `download_referencefile(referencefile_id)`

2. `DocumentSourceImportService`
   - Owns AI Curation import workflow.
   - Resolves identifiers and references through the configured provider.
   - Selects canonical converted Markdown.
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
   - Generalize `pdf_processing_jobs` to document import jobs in the same release, or add a `job_type` field and migrate all active document processing to `literature_import`.
   - Reuse the list/SSE/polling UX only after the underlying data model no longer assumes direct PDFX.

5. ABC Markdown validation
   - The current AI Curation `markdown_to_pipeline_elements` helper is a simple line-oriented parser.
   - The ABC parser repo provides `read_markdown()` and `validate_markdown()` for ABC-format Markdown.
   - The replacement implementation should validate downloaded ABC Markdown with `agr_abc_document_parsers.validate_markdown()` before ingestion, then convert to pipeline elements.
   - If a document fails validation, record the Literature provenance and fail the import with a specific validation error rather than silently ingesting lossy text.
   - Do not let this remain implicit, because extracted evidence quality may depend on section/table/list fidelity.

### Backend endpoints

Required endpoints:

| Endpoint | Purpose |
| --- | --- |
| `POST /api/weaviate/documents/import/source/search` | Search the configured document-source provider for the import picker. |
| `POST /api/weaviate/documents/import/source` | Import one or more identifiers/provider reference IDs. |
| `POST /api/weaviate/documents/import/source/md5` | Accept a transient PDF or checksum for read-only MD5 lookup/import of existing Literature artifacts. |
| `GET /api/weaviate/documents/import/source/{job_id}` | Direct import-job detail if generalized document job endpoints are not enough. |
| `GET /api/weaviate/documents/source/lookup` | Preflight lookup for UI validation/preview. |
| `GET /api/weaviate/documents/source/health` | Auth/config/connectivity health for admin diagnostics. |

Removed upload route:

- Replace product usage of `POST /api/weaviate/documents/upload` with read-only search, identifier, and MD5 import endpoints.
- Delete the previous route in the same release.
- Do not keep a route alias that can bypass the document-source import service.

Job/status migration note:

The current AI Curation pipeline is not stage-name agnostic everywhere. `ProcessingStage` is a fixed enum, and durable PDF job status values are constrained to the existing terminal/running states. The new document-source stages in this document are product-level names, not a statement that the current DB/API can already store those literal values.

Required job migration:

- Durable `status` values can remain broad (`pending`, `running`, `completed`, `failed`, `cancelled`) if they are renamed/generalized away from PDF-specific meaning.
- Provider-neutral stages such as `document_source_lookup`, `document_source_download`, and `markdown_validation` must be added to the stage schema, frontend mapping, SSE/polling contracts, and tests in the same release.
- Do not add `timeout` as a durable job status unless the SQL/API enum permits it. Represent timeout as `failed` with a retryable timeout error or migrate the status contract explicitly.

### Configuration

Add document-source configuration:

- `DOCUMENT_SOURCE_PROVIDER`: required; `abc_literature` for the Alliance cutover.
- `DOCUMENT_SOURCE_REQUEST_TIMEOUT_SECONDS`
- `DOCUMENT_SOURCE_IMPORT_BATCH_LIMIT`
- Provider-specific ABC Literature settings under package/deployment config:
  - `ABC_LITERATURE_API_BASE_URL`
  - `ABC_LITERATURE_AUTH_MODE`
  - `ABC_LITERATURE_BEARER_TOKEN`
  - `ABC_LITERATURE_COGNITO_TOKEN_URL`
  - `ABC_LITERATURE_COGNITO_CLIENT_ID`
  - `ABC_LITERATURE_COGNITO_CLIENT_SECRET`
  - `ABC_LITERATURE_COGNITO_SCOPE`
  - group/access-context mapping for read-only authorization checks

The auth mode should mirror the existing PDF extraction auth pattern:

- `none` for local fake service or explicitly public endpoints.
- `static_bearer` for local testing with a supplied token.
- `cognito_client_credentials` for service-to-service access.

Startup/health validation should say:

- Literature base URL missing.
- Auth missing/misconfigured.
- OpenAPI/reachable but unauthorized.
- Basic read-only lookup/download endpoints reachable.

Required configuration policy:

- A document-source provider configuration is required for document import. If it is missing, document import endpoints should fail startup/readiness or return an admin-facing configuration error.
- For the Alliance deployment, ABC Literature credentials and read-access group context are required.
- Do not add a direct-PDFX escape hatch for missing provider credentials.

Health check guardrail:

Do not call `conversion_request` from routine startup, health endpoints, or normal imports. Despite being a GET endpoint, it can start conversion jobs when sources are pending. Any conversion smoke testing belongs outside AI Curation's read-only integration contract.

### Provenance schema

Add provider-neutral nullable columns to `pdf_documents`:

- `source_provider`: configured provider key, e.g. `abc_literature`
- `source_provider_reference_id`
- `source_provider_reference_curie`
- `source_provider_source_file_id`
- `source_provider_converted_artifact_id`
- `source_provider_pdf_artifact_id`
- `source_external_ids`: compact JSON object for PMID, PMCID, DOI, and other provider-supported IDs
- `source_md5`
- `source_file_class`
- `source_file_extension`
- `source_artifact_status`
- `source_import_status`
- `source_imported_at`
- `source_payload_path`
- `source_markdown_path`
- `viewer_mode`: expected ABC happy path is PDF-backed (`local_pdf` for cached
  local/upload bytes, or `provider_pdf_proxy` only if a future provider proxy is
  implemented)

ABC Literature adapter mapping:

- ABC reference curie -> `source_provider_reference_curie`
- ABC numeric/reference ID -> `source_provider_reference_id`
- ABC `referencefile_id` for the source PDF -> `source_provider_source_file_id`
- ABC `referencefile_id` for converted Markdown -> `source_provider_converted_artifact_id`
- PMID/PMCID/DOI -> `source_external_ids`

Existing PDF-shaped contracts that must be handled in Workstream 2:

- `backend/src/models/sql/pdf_document.py` currently requires `file_path`, `file_hash`, positive `file_size`, and positive `page_count`.
- `backend/src/models/document.py` currently requires `DocumentMetadata.page_count > 0`.
- `backend/src/lib/weaviate_client/documents.py` creates Weaviate documents through a PDF-shaped `PDFDocument` model.
- `backend/src/api/pdf_viewer.py` and download routes assume local PDF files under upload storage.

Workstream 2 must keep these contracts PDF-backed for successful ABC imports.
The chat/PDF viewer is core product behavior, so ABC converted Markdown must not
replace the document PDF.

Repurpose existing fields:

- `file_path`: points to the AI Curation-served PDF artifact for successful ABC
  imports. For upload/checksum imports this can be the retained uploaded PDF;
  for identifier imports it must be a cached/proxied provider source PDF before
  the import is considered complete.
- `pdfx_json_path`: stop writing and stop reading in active import code. The migration may leave this as a nullable historical column, but new imports must not write direct PDFX metadata here.
- `processed_json_path`: still useful for converted Markdown processed elements.

For provider imports:

- Do not create a text-only happy path for ABC Literature. Store or proxy the
  source PDF so the AI Curation viewer/download/chat surfaces remain PDF-backed.
- Store downloaded Markdown as a first-class source text artifact and use it for
  processed JSON/chunks.
- Update PDF viewer/download contracts and tests so successful ABC imports report
  `pdf_available=true`, expose a viewer URL, and also expose source Markdown.

Weaviate document metadata mirror:

- `source_provider`
- `source_provider_reference_curie`
- `source_provider_reference_id`
- `source_provider_source_file_id`
- `source_provider_converted_artifact_id`
- `source_external_ids`
- `source_md5`
- `viewer_mode`

Do not store large Literature API payloads directly in Weaviate metadata. Save full payloads to storage and store paths/IDs.

## Proposed Flows

### Flow A: Identifier import

Inputs:

- One or more required cutover identifiers: PMID or ABC curie.
- PMCID, DOI, and other cross-references only if Workstream 0 fixtures prove `by_cross_reference` support before cutover.
- Optional group/MOD context for read authorization, if Literature requires it.

Steps:

1. Normalize identifiers.
   - `39671436` -> `PMID:39671436` if numeric and configured as PMID default.
   - `PMID39671436` -> `PMID:39671436`.
   - `AGRKB:...` remains ABC curie.
   - `PMCID:...` and DOI are accepted only when Workstream 0 has verified the cross-reference lookup contract.

2. Resolve reference.
   - For PMID: `GET /reference/external_lookup/PMID:{id}`.
   - For ABC curie: `GET /reference/{curie}`.
   - For PMCID/DOI/cross-reference: `GET /reference/by_cross_reference/{value}` only when fixture coverage proves it is indexed.
   - If missing: stop. AI Curation must not create the reference in ABC Literature.

3. Fetch file list.
   - `GET /reference/referencefile/show_all/{reference_curie}`.

4. Select converted Markdown.
   - Prefer `converted_merged_main` with `file_extension=md` and `file_publication_status=final`.
   - Accept global/null MOD rows as usable.
   - Prefer source order:
     1. nXML-derived `_nxml` converted main.
     2. PDFX merged `_merged` converted main.
     3. Other converted main rows only if explicitly configured.
   - Do not use TEI-derived `_tei` as canonical.

5. If no converted main Markdown exists:
   - Stop with a specific curator-facing message: ABC Literature does not yet have importable converted Markdown for this reference.
   - Do not call `conversion_request`.
   - Do not trigger PDFX, TEI overwrite, or any Literature mutation.

6. Download converted Markdown.
   - `GET /reference/referencefile/download_file/{converted_referencefile_id}`.

7. Create or reuse AI Curation document.
   - Deduplicate by `(user_id, source_provider, source_provider_reference_curie, source_provider_converted_artifact_id)`.
   - Also deduplicate by `source_md5` when known.

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

1. AI Curation calls `POST /search/references/` through the configured provider adapter.
2. UI shows returned `hits` with title, authors, date, citation, curie, cross-references, and highlights.
3. User selects one or more references.
4. AI Curation imports selected papers by `curie` using Flow A from the file-list step onward.
5. If search returns an Elasticsearch/reindexing error, show that the Literature search index may be rebuilding and allow direct PMID/curie entry as an alternate input path.

Cutover UI fields:

- Search text.
- Field selector: All, Title, Author, Xref.
- Published-year range.

Do not trigger conversion from search results. Selection should be a separate explicit action.

### Flow B: Local PDF MD5 lookup

Inputs:

- PDF file used only as transient local input for checksum lookup.
- Optional PMID or ABC curie.
- PMCID/DOI only when Workstream 0 fixtures prove cross-reference support.

Steps:

1. Validate file type and size.
2. Compute raw MD5 from uploaded bytes before any local storage, extraction, or durable job work.
3. Call `GET /reference/referencefile/by_md5/{md5}`.

If MD5 matches exactly one source with converted Markdown:

4. Select converted main Markdown from `converted_referencefiles`.
5. Download Markdown.
6. Create AI Curation document with provenance.
7. Ingest Markdown.

If MD5 matches exactly one source without converted Markdown:

4. Stop with a message that ABC Literature knows the file but does not yet have converted Markdown available for read-only import.
5. Do not trigger conversion.

If MD5 matches multiple references:

4. If user supplied a reference identifier and one match belongs to it, select that match.
5. Otherwise return an ambiguity response requiring curator selection.
6. Do not guess based on title/filename alone.

If MD5 has no match:

4. Stop with a message that the PDF is not known to ABC Literature.
5. Do not store, upload, extract, convert, or attach the file.
6. If the curator has a PMID/reference, offer identifier/search import. If the reference still has no converted Markdown, the paper must be processed in ABC Literature first.

Important policy:

Pure PDF upload is not an import source in the cutover. A local PDF is only a temporary MD5 probe. Unknown PDFs, known PDFs without converted Markdown, and PDFs whose artifacts are inaccessible to the caller all stop in AI Curation.

Read-only caveats:

- The browser/backend should treat the uploaded bytes as transient and dispose of them after MD5 lookup.
- Do not call `file_upload`, `reference/add`, or `conversion_request`.
- Do not expose controls that imply AI Curation can save a PDF into ABC Literature.

### Flow C: Direct local PDFX removal

The existing direct local PDFX behavior is removed as part of the cutover:

- Remove product code that lets AI Curation submit PDFs directly to PDFX from document upload.
- Remove PDF extraction worker wake/health UI from the document import path.
- Remove low-level PDFX client calls from active document ingestion.
- Existing `/documents/upload` calls are replaced with read-only search/identifier/MD5 import endpoints.
- If Literature is unavailable or unconfigured, document import fails with a configuration/service error.

Curator-facing language should distinguish:

- "Found converted paper in ABC Literature."
- "ABC Literature has no converted Markdown available for this paper yet."
- "This PDF is not known to ABC Literature."
- "You do not have access to this Literature artifact."
- "ABC Literature is unavailable; document import cannot run right now."

## Status Mapping

Map read-only Literature import state to AI Curation job stages:

| Import state | AI Curation stage | Progress | Message |
| --- | --- | --- | --- |
| lookup starting | `document_source_lookup` | 5 | Looking up paper in ABC Literature |
| reference found | `document_source_lookup` | 15 | Found ABC Literature reference |
| MD5 matched | `document_source_lookup` | 20 | Found matching file in ABC Literature |
| converted Markdown found | `document_source_download` | 45 | Downloading converted Markdown |
| no converted Markdown | `failed` | 100 | ABC Literature has no converted Markdown available yet |
| inaccessible artifact | `failed` | 100 | You do not have access to this Literature artifact |
| unknown local PDF MD5 | `failed` | 100 | This PDF is not known to ABC Literature |
| markdown parsed | `chunking` | 65 | Preparing document chunks |
| chunks created | `embedding` | 75 | Generating embeddings |
| stored | `storing` | 90 | Storing document |
| complete | `completed` | 100 | Document ready |

Cancellation:

- AI Curation can stop its local import job.
- There is no Literature/PDFX work to cancel because AI Curation does not start conversion.

Timeout:

- If a read-only Literature lookup or download times out, mark the local job as `failed` with retryable timeout metadata unless the migration explicitly adds `timeout` to the durable status contract.
- A retry should repeat read-only lookup/list/download calls only.

## Converted Markdown Selection Rules

Canonical selection:

1. `file_class == "converted_merged_main"`
2. `file_extension == "md"`
3. `file_publication_status == "final"`
4. Accessible by open access, caller MOD, developer/all access, or global/null MOD association.
5. Prefer non-TEI-derived rows.
6. Prefer `_nxml` over `_merged` only if both are present and Valerio confirms nXML-derived Markdown is preferred.
7. If multiple candidates remain, choose newest only if all belong to the same source, otherwise require deterministic selection logic or curator choice.

Supplement policy:

- The hard cutover scope is main converted Markdown.
- Supplement files are not ingested by this plan.
- UI and provenance must say main text only rather than silently implying full-paper coverage.
- Supplement gaps or Literature-side supplement failures should not block main import when converted main Markdown exists.

TEI policy:

- Do not use `tei` as canonical display/import artifact.
- Never call `overwrite_tei_md=true` from AI Curation.
- If only TEI-derived Markdown exists, block import and ask the curator to have the paper converted through ABC Literature.

## Artifact And Viewer Strategy

There are three different artifacts:

1. Source PDF in ABC Literature.
2. Converted Markdown in ABC Literature.
3. AI Curation processed JSON/chunks.

Cutover viewer behavior:

- For identifier import, ingest converted Markdown and cache/proxy the source PDF
  before the document is available in AI Curation.
- Use `viewer_mode=local_pdf` when the PDF is cached in AI Curation storage.
- Use `viewer_mode=provider_pdf_proxy` only if a future provider proxy is built
  and tested end to end.
- Evidence search and extraction should work from text chunks.
- PDF viewer/download/chat surfaces must remain available for successful ABC
  imports.
- Download dialog should offer the real PDF, source Markdown, processed JSON, and
  provenance.

For local PDF MD5 lookup:

- Compute MD5 and perform read-only Literature lookup.
- If the lookup resolves to converted Markdown, retain the uploaded PDF as the
  AI Curation-served PDF artifact and ingest ABC converted Markdown as the text
  source.
- Store ABC provenance for MD5 matches that resolve to existing Literature artifacts.
- Image/figure manifests and page/text coordinate mapping are cutover scope only
  if the required provider/PDFX sidecar data is available and tested; otherwise
  the UI must still keep the PDF viewer available while disabling only those
  unsupported sidecar affordances.

## Frontend Design

### Documents page

Replace the Documents upload affordance with a paper discovery/import control:

- Search mode lets curators type title, author, citation text, year/date filters, PMID/PMCID/DOI/cross-reference, or ABC curie.
- Search results come from ABC Literature `POST /search/references/`.
- Identifier mode accepts comma-separated identifiers.
- Supports up to `DOCUMENT_SOURCE_IMPORT_BATCH_LIMIT` identifiers, default 10.
- Shows one row per requested paper:
  - Identifier
  - Provider reference ID/curie
  - PMID/DOI or other external IDs when known
  - State
  - Message
  - Result document link
  - Retry/action button

States:

- looking up
- found
- already converted
- downloading
- ingesting
- ready
- failed
- needs reference selection
- needs ABC Literature conversion

Do not make the Documents page a marketing or explanatory page. It should stay a work surface for managing documents.

### Local PDF Lookup UX

Current upload flow posts directly to `/api/weaviate/documents/upload`. The cutover replaces that client entry point with read-only Literature MD5 lookup:

- Upload starts with MD5 lookup.
- If found in Literature with accessible converted Markdown, say it matched ABC Literature and skip extraction work in AI Curation.
- If found but no accessible converted Markdown exists, say the paper must be converted in ABC Literature before import.
- If not found, say the PDF is not known to ABC Literature.
- The UX must not offer local direct extraction or upload-to-Literature.

### Document list/provenance display

Update document list/details/download dialog to show:

- Source: ABC Literature.
- ABC reference curie.
- PMID/DOI if available.
- Source file ID and converted file ID where useful.
- Viewer availability: PDF available through provider, or text only.

The UI should not surface raw implementation terms like "conversion_request" to curators.

## Other Consumer Safety

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

- Keep PDFX service changes additive for ABC Literature and any other consumers.
- Do not remove or rename existing fields.
- Preserve currently documented status values.
- Add new fields like queue position, active run count, or worker state as optional data.
- Coordinate with ABC Literature Service before requiring any new PDFX status semantics.

If Literature API changes:

- Add optional fields to existing response schemas rather than changing required fields.
- Version or feature-detect new behavior if AI Curation depends on it.
- Open a matching ABC Literature UI PR only if UI behavior needs to adapt.

## Replacement Implementation Plan

This is one replacement release. The workstreams below can be developed in parallel, but no workstream ships alone. Cutover is complete only when search/select import, identifier import, local-PDF MD5 lookup, provenance, markdown ingestion, job reporting, UI, docs, and tests all land together.

### Workstream 0: Contract Smoke And Fixtures

Goal:

Verify the exact Literature API behavior in stage/prod and capture fixtures before cutting code paths over.

Work:

- Add a small local script or test helper to call:
  - `search/references`
  - `external_lookup`
  - `by_cross_reference`
  - `reference/{curie_or_reference_id}`
  - `show_all`
  - `by_md5`
  - `download_file`
- Capture non-secret JSON fixtures for:
  - search results by title, author, date, and cross-reference
  - already converted non-TEI main Markdown
  - TEI-only converted Markdown
  - MD5 match with converted referencefile
  - MD5 match without converted referencefile
  - MD5 match with multiple references
  - no-MD5-match response
  - inaccessible/non-authorized artifact response
- Confirm auth mode needed for stage/prod.
- Confirm PMCID and DOI lookup path with Valerio before exposing those labels in the UI.
- Confirm preferred converted Markdown source order (`_nxml` vs `_merged`).
- Confirm whether `show_all`, `by_md5`, and `download_file` are scoped by caller user/group access.
- Confirm whether Literature has a read-only status endpoint for "known but not converted" without starting conversion.

Exit criteria:

- We can search Literature and select a reference by `curie`.
- We can download converted Markdown for at least one known reference.
- We know whether PMCID/DOI can be supported in the cutover UI.
- We know whether `download_file` returns `application/octet-stream`, `text/markdown`, or only blob bytes.
- We know how Literature represents inaccessible, unknown, and known-but-not-converted artifacts without requiring AI Curation mutations.

### Workstream 1: Document Source Client And Import Service

Goal:

Create the backend integration boundary and make it the only document-ingestion path.

Files likely touched:

- New `backend/src/lib/document_sources/provider.py`
- New `backend/src/lib/document_sources/abc_literature/client.py`
- New `backend/src/lib/document_sources/abc_literature/models.py`
- New `backend/src/lib/document_sources/abc_literature/errors.py`
- New `backend/src/lib/documents/document_source_import_service.py`
- `backend/src/api/documents.py` or new `backend/src/api/document_imports.py`
- `backend/src/api/admin/connections.py` or a new health endpoint
- Tests under `backend/tests/unit/lib/literature/`

Work:

- Implement async HTTP client with timeouts.
- Implement auth header builder.
- Parse response schemas into typed models or typed dictionaries.
- Preserve raw payload for diagnostics without logging secrets.
- Implement reference search, identifier resolution, MD5 lookup, file-list retrieval, download, and read-only import.
- Delete the `/documents/upload` ingestion route and update clients to call the document-source import endpoints.
- Remove any product path that submits uploaded PDFs directly to PDFX.
- Remove any product path that uploads, edits, adds references, triggers conversion, or mutates ABC Literature.
- Add fake-service tests from KANBAN-1242.

Exit criteria:

- Unit tests cover search, lookup, MD5 exact/multiple/no match, download, auth failure, 403, 404, 422, timeout, TEI-only policy, no-converted-Markdown policy, and read-only mutation guardrails.
- Static search/lookup health checks are read-only and do not start conversion.

### Workstream 2: Provenance And Document Model Migration

Goal:

Persist external document-source details and make text/Markdown-backed documents first-class records.

Files likely touched:

- `backend/src/models/sql/pdf_document.py`
- New Alembic migration under `backend/alembic/versions/`
- `backend/src/models/document.py`
- `backend/src/lib/weaviate_client/documents.py`
- `backend/src/api/pdf_viewer.py`
- document download/info endpoints in `backend/src/api/documents.py`
- Contract tests for document list/detail/download metadata

Work:

- Add provider-neutral provenance columns.
- Keep successful provider imports PDF-backed; do not create
  `external_converted_markdown` documents as the ABC happy path.
- Store downloaded Markdown as a first-class text artifact.
- Keep local cached provider PDFs, or a tested provider PDF proxy, for every
  successful ABC import.
- Update document creation models.
- Update viewer/download behavior so successful ABC imports expose a PDF viewer
  URL and PDF download while also exposing source Markdown.
- Add indexes:
  - `(user_id, source_provider, source_provider_reference_curie)`
  - `(user_id, source_provider, source_provider_converted_artifact_id)`
  - `(source_md5)`
- Mirror small provenance fields into Weaviate document metadata.
- Migrate or tolerate pre-cutover rows without making them an active import path.

Exit criteria:

- A Literature search/import/MD5-matched document can be stored, listed, downloaded as Markdown/processed JSON, and loaded for chat.
- PDF-specific viewer/download calls return a clear unavailable/proxy response based on `viewer_mode`.

### Workstream 3: Markdown Ingestion Path

Goal:

Ingest ABC converted Markdown without PDFX.

Files likely touched:

- `backend/src/lib/pipeline/orchestrator.py`
- `backend/src/lib/pipeline/pdfx_parser.py`
- New `backend/src/lib/pipeline/markdown_elements.py`
- New `backend/src/lib/pipeline/markdown_ingestion.py` or method on orchestrator
- `backend/tests/unit/pipeline/`

Work:

- Move `markdown_to_pipeline_elements` to a neutral module or replace it with an ABC-aware adapter.
- Validate downloaded Markdown with `agr_abc_document_parsers.validate_markdown()` before ingestion.
- Add `process_markdown_document(...)`.
- Save source payload/Markdown.
- Save processed JSON.
- Reuse chunking/hierarchy/embedding/storage.
- Remove direct PDFX parsing from active document ingestion.

Exit criteria:

- Unit tests ingest sample ABC Markdown into chunks without PDFX.
- Invalid ABC Markdown fails with a specific import error.
- Existing extraction/evidence tools can search and read chunks from provider-backed documents.

### Workstream 4: Job, Status, And Progress Replacement

Goal:

Replace PDF-specific job reporting with Literature import reporting.

Files likely touched:

- `backend/src/lib/pdf_jobs/service.py`
- `backend/src/lib/pdf_jobs/upload_intake_service.py`
- `backend/src/lib/pdf_jobs/upload_execution_service.py`
- `backend/src/models/sql/pdf_processing_job.py`
- `backend/src/models/pipeline.py`
- `backend/src/api/pdf_jobs.py`
- frontend job/progress components
- related tests

Work:

- Generalize PDF job models/endpoints or add document import job models.
- Add stages for `document_source_search`, `document_source_lookup`, `document_source_download`, `markdown_validation`, `chunking`, `embedding`, `storing`.
- Keep broad durable statuses if desired (`pending`, `running`, `completed`, `failed`, `cancelled`), but remove PDF-specific assumptions.
- Map read-only Literature lookup/download results into job details.
- Treat local cancellation as "stop AI Curation import"; there is no Literature/PDFX work to cancel because AI Curation does not start it.

Exit criteria:

- Job list, job detail, SSE, and polling show Literature import state faithfully.
- No UI text says AI Curation is converting, uploading, or saving files in ABC Literature.

### Workstream 5: Search, Identifier, And MD5 Lookup UI Replacement

Goal:

Replace Documents page and PDF viewer upload UX with a read-only document-source-backed import work surface.

Files likely touched:

- `frontend/src/pages/weaviate/DocumentsPage.tsx`
- New components under `frontend/src/components/weaviate/`
- `frontend/src/services/weaviate.ts`
- `frontend/src/features/documents/pdfUploadFlow.ts`
- `frontend/src/components/pdfViewer/usePdfViewerUpload.ts`
- `frontend/src/components/weaviate/DocumentDownloadDialog.tsx`
- Tests under frontend unit/integration suites

Work:

- Add Literature search/select import using `POST /search/references/`.
- Add identifier import for PMID and ABC curie, plus PMCID/DOI if fixtures prove cross-reference support.
- Add local-PDF MD5 lookup that resolves through Literature and imports converted Markdown only when an existing authorized artifact is found.
- For unknown PDFs, known PDFs without converted Markdown, or inaccessible artifacts, show a clear stop state.
- Show per-paper progress and partial failures.
- Show provenance in document list/details/download dialog.
- Remove direct PDFX worker wake/extraction affordances from Documents upload UI.
- Remove any UI control that implies AI Curation can upload to ABC Literature or start conversion there.

Exit criteria:

- Curator can search for a paper, select it, import it, see progress, and load it for chat.
- Curator can paste an identifier and import it.
- Curator can use a local PDF only when its MD5 resolves to an existing authorized converted Literature artifact.
- Curator cannot send an unknown PDF directly to AI Curation PDFX or ABC Literature.

### Workstream 6: Documentation, Deployment, And Cutover

Goal:

Ship the replacement as the only document ingestion behavior.

Work:

- Update curator and developer docs.
- Update environment documentation to mark document-source config as required for document import.
- Add dashboard/logging around import outcomes.
- Confirm ABC Literature/Blue Team are comfortable with search, lookup, listing, and download traffic.
- Remove stale direct-PDFX upload tests or rewrite them around Literature behavior.
- Run backend, frontend, contract, and stage smoke validation.

Exit criteria:

- Curators can import by search, identifier, and MD5 match through ABC Literature.
- The active Documents workflow contains no direct AI Curation-to-PDFX path.

## Testing Plan

Backend unit tests:

- Document-source provider interface and ABC Literature adapter:
  - lookup success/failure
  - MD5 match no/multiple/exact matches
  - download bytes
  - auth/token caching
  - timeout/retryable errors
- Document-source import service:
  - already converted
  - known but not converted
  - inaccessible artifact
  - duplicate document
  - global/null MOD access
- Markdown ingestion:
  - ABC Markdown to elements
  - no usable elements
  - sections/tables/lists
  - processed JSON path saved

Backend contract tests:

- Import endpoint request/response.
- Progress/status endpoint for document-source stages.
- Document list/detail includes provenance.
- Download info behaves correctly for provider imports with both PDF and source
  Markdown available.

Frontend tests:

- Identifier input validation and batch limit.
- Per-paper progress rows.
- Partial failure display.
- Document list provenance.
- Local-PDF MD5 lookup messaging for Literature match/no match/no converted Markdown.

Integration/live smoke:

- Stage Literature known PMID with existing converted main Markdown.
- Stage Literature MD5 match for known PDF.
- Unauthorized/missing Literature auth.
- Literature timeout/retry.

Do not make live PDFX conversion a default PR test. AI Curation should not trigger it at all under this read-only design.

## Logging And Observability

Backend logs should include structured fields:

- `operation`: `document_source_lookup`, `document_source_md5_lookup`, `document_source_download`, `document_source_import`
- `document_id`
- `job_id`
- `user_id` or safe internal user key
- `identifier`
- `provider_reference_id` or `provider_reference_curie`
- `source_provider_source_file_id`
- `source_provider_converted_artifact_id`
- `import_status`
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

- imports by document-source path (`search_select`, `identifier`, `md5_match`)
- no-converted-Markdown and inaccessible-artifact counts
- document-source provider API latency
- import duration
- MD5 exact/multiple/no match counts
- duplicate imports avoided

## Risks And Open Questions

### PMCID/DOI support

Current `external_lookup` code supports PMID-like prefixes only. PMCID and DOI likely require `by_cross_reference`, but we need confirmation that Literature stores those cross-references consistently.

Cutover decision:

- Expose PMCID/DOI only if `by_cross_reference` fixtures prove those IDs are indexed consistently.
- If fixtures are not ready, the cutover UI labels supported identifier import as PMID and ABC curie, while the broader search box can still find papers by indexed text and cross-references.

### Unknown local PDF

A local PDF with no MD5 match cannot be imported by AI Curation under the read-only contract.

Cutover decision:

- Do not process unknown PDFs locally.
- Do not upload unknown PDFs to ABC Literature.
- Tell the curator the PDF must be added and converted through ABC Literature before AI Curation can import it.

### Multiple MD5 matches

The MD5 endpoint can return multiple references. AI Curation must not guess in ambiguous cases.

Decision needed:

- If user supplied reference context, pick the matching reference.
- Otherwise require curator selection in UI.

### Text-only viewer

Identifier import may have converted Markdown without a local PDF. Existing AI Curation UI assumes PDF download/viewer in a few places.

Cutover decision:

- Text-only mode is required for imported papers where no source PDF is cached locally.
- Provider PDF proxy or source-PDF cache is required for records where the UI offers PDF viewing.
- The UI must not show a broken local PDF viewer for text-only documents.

### SQL constraints

`pdf_documents` currently requires `file_path`, `file_hash`, positive `file_size`, and positive `page_count`. Literature-only text imports do not naturally satisfy those fields.

Cutover decision:

- Relax or generalize the table in this replacement release.
- Store downloaded Markdown as the artifact and set meaningful text artifact values.
- Do not create fake PDF metadata.

### Conversion status without mutation

The reviewed Literature `conversion_request` endpoint can start conversion jobs. AI Curation needs either to avoid it entirely or consume a Blue Team-confirmed read-only status/listing contract.

Cutover decision:

- Use `show_all` and downloaded artifact availability as the initial read-only source of truth.
- If a future status endpoint is needed, it must be non-mutating before AI Curation uses it.
- Do not poll `conversion_request` as part of normal import.

### Permissions

AI Curation service credentials need enough Literature access to:

- read reference files
- download converted Markdown

They should not have write/admin permissions for reference creation, file upload, metadata edits, conversion triggers, or TEI overwrite. If a service token cannot be restricted that way today, treat that as part of the access-contract blocker.

### ABC embeddings overlap

Blue Team has ABC embeddings work in flight. This AI Curation migration should not wait for it, but the provenance fields should make it possible to reuse ABC embeddings in a separate architecture change.

## Questions For Valerio / Blue Team

1. Is `GET /reference/by_cross_reference/{value}` the intended path for PMCID and DOI, or should AI Curation only promise PMID/ABC curie first?
2. Are `show_all`, `by_md5`, and `download_file` scoped by caller user/group access, or do we need new scoped/redacted response behavior?
3. For converted main Markdown selection, should `_nxml` beat `_merged`, or should `converted_merged_main` newest/final be enough?
4. Is there a non-mutating endpoint to report "known but not converted" status, or should AI Curation rely only on `show_all`?
5. For no-MD5-match PDFs, is the expected user action always "add/process this in ABC Literature first"?
6. Is service-to-service Cognito client credentials the intended auth mechanism for AI Curation to call Literature?
7. Which imported records should cache/proxy source PDFs, and which should be text-only?
8. Are supplement Markdown files required for the cutover, or is main text plus explicit supplement warnings enough?
9. What rate limits or traffic expectations apply to read-only search, MD5 lookup, file listing, and download calls?
10. Can Blue Team provide a service credential/scope that is read-only by construction?

## Final Assessment

This migration should be treated as a replacement, not an enhancement to the current local uploader. The existing AI Curation pipeline can still be reused after the parse step because PDFX already hands the app Markdown before chunking, but direct AI Curation-to-PDFX upload and any AI Curation-to-ABC mutation should leave the active Documents workflow.

The main work is not PDF extraction. It is product and data-contract work:

- how users identify papers,
- how we handle unknown PDFs,
- how provenance is stored,
- how text-only imports behave in a PDF-oriented UI,
- and how we report "not available yet" without starting conversion.

The replacement is viable if ABC Literature confirms the read-only access contract for search, reference lookup, MD5 lookup, converted file listing, and download. The cutover should land only when search/select import, identifier import, MD5-matched local PDF import, converted Markdown ingestion, provenance, job reporting, and text-only/PDF viewer behavior are all implemented together.
