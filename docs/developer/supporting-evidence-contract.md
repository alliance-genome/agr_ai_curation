# Supporting evidence passed to validators

Supporting paper evidence is a collection, even when there is only one quote.
Use the existing `source: evidence_record`, `output: quote_bundle`,
`allow_multiple: true` selector. Keep optional evidence `required: false` and
`context_only: true`; do not apply this rule to scalar identifiers or ontology
selections. Missing required evidence remains a selector failure.

Each bundle carries `evidence_record_id`, `verified_quote`, and the selected
`field_path` when available. Available `chunk_id`, `section`, `subsection`,
`page`, `document_id`, `source_document_id`, and `figure_reference` remain on
their own record, never in parallel arrays. Object evidence references and
existing field selectors bound the collection. Duplicate references to one
evidence ID produce one bundle, while different IDs (including conflicting
quotes) remain distinct. Consumers must use database/tool grounding for
identity resolution; accepting multiple quotes is not evidence of resolution.

## Shipped binding audit (ALL-1214)

| Domain pack | Supporting-evidence bindings | Change |
| --- | --- | --- |
| Allele | Allele mention reference validation; its reusable custom-profile context input | Scalar quote to `evidence_quotes` bundle array |
| Disease | Experimental-condition validation | Scalar quote to bundle array |
| Phenotype | Phenotype ontology-term and experimental-condition validation | Scalar quote and separate chunk/section inputs to bundle arrays |
| Gene | Object-level Alliance gene reference lookup | Payload quote to evidence-record bundle array |
| Gene expression | Eight supporting-evidence selectors | Already bundle arrays; field filters unchanged |
| GO | GO supporting-evidence selector | Already a bundle array; requiredness unchanged |
| Base / generic | No native supporting-evidence selectors | Custom profiles use their explicitly mapped capability |

The runtime-registry audit test covers all 14 shipped selectors (including
object-level bindings), zero/one/two records, unrelated objects and fields,
duplicate references, and per-quote locations. Compiled allele custom-profile
tests cover optional context and nested fields. A native allele dispatch test
proves two conflicting quotes reach the runner and an unresolved result stays
unresolved. Tests are deterministic; they do not prove a live model will make
the correct biological decision.

Gene extraction still emits one object per candidate/evidence pairing; this
change does not merge those objects. Validator dispatch retains its existing
batching policy: requests carrying quote bundles retain isolated runs where
configured. Requests with quote bundles for different records share one run only
when every input is exactly equal: the selected inputs (including every quote
bundle), the request evidence (ID, entity, locators and quote), the guidance and
the binding. No similarity matching is used. Each record keeps its own answer.
A copy for another record ends its explanation with "Checked once with N other
records that had the same value and evidence.", where N counts records, not
fields; a copy for another field of the same record keeps the answer unchanged. Validators receive supplied evidence directly, cannot search/read
the paper or manage source evidence, and return resolutions/questions separately.
Database/ontology lookup tools remain available. Missing context is reported to
the supervisor without an automatic extraction retry.

### What the validator model sees

The persisted request keeps every evidence record whole. The model input
(`validator_request_payload_for_agent`) shows each request-level `evidence`
record as `evidence_record_id`, `entity`, `section`, `subsection`, `page`,
`figure_reference` and `verified_quote` only, and leaves out a record whose ID and quote already appear
in `selected_inputs.evidence_quotes`. That input keeps its name and shape, because
custom validators read it. A batch input lists each distinct record once in
`evidence_records`; each request names its own in `evidence_record_ids`. A record
without an ID, or one whose ID another request supplies with different content,
stays in that request's own `evidence` list. Evidence references in decisions are
still checked against the persisted request.

## Rollout

Deploy package YAML and validator prompts together. Allele's reusable
capability now advertises `evidence_quotes` as an array of records instead of
the former scalar `evidence_quote`. Its capability fingerprint and allele
domain-pack version change (0.1.0 to 0.1.1), giving the persisted immutable
capability snapshot a new identity. The application release version is separate.
Previously pinned custom-profile mappings must pass the existing review/resave
flow against the new capability before use; do not silently rewrite saved
revisions or bypass fingerprint checks. Historical failed validation results
are not automatically repaired; revalidation is a separate curator action.

### Source-block boundaries and existing documents

Title- and paragraph-based ingestion keeps source elements separated by blank
lines. New chunks do not borrow the preceding element/section's tail: doing so
would attach text to a chunk whose element/page/section provenance does not own
it. Configured overlap still applies when splitting an oversized single element;
character/sentence chunking is unchanged. Adjacent-chunk navigation remains
available for contextual reading.

### Table pieces (whole rows)

Title-based chunking hands every Markdown table element to
`backend/src/lib/pipeline/table_chunking.py`. A table piece starts and ends on
a row boundary, never inside a row, and table pieces get no overlap. Rows are
packed up to the chunk size (1,500 characters); a row longer than that is a
piece of its own, whole. A table that fits beside the text before it stays in
that piece, as before. Inside table rows, `<br>` becomes a space and runs of
spaces become one; the stored converted Markdown is unchanged.

