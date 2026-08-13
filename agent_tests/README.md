# Agent Tests

`agent_tests/` contains operator-run validation runbooks for workflows where
ordinary CI cannot honestly assert the full result. They are not a replacement
for unit tests, full backend/frontend gates, or deployed smoke. They are an
evidence-capture layer for live PDF, chat, formatter, trace, and reviewer
judgment checks.

## When To Run

Run these during release validation when a change affects:

- PDF-grounded chat extraction quality.
- CSV, TSV, or JSON formatter exports.
- Audit tab, TraceReview, or Agent Studio trace access.
- Curator-reported workflows that depend on real prompts and real documents.

## Evidence Directory

Create one evidence directory per run:

```bash
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE_DIR="temp/agent_test_runs/${RUN_ID}"
mkdir -p "$EVIDENCE_DIR"
```

Each runbook lists the required files. At minimum, save:

- prompts and chat transcript,
- trace IDs,
- downloaded CSV/TSV/JSON files,
- row-count or JSON-shape command output,
- screenshots when browser UI is involved,
- TraceReview or Agent Studio excerpts,
- a short verdict file named `verdict.md`.

## Verdict Format

Use this shape for `verdict.md`:

```markdown
# Verdict

- Runbook:
- Environment:
- App version / git SHA:
- Operator:
- Started:
- Finished:
- Result: pass | partial | fail

## System Invariants

- [ ] Required file count/path behavior passed.
- [ ] Required formatter/tool path passed.
- [ ] Required trace/Audit evidence was captured.

## Content Review

- [ ] Content is plausible and grounded in the PDF.
- [ ] Known release-specific rows/fields were present, or variance was explained.

## Notes

```

## Reviewer Roles

Use a second reviewer or subagent when possible:

- `operator`: runs the workflow and saves raw artifacts.
- `trace reviewer`: inspects Audit, TraceReview, or Agent Studio for tool path.
- `content reviewer`: checks downloaded files for row count, headers, and PDF
  grounding.

Keep system invariants separate from content-quality judgment. If the LLM
extracts a different biological set, mark content review as `partial` without
overclaiming, while still recording whether the platform mechanics passed.
