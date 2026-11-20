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

### Which request evidence a binding receives (ALL-1584)

A request's `evidence` list holds the record's evidence that belongs to that
binding, not the whole record's. Code decides this from stored structure only:
the field tags on each evidence record (`field_path`, `field_paths`,
`envelope_targets[].field_path`) and the binding's declared paths. It never
reads quote text. The rule lives in `backend/src/lib/domain_packs/evidence_scope.py`.

- A binding's own paths are its target field (resolved to the element it
  validates), its `applies_to.field_paths`, its `route_by` path and every path
  its selectors read (`payload` and `payload_keyed_literal` `path`;
  `evidence_record` and `object_ref` `field_path`), including `context_only`
  inputs. Paths of a fanned-out element carry the engine's indexes the same way
  `selected_inputs` do.
- A tag matches an own path when one path equals, contains or lies inside the
  other, segment by segment on field names (`site` and `site.anatomy.mention`
  both match `site.anatomy`; `site.component` and `site.anat` do not). A list
  index matters only where both paths give one for the same segment, so an
  unindexed tag reaches every element and `terms[0]` reaches only element 0.
- Untagged evidence goes to every binding on the record.
- Evidence whose tags match no declared path of any active validator binding on
  the record (for example a free-text statement field that no validator reads)
  also goes to every binding on the record. Ownership is computed per object from
  every active validator binding of the data type
  (`DomainPackValidationRegistry.ownership_bindings`), independent of the states
  a caller matches, the payload and group or flow filtering. A flow that leaves
  a check out narrows `bindings` only, so that check still owns its fields'
  evidence. Each match carries the result as `record_binding_paths`; a saved
  review plan stores it once per distinct value in `match_parts` and saves the
  registry's `ownership_bindings` next to its `bindings`.
- A tag that names a list element the record does not have (`terms[5]` on a
  two-item list) is read by no binding, so it claims nothing either.
- For a custom profile, a mapping that is unavailable to the current user but
  whose exact capability still declares its slots keeps owning its fields.
- A record with at least one claimed tag goes only to the bindings whose own
  paths match one of its tags, even if it also carries an unclaimed tag.
- A binding with an `evidence_record` selector that has no `field_path` reads the
  whole record's evidence by its own configuration and receives all of it.
- Field-scoped `evidence_quotes` are always a subset of the request evidence, so
  compact decision evidence checks keep accepting them.

This applies to every request: initial validation, reconsideration and
re-validation after a source selection. The record itself keeps every evidence
reference; exports, the review packet's record evidence, quick checks and
follow-up extractor context read the record and are unchanged. The review
packet's per-answer `evidence` shows what that validator was sent.

Because the binding's source input identity includes its request evidence,
evidence appended for one field gives a new input version only to the bindings
that receive it. Sibling bindings keep their current input and their answers.

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

Existing documents need explicit reprocessing to benefit from corrected ingestion.
Do not replace indexed text beneath existing evidence references or silently
rewrite historical quotes. Before repairing an existing document, verify the
chosen import/reprocessing workflow preserves the historical source identity and
audit records (use a distinct document/source identity when necessary). Broad
reindexing and repair of stored curator documents are separate operational work.

The v0.10.9 hotfix advances the allele domain pack to `0.1.2` to separate
the operational validator tool allowance from immutable capability identity. Saved
profiles require reviewed `0.1.1` to `0.1.2` capability transitions; preserve
previous capability receipts and custom validator revision pins. Scientific
input/output mappings remain unchanged. The live binding supplies the configured
`max_tool_calls`; capability snapshots omit that operational setting so later
allowance changes do not invalidate saved profiles.
