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
scoped-evidence behavior: requests carrying quote bundles use isolated runs
where needed, rather than sharing mutable evidence across batched targets.

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
