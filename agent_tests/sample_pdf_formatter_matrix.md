# Sample PDF Formatter Matrix

## Purpose

Validate CSV, TSV, and JSON formatter behavior from a real small PDF workflow
without pretending CI can assert exact LLM biological content every run.

## Environment

- Dev URL: current dev private IP over VPN.
- Backend/frontend: current release candidate from `origin/main` or the
  approved release tag.
- PDF fixture: `backend/tests/fixtures/sample_fly_publication.pdf`.
- Evidence directory: `temp/agent_test_runs/<timestamp>/sample_pdf_formatter_matrix/`.

## Setup

Copy the sample PDF into the evidence directory:

```bash
cp backend/tests/fixtures/sample_fly_publication.pdf "$EVIDENCE_DIR/input.pdf"
```

Upload `input.pdf` in dev, wait for processing, and load it for chat.

## Prompts

Start with one extraction prompt:

```text
From the loaded paper, extract a small curation-ready set of gene or allele candidates. Keep the result grounded in the PDF and include enough evidence context to review it.
```

Then request each formatter from the same saved extraction context.

CSV:

```text
Create a CSV export with one row per candidate. Use clear curator-facing headers, include the candidate label, source evidence phrase, section, and any identifier you resolved. Sort by candidate label.
```

TSV:

```text
Create a TSV export from the same saved structured candidates. Do not invent rows. Include only canonical object rows that came from the saved extraction.
```

JSON:

```text
Create a JSON export from the same saved structured candidates. Group rows by object type if there is more than one type; otherwise return rows with source metadata.
```

## Steps

1. Save the prompts in `prompts.md`.
2. Upload and process `input.pdf`.
3. Run the extraction prompt.
4. Request CSV, TSV, and JSON exports.
5. Download all generated files into the evidence directory.
6. Capture the chat session ID, response trace IDs, and file IDs in `ids.md`.
7. Save Audit or TraceReview evidence for each export trace.
8. Open at least one export response in Agent Studio and ask:

```text
For this export, identify the formatter specialist, projection-plan tool calls, and finalize_and_save call. Confirm whether any raw save/export tool was used.
```

## File Checks

Run after downloading files:

```bash
python3 - <<'PY' "$EVIDENCE_DIR"
import csv
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
for path in sorted(root.glob("*")):
    if path.suffix == ".csv":
        rows = list(csv.DictReader(path.open(newline="", encoding="utf-8-sig")))
        print(f"{path.name}: csv rows={len(rows)} headers={list(rows[0].keys()) if rows else []}")
    elif path.suffix == ".tsv":
        rows = list(csv.DictReader(path.open(newline="", encoding="utf-8-sig"), delimiter="\t"))
        print(f"{path.name}: tsv rows={len(rows)} headers={list(rows[0].keys()) if rows else []}")
    elif path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        shape = type(data).__name__
        size = len(data) if isinstance(data, list) else len(data.keys()) if isinstance(data, dict) else "n/a"
        print(f"{path.name}: json shape={shape} size={size}")
PY
```

Save output as `file_check.txt`.

## Pass Criteria

- CSV, TSV, and JSON files are downloadable.
- Trace evidence shows visible formatter specialists using projection-plan tools
  and `finalize_and_save`.
- No trace uses supervisor `export_to_file` for these formatter exports.
- No trace uses raw `save_csv_file`, `save_tsv_file`, or `save_json_file` with
  model-authored rows or file content.
- CSV and TSV contain flat object rows with usable headers.
- TSV is sourced from canonical object rows, not artifact-summary prose.
- JSON is valid and uses the requested rows/grouped shape where appropriate.
- Content reviewer finds the files plausible and grounded in the PDF.

## Partial Criteria

If the candidate set differs from a prior run, mark content review as partial
unless rows are clearly ungrounded. System invariants can still pass.

## Required Artifacts

- `input.pdf`
- `prompts.md`
- downloaded CSV, TSV, and JSON files
- `file_check.txt`
- `ids.md`
- Audit/TraceReview/Agent Studio evidence
- `verdict.md`
