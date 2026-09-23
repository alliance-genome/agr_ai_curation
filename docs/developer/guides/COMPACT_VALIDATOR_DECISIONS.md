# Compact validator decisions

Alliance validators return scientific decisions to a finalization tool, not a
second model-authored copy of database records. The public canonical result
schemas remain unchanged for validation, curator review, materialization and
exports.

## Ownership

- The model assesses relevance, identity and ambiguity, selects candidates,
  explains its conclusions and cites evidence. Component and RGD policy
  judgments retain their existing scientific types and rules.
- Lookup wrappers capture original responses before presentation compaction.
  The invocation-local workspace owns request identity, immutable source records,
  concrete lookup arguments, returned counts and opaque record references.
- Assembly copies selected factual fields, builds typed domain result rows,
  computes missing fields and validates the existing canonical result schema.
  Unknown, foreign, duplicate or fabricated references are rejected. A rejected
  finalization clears accepted state; acceptance ends the run without requiring
  another model-authored result.

For example, a lookup returning 26 alleles registers count 26 even if one allele
is selected. The model submits that allele's reference and its scientific reason;
code copies its identifier and nullable provider. It cannot replace the provider
with a guess or report the selected-row count as the lookup count.

## Package contract

All 15 Alliance validator schema modules export `COMPACT_VALIDATOR_RUNTIME`, a
`(package_id, "module:factory")` declaration. The runtime loads the owning package
through the existing registry/import-path mechanism. Alliance response projections
and scientific assembly belong to `agr_ai_curation_alliance.compact_*`; the backend
workspace and tool boundary contain no Alliance field mappings. Other packages
without this declaration retain their own existing output contracts.

The factory receives complete trusted requests, the canonical result schema and
profile-mapped request IDs. It returns per-request decision contracts and a lookup
adapter. A record has canonical facts, domain rows, an optional resolved object,
and a JSON-pointer source location. The workspace keeps each complete lookup
response. The model sees one view of it that shows each returned row once, adding
`validator_record_refs` and `validator_lookup_refs`; `lookup_model_view` drops the
envelope's restatements of those rows (`candidate_matches`, `result_projections`,
an attempt's matched-row projection) and text repeated as explanation or attempt
coverage. The field names a record offers for slot copies are listed once per tool
response (each page, when paged) as `validator_record_available_fields`, the set most
of that response's refs share; a ref carries its own `available_fields` only where
its names differ (ALL-1291). The model reads a ref's fields from the same response,
never from another page. `validator_record_refs`, `validator_record_available_fields`
and `validator_lookup_refs` are runtime-owned: a provider response using any of them
is rejected. Row lists are never reindexed, so source pointers resolve in the view and
still distinguish duplicate identifiers with different records. These references
never resolve in another invocation.

Batch calls require explicit `validator_request_ids`; bulk response input groups
are partitioned before references are assigned. The original call's total count
is retained for every served request. Batch acceptance is all-or-nothing and
requires exactly one decision per request. Single-request scope is runtime-owned.

Only declared result slots are accepted. Factual slots can copy their namesake or
package-declared equivalent fields. Model-authored scientific values require an
explicit typed scientific-slot contract; arbitrary strings cannot stand in for
provider facts. Profile-mapped results have no root `resolved_objects`, preserving
the profile's existing mapped-output boundary.

## Domain and execution behavior

The same factory is used by package single/batch dispatch and streaming, including
pinned custom agents and custom flow validator attachments. Attachments retain
the trusted request independently of the compact model-facing input. Standalone
streaming allocates request identity and accepts either a free-text query or
structured JSON inputs; it does not invent missing structured component/policy
inputs from prose.

Custom flow attachments receive accepted canonical results through an invocation-local
callback, not the human-readable supervisor summary. Only accepted finalization can
trigger that callback; a missing result fails closed. The dispatcher still checks
request, binding, validator and target identity. Supervisor consumers continue to
receive compact display summaries.

After sidecar validation commits its domain-envelope checkpoint, downstream
formatters use a separate validated candidate carrying that exact envelope. The
original extraction candidate remains unchanged for immutable persistence and
payload-hash checks. Exports therefore include committed validation findings and
resolved or unresolved summaries, without re-reading a mutable latest revision.

GO result collections include selected records only; unresolved input strings are
copied using JSON pointers into supplied inputs. Hierarchy details may be joined
from same-identity lookups, with final schema validation rejecting incomplete
required facts. Orthology query-gene metadata is separate from ortholog rows.
Condition components retain their owner, source inputs, field path, partial
results and scoped lookup audit; root values cannot contradict component values.
Each request exposes its package-owned `domain_contract`: the exact component
names, allowed statuses, owning lookup methods and component-slot rules. Include
supplemental `relation` and `evidence_quotes` components as `not_checked` when
listed, with no candidate, slot or lookup assertions. Component slots use record
field names (for example, `curie` copies `curie`); root slots such as
`condition_class_curie` are separate. Missing, extra or duplicate components and
invalid component-slot mappings return explicit repair diagnostics. This
guidance is request-specific even in a batch and does not copy provider records
or weaken the existing grounding checks.
The RGD policy evaluator is unchanged: proposed facts come from supplied inputs,
scientific judgments come from the model, and code computes policy consequences.

Runtime finalization instructions supersede older full-result authoring
instructions without modifying saved custom prompts or execution revisions.
No saved data is migrated by this change.

Before production, inventory saved agents and flow attachments using these
schemas, including their pinned execution revisions, inherited tools, prompts
and finalization settings. Rehearse those exact snapshots on an isolated copy:
confirm compact finalizer schemas and reference-bearing tools, accepted stopping,
canonical artifacts and profile output mappings. Keep authored scientific policy
unchanged. If a snapshot needs a prompt/schema/tool repair, record the exact
agent, revision, flow and proposed replacement and obtain approval before writing
saved state. Do not rewrite historical revisions or silently repoint flows.

## Verification

Run focused Docker unit tests for `test_compact_decisions.py`,
`test_compact_runtime.py`, `test_compact_record_adapters.py`, validator dispatch,
streaming helpers, profile validation, flow execution and RGD policy validation.
Fixtures must exercise actual wrapped lookup outputs rather than manufacture
canonical finalization payloads. These deterministic tests do not establish paid
model quality or production release readiness; those require separately approved
replay and release gates.
