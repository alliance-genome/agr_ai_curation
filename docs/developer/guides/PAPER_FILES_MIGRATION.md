# Papers and their files: migration, fill step and piece-number check

Release 0.12.0 lets a paper hold several files: its main paper and its
supplements. Each Library paper is a row of `pdf_documents`; each of its files
is a row of `pdf_document_files`. This guide covers the one-time move of
existing papers to that layout. It applies to any deployment upgrading across
Alembic revision `c6d7e8f9a0b1`.

## The data model

- `pdf_documents` (the paper) keeps its id, `filename` (the paper's name in the
  Library), `title`, `upload_timestamp`, `last_accessed`, owner, status, error,
  processing times, provider reference id, CURIE and external ids, and its
  `pdf_document_reference_links` row. It gains:
  - `document_kind`: `library`, or `benchmark_frozen` for benchmark runtime
    copies, which are never shown in the Library. This replaces every check of
    `viewer_mode = 'benchmark_frozen'`.
  - `next_chunk_index`: the next free piece number in the search index. A
    file's range is reserved in one short transaction that locks the paper row
    and advances the counter; numbers are never reused.
- `pdf_document_files` (a file) holds every per-file column that used to be on
  the paper: storage paths, hash, size, page count, extraction and processed
  JSON paths, hierarchy metadata and per-file provider provenance. New columns:
  `role` (`main` or `supplement`), `display_name`, `status` (`pending`, `ready`,
  `failed`), `error_message`, `tab_order`, `added_at`, `display_mode`
  (`local_pdf`, `provider_pdf_proxy` or `text`, replacing `viewer_mode`), and the
  file's pieces: `chunk_index_start`, `chunk_index_end`, `chunk_count`, and
  `text_bytes`.
- A file's pieces are the paper's search-index pieces whose `chunkIndex` lies in
  `chunk_index_start..chunk_index_end`. Stored pieces are never rewritten. A
  `ready` file without a range is a named error (`FileRangeMissingError`); nothing
  guesses one.
- `text_bytes` has one definition for every file: the UTF-8 bytes of the `text`
  of each parsed element that chunking keeps (every element except page footers),
  after clean-up, as saved in the file's processed JSON. It is never the size of
  the JSON file. Text imports and PDFs are measured the same way
  (`src.lib.document_files.service.element_text_bytes`).
- Constraints: one `main` file per paper; main-file hashes are unique (duplicate
  upload detection reads `uq_pdf_document_files_main_file_hash`); file paths are
  unique; a provider source file appears once per paper; `page_count` is set only
  for PDF display modes and is NULL for text files.
- Deleting a paper deletes its files (`ON DELETE CASCADE`). The schema does not
  stop a file from being removed; which files may be removed is enforced by the
  removal endpoint when it exists.

The API still reports `viewer_mode`: it is the main file's display mode. Text
files now report `text` (previously `text_only`), and plain uploads report
`local_pdf` (previously no value). `/api/pdf-viewer/documents/{id}` reports
`page_count: null` for a text paper (the shapes are pinned in
`backend/tests/unit/api/test_pdf_viewer_text_only.py`).

## The viewer: one tab per file

The viewer shows a paper through its files, never through the single main-file
fields. Both places that open a paper in the shared viewer read the files list
(`GET /api/pdf-viewer/documents/{id}/files`, or `files` on the curation session's
document) and send it with `showPaperFilesInViewer` (`pdfEvents.ts`):

- Chat restore (`chatDocumentRehydration.ts`) reads the files route on every
  restore, because supplements can join a paper after chat stored it. The chat's
  active document does not carry files.
- The curation workspace sends `workspace.session.document.files`.

