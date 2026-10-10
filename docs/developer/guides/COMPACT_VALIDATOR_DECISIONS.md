# Compact validator decisions

Alliance validators return scientific decisions to a finalization tool, not a
second model-authored copy of database records. The public canonical result
schemas serve validation, curator review, materialization and exports.
Scientific `status` is separate from structural completeness (`output_issues`).

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
scientific judgments and policy consequences come from the model. Code copies
proposal facts and preserves those judgments without fabricating lookup events.

Runtime finalization instructions supersede older full-result authoring and
blanket lookup-before-output instructions, including frozen generated layers,
without modifying saved custom prompts or execution revisions. Curator scientific
criteria and actual database-verification obligations remain in force. An explicit
unresolved judgment does not require a ceremonial lookup. No saved data is migrated
by this change; execution evidence must identify the current runtime layer and
application commit alongside the original saved revision.

For compact decisions, the invocation's source workspace checks record membership,
identity and evidence references before assembling canonical output. Streaming
finalization does not recheck that output with the older success-outcome filter:
an LLM can select a supported record from an ambiguous lookup response. The legacy
provenance check remains for noncompact callers; schema, completeness and document
evidence checks still apply to compact output.

Expected decision/schema errors remain repairable finalizer rejections. Unexpected
adapter type/key errors clear accepted state, report a content-free operational
failure through the runtime observability facade, and terminate the SDK run;
they are not instructions for the model to repair server code.

TraceReview preserves raw lookup catalogs and compact decisions, and adds
request-scoped record/lookup reference joins. Unmatched or contradictory catalogs
remain explicit diagnostic gaps, not reconstructed scientific results. Suppressed
upstream validators are exposed separately from validation findings. These joins
do not claim to reconstruct committed custom findings absent from a trace.

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


## Scientific judgment and incomplete output

The LLM decides whether tools are needed. Evidence-grounded scientific slots can
be resolved without a lookup. Database-verification claims and record slots still
need real supporting records; source-copy, request ownership and evidence checks
remain mandatory. Neither a successful lookup nor any lookup at all is a universal
requirement for a scientific judgment. An explicit unresolved judgment without tools
is saved as `unresolved`, with its explanation, rather than an invented tool failure.

`DomainValidatorResultBase.status` always records the model's judgment.
`output_issues` records structural problems such as missing required fields or
contradictory aggregate/component outputs. Consumers must use `is_resolved` for
write-back and completion, not `status == "resolved"` alone. A result is complete
when `is_complete` is true. Incomplete results are rejected for bounded correction;
valid values, explanations, candidates and actual lookup history remain intact.
Single and batch finalizers retain the latest safely assembled incomplete result
when the run ends or reaches its existing turn budget. Invalid later attempts do
not erase that snapshot. Request identity failures and fabricated references never
become trusted partial values. Benchmark budgets and cancellation still propagate.

Materialization writes open incomplete-output findings and preserves the full
canonical snapshot, including required nullable fields, for persistence round trips.
It does not publish the incomplete result's values as validated identities. Existing
curator overrides remain authoritative. Chat and flow audit labels distinguish
incomplete output from a resolved scientific judgment. Standalone specialist
finalization rejects incomplete results and attaches the retained snapshot to its
terminal structured-output failure; custom flow dispatch rechecks request identity
before carrying that snapshot into the normal incomplete-result materialization.


## Upgrading saved agents after resolver-tool retirement

Migration `b8f2c3d4e5a6` removes only `search_domain_field_terms`,
`inspect_ontology_term`, and `resolve_domain_field_term` from editable
`agents.tool_ids` and removes their `tool_policies` rows. Run migrations before
startup runtime validation: otherwise an editable row with an unavailable tool
can be persistently deactivated. The migration preserves other tool order,
activity/routing flags, ownership, custom prompts, and unrelated policies. It
does not reactivate agents that were already inactive.

Executable revisions, fingerprints, saved flow pins, and historical results are
immutable and are not rewritten. Cleaning an editable row is **not** an
executable upgrade: an old pin with a retired tool remains inspectable but cannot
run. Inventory affected heads and pinned consumers before deployment. For each
reviewed agent, save a new revision through the normal owner-authorized update
API with its current `expected_revision_id`, omitting `tool_ids` so the cleaned
editable list is used. Review any additional authoring errors; do not bypass
model, tool, ownership, or output-contract checks. Select the accepted new
revision explicitly in each intended flow. Do not silently advance historical
runs or unrelated pins.

This is a forward-only cleanup, following the model-retirement migration
precedent. Downgrade does not restore unavailable tools. Other missing package
tools retain their existing availability checks. The migration was added during
the v0.10.5 merge-back review; it is not part of the immutable v0.10.5 tag. The
production release inventory had no editable rows containing these tools.
