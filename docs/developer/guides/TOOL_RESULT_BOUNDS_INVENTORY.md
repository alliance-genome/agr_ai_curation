# Tool Result Bounds Inventory

ALL-1278 / KANBAN-1835. This inventory lists every registered tool family, says
whether its result reaches a model, and records how that result is bounded, with
the test that proves it. Where a family is not bounded yet, the entry records a
blocker.

## Contract

The application keeps the full data: workspaces, captured lookups, stored
evidence and persisted results. The model gets compact pages, and it gets exact
chunks when it needs more detail.

| Setting | Default | Meaning |
|---|---|---|
| `TOOL_RESULT_MAX_BYTES` | 32768 (minimum 2048) | Total budget for one bounded model-facing result, including page metadata, descriptors and errors. Measured as the largest of: the UTF-8 bytes of Python `str()` (what the Agents SDK sends), JSON without escaping, and ASCII-escaped JSON. |
| `EVIDENCE_LIST_MAX_LIMIT` | 100 | Largest `list_recorded_evidence` page. The default page is still `LIST_RECORDED_EVIDENCE_LIMIT`. |
| `BUILDER_LIST_MAX_LIMIT` | 100 | Largest `list_staged_*` / `find_staged_*` page. The default page is still `BUILDER_LIST_DEFAULT_LIMIT` (50). |
| `SECTION_READ_PAGE_MAX_CHUNKS` | 100 | Largest `read_section` / `read_subsection` page. The default page is still `SECTION_READ_MAX_CHUNKS` (30). |

Why these defaults:

- **Byte budget.** 32 KiB is about 8k tokens: under 1% of a 1M-token window and about 3% of a 272k window per result. That still fits a typical page of about 20 section passages, or the default page of most lookups. The largest observed `agr_curation_query` result was 44,269 characters (trace `8a4e5797c4a6ad0ad757a0019ae4a7de`); under this budget it pages instead of arriving whole.
- **Row maximums.** The row maximums sit at or above the old default pages, so existing calls are unchanged.

The same behavior applies to every family:

- **Large page requests.** When a caller asks for more rows than the maximum, the tool clamps to the maximum and reports `requested_*`, `effective_*` and `*_clamped`. This is normal and is not reported to Sentry.
- **Page ends.** Pages end at the row limit or at the byte budget (`page_ended_by`: `limit`, `size_budget` or `end`) and give an explicit next offset or `next_call`.
- **Oversized single values.** A value too large for one response is withheld behind a descriptor that names its `detail_path`, size and `sha256`. Exact chunks (`detail_cursor`, `next_cursor`) rebuild it byte for byte.
- **Bad cursors and stale results.**
  - A malformed or past-the-end offset or cursor returns an explicit `invalid_result_cursor`. It is never reset to 0.
  - If a recomputed result no longer matches its `result_sha256`, the tool returns `stale_result_cursor`.
- **Failure reporting.** If even the compact form cannot fit, the tool returns a compact `tool_result_budget_unmet` failure. That failure is reported once to Sentry through `report_payload_contract_violation` (category `tool_result_budget_escape`). Package code cannot import the backend, so the backend adapter reports package failures; a failure the tool already reported is marked and not captured again.

Implementation: `backend/src/agr_ai_curation_runtime/tool_result_bounds.py` holds
the pure contract that packages share, and
`backend/src/lib/openai_agents/tool_result_bounds.py` holds the config and
Sentry side.

## Enforcement points

| Path | Where | What |
|---|---|---|
| Subprocess package tools (read-only lookups, REST, SQL) | `catalog_service._resolve_package_tool` → `_bounded_package_result` | The adapter adds `result_offset`, `result_sha256`, `detail_path` and `detail_cursor` to the tool schema. It strips them before the package runs and serves bounded pages or exact detail chunks from the recomputed, hash-checked result. Results that fit are returned unchanged. |
| Validator lookup capture (compact runtime) | `compact_runtime.CompactValidatorRuntime.wrap_lookup_tool` | Requests the complete result (`full_tool_results_requested`) and captures it in the application. The model then pages the stored lookup with `lookup_ref`, with no re-query. Pages and detail reads serve the lean model view (each returned row once, ALL-1285); the complete response stays in the workspace. Each page carries exactly the `validator_record_refs` for its rows. |
| Inline package tools (documents, evidence) | The tools themselves; `_report_inline_package_result` | The tools bound their own results. The adapter reports `tool_result_budget_unmet` results, and also reports any result over budget as an observed escape (`enforced=false`). |
| Builder run-state tools | Shared builder helpers; `streaming_tools._enforce_run_state_tool_result_budget` | Acknowledgments carry counts only, and pages are size-fitted. An oversized result is reported and replaced by a compact failure that keeps `operation_status`. |