Every file with a `content_url` (a ready PDF, main or supplement) or a `text_url`
(a ready text file) gets a tab, in `tab_order`; a file still being added gets none.
The main file opens first; a paper without a main file opens its first file with a
URL; a paper with no such file clears the viewer and says why ("This paper's main
file is not available to show." when its main file is not ready, otherwise "This
paper's files are still being added."). When the main file is not ready but other
files are, their tabs show with the same main-file note. A text paper therefore shows its
text tab and never leaves the previous PDF on screen. The page, scroll, pdf.js
page-text corpus, PDF search, highlight and any waiting evidence navigation are
kept per file (`PdfViewer.tsx`).

Evidence opens its own file first (`EvidenceNavigationCommand.fileId`):

- no `file_id` (saved before 0.12.0): the viewer shows "This evidence was saved
  before files were recorded" and opens nothing;
- a `file_id` that is not one of the paper's tabs: the file was removed; nothing
  opens;
- a PDF file: today's quote search, against that file's own pages only. Evidence
  without a page never falls back to a page;
- a text file: a record with stored fragments is located fragment by fragment
  (each fragment is an exact slice of one stored piece; the record's quote joins
  them with a blank line the file does not have between rows or sentences), and
  the union is highlighted; when only some fragments are found, the banner says
  so. A record without fragments (a review-grid anchor) is located by its quote.
  Each is searched in the blocks' texts joined by a blank line, exactly as the
  server serves them (every stored piece
  is an exact slice of that joined text, so a quote may cross blocks); nothing is
  normalised or loosened in the browser, and pdf.js and the fuzzy matcher are never
  called. When the quote is not found, the first block of the evidence's section is
  marked (evidence `section` against the first non-empty of the block's
  `parent_section` and `section_title`, and `subsection`, field by field; a name
  containing " > " is never split), and the banner says the
  quote was not found. The one known miss: a quote across a sentence cut inside an
  element over 1,500 characters (the piece has one space where the block keeps the
  original whitespace) takes this "not found" path.
- evidence naming a file of the paper that is not ready yet says so (not
  "removed");
- evidence the viewer cannot show changes nothing but its banner (an alert that
  can be closed): no tab switch, no scroll, the current highlight stays.

## A paper without a main file

A paper can be imported from its supplements alone, so a paper with no `main`
file row is a valid state (more than one is not: `uq_pdf_document_files_one_main`).
Readers that show any paper use `PDFDocument.main_file_or_none` and report the
absence plainly; nothing invents a main file:

- Library and viewer lists, paper detail and the viewer URL report
  `viewer_mode`, `viewer_url`, `page_count`, `file_size` (`file_size_bytes`)
  and `file_hash` as `null`.
- The PDF content route and every main-file download return 404; download info
  lists nothing available.
- Renaming changes the paper's name and the search index only (there is no
  stored main PDF to rename).
- Source provenance holds the paper's own fields only; execution provenance,
  validation history and cost attribution record no main-file values; the
  document outline has no hierarchy; a curation session has no viewer URL or
  page count.
- A benchmark cannot use such a paper as a local source (benchmarks read main
  files only).

`PDFDocument.main_file` stays for paths that only ever act on a main file (main
paper imports, uploads, benchmark papers) and raises `MissingMainFileError`
when there is none.

## Stored files per file

New provider imports save each file's converted Markdown and processed
elements under the file's id: `{user}/source_markdown/{file_id}.md` and
`{user}/processed_json/{file_id}.json`. Files stored earlier keep their paths
(named by paper id); every reader and the delete path use the paths stored on
the file row, never a rebuilt one.

## One active job per paper (Alembic `d7e8f9a0b1c2`)

A partial unique index, `uq_pdf_processing_jobs_one_active_per_document`, allows
at most one job per paper in `pending`, `running` or `cancel_requested`.
`pdf_jobs.service.create_job` raises `ActiveJobExistsError` for a second one.

