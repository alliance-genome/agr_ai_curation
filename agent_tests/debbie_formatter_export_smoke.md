# Debbie Formatter Export Smoke

## Purpose

Recreate Debbie Krupke's tumor-term PDF/chat/export workflow on dev and prove
the structured formatter path no longer produces duplicate or inconsistent CSV
exports.

## Environment

- Dev URL: the current dev private IP from the deployment note, opened over VPN.
- Backend: current `origin/main` deployed to dev.
- Auth: `DEV_MODE=true` dev user.
- Evidence directory: `temp/agent_test_runs/<timestamp>/debbie_formatter_export_smoke/`.

## Inputs

- Original Debbie PDF retrieved read-only from production session/document
  evidence.
- Known production investigation references:
  - Session: `d2d3cf18-04f0-44e3-a965-09381b0f2bca`
  - Trace: `360762fa9fa0f7383115e86bb9bc88d6`
  - Canonical result: `extraction-result:4170023b-8ba3-44e2-ad7c-dacaa3a3a221`
- Known previously dropped row:
  `B cell | lymphoma | Mus musculus | Endogenous tumor | Results | lymphoma incidence; B-cell lymphomas`

These production identifiers are intentionally committed as internal evidence
locators for the Debbie investigation. They are not credentials or secrets.

## Prompts

Use Debbie's original prompt when available from the production session bundle.
If the exact text is unavailable, use a focused reproduction prompt:

```text
From the loaded PDF, extract tumor classification term rows. For each row include Organ/Cell Type of origin, Tumor classification term, Species, Tumor type, Section, and Extracted phrase. Then create a CSV export of the rows.
```

After the assistant responds, request the export in the same session:

```text
Please save this as a CSV file with one row per tumor term and the same columns shown above.
```

Repeat once:

```text
Please save the CSV again using the same row set and filename intent.
```

## Steps

1. Create the evidence directory.
2. Save the PDF as `input.pdf` in the evidence directory.
3. Upload `input.pdf` on dev and wait for processing to finish.
4. Load the document for chat.
5. Run the prompt sequence above.
6. Save a screenshot or API capture showing the final assistant response and
   file chips.
7. Download every CSV surfaced by the final response into the evidence
   directory.
8. Copy the chat session ID, trace ID, and file ID into `ids.md`.
9. Open the Audit tab and save evidence that the visible CSV formatter was used.
10. Open the response in Agent Studio and ask:

```text
Summarize the trace path for the CSV export. Did a visible CSV formatter specialist call finalize_and_save, and did the supervisor avoid export_to_file?
```

11. Save the Agent Studio answer or TraceReview export.

## File Checks

Run from the repo root after downloading the CSV:

```bash
python3 - <<'PY' "$EVIDENCE_DIR"/*.csv
import csv
import sys
from pathlib import Path

paths = [Path(p) for p in sys.argv[1:]]
print(f"csv_file_count={len(paths)}")
for path in paths:
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8-sig")))
    headers = rows[0].keys() if rows else []
    print(f"{path.name}: row_count={len(rows)} headers={list(headers)}")
    expected = {
        "Organ/Cell Type of origin": "B cell",
        "Tumor classification term": "lymphoma",
        "Species": "Mus musculus",
        "Tumor type": "Endogenous tumor",
        "Section": "Results",
        "Extracted phrase": "lymphoma incidence; B-cell lymphomas",
    }
    print("contains_known_dropped_row=", any(all(row.get(k) == v for k, v in expected.items()) for row in rows))
PY
```

Save the output as `row_check.txt`.

## Pass Criteria

- Exactly one CSV download/file chip is surfaced for the requested export.
- Repeating the export reuses or updates the same structured file identity
  rather than creating a duplicate draft file.
- Trace evidence shows a visible CSV formatter specialist using
  `finalize_and_save`.
- Trace evidence does not show supervisor `export_to_file`.
- Trace evidence does not show raw `save_csv_file(data_json=...)`.
- CSV headers are curator-usable.
- CSV has 9 rows when the original biological extraction is reproduced.
- The known `B cell / lymphoma` row is present when the original biological
  extraction is reproduced.

## Partial Criteria

Mark content review as partial if the LLM extracts a different biological row
set from the PDF. In that case, still record whether the system invariants
passed: one file, visible formatter path, no raw export path, stable file
identity, and usable CSV headers.

## Required Artifacts

- `input.pdf`
- `prompts.md`
- `ids.md`
- downloaded CSV file(s)
- `row_check.txt`
- UI screenshot or API capture showing file chips
- Audit/TraceReview/Agent Studio evidence
- `verdict.md`
