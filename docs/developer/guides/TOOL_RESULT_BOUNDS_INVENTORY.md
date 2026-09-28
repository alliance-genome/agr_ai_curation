# Tool Result Bounds Inventory

ALL-1278 / KANBAN-1835; remaining dispositions closed by ALL-1281 / KANBAN-1838. This inventory lists every registered tool family, says
whether its result reaches a model, and records how that result is bounded, with
the test that proves it. Explicit exemptions describe semantic handoffs that must stay complete. These
are dispositions, not claims of a total byte bound.

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

Families using the bounded-result contract share this behavior:

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
| Subprocess package tools (read-only lookups, REST, SQL) | `catalog_service._resolve_package_tool` → `_bounded_package_result` | The adapter adds `result_offset`, `result_sha256`, `detail_path`, `detail_cursor` and a captured-write `result_ref` to the tool schema. It strips them before the package runs and serves bounded pages or exact detail chunks from the recomputed, hash-checked result. Results that fit are returned unchanged. |
| Validator lookup capture (compact runtime) | `compact_runtime.CompactValidatorRuntime.wrap_lookup_tool` | Requests the complete result (`full_tool_results_requested`) and captures it in the application. The model then pages the stored lookup with `lookup_ref`, with no re-query. Pages and detail reads serve the lean model view (each returned row once, ALL-1285); the complete response stays in the workspace. Each page carries exactly the `validator_record_refs` for its rows, plus that page's shared `validator_record_available_fields` (a ref lists its own `available_fields` only where they differ, ALL-1291), so every page is self-describing. These view keys are reserved: a provider response using them is rejected. |
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
| **Builder mutations** (7 families: gene expression, gene mention, allele, phenotype, disease, GO recommendation, generic): `stage_*`, `patch_*`, `discard_*`, `finalize_*` | `_builder_summary` returns counts (candidates, pending refs, evidence), not id arrays. Acknowledgments name the changed record (`candidate_id`, `discarded_candidate_id`). The finalization receipt carries counts plus the materialized `candidate_ids`; the full lists stay on the workspace finalization. The run-state adapter guards against escapes. | `bld::test_builder_ack_does_not_grow_with_the_workspace`, `::test_discard_ack_names_the_changed_candidate_and_stays_constant`; `test_gene_expression_builder_tools.py::test_finalize_returns_compact_builder_summary`; `pkg::test_oversized_builder_result_becomes_reported_compact_failure` |
| **Builder pages** (7 families): `list_staged_*`, `find_staged_*` | Clamped to `BUILDER_LIST_MAX_LIMIT` and fitted by bytes. Per-row decorations (generic attribute keys and drift notices) are counted inside the page budget. A candidate too large for one page is withheld with its identity, counts and `detail_read` arguments for the corresponding `find_staged_*` tool. That tool reads the exact redacted summary (including decorations) in hash-checked chunks. Every read resolves through the active builder workspace and the same filters. Offsets are validated. | `bld::test_huge_page_limit_clamps_to_builder_maximum`, `::test_wide_candidates_page_by_size_completely_without_duplicates`, `::test_per_row_decorations_count_toward_the_page_budget`, `::test_oversized_single_candidate_is_withheld_with_identity_and_counts`, `::test_invalid_builder_offset_is_explicit`, `::test_all_find_tools_read_exact_summary_and_reject_stale_or_other_workspace` |
| `list_generic_object_classes` | A static class catalog, served through the subprocess adapter. | Adapter contract: `pkg::test_large_query_pages_completely_by_size_with_hash_checked_continuation` |
| **Retrieval sections:** `read_section`, `read_subsection` | `max_chunks` is clamped to `SECTION_READ_PAGE_MAX_CHUNKS`, and pages are fitted by the bytes of the whole result (text, locators, `doc_items`). A passage too large for any page is listed with `content_withheld` and read with `read_chunk`. Offsets are validated. | `doc::test_long_passages_page_by_size_with_complete_continuation[section|subsection]`, `::test_extreme_max_chunks_clamps_and_reports`, `::test_invalid_section_offset_is_explicit`, `::test_single_oversized_passage_is_withheld_with_a_read_chunk_pointer` |
| **Retrieval search:** `search_document` | Every ranked hit stays listed. Full text is included in rank order while the result fits; any other hit becomes a pointer (`content_withheld`, `content_chars`) to be read with `read_chunk`. | `doc::test_search_keeps_every_ranked_hit_within_budget` |
| **Retrieval chunk:** `read_chunk` | The whole chunk when it fits. Otherwise contiguous, exact, span-aligned windows (`content_range`, `span_page.next_span_offset`) that together rebuild the chunk and every span. | `doc::test_oversized_chunk_reads_in_exact_span_windows` |
| **Curation DB lookups:** `agr_curation_query`, `agr_species_context_lookup`, `search_domain_field_terms`, `inspect_ontology_term` | Subprocess adapter pages: by row (list data, or the largest list inside data), or by field for one wide record. Oversized rows and fields are withheld behind exact detail chunks. Row caps (`AGR_DEFAULT_LIMIT` / `AGR_HARD_MAX`) are unchanged, and no fields are removed. | `pkg::test_compact_query_results_are_returned_unchanged`, `::test_large_query_pages_completely_by_size_with_hash_checked_continuation`, `::test_oversized_single_record_is_read_exactly_through_detail_chunks`, `::test_changed_result_is_reported_stale_not_mixed`, `::test_invalid_query_offset_is_explicit`, `::test_unmeetable_budget_reports_one_escape`; `core::test_wide_single_record_pages_by_field_and_reassembles` |
| **External REST and SQL:** `chebi_api_call`, `quickgo_api_call`, `alliance_api_call`, `go_api_call`, `resolve_gene_product`, `agr_literature_reference_lookup`, `curation_db_sql` | The same subprocess adapter contract (REST bodies are paged as `data`, SQL `rows` are paged). Their existing source limits are unchanged (`GO_ANNOTATIONS_PAGE_MAX_RESULTS`, `RNA_GENE_PRODUCT_MAX_CANDIDATES`, `LITERATURE_REFERENCE_HARD_MAX`). Read-only continuation re-runs the call. Oversized REST write responses are captured once in the resolved tool instance and returned inside a bounded envelope with `result_ref`. Continuations require that ref, unchanged request inputs, and matching user/document/session/trace scope; they never execute the write. Captures expire with the tool instance; unknown/expired refs fail explicitly. | Adapter contract tests above; `pkg::test_write_response_is_captured_and_read_without_repetition`; primary-collection selection in `core` |
| **Validator lookups** (`LOOKUP_TOOLS` in the compact runtime) | The complete lookup is captured in the application. The model pages the stored lookup with `lookup_ref`, getting its rows plus exactly those rows' record refs, with no re-run and scoped to the invocation. An unknown `lookup_ref`, or paging without one, is rejected. | `pkg::test_validator_capture_keeps_complete_lookup_and_pages_refs_with_rows` |
| **Provider loop** | A mocked Agents SDK run shows that the tool output in history holds only the bounded page; rows past the page stay in the application. | `pkg::test_provider_loop_history_holds_only_the_bounded_page` |
| `resolve_domain_field_term` (run state) | Its candidate `limit` (and those of `search_domain_field_terms`, `inspect_ontology_term` and the internal `get_domain_field_term_options`) is clamped to `TOOL_PAGE_MAX_LIMIT` (default 50). The byte size is enforced like any run-state result: an oversized result is replaced (`enforced=true`). **Blocker B3, closed.** | `test_all1278_regressions.py::test_term_resolver_limit_is_capped`, `::test_term_search_limit_is_capped`; `pkg::test_an_oversized_resolver_result_is_replaced_like_any_run_state_result` |
| `get_agent_contract` | **Owned by ALL-1277** (`backend/src/lib/agent_contracts.py`). Excluded from this adapter's measurement. | See ALL-1277 |

