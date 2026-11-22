# Pending PDF no-job orphan repair

Historical upload failures can leave a PostgreSQL `pdf_documents` row in
`pending` without a corresponding `pdf_processing_jobs` row. The manual repair
command reports or terminalizes only documents older than the configured race
window. It does not process stale live jobs; normal PDF job reconciliation
continues to own those records.

Only papers that were never processed qualify: the paper's main file
(`pdf_document_files`, role `main`) has no processed-elements JSON, no
extraction (PDFX) JSON and no range of pieces in the search index. A pending
paper with no job whose main file has any of these was processed (for example
a paper from the pre-job pipeline, which never wrote the SQL status; v0.11.13
repaired the 48 such papers on production); it is listed under `skipped` with
reason `skipped_has_processed_output` and is never marked failed.

The repair creates a synthetic failed job with the canonical PDF processing
receipt, then reconciles the document through the PDF job service. Existing
document and job APIs expose the resulting `failed` status and retry guidance.
Reports contain document/job identifiers, timestamps, status, and reason only;
they do not contain document text.

## Production procedure

Production execution is manual. Run the dry-run first against the deployed
backend image and retain its JSON report:

```bash
docker compose -f docker-compose.production.yml run --rm backend \
  python -m src.lib.pdf_jobs.orphan_repair_cli --dry-run --json
```

Review the cutoff and qualifying count. Adjust
`PDF_NO_JOB_ORPHAN_THRESHOLD_SECONDS`, `PDF_NO_JOB_ORPHAN_BATCH_SIZE`,
`PDF_NO_JOB_ORPHAN_REPAIR_TIMEOUT_SECONDS`, or
`PDF_NO_JOB_ORPHAN_REPAIR_RETRY_COUNT` through the operator-managed environment
when needed. For a one-off override, pass the setting with Compose, for example
`run --rm -e PDF_NO_JOB_ORPHAN_BATCH_SIZE=25 backend ...`. Apply one bounded
batch only after approving the report:

```bash
docker compose -f docker-compose.production.yml run --rm backend \
  python -m src.lib.pdf_jobs.orphan_repair_cli --apply --json
```

Run dry-run again after application. A repaired row now has a durable job and
will not qualify again, so repeated application is idempotent. Continue with
additional bounded apply runs only when the follow-up report contains another
expected batch. `PDF_NO_JOB_ORPHAN_REPAIR_APPLY=false` is the safe default;
`--dry-run` and `--apply` explicitly override it for one invocation.