## Package tool bindings (`packages/alliance/tools/bindings.yaml`, 67 bindings)

All of these results are returned to a model. Test files are shortened as follows:

- `ev` = `backend/tests/unit/lib/openai_agents/tools/test_evidence_workspace_result_bounds.py`
- `bld` = `backend/tests/unit/lib/openai_agents/tools/test_builder_result_bounds.py`
- `doc` = `backend/tests/unit/lib/packages/alliance/test_weaviate_search_result_bounds.py`
- `pkg` = `backend/tests/unit/lib/agent_studio/test_package_tool_result_bounds.py`
- `core` = `backend/tests/unit/lib/openai_agents/test_tool_result_bounds.py`

| Family / tools | Disposition | Evidence |
|---|---|---|
| **Evidence list:** `list_recorded_evidence` | Page clamped to `EVIDENCE_LIST_MAX_LIMIT` and fitted by bytes. Quotes are omitted (character count kept). A summary too large for a page is withheld with its identity and routed to `get_recorded_evidence`. Offsets are validated. | `ev::test_huge_requested_limit_clamps_to_configured_maximum`, `::test_wide_records_page_by_serialized_size_without_duplicates`, `::test_invalid_or_stale_offset_is_an_explicit_error`, `::test_unmeetable_budget_returns_compact_failure_and_reports_once` |
| **Evidence detail:** `get_recorded_evidence` | The whole record when it fits. Otherwise a bounded view with withheld long values (for example `record.verified_quote`), read through exact `detail_path` chunks, with stale detection. Document scope and validator scope are enforced before any read. | `ev::test_oversized_single_quote_is_read_exactly_in_bounded_chunks`, `::test_detail_read_reports_changed_record_as_stale`, `::test_detail_reads_keep_document_and_validator_scope` |
| **Evidence mutations:** `attach_evidence_to_object`, `detach_evidence_from_object`, `discard_recorded_evidence`, `update_recorded_evidence_metadata` | The acknowledgment is the changed record's compact summary, without the quote. If agent-authored metadata makes even that too large, only the identity is returned; the mutation is never reported as failed. | `ev::test_mutation_acknowledgment_omits_quote_and_stays_bounded` |
| **Evidence creation:** `record_evidence` | Acknowledges one record, limited to the spans of one source chunk. The adapter reports any result over budget as an observed escape. | `pkg::test_inline_tool_escape_is_reported_but_sibling_contract_tool_is_not` |
| **Builder mutations** (7 families: gene expression, gene mention, allele, phenotype, disease, GO recommendation, generic): `stage_*`, `patch_*`, `discard_*`, `finalize_*` | `_builder_summary` returns counts (candidates, pending refs, evidence, resolver selections), not id arrays. Acknowledgments name the changed record (`candidate_id`, `discarded_candidate_id`). The finalization receipt carries counts plus the materialized `candidate_ids`; the full lists stay on the workspace finalization. The run-state adapter guards against escapes. | `bld::test_builder_ack_does_not_grow_with_the_workspace`, `::test_discard_ack_names_the_changed_candidate_and_stays_constant`; `test_gene_expression_builder_tools.py::test_finalize_returns_compact_builder_summary`; `pkg::test_oversized_builder_result_becomes_reported_compact_failure` |
| **Builder pages** (7 families): `list_staged_*`, `find_staged_*` | Clamped to `BUILDER_LIST_MAX_LIMIT` and fitted by bytes. Per-row decorations (generic attribute keys and drift notices) are counted inside the page budget. A candidate too large for one page is withheld with its identity and counts. Offsets are validated. | `bld::test_huge_page_limit_clamps_to_builder_maximum`, `::test_wide_candidates_page_by_size_completely_without_duplicates`, `::test_per_row_decorations_count_toward_the_page_budget`, `::test_oversized_single_candidate_is_withheld_with_identity_and_counts`, `::test_invalid_builder_offset_is_explicit` |
| `list_generic_object_classes` | A static class catalog, served through the subprocess adapter. | Adapter contract: `pkg::test_large_query_pages_completely_by_size_with_hash_checked_continuation` |
| **Retrieval sections:** `read_section`, `read_subsection` | `max_chunks` is clamped to `SECTION_READ_PAGE_MAX_CHUNKS`, and pages are fitted by the bytes of the whole result (text, locators, `doc_items`). A passage too large for any page is listed with `content_withheld` and read with `read_chunk`. Offsets are validated. | `doc::test_long_passages_page_by_size_with_complete_continuation[section|subsection]`, `::test_extreme_max_chunks_clamps_and_reports`, `::test_invalid_section_offset_is_explicit`, `::test_single_oversized_passage_is_withheld_with_a_read_chunk_pointer` |
| **Retrieval search:** `search_document` | Every ranked hit stays listed. Full text is included in rank order while the result fits; any other hit becomes a pointer (`content_withheld`, `content_chars`) to be read with `read_chunk`. | `doc::test_search_keeps_every_ranked_hit_within_budget` |
| **Retrieval chunk:** `read_chunk` | The whole chunk when it fits. Otherwise contiguous, exact, span-aligned windows (`content_range`, `span_page.next_span_offset`) that together rebuild the chunk and every span. | `doc::test_oversized_chunk_reads_in_exact_span_windows` |
| **Curation DB lookups:** `agr_curation_query`, `agr_species_context_lookup`, `search_domain_field_terms`, `inspect_ontology_term` | Subprocess adapter pages: by row (list data, or the largest list inside data), or by field for one wide record. Oversized rows and fields are withheld behind exact detail chunks. Row caps (`AGR_DEFAULT_LIMIT` / `AGR_HARD_MAX`) are unchanged, and no fields are removed. | `pkg::test_compact_query_results_are_returned_unchanged`, `::test_large_query_pages_completely_by_size_with_hash_checked_continuation`, `::test_oversized_single_record_is_read_exactly_through_detail_chunks`, `::test_changed_result_is_reported_stale_not_mixed`, `::test_invalid_query_offset_is_explicit`, `::test_unmeetable_budget_reports_one_escape`; `core::test_wide_single_record_pages_by_field_and_reassembles` |
| **External REST and SQL:** `chebi_api_call`, `quickgo_api_call`, `alliance_api_call`, `go_api_call`, `resolve_gene_product`, `agr_literature_reference_lookup`, `curation_db_sql` | The same subprocess adapter contract (REST bodies are paged as `data`, SQL `rows` are paged). Their existing source limits are unchanged (`GO_ANNOTATIONS_PAGE_MAX_RESULTS`, `RNA_GENE_PRODUCT_MAX_CANDIDATES`, `LITERATURE_REFERENCE_HARD_MAX`). Paging re-runs the call, so REST writes (`method` POST, PUT, PATCH or DELETE) are never paged: an oversized write response is a reported `tool_result_budget_unmet` failure (blocker B7). | Adapter contract tests above; `pkg::test_write_requests_are_never_paged_by_repetition`; primary-collection selection in `core` |
| **Validator lookups** (`LOOKUP_TOOLS` in the compact runtime) | The complete lookup is captured in the application. The model pages the stored lookup with `lookup_ref`, getting its rows plus exactly those rows' record refs, with no re-run and scoped to the invocation. An unknown `lookup_ref`, or paging without one, is rejected. | `pkg::test_validator_capture_keeps_complete_lookup_and_pages_refs_with_rows` |
| **Provider loop** | A mocked Agents SDK run shows that the tool output in history holds only the bounded page; rows past the page stay in the application. | `pkg::test_provider_loop_history_holds_only_the_bounded_page` |
| `resolve_domain_field_term` (run state) | Its candidate `limit` (and those of `search_domain_field_terms`, `inspect_ontology_term` and the internal `get_domain_field_term_options`) is clamped to `TOOL_PAGE_MAX_LIMIT` (default 50). The byte size is observed only: an oversized result is reported (`enforced=false`) and passed through unchanged, because the resolver ledger consumes the complete output. **Blocker B3.** | `test_all1278_regressions.py::test_term_resolver_limit_is_capped`, `::test_term_search_limit_is_capped`; `pkg::test_resolver_ledger_tool_is_observed_not_replaced` |
| `get_agent_contract` | **Owned by ALL-1277** (`backend/src/lib/agent_contracts.py`). Excluded from this adapter's measurement. | See ALL-1277 |

