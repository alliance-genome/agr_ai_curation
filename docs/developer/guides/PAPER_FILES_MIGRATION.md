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
files now report `text` (previously `text_only`).

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