## Backend-internal tools (main chat, flows, specialists, validators)

| Tool | Model-facing | Disposition |
|---|---|---|
| Flow chat-output and formatter tools (`explain_formatter_capabilities`, `inspect_output_*`, `build_default_projection_plan`, `validate_output_projection`, `preview_output_projection`, `finalize_and_save`, `formatter_cannot_complete`), plus flow chat output in `flows/executor.py` | Yes | **Owned by ALL-1275** (application-owned rendering and bounded output inspection). Not edited here. |
| `inspect_results` (chat and preferred flow) | Yes | Closed (B1, ALL-1287). Every response, including errors, is measured against `TOOL_RESULT_MAX_BYTES`. `summary` returns counts only (by object type, status, validation state, findings, validator results, evidence). `objects`, `validation`, `validator_results`, `evidence`, `details`, `list` and `search` pages are fitted by bytes (default object page `INSPECT_RESULTS_OBJECT_PAGE_SIZE`=20, clamped to `INSPECT_RESULTS_OBJECT_MAX_PAGE_SIZE`) and continue with `next_call`. Pages carry `result_sha256` (result content plus view), so a changed result or filter returns `stale_result_cursor`; malformed or past-end cursors return `invalid_result_cursor`. Objects filter by `object_type`, `status`, `validation_state`, `severity`, `query` and `field_path`, and `fields` selects manifest fields. No value is shortened: values over `SUPERVISOR_FIELD_TEXT_LIMIT` characters, and rows too large for a page, are withheld with size, `value_sha256` and a `read` call. `field`, one finding (`finding_ref`), one validator result (`validator_result_key`) and evidence `detail_path` reads return the whole value when it fits, otherwise exact contiguous chunks. Every read resolves through the curator's authorized records first. | `test_inspect_results_bounds.py::test_default_object_page_is_smaller_and_fits_the_budget`, `::test_object_pages_are_complete_in_order_without_duplicates`, `::test_summary_is_a_compact_inventory_of_counts`, `::test_filters_by_type_status_validation_and_field_text`, `::test_unknown_or_hidden_fields_and_bad_filters_are_rejected`, `::test_oversized_unicode_field_is_withheld_and_read_exactly`, `::test_findings_and_validator_results_page_and_read_exactly`, `::test_evidence_pages_and_oversized_quote_reads`, `::test_other_session_results_are_rejected_for_every_read`, `::test_changed_result_and_bad_cursors_are_explicit`, `::test_unmeetable_budget_returns_compact_failure_and_reports_once`, `::test_list_and_search_pages_fit_and_continue` |
| `recall_chat_history` | Yes | Closed (B1). `recent`, `turn` and `search` pages are fitted by bytes and continue with `next_cursor`; malformed or stale cursors return `invalid_cursor`. Only a cursor past the current end is detected as stale; a transcript that grows between pages is not. A message too large for a page is withheld with its size and `sha256` and one `detail_calls` entry per text field, longest first (a flow row's long text is in `flow_assistant_message`). `detail="message"` reads that field exactly in chunks (`content_cursor`, `next_content_cursor`), and the page message says when rows are withheld. Echoed inputs (turn reference, query, unsupported detail) are short previews, and a search query over `SUPERVISOR_FIELD_TEXT_LIMIT` characters is rejected as `invalid_request`. Every read is scoped to the curator's own active session. | `test_supervisor_recall_result_bounds.py::test_recent_pages_by_size_and_withheld_messages_read_exactly`, `::test_turn_and_search_results_page_by_size`, `::test_message_detail_is_scoped_to_the_active_session`, `::test_invalid_or_stale_recent_cursor_is_explicit`, `::test_invalid_content_cursor_is_explicit`, `::test_unmeetable_budget_returns_compact_failure_and_reports_once`, `::test_withheld_flow_row_points_at_its_long_field`, `::test_echoed_inputs_stay_short_and_long_queries_are_rejected`; `test_all1278_regressions.py::test_b1_recall_chat_history_recent_page_is_bounded` |
| `inspect_chat_traces` | Yes | Existing page limits, and each string is cut to 1200 characters, so results are bounded in practice. There is no total byte budget. A possible loss where the 1200-character cap meets 8000-character conversation chunks is unconfirmed. Deferred with B1's remainder. |
| `ask_*_specialist` and specialist-as-tool wrappers, flow step tools, flow curation prep and handoff | Yes (to the supervisor) | Structured results are reduced to manifests (`_reduce_specialist_output_for_supervisor`). Unstructured answer text and curation-prep `envelope_refs` remain complete semantic handoffs: explicit permanent exemption B2 below. They are not byte-bounded retrieval results. |
| `finalize_structured_result`, `finalize_validator_result`, `finalize_validator_batch_results` | Yes | Validator finalizers return a constant acceptance receipt in both compact and non-compact modes; canonical results remain in finalization state. Structured specialist finalization keeps its existing contract. See B2 below. |
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

## Closed dispositions (ALL-1281)

These decisions replace the Sep 22 deferrals for B2-B7. No change is made to
ALL-1275 formatter contracts, ALL-1277 agent discovery, or ALL-1279 provider
measurement/blocking.

- **B1. Supervisor context recall tools.** Closed for `recall_chat_history` and
  `inspect_results` (ALL-1287); see the table. The separate `inspect_chat_traces`
  remainder retains its existing page/per-field bounds and remains outside ALL-1281.
- **B2. Handoffs: receipt bound plus permanent semantic exemptions.** Accepted
  `finalize_validator_result` and `finalize_validator_batch_results` no longer
  echo accepted results, including in non-compact mode. Both return a constant
  receipt while the full result stays in application finalization state. Nothing
  scientific is truncated, and the authoring validator already has its input.
  Rejected-result repair feedback is unchanged. Evidence:
  `test_validator_dispatch.py::test_accepted_validator_receipt_does_not_echo_large_result`
  (single and batch, large Unicode explanations).
  - Unstructured specialist answers are intentionally exempt from the retrieval
    page contract. They are the specialist's authored answer to the supervisor,
    not an expanding database/document lookup. Paging them would turn a completed
    specialist invocation into an inspection protocol and change answer semantics.
    Keep the complete answer, including scientific text; do not silently shorten
    it or claim a byte bound. Evidence:
    `test_streaming_tools_helpers.py::test_specialist_answer_handoff_preserves_exact_scientific_text`.
    Structured extraction results still use the existing supervisor manifests.
  - Curation-prep `envelope_refs` are an application handoff manifest, not source
    scientific content. `flows/executor.py::_make_curation_prep_tool` returns
    `CurationPrepAgentOutput`; `curation_workspace/pipeline.py` consumes the
    complete revision selection and checks its review-row total. Permanently retain
    that complete typed handoff. Paging/removing refs would change which revisions
    reach curator review. The schema and pipeline remain authoritative; this is an
    explicit exemption, not an assertion that the manifest has a total byte cap.
    Evidence: `test_pipeline.py::test_execute_post_curation_pipeline_persists_domain_envelope_projection_ref_from_envelope_row`
    and `::test_execute_post_curation_pipeline_materializes_envelope_rows_without_normalizer`.
  - `finalize_structured_result` stays with its existing structured finalization
    contract. Do not alter formatter or supervisor semantic contracts to impose
    the lookup page shape. Provider-boundary accounting remains ALL-1279's owner.
- **B3. Resolver ledger: permanently retired.** Main removed extraction searches
  and the resolver ledger (ALL-1276). There is no complete ledger response to
  capture. Preserve the existing `TOOL_PAGE_MAX_LIMIT` clamp and enforced
  `TOOL_RESULT_MAX_BYTES` run-state guard; do not reintroduce a ledger/cache for
  this retired use case. Evidence: `test_all1278_regressions.py` term-limit tests
  and `pkg::test_an_oversized_resolver_result_is_replaced_like_any_run_state_result`.
- **B4. Groq adapter: bounded.** Optional page/detail arguments travel inside
  `payload_json`, preserving Groq's all-required outer schema. The shared
  `bounded_json_result` contract supplies hash-checked pages and exact chunks;
  application capture can still request the full response. The backend adapter
  reports only unexpected budget escapes. No Groq model needs to be configured
  to exercise this path. Evidence:
  `test_agr_curation_provider_config.py::test_groq_wrapper_pages_unicode_and_preserves_full_capture`
  and `::test_groq_wrapper_reads_oversized_record_exactly_and_checks_hash`.
- **B5. PDF provenance: permanent keep-in-budget disposition.** Retain
  `doc_items` in model-facing retrieval pages and count every bounding box
  against the same byte budget. Moving it to a second channel is unnecessary
  for boundedness and would change the stream-level `CHUNK_PROVENANCE` contract.
  The current stream consumer keeps the exact coordinates needed for highlighting.
  Evidence: `doc::test_pdf_provenance_stays_exact_inside_the_page_budget` and the
  existing section/chunk continuation tests. No provenance is silently removed.
- **B6. Builder summaries: bounded exact detail reads.** All seven find tools
  accept `detail_path="candidate"`, `candidate_id`, `detail_cursor` and
  `result_sha256` from the withheld row's `detail_read`. Concatenated canonical
  JSON chunks recover the complete redacted summary, including long validation
  errors, reference arrays and generic decorations. Staged scientific values stay
  application-side as before. Active workspace/filter checks precede every read;
  changed summaries and invalid cursors fail explicitly. Evidence:
  `bld::test_all_find_tools_read_exact_summary_and_reject_stale_or_other_workspace`
  across seven tools at 2048 and 8192 bytes.
- **B7. REST write responses: captured once, then paged.** The adapter stores the
  original response before bounding presentation. It binds opaque `result_ref`
  reads to the tool instance, original arguments, user/document and current
  session/trace context. Missing, changed-scope or unknown refs fail before any
  package execution. A ref does not persist across tool lifetimes; restarting a
  write is never an automatic recovery path. Complete application captures bypass
  presentation as before. Evidence:
  `pkg::test_write_response_is_captured_and_read_without_repetition` covers POST,
  PUT, PATCH and DELETE, Unicode, minimum budgets, upstream changes, invalid refs
  and cross-session reads while asserting exactly one write.

### TraceReview extraction for bounded continuations

TraceReview parses the Agents SDK's Python dictionary literals and complete
`AgrQueryResult` field representations, including nested detail content, hashes,
returned ranges, cursors and captured response refs. Raw evidence remains intact.
SDK serialization regressions in
`backend/tests/unit/lib/openai_agents/test_bounded_result_trace_review.py` prove
exact Unicode reconstruction across detail chunks at the minimum and larger
budgets. Parser and analyzer regressions also cover nested pages, empty results,
escaped field-like scientific text and rejection of nonliteral expressions.