## Backend-internal tools (main chat, flows, specialists, validators)

| Tool | Model-facing | Disposition |
|---|---|---|
| Flow chat-output and formatter tools (`explain_formatter_capabilities`, `inspect_output_*`, `build_default_projection_plan`, `validate_output_projection`, `preview_output_projection`, `finalize_and_save`, `formatter_cannot_complete`), plus flow chat output in `flows/executor.py` | Yes | **Owned by ALL-1275** (application-owned rendering and bounded output inspection). Not edited here. |
| `inspect_results` (chat and preferred flow) | Yes | Closed (B1, ALL-1287). Every response, including errors, is measured against `TOOL_RESULT_MAX_BYTES`. `summary` returns counts only (by object type, status, validation state, findings, validator results, evidence). `objects`, `validation`, `validator_results`, `evidence`, `details`, `list` and `search` pages are fitted by bytes (default object page `INSPECT_RESULTS_OBJECT_PAGE_SIZE`=20, clamped to `INSPECT_RESULTS_OBJECT_MAX_PAGE_SIZE`) and continue with `next_call`. Pages carry `result_sha256` (result content plus view), so a changed result or filter returns `stale_result_cursor`; malformed or past-end cursors return `invalid_result_cursor`. Objects filter by `object_type`, `status`, `validation_state`, `severity`, `query` and `field_path`, and `fields` selects manifest fields. No value is shortened: values over `SUPERVISOR_FIELD_TEXT_LIMIT` characters, and rows too large for a page, are withheld with size, `value_sha256` and a `read` call. `field`, one finding (`finding_ref`), one validator result (`validator_result_key`) and evidence `detail_path` reads return the whole value when it fits, otherwise exact contiguous chunks. Every read resolves through the curator's authorized records first. | `test_inspect_results_bounds.py::test_default_object_page_is_smaller_and_fits_the_budget`, `::test_object_pages_are_complete_in_order_without_duplicates`, `::test_summary_is_a_compact_inventory_of_counts`, `::test_filters_by_type_status_validation_and_field_text`, `::test_unknown_or_hidden_fields_and_bad_filters_are_rejected`, `::test_oversized_unicode_field_is_withheld_and_read_exactly`, `::test_findings_and_validator_results_page_and_read_exactly`, `::test_evidence_pages_and_oversized_quote_reads`, `::test_other_session_results_are_rejected_for_every_read`, `::test_changed_result_and_bad_cursors_are_explicit`, `::test_unmeetable_budget_returns_compact_failure_and_reports_once`, `::test_list_and_search_pages_fit_and_continue` |
| `recall_chat_history` | Yes | Closed (B1). `recent`, `turn` and `search` pages are fitted by bytes and continue with `next_cursor`; malformed or stale cursors return `invalid_cursor`. Only a cursor past the current end is detected as stale; a transcript that grows between pages is not. A message too large for a page is withheld with its size and `sha256` and one `detail_calls` entry per text field, longest first (a flow row's long text is in `flow_assistant_message`). `detail="message"` reads that field exactly in chunks (`content_cursor`, `next_content_cursor`), and the page message says when rows are withheld. Echoed inputs (turn reference, query, unsupported detail) are short previews, and a search query over `SUPERVISOR_FIELD_TEXT_LIMIT` characters is rejected as `invalid_request`. Every read is scoped to the curator's own active session. | `test_supervisor_recall_result_bounds.py::test_recent_pages_by_size_and_withheld_messages_read_exactly`, `::test_turn_and_search_results_page_by_size`, `::test_message_detail_is_scoped_to_the_active_session`, `::test_invalid_or_stale_recent_cursor_is_explicit`, `::test_invalid_content_cursor_is_explicit`, `::test_unmeetable_budget_returns_compact_failure_and_reports_once`, `::test_withheld_flow_row_points_at_its_long_field`, `::test_echoed_inputs_stay_short_and_long_queries_are_rejected`; `test_all1278_regressions.py::test_b1_recall_chat_history_recent_page_is_bounded` |
| `inspect_chat_traces` | Yes | Existing page limits, and each string is cut to 1200 characters, so results are bounded in practice. There is no total byte budget. A possible loss where the 1200-character cap meets 8000-character conversation chunks is unconfirmed. Deferred with B1's remainder. |
| `ask_*_specialist` and specialist-as-tool wrappers, flow step tools, flow curation prep and handoff | Yes (to the supervisor) | Structured results are reduced to manifests (`_reduce_specialist_output_for_supervisor`). Unstructured answer text and the curation-prep `envelope_refs` are not size-budgeted. **Blocker B2.** |
| `finalize_structured_result`, `finalize_validator_result`, `finalize_validator_batch_results` | Yes | In compact validator runtime mode, only the acceptance receipt is returned. The non-compact validator mode echoes the accepted result. **Blocker B2.** |
| `prepare_for_curation` | Yes | Short status that echoes the model-supplied `result_refs`, so its size depends only on the model's own input. |

## Agent Studio tools

Every Studio tool result passes through the generic provider cap,
`AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS` (`api/agent_studio.py::_provider_tool_result_content`).
Oversized results reach the model as a compact summary with hashes, and the full
result is recalled through `get_chat_turn`. Package diagnostic tools use
`agent_studio/diagnostic_tools/result_contracts.py` (summary, page and detail
views). This ticket leaves all of that unchanged. Evidence:
`test_agent_studio_context_compaction.py`,
`test_agent_studio_diagnostic_result_contracts.py`, `test_flow_tools.py`,
`test_tool_inventory_search.py`, `test_domain_envelope_tools.py`,
`test_capability_catalog.py`.

## Recorded blockers

The coordinator deferred B2, the rest of B3, and B4 through B7 to follow-up tickets on 2026-09-22. Each keeps the disposition recorded below.

- **B1. Supervisor context recall tools. Closed for `recall_chat_history`** (the path that was reachable and unbounded); see its table entry. `inspect_results` is closed by ALL-1287 (production reached 124,879 characters in one result); see its table entry. Remainder deferred: `inspect_chat_traces` keeps its existing page limits and per-field caps without a total byte budget. It is bounded in practice, and ALL-1279's provider-boundary measurement covers it.
- **B2. Specialist and validator result echoes** (unstructured specialist answers, curation-prep `envelope_refs`, non-compact `finalize_validator_*` echoes) are not size-budgeted.
  - These are handoff contracts rather than inspection tools. Bounding them changes supervisor and validator semantics, which belong to the compact-decision design (ALL-1270 follow-up) and ALL-1279 measurement.
- **B3. `resolve_domain_field_term`** now clamps its candidate limit to `TOOL_PAGE_MAX_LIMIT`, but its total byte size is observed and reported, not replaced, because the resolver ledger records the complete output.
  - Bounding it needs the ledger to capture the complete result in the application (as the validator capture now does) before presentation.
- **B4. Groq provider adapter for `agr_curation_query`** (`create_groq_agr_curation_query_tool`) replaces the package tool in process, so it bypasses the subprocess adapter.
  - Groq requires every property to be required, which conflicts with optional view arguments.
  - Rows remain capped by `AGR_HARD_MAX`. It is only used for Groq models, and `packages/core/config/models.yaml` currently configures none.
- **B5. `doc_items` bounding boxes** in `search_document` and `read_section` results are application data. The stream-level `CHUNK_PROVENANCE` emission reads them from the tool output for PDF highlighting.
  - They now count toward the byte budget, but they still travel through model context.
  - Emitting provenance on the application side and removing them from model output needs a coordinated change to `streaming_tools._emit_chunk_provenance_from_output`.
- **B6. Oversized single builder candidate.** A redacted candidate summary too large for one page (more than ~30 KB of validation errors or ids for a single candidate) is withheld with its identity and counts, but there is no exact per-candidate detail read yet. The full state stays in the workspace and is used by finalization.
- **B7. Oversized REST write responses.** Paging re-runs a call, so a POST, PUT, PATCH or DELETE response larger than the budget is returned as a reported compact failure rather than paged. Capturing the write response once on the application side (as validator lookups are) would make it readable. The chemical agent prompt permits `POST` to ChEBI, so this path is reachable; it fails explicitly and is reported rather than returning an unbounded result.