The migration fails, changing nothing, if a paper already has two or more
active jobs. It names the papers (`ActivePdfJobsMigrationError`) and never
picks a job to keep. Before the release, run this read-only check against the
deployment (it works on the previous release's schema too):

```sql
BEGIN TRANSACTION READ ONLY;
SELECT document_id,
       count(*) AS active_jobs,
       array_agg(status ORDER BY created_at) AS statuses,
       min(created_at) AS first_created,
       max(updated_at) AS last_updated
FROM pdf_processing_jobs
WHERE status IN ('pending', 'running', 'cancel_requested')
GROUP BY document_id
HAVING count(*) > 1
ORDER BY document_id;
ROLLBACK;
```

No rows means the migration will apply. Any row is a paper whose jobs must be
looked at by hand (stale jobs are normally finished by the stale-job
reconciler) before upgrading.

## The migration (Alembic `c6d7e8f9a0b1`)

It changes only PostgreSQL. For every paper it creates one `main` file that
carries the paper's former per-file values unchanged, sets `document_kind`, and
drops the moved columns. Mapping:

| Old `viewer_mode` | File `display_mode` | Paper `document_kind` |
| --- | --- | --- |
| NULL (plain upload) | `local_pdf` | `library` |
| `local_pdf` | `local_pdf` | `library` |
| `provider_pdf_proxy` | `provider_pdf_proxy` | `library` |
| `text_only` | `text` (page count dropped; it was a placeholder) | `library` |
| `benchmark_frozen` | `text` (frozen inputs are JSON, Markdown or XML, never a PDF) | `benchmark_frozen` |

A paper with status `completed` gets a `ready` file; any other status gives a
`failed` file. The paper keeps its own status. The file's display name is the
paper's filename, and its `added_at` is the paper's upload time.

Properties:

- Forward-only. Its downgrade raises. Inside a maintenance window the recovery
  is a restore of the pre-migration database backup; after traffic reopens,
  problems are fixed forward.
- Restartable. Alembic runs all pending migrations in one transaction, so a
  failure leaves the database as it was.
- Fails loudly. A row it cannot convert (an unknown `viewer_mode`, or a missing
  required file value) stops it with `PaperFilesMigrationError` naming the
  internal row ids. Nothing is skipped and no value is invented.
- Verifies itself before dropping the old columns: as many main files as papers,
  every paper with exactly one main file, every copied value equal, every kind as
  mapped, and the paper count unchanged at the end.
- The copy is one set-based statement. The schema changes take short ACCESS
  EXCLUSIVE locks on `pdf_documents`, so it runs only with traffic stopped.

After the migration, existing papers have no piece ranges, text sizes or next
piece number yet. New imports refuse to reserve a range on such a paper
(`ChunkRangeUnavailableError`), so run the fill step before traffic reopens.

## The fill step

```bash
docker compose -f docker-compose.production.yml run --rm --no-deps backend \
  python -m src.lib.document_files.fill_existing
```

It reads every paper and main file from PostgreSQL, each main file's processed
JSON from disk, and the `documentId` and `chunkIndex` of every stored piece in
each owner's tenant. It writes, in one transaction, only each main file's
`chunk_index_start`, `chunk_index_end`, `chunk_count` and `text_bytes`, and
each paper's `next_chunk_index`. It writes nothing to the search index or disk
and touches no evidence, session, flow result or piece.

It stops, writing nothing (exit status 1, with the reason and internal ids), if:

- a paper's stored piece numbers are not exactly `0..count-1`, each once;
- a `ready` file has no stored pieces or no processed JSON;
- a processed JSON file cannot be read as a list of elements;
- a paper already has more than one file;
- a value it would write differs from one already stored.

Papers that are not ready keep their pieces' range so the numbers are never
reused; a file without processed JSON keeps `text_bytes` NULL. Piece groups in
the search index with no paper row are counted and never touched. Running the
step again reports `papers_already_filled` and writes nothing.

After writing it re-reads PostgreSQL and the search index (`verify`): every
ready file has a range, count and size, the ranges match the stored pieces, and
each paper's next piece number is past its last range. `--verify` runs only that
check. The report holds counts and internal row ids only.

## The read-only piece-number check

Before the release, check a deployment that still runs the previous release:

```bash
docker exec -i <backend container> python - --json \
  < backend/src/lib/document_files/piece_numbers.py
```

It reads only `pdf_documents.id`, `user_id` and `status` and `users.auth_sub`
(columns every release has) inside one `READ ONLY` transaction, and lists each
tenant's stored `documentId` and `chunkIndex`. It prints counts (papers, papers
with pieces, pieces, the largest paper, counts by status, piece groups without a
paper row) and the internal ids of papers whose piece numbers are not unbroken.
It never prints titles, file names, paper identifiers or text. Exit status 0
means every paper is unbroken; 1 means some are not and the fill step would stop.

## Services that read these tables

Every service that runs the backend image reads `pdf_documents` and must run the
same release: `backend` (API and the migration on start) and `benchmark_worker`
(frozen benchmark papers). Stop the benchmark worker before the migration and
start both on the new image. TraceReview and the other services do not read
these tables.
