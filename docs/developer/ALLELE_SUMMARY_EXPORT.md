# Source-linked mention exports (ALL-1209)

The existing object projection and deterministic serializer expose package-declared
`object.summary.AlleleMention.*` fields alongside `object.pack.AlleleMention.*`.
Use `row_source=object` and an `object.object_type == AlleleMention` filter for a
per-mention CSV. No separate row builder or arbitrary nested-path evaluator is used.
Inventory, row inspection, validation, preview, and final save share these fields.
Studio's selected-field catalog also includes them and fingerprints their contract;
selected-field mode still preserves every source object and disallows filters.

The allele package declares the validator binding, resolved scalar fields, and
evidence object identity. Core projection has no allele-specific identifiers.
Relationships stay within one saved envelope/result. Typed object references are
required; a validator target's effective object ID is supported only with its
object type and domain pack, and only when unique. Names, labels, array order,
shared run IDs, and IDs from other results are never join keys.

Each mention retains its source/result/envelope/object identifiers and evidence
IDs. The summary retains finding IDs, recorded finding and lookup statuses,
request IDs, target references, messages, and resolved values. Multiple findings
remain inspectable; flat validated values are supplied only when recorded lookup
statuses and values agree. Ambiguous identity, conflicting provenance, or mixed
lookup outcomes withhold flat values. Identical repeated evidence records are
deduplicated by explicit evidence ID and content; contradictory or missing
evidence is disclosed instead of arbitrarily selected. No acceptance, writeback,
confidence probability, or biological revalidation is inferred.

## Investigation and verification

Read-only inspection of the four saved extraction records established:

- Case A, result `8706dd5a-b49d-483d-b522-53a48afb30d8`: six mention rows,
  two linked validated IDs (`MGI:3716464`, `MGI:5487287`), four unresolved.
- Case C, result `f86027b7-8067-48fa-b4bd-39bc5e7e370f`: five mention rows,
  two linked validated IDs (`MGI:2679081`, `MGI:6163733`), three unresolved.
- Case B allele result `b4389545-8d8e-41a2-a732-2c63c7911535`: eight mentions,
  one linked validated ID (`MGI:3716464`), four unresolved and three open
  findings without a completed lookup result. All evidence links remain available.
- Case B generic result `e7979a36-0bfc-467f-a47e-dc095a6f26e1`: eight generic
  objects with no object/field links to the allele result. Source candidates use
  separate generic/allele namespaces. A shared builder run ID and similar labels
  do not establish per-item identity. The paper-stated `RRID:MGI:5487397` must
  remain paper-stated. Historical generic-to-allele enrichment is **not supported**.
  Export either source separately; future combined exports require a deliberately
  saved, source-qualified item mapping, not an inferred name join.

Historical verification was a local deterministic projection replay of saved
object payloads, references, and validation result fields obtained read-only from
production. It was not a new extraction, full agent replay, or production save.
All 6/5/8 respective allele mention rows were retained and serialized locally.
Repository regression fixtures are explicitly reconstructed representative
topologies, not raw trace captures. They exercise source isolation, duplicate IDs
and labels, missing/contradictory links, mixed findings, repeated evidence,
inventory, the formerly rejected nested Case C field shape, supported scalar
inspection/validation, and formatter save callback with real CSV serialization.
The callback writes a temporary local file and returns a test download URL; it
does not claim a production download was created. Existing file-storage tests
cover the storage layer. Tool previews keep their existing bounded/whitespace-
normalized presentation; saved CSV uses original values and proper quoting.
