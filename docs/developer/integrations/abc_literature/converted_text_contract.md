# ABC Literature Converted Text Contract

Status: implementation contract for ALL-358.

This document defines how AI Curation selects and downloads ABC Literature
converted text for the first ABC-backed import cut. The core application should
continue to speak provider-neutral document-source concepts; ABC endpoint names
and `referencefile` details belong inside the ABC adapter, tests, and this
integration documentation.

## Stable Retrieval Path

Use metadata endpoints to find candidate artifacts, then use `download_file` to
retrieve bytes:

1. `GET /reference/referencefile/by_md5/{md5sum}` for PDF checksum uploads.
2. `GET /reference/referencefile/show_all/{curie_or_reference_id}` for
   identifier/reference imports and post-conversion refresh.
3. `GET /reference/referencefile/download_file/{referencefile_id}` for the
   selected PDF or converted Markdown bytes.

`by_md5` and `show_all` return metadata and IDs only. Converted Markdown bytes
must come from `download_file`.

All first-cut endpoints require a bearer credential. Metadata endpoints can
return files the curator cannot download, so `download_file` is the final
server-side access gate. A `403` from `download_file` is final for that curator.

## Access Rules

AI Curation must select converted text only after it has an authorized
source/main PDF artifact.

The source PDF access policy is authoritative. Historical converted rows can
have null MOD metadata, so a null MOD on a converted Markdown row is not a
public-access grant. Converted Markdown inherits access from the selected source
PDF.

AI Curation may use a service credential for metadata lookup, but final provider
downloads must use the request-local curator token when access needs to reflect
the logged-in curator. Do not persist the curator token or return it to the
browser.

## Canonical Converted Text

For ABC Literature, the canonical first-cut text target is:

- `file_class=converted_merged_main`
- Markdown format (`file_extension=md`)
- non-TEI-derived display/class signal

When multiple canonical candidates exist, prefer:

1. `_nxml` / nXML-derived Markdown.
2. other non-TEI merged/main Markdown.
3. no TEI fallback in this cut.

TEI-derived `converted_merged_main` rows are not canonical for AI Curation
imports in this wave. If only TEI-derived Markdown exists for an otherwise
authorized ABC reference, AI Curation should continue the provider-conversion
path or report that canonical converted text is not ready; it should not ingest
the TEI row as the main paper text.

For non-ABC document-source providers, the core selector accepts provider
normalized ready Markdown artifacts without requiring ABC's
`converted_merged_main` class name.

## Error And Fallback Behavior

- No provider checksum/reference match: keep local upload fallback only for
  unknown/no-match PDFs.
- Inaccessible source PDF: do not download or ingest converted text.
- Authorized source PDF with no canonical converted Markdown: request ABC
  conversion when available, using `wait=false` and `overwrite_tei_md=false`.
- Conversion running: keep the local AI Curation job in a running/waiting state
  and poll through provider metadata.
- Conversion failed/no sources: fail the provider-backed import with a clear
  provider-conversion status.
- Multiple equally preferred canonical candidates: treat as ambiguous instead
  of guessing.
- `download_file` 403: fail that provider download for the curator; do not
  retry with a broader service credential.

Known ABC papers should not silently fall back to local PDFX while canonical
provider conversion is pending.

## Download Bytes

The ABC `download_file` OpenAPI response schema is underspecified. AI Curation
treats the response body as raw bytes and does not rely on `Content-Type` or
`Content-Disposition` for the first cut.

For converted Markdown, decode as UTF-8 Markdown during ingestion. Store the
retrieved Markdown as AI Curation source Markdown and keep compact provenance
with the source PDF referencefile ID, converted Markdown referencefile ID,
source MD5, file class/extension, and source access scope.

## Fixture Evidence

Stage evidence from 2026-06-25:

- Reference: `AGRKB:101000000055784`
- PMID: `23970418`
- Source PDF referencefile: `4040596`
- Source MD5: configured with `ABC_LITERATURE_SMOKE_KNOWN_MD5`
- Converted Markdown referencefile: `4672234`
- Converted class/extension: `converted_merged_main` / `md`
- Source access: `FB`
- Converted Markdown SHA-256: recorded in the local live-smoke evidence file
- Converted Markdown byte count: `63756`

Evidence files:

- `file_outputs/temp/abc_literature_live_smoke_20260625T130209Z.json`
- `file_outputs/temp/abc_literature_ready_upload_smoke_20260625T145042Z.json`
