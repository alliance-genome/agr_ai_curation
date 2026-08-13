# Chat, Flow, And Trace Review

## Purpose

Verify that a formatter export produced through chat or flow is visible in the
Audit tab, TraceReview, and Agent Studio, and that the trace proves the
structured formatter path.

## Environment

- Dev URL: current dev private IP over VPN.
- Backend/frontend: current release candidate from `origin/main` or the
  approved release tag.
- Evidence directory: `temp/agent_test_runs/<timestamp>/chat_flow_trace_review/`.

## Workflow Options

Use either:

- a chat export created from a loaded PDF, or
- a simple curation flow ending in CSV, TSV, or JSON formatter.

Prefer a flow formatter if the release specifically touches batch or flow
execution. Prefer chat if the release specifically touches curator chat export.

## Steps

1. Create the evidence directory.
2. Run the selected chat or flow workflow on dev.
3. Save the browser screenshot showing the assistant response or flow result
   with the downloadable file.
4. Copy the session ID, trace ID, flow run ID if present, and file ID into
   `ids.md`.
5. Open the Audit tab and save a screenshot or JSON/API capture showing the
   trace entry.
6. Open the response or flow run in Agent Studio.
7. Ask Agent Studio:

```text
Can you summarize the trace for this formatter export? Include the formatter specialist name, projection-plan inspection or validation calls, finalize_and_save, saved file metadata, and whether supervisor export_to_file or raw save_*_file tools appeared.
```

8. Save the Agent Studio response as `agent_studio_trace_review.md`.
9. If TraceReview API is available from the server, export or capture the
   relevant views:

```bash
curl -s "http://localhost:8001/api/traces/<TRACE_ID>/views/tool_calls" > tool_calls.json
curl -s "http://localhost:8001/api/traces/<TRACE_ID>/views/trace_summary" > trace_summary.json
```

## Pass Criteria

- Audit tab shows a trace for the chat response or flow run.
- Agent Studio can access and summarize the trace.
- Trace evidence identifies the visible formatter specialist.
- Trace evidence includes projection-plan inspection, validation, preview, or
  finalization activity.
- Trace evidence includes `finalize_and_save` and saved file metadata.
- Trace evidence does not show supervisor `export_to_file`.
- Trace evidence does not show raw `save_csv_file`, `save_tsv_file`, or
  `save_json_file` accepting model-authored file content.

## Required Artifacts

- browser screenshot or API capture showing the downloadable file
- `ids.md`
- Audit tab evidence
- `agent_studio_trace_review.md`
- TraceReview JSON files if available
- downloaded formatter output file
- `verdict.md`