The header rows stay where the source has them: they are the first rows of the
table's first piece. Every table piece records its rows in the chunk metadata
(`ChunkMetadata.table_segments`): the table number within the file, the
piece's character range, its first and last data row, the total data rows, the
header row count, the header rows' text, and where the Markdown separator line
sits (first piece only; only the table's second line is ever the separator, so
an all-dash data row such as `| - | - |` stays a data row). The header text is never copied
into stored piece text. The header rule counts empty cells only: the header is
the Markdown header row plus each following row up to and including the first
row whose filled share is at least `DOCUMENT_TABLE_HEADER_MIN_FILLED_FRACTION`
(0.5), at most `DOCUMENT_TABLE_HEADER_MAX_ROWS` (5) rows. Two weak spots are
pinned by tests: a column-name row that is itself less than half filled pulls
the first data row into the header, and a header longer than a piece is cut
like ordinary rows while every piece's metadata carries the whole header.

A heading followed by a heading under it (its section path is a proper prefix
of the next heading's, in the same parent section) stays with it, up to the
chunk size; the innermost heading owns the section metadata, and its section
path still names every stacked heading. A workbook's file title followed by a
sheet heading is such a pair. A heading followed by a sibling heading, or with
nothing after it, is still a piece of its own, so no section loses its own
metadata. A heading that does not fit beside its next element is carried into
that element's first piece, which can then exceed the chunk size by the
headings' length.

A row too long to embed (over `EMBEDDING_MODEL_TOKEN_LIMIT` minus
`EMBEDDING_TOKEN_SAFETY_MARGIN`, 7,691 tokens by default) is kept whole, so
storage fails at the token preflight with `Weaviate storage failed: Chunk N for
document <id> has T tokens (limit: L)`. This also applies to main papers: such a
row used to be split, and now the whole import fails loudly instead. Nothing
splits it as a fallback. Refusing such a supplement before it is parsed is the
supplement file check's job.

These rules apply to text cut after this change. Stored pieces are not rewritten.

### Table rows as quotable lines

`build_evidence_spans` reads the piece's `table_segments` (the structural flag
written at chunking, never a guess from the text). Inside a segment, each table
row is one span of type `table_row`; only the separator line chunking recorded
(`separator_char_start`) is no span, so an all-dash data row stays a row. Text outside segments is split into sentences exactly as before, so a
piece without table segments, including any table text stored before
whole-row pieces, gets the same spans byte for byte. The span hash keeps the
`pdf_sentence_v1` salt, so every span ID minted earlier still resolves. The
`spanizer_version` on saved evidence therefore no longer tells the two
segmentations apart; `span_type` does.
`resolve_evidence_span_id` takes the same segments, and a span inside one
resolves as `table_row`; `record_evidence` passes them from the stored chunk
metadata, so a saved source fragment records `span_type: table_row`.

`read_chunk`, `read_section`, `read_subsection` and `search_document` return the
segments as `tables`. The model view (`document_result_view.py`) prints, above
the first shown row of each table, one unnumbered label line,
`Columns of table N (this passage holds rows A to B of T): <first header row>`,
with further header rows on lines indented four spaces. Search hits list the
same labels under `table_labels` and keep their content unchanged. The label is
never an issued span, so `record_evidence` cannot select it. TraceReview's
`document_read_results.py` decodes label lines (prefix `Columns of table `) as
context and the rows as numbered lines; the round-trip fixture
`trace_review/backend/tests/fixtures/numbered_document_view.json` includes a
table passage.

Rows as lines turn a table piece from about one span into one per row, so a
whole-sheet read stores many more span definitions in the conversation history.
A conversation that read a table piece cut by whole rows before this change
would see its definitions change; whole-row pieces and row spans therefore ship
in the same release.

Evidence sentence generation treats blank lines as source-block boundaries,
including headings without terminal punctuation. Single line breaks remain
possible PDF line wraps. Spans are exact slices of stored chunk text; no cleanup
or invented sentence punctuation is included in verified quotes.

The `pdf_sentence_v1` offset/hash identity contract is unchanged. New span lists
can have finer boundaries, but resolution of a saved ID validates its encoded
source offsets and hash directly rather than regenerating the sentence list.
Previously saved evidence and IDs therefore remain valid against unchanged
stored chunks, including historical mixed spans. Existing fused text without
boundaries is not repaired by deploying this correction.

Existing documents benefit from corrected ingestion only when they are imported
again; there is no reprocess route. Do not replace indexed text beneath existing
evidence references or silently rewrite historical quotes. Before repairing an
existing document, verify the chosen import workflow preserves the historical source identity and
audit records (use a distinct document/source identity when necessary). Broad
reindexing and repair of stored curator documents are separate operational work.

The v0.10.9 hotfix advances the allele domain pack to `0.1.2` to separate
the operational validator tool allowance from immutable capability identity. Saved
profiles require reviewed `0.1.1` to `0.1.2` capability transitions; preserve
previous capability receipts and custom validator revision pins. Scientific
input/output mappings remain unchanged. The live binding supplies the configured
`max_tool_calls`; capability snapshots omit that operational setting so later
allowance changes do not invalidate saved profiles.
