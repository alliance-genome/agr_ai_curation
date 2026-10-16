# Tool result and selection boundaries

The former per-tool serialized-size budgets are retired. The shared native
Responses policy owns model-context capacity for all selected tool, user,
validator and model content. See [Agent context continuation](AGENT_CONTEXT_CONTINUATION.md)
for the actual request count, configured compaction threshold, complete-prefix
compaction, exact durable ingestion and failure behavior.

| Surface | Current behavior |
| --- | --- |
| PDF search | All selected ranked hits retain their complete text and document-local chunk index. Retrieval ranking/query limits remain explicit selection controls. |
| PDF section/subsection | Return the whole selected section by default; explicit chunk offset/count selects a requested window. No byte quota or withheld source text. |
| PDF chunk | Exact text and deterministic span selections; canonical UUID/hash provenance remains internal. Required source failures stop extraction. |
| Package queries | Complete selected result; explicit row/field views preserve hash and authorization. |
| Package writes | Capture once, return complete data plus result_ref; exact reads never execute the write again. |
| Evidence/builders | Explicit list filters/row pages and typed mutation/finalization receipts remain business contracts. Exact record/source content is available through the authorized read tools. |
| Supervisor inspection | Explicit summary/index/preview projections help locate results. The single-object preview may use SUPERVISOR_FIELD_TEXT_LIMIT to describe long fields behind exact read calls. Explicit field/validation detail reads return selected content whole; no response-size eviction. |
| Studio/Workshop | Full selected prompts, metadata, guides and tool results. Main and Studio histories are separately owner-scoped and durable. |
| TraceReview | Advisory serialized-character/token metadata, explicit named sections/record pages and exact payload reads. No Studio provider-envelope quota. |
| Formatters/exports | Curator field selection, row/column projections, previews and finalized artifact receipts remain explicit output contracts. They do not truncate scientific text to fit model context. |
| Transport/storage | Explicit transport frame, artifact and storage constraints remain documented operational settings; they are not model-context heuristics. |

Every operational limit must be environment-configurable and documented in
`.env.example`. Tests must verify exact selected content, owner/source integrity,
complete continuation and writes occurring once. Do not restore `TOOL_RESULT_MAX_BYTES`,
`budget_failure`, per-section byte quotas, or last-four-result eviction.

Coverage lives in the context-capacity/real-SDK transport tests, durable history
and source-reference persistence tests, package result adapters, document tools,
Studio exact-read tests and TraceReview endpoint/metric tests. A passing mocked
provider test is not evidence of live provider acceptance or scientific quality.
