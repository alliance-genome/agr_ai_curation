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

### Bounded benchmark downloads

The Alliance client's `download_referencefile` accepts an optional `max_bytes`
keyword for callers that must enforce a size limit before retaining a paper.
Benchmark adapters supply their existing configured input-byte limit; the client
does not introduce a separate limit or import backend configuration.

Bounded downloads preserve exact bytes, request identity encoding, reject other
content encodings and redirects, check declared size when present, and stop as
soon as actual streamed bytes exceed the limit. Overflow raises the existing
sanitized `ABCLiteratureHTTPError` with status 413. Responses are closed on
success, failure and cancellation, without retrying or changing credentials.
Callers that omit the keyword retain their existing download behavior.

This transport option does not select artifacts, approve benchmark references,
request conversion, or change the access rules below.

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

When AI Curation reads ABC with a machine reader (a `service` bearer: the
login-free development reader or a benchmark conversion's application
credential), `download_file` is no longer a per-curator gate because ABC gives
that client every file. Under a `service` bearer, converted Markdown and figure
metadata are used only when nested under (bound to) the selected, authorized
source PDF. Reference-level converted rows are never selected and provider
conversion is never requested; without bound Markdown the authorized source PDF
is parsed locally with PDFX. A `curator` bearer keeps the reference-level
selection below.

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

The ABC provider adapter enforces the non-TEI requirement before artifacts
reach the shared main-text selector. TEI-signaled rows are not lower-ranked
fallbacks; they are outside the canonical candidate set.

For non-ABC document-source providers, the core selector accepts provider
normalized ready Markdown artifacts without requiring ABC's
`converted_merged_main` class name.

## Error And Fallback Behavior

- No provider checksum/reference match: keep local upload fallback only for
  unknown/no-match PDFs.
- Checksum match whose source PDF has `file_class=supplement`: process the exact
  curator-uploaded PDF locally. Never substitute reference-level
  `converted_merged_main`, because it represents the main paper rather than the
  uploaded supplement.
- Inaccessible source PDF: do not download or ingest converted text.
- Authorized source PDF with no canonical converted Markdown: request ABC
  conversion when available, using `wait=false` and `overwrite_tei_md=true`.
- Conversion running: keep the local AI Curation job in a running/waiting state
  and poll provider conversion status. Raw progress sidecar IDs are hints only;
  downloadable figure metadata must be selected from the provider artifact list
  through the provider metadata-artifact contract.
- Conversion failed/no sources: fail the provider-backed import with a clear
  provider-conversion status.
- Multiple equally preferred canonical candidates: treat as ambiguous instead
  of guessing.
- `download_file` 403: fail that provider download for the curator; do not
  retry with a broader service credential. If the denied artifact is selected
  canonical Markdown for a curator-uploaded PDF that is already saved locally,
  mark the provider-text import failed and process that saved PDF through the
  normal local pipeline on the same document/job. A source-PDF denial during
  identifier import remains an item-level failure with no document/job.

Known ABC main papers should not silently fall back to local PDFX while canonical
provider conversion is pending. The explicit saved-PDF branch above applies
only to an authoritative access denial for selected canonical Markdown, not to
pending conversion, ambiguous selection, or another provider failure.

## Download Bytes

The ABC `download_file` OpenAPI response schema is underspecified. AI Curation
treats the response body as raw bytes and does not rely on `Content-Type` or
`Content-Disposition` for the first cut.

For converted Markdown, decode as UTF-8 Markdown during ingestion. Store the
retrieved Markdown as AI Curation source Markdown and keep compact provenance
with the source PDF referencefile ID, converted Markdown referencefile ID,
source MD5, file class/extension, and source access scope.

### From Markdown to stored text

The saved source Markdown is never changed. The text that is cut into pieces,
embedded and quoted is built from it by `markdown_to_pipeline_elements`
(`backend/src/lib/pipeline/pdfx_parser.py`), the same step that PDF extraction
output and benchmark document conversions go through. Each element's raw text
is cleaned exactly once by `backend/src/lib/markdown_plain_text.py`, which
reads it with a CommonMark parser (`markdown-it-py`):

- Only markup the parser reads as formatting is removed: `*` and `_` emphasis,
  `~~` strikethrough, code span backticks, link brackets and image syntax (the
  description is kept). Literal characters stay, for example `p.Tyr318*`,
  `****` and `~5 kb`. Underscores inside a name are never touched
  (`snake_case`, `w_1118`). A name written with an underscore at both ends
  loses those two underscores when it starts at a word edge or after
  punctuation, because CommonMark reads the pair as italics: `__main__`
  becomes `main`, a header cell `_gene_id_` becomes `gene_id`,
  `${SAMPLE}_R1_` becomes `${SAMPLE}R1` and `df['_p_value_']` becomes
  `df['p_value']`. No gene, allele or construct notation tested loses
  characters (`y[1] w[*]; P{w[+mC]=UAS-GFP}attP2`, `unc-119(ed3)`,
  `FBal_0001_`).
- Text in angle brackets is kept exactly as written, for example `<UAS:GFP>`,
  `Tg(<lexAop:GFP>)`, `Pax6<Sey>`, web addresses and e-mail addresses:
  autolinks and raw HTML are both off.
- `<u>`, `<sup>` and `<sub>` tag pairs are removed first, so
  `Ca**<sup>2+</sup>**` becomes `Ca2+`. Characters that were inside such a
  pair are never read as markup (`val<sup>*</sup>` keeps its asterisk). Other
  HTML stays as literal text.
- Backslash escapes are undone (`\*` becomes `*`), except an escaped
  backslash, which stays as two backslashes: in a table cell undoing it would
  make the next pipe part of the cell, and prose follows the same rule.
- Character references are decoded (`&amp;` becomes `&`; `&#0;` and NUL
  become the replacement character U+FFFD), and the parser's output is
  normalized with NFKC, so a character code ends the same as the literal
  character: `&nbsp;` and a literal no-break space both become a space,
  `&micro;` and `µ` both become `μ`. A carriage return becomes a line feed.
- Tables are cleaned cell by cell, so markup never pairs across cells, and an
  escaped pipe (`\|`) stays escaped inside its cell. A pipe or backslash
  produced by decoding (`&#124;`, `&vert;`, `&#92;`) is re-escaped, so
  decoding never adds, removes or moves a cell boundary.
- Fenced code blocks keep their text as written.
- Every step is linear in the length of the text. Tests compare CPU time for
  2 KB and 16 KB of each hostile input (runs of `[`, `![`, `*`, `_`, `&`, `<`,
  tags) using the median of five paired CPU-time ratios. A realistic 1 MB
  fly genotype table takes about 1.4 s. The earlier regex clean-up was
  quadratic on bracket runs.

A second pass would turn an escaped `\*x\*` back into emphasis and delete
the asterisks, so the clean-up is never applied to text it already returned.
Known limit: a pair of literal asterisks that the converted text does not
escape, such as `w[*]; y[*]`, still reads as emphasis and loses both
asterisks (ABC ticket SCRUM-6973).

Papers imported before 0.12.0 keep the pieces they were stored with.

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
