# ABC Literature Converted Text Retrieval Contract

This is the stable AI Curation contract for retrieving converted text from ABC
Literature after conversion has completed. It is provider-specific policy behind
the document-source boundary; core upload, ingestion, viewer, and persistence
code should talk about source PDFs and converted text artifacts rather than ABC
`referencefile` names.

## Stable Read Path

Use only read endpoints for the first cutover:

1. Resolve the reference/source artifact:
   - PDF checksum upload path: `GET /reference/referencefile/by_md5/{md5sum}`.
   - Identifier/reference path: `GET /reference/{curie_or_reference_id}` when
     needed, then `GET /reference/referencefile/show_all/{curie_or_reference_id}`.
2. Select one authorized source/main PDF.
3. Select one converted main Markdown artifact derived from that PDF.
4. Download bytes with
   `GET /reference/referencefile/download_file/{referencefile_id}`.

Do not call `POST /xml2md/convert`,
`GET /reference/referencefile/conversion_request/{curie_or_reference_id}`, file
upload, reference add, or any endpoint/parameter that can create conversion
jobs, overwrite TEI Markdown, or otherwise mutate Literature state.

## Provider-Neutral Selection Rule

AI Curation should ingest a generic "converted main Markdown" artifact only when
it can prove the artifact is derived from an authorized source/main PDF.

Provider-neutral outcome mapping:

| Condition | AI Curation outcome |
|-----------|---------------------|
| Authorized final main PDF plus final non-TEI converted main Markdown | Ready to import converted text |
| No matching reference/source PDF | No provider match / not ready |
| Source PDF is MOD-scoped and curator lacks that MOD | Access denied / not importable |
| Converted artifact exists but no authorized source PDF can be associated | Access denied / not importable |
| No final `converted_merged_main` Markdown | Converted text not available |
| Only TEI-derived Markdown exists | TEI-only, not canonical |
| Multiple converted candidates exist | Deterministically select by suffix preference, then highest `referencefile_id` |
| `download_file` returns `403` | Final access denial; do not retry with service credentials |

The source PDF is authorized when it is open access, global/no-MOD scoped, or its
source `referencefile_mods[].mod_abbreviation` intersects the curator's internal
MOD IDs (`FB`, `WB`, `MGI`, `ZFIN`, `RGD`, `SGD`, `HGNC`). For logged-in curator
requests, derive those IDs from the validated auth claims and `config/groups.yaml`.
The local SQL `users` row is identity/profile storage, not the entitlement source.

## ABC Field Policy

Canonical converted Markdown:

| ABC field | Required value |
|-----------|----------------|
| `file_class` | `converted_merged_main` |
| `file_extension` | `md` |
| `file_publication_status` | `final` when present |
| derived source | Authorized source PDF with `file_class=main`, `file_extension=pdf`, `file_publication_status=final` |

ABC `by_md5` returns source referencefile rows and metadata-only
`converted_referencefiles` children containing `referencefile_id`, `display_name`,
`file_class`, and `file_extension`. The converted bytes still come only from
`download_file/{referencefile_id}`. `show_all` returns full referencefile rows
and is the refresh path after identifier resolution or conversion polling.

Derived-display suffix preference for multiple final `converted_merged_main`
Markdown rows:

1. `_merged`
2. `_nxml`
3. `_grobid`
4. `_docling`
5. `_marker`

When candidates have the same suffix, choose the highest numeric
`referencefile_id` so selection is deterministic. Exclude `_tei`; TEI-derived
Markdown is not canonical for AI Curation import unless a future ticket changes
this policy. `converted_merged_supplement` and other supplemental/non-main
classes are not fallbacks for the main paper.

Converted-row MOD metadata is not an access grant. Historical bulk-converted
Markdown rows may have `referencefile_mods[].mod_abbreviation = null` even when
their source PDF is MOD-scoped. Always evaluate access from the source/main PDF
row and treat a null/global converted row as metadata only.

## Download Contract

`GET /reference/referencefile/download_file/{referencefile_id}` is the
authoritative byte and access gate.

| Aspect | Contract |
|--------|----------|
| Auth | Requires a bearer credential accepted by Literature. For final curator downloads, pass the logged-in curator bearer token in memory. Do not return, log, or persist it. |
| Access | Server-side access is checked on this call. A `403` is final for that curator and artifact. |
| Response body | Raw file bytes, not JSON and not a wrapped payload. |
| Content type | Literature currently returns `application/octet-stream` for referencefile downloads, including Markdown. AI Curation must sniff/use metadata rather than rely on `text/markdown`. |
| Filename | `Content-Disposition` attachment filename is the ABC `display_name` plus `.` plus `file_extension`, for example `<display_name>.md`. |
| Encoding | Converted Markdown bytes are UTF-8 text for AI Curation ingestion. Decode as UTF-8 and fail the import if decoding fails. |
| Missing file ID | `404` when the referencefile row is not available. |
| Forbidden | `403` when the caller cannot download the file. Do not bypass with a service token for curator-visible imports. |
| Other failures | Treat 5xx, timeout, malformed response, or decode failure as provider unavailable/import failed with sanitized errors. |

Metadata/listing may use a configured service credential for backend jobs, but a
service credential can have full Literature access. If service credentials are
used before download, AI Curation must still apply its own source-PDF access
filter before showing, downloading, caching, ingesting, or serving restricted
content.

## Endpoint Sufficiency

No new Literature endpoint is required for the initial cutover. The sufficient
contract is:

- `GET /reference/referencefile/by_md5/{md5sum}` for uploaded-PDF checksum
  discovery.
- `GET /reference/{curie_or_reference_id}` for read-only reference resolution.
- `GET /reference/referencefile/show_all/{curie_or_reference_id}` for complete
  referencefile refresh.
- `GET /reference/referencefile/download_file/{referencefile_id}` for final
  authorized bytes.

A future endpoint could improve ergonomics by returning server-filtered
`can_download` and explicit converted-source relationships, but AI Curation must
not depend on that for the first integration.

