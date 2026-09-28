# Program-rendered chat reports

A chat formatter may deliver one projection or an ordered report of independently
configured sections. The model selects sources and fields; application code
reads the saved bundle, validates the plans and renders every table cell. Raw
rows, Markdown tables and replacement scientific data are not accepted.

For one table, use `plan_json` as before. For independent sections, pass
`report_json` to `validate_output_projection` and `finalize_chat_output`:

```json
{
  "sections": [
    {
      "heading": "Genes",
      "plan": {
        "format": "chat",
        "row_source": "object",
        "source_keys": ["source-key-from-inventory"],
        "columns": [
          {"key": "gene", "header": "Gene", "field_ref": "field-from-catalog"}
        ]
      }
    }
  ]
}
```

Repeat sections with their own source keys, columns, filters and sorts. Preview
each section through `preview_output_projection(plan_json=...)`; validate the
whole report without delivery, then finalize once. `report_json` and `plan_json`
are mutually exclusive. `group_by` remains grouping within one projection with
shared columns; `chat_layout=sections` still means one heading per row.

All sections pass the existing strict parser and formatter constraints before
rendering. Unknown fields/sources or invalid later sections prevent the entire
report from being delivered. Valid filters matching zero rows produce an
explicit no-rows section; missing upstream data is not represented as an empty
scientific result. A wholly empty report is rejected. Existing selected-field
restrictions remain in force; reports do not provide an escape from them.

`FLOW_OUTPUT_CHAT_MAX_SECTIONS` defaults to 12. Aggregate matching rows across
all sections obey `FLOW_PROJECTION_MAX_ROWS` (repeated sources count each time),
and the complete rendered report including headings/notes obeys
`FLOW_OUTPUT_CHAT_MAX_CHARS`. Oversized reports fail explicitly, never silently
truncate. Heading length uses `FLOW_OUTPUT_CHAT_NOTES_MAX_CHARS`; headings are
single-line escaped labels. All settings are documented in `.env.example`.

One application-held report travels through the existing `CHAT_OUTPUT_READY`
and durable transcript paths. The main chat currently displays the Markdown
report as text; this change does not introduce an HTML table renderer. The model receives only bounded validation
summaries and a completion receipt, not the final rendered report. CSV/TSV/JSON
file finalization is unchanged. This does not add a saved-report editor or fix
missing extraction/validator inputs. Before release, inspect saved-agent prompt
pins and custom instructions through the agent-upgrade gate; updating the
packaged prompt alone does not establish adoption by every saved flow.
