# Generic profiles and executable agent revisions

Generic profiles describe closed `generic_object.attributes` contracts. They are
not domain packs, LinkML schemas, or submission contracts. PostgreSQL owns the
definitions and immutable identities; prompts consume that state.

## Profile contract

`GenericProfileContract` in `backend/src/schemas/generic_extraction_profile.py`
is the shared normalizer for API and persistence. Supported value kinds are
string, integer, number, boolean, enum, object, and array. Objects contain ordered
fields and are closed at every level. `required` controls key presence;
`nullable` independently permits a null value. There is no data-default mechanism.

Field keys are normalized and checked against reserved system fields. Optional
`source_labels` recognize source headings; they are not extra output fields.
Alias/canonical-key ambiguity is checked among fields in the same object.
Normalized order, labels, types, and constraints participate in the SHA-256
fingerprint. Compatibility findings identify field paths and breaking changes.

Depth, field count, contract bytes, and listing page size are configured under
Operational limits in `.env.example`. Optional `validator_mappings` use the typed
package capability contract below; no executable expressions or arbitrary JSON
Schema are accepted.

## Persistence and authorization

`generic_extraction_profiles` holds owner/project visibility, archive state, and
the current revision. `generic_extraction_profile_revisions` holds immutable
contract bytes, revision numbers, fingerprints, and creator metadata. Profile
revision updates and archive operations require the expected current revision.

`agent_execution_revisions` references canonical `agents.id`; it does not create
a second custom-agent identity. Each snapshot records model settings, instructions
and prompt-layer manifests, tools and inherited system-tool policy, group policy,
access floors, group rules/overrides, template source, output contract, curation
metadata, and structured finalization. Save notes are immutable revision metadata,
not execution-fingerprint inputs. Profile-bound snapshots pin the exact profile
revision UUID, revision number, and fingerprint.

Foreign keys protect referenced contracts from deletion, and database triggers
reject revision updates. Current visibility and saved group restrictions are
checked on reads/execution. Current tool execution policy and installed bindings
still apply: snapshots freeze configuration, not application code or privileges.
Archiving hides new selection; authorized explicit historical pins remain usable.

## Output transitions and saves

The complete `output_contract` distinguishes four states:

| State | Mode | Packaged schema | Profile pin |
| --- | --- | --- | --- |
| `none` | null | null | null |
| `structured_extraction` | `domain` | required | null |
| `structured_extraction` | `profile_bound_generic` | null | required |
| `structured_extraction` | `unprofiled_generic` | null | null |

Create/update requests accept either `output_contract` or `new_generic_profile`.
Inline profile creation and the new agent revision share one transaction. Update
requires `expected_revision_id` or the existing `expected_updated_at` guard;
stale saves are rejected after locking the agent row.

Omitting output fields preserves the saved contract. Explicitly clearing the
packaged-schema field selects `none`, not unprofiled generic. Clients must not
send an incidental null schema on unrelated edits to a profile-bound agent.

Default custom-agent execution resolves the current saved head. Explicit
`execution_revision_id` executes that revision without reading mutable execution
settings. Chat/Flow-wide model overrides do not replace custom-agent settings.
Restore appends a complete copied configuration with an expected-head guard;
it does not restore display name, description, or icon. Exact cloning copies the
saved configuration and profile reference, without a second editable profile.
Explicit clone edits create a subsequent revision and preserve inherited bounds.

## Profile-bound execution and conformance

Saved profile execution resolves and authorizes the exact agent/profile revision
before constructing tools. `AgentExecutionReceipt` contains canonical agent UUID
and key, executable revision UUID/number/fingerprint, and the output contract with
its full profile pin. Ordinary preferred-agent chat persists this receipt in the
initial user turn; an incomplete retry reauthorizes that pin instead of selecting
today's head. A historical custom-agent turn without a receipt is rejected, not
silently upgraded. Both isolated test endpoints resolve the same saved runtime
and stream its receipt in `RUN_STARTED`.

`ResolvedGenericProfile` in `lib/agent_studio/profile_conformance.py` owns one
non-coercing recursive contract. Use `require_candidate` for builder drafts and
`require_envelope` for materialized/edited envelopes; pass the expected execution
receipt and canonical agent key when available. `validate_attributes` returns
bounded candidate/path/type/repair issues. `patch_attributes` applies canonical
whole-subtree replacements or existing array-index updates to a copy and validates
the complete result. A tool's entire patch list is atomic. Aliases are recognition
text only; no unknown-key bag, implicit deletion, coercion, or invented values is
supported. Record bytes, visited values and issue count have environment-backed
limits documented in `.env.example`.

Profile mode narrows the already-authorized generic stage/patch tools and removes
catalog/class selection. It does not add tool capabilities. Optional fields remain
optional in a non-strict provider schema; backend conformance is still mandatory.
Run-state rebinding preserves the closed callable/schema pair. Both specialist and
direct runs retain canonical identity and consume finalized backend envelopes,
including empty extractions; model-authored replacement output is not accepted.
Internal events and materialized provenance carry identities, not full contracts.

Provider serialization tests cover the configured native OpenAI Responses driver
and direct OpenAI-compatible Chat Completions drivers (Gemini, Groq, OpenRouter).
The branch does not use LiteLLM or a Bedrock agent driver. Groq's existing
response-format compatibility mode is unnecessary for builder agents because their
model `output_type` is intentionally `None`. Tests inspect the final SDK parameters
after adapter/rebinding steps; this is distinct from live-provider smoke evidence.

## Saved flows, results, and curator edits

Each custom flow node selects an `agent_revision_id`. Its execution receipt is
derived from that exact authorized revision; saving, loading, AI verification,
chat/batch preflight, and execution do not substitute the mutable agent head.
System nodes remain ID-based. A legacy custom node without a resolvable pin can
be inspected with findings but cannot execute. `prompt_version` remains audit
metadata, not an executable identity. Flows themselves remain mutable; there is
no whole-flow snapshot or copied profile definition in node JSON.

The node panel browses saved executable revisions with Apply/Cancel semantics.
AI retargeting uses `retarget_agent_revision` with an explicit revision UUID and
reverifies the resulting output contract. A save acknowledgement may hydrate the
same revision's receipt without discarding newer unapplied panel edits.

Extraction results and domain envelopes retain both the full execution receipt
and a normalized revision foreign key. Reusing an idempotency identity with a
different receipt is rejected. Workspace sessions can contain multiple source
revisions: session membership records those revisions and each candidate retains
its own source receipt. Manual candidates select an existing session revision;
only an unambiguous single source is inferred. They cannot independently choose
a profile or use the latest head.

`curation_workspace/execution_contracts.py` applies the same closed profile checks
at result persistence, envelope checkpoints, manual create/edit, validation/cache
reuse, and submission/export payload construction. Nested curator attribute edits
use the saved profile's `patch_attributes`, not global generic-pack declarations.
The ordinary domain-pack protected/editable policy still owns non-attribute paths.
Invalid profile edits return structured conformance/identity findings through the
Workspace API without persisting partial candidate or envelope changes.
Reset also rebuilds a profiled manual candidate's canonical payload from its
validated seed fields. Historical receipt-less envelopes remain editable without
inventing a revision. First materialization of a historical custom result requires
a stored receipt-less source with matching producer and document; new custom
extraction writes still require their exact receipt.

## Profile-aware formatter projections

The exact saved contract supplies recursive field discovery, including optional
fields absent from every result. `attributes.sources[].name` is exposed through
the existing `object.attribute.sources[].name` row-reference namespace. Field
catalogs carry declared kinds, array depth, nullability, enum values, and source
receipts. They are transient authoring/runtime metadata, not a second editable
definition. Empty extractions still expose a catalog; they do not bypass existing
canonical-object requirements for curation TSV exports.

Array traversal retains every list level and missing/null slot. Parallel name
and identifier arrays remain aligned for existing `pair_join` and conditional
transforms; projection does not coerce or rewrite source values.

Saved-flow verification rejects missing profile references and incompatible
numeric predicates, including conditions inside columns. Intentional unprofiled
generic sources report undeclared-field warnings without acquiring an invented
schema. Runtime `source_keys` and `source_extraction_result_ids` are artifact
identities, not graph node IDs; either selector can include a source. If attached
profiles disagree about a numeric field and a plan explicitly selects runtime
sources, authoring reports a deferred source-type warning rather than assuming
which node the selector names. Runtime validation checks the declared types of
the actual selected sources before export. Nonselective incompatible predicates
remain authoring errors.

## Optional semantic validator mappings

`validator_mappings` is a typed immutable part of a profile revision, not an
agent-level validator list. Each mapping names the exact composite package,
package version, domain pack, domain-pack version and binding ID, plus the
capability fingerprint returned by inspection. Mapping IDs, canonical input and
output paths, mode and selected policy participate in the profile fingerprint
and revision comparison. Structural conformance is still always on.

Packages opt individual existing bindings in through `custom_profile_reuse`.
It declares recursive input/output value schemas, nullable/required inputs,
required alternative slot groups, permitted constant/context sources, explicit
whole-array/per-element support, evidence needs and allowed policy choices.
Existing selectors, implementation identity, batching and `group_scope` remain
authoritative. Fixed/context selectors cannot be replaced by curator-provided
selectors. Scoped provider paths must be mapped explicitly to bounded provider
inputs; unsupported scope combinations stay inspectable but not selectable.

Mappings use `inputs: {slot: {source: field, field_path: attributes.key}}`;
`source: constant` supplies `value` only when permitted, and `source: context`
selects only the package-owned selector. Outputs are `{slot: attributes.key}`
and must have separately declared compatible destinations. Nested paths traverse
declared objects; `[]` denotes explicit per-element fan-out over one shared array
domain. Whole-array inputs require package opt-in. There is no implicit indexing,
coercion, alias inference or composition of overlapping write destinations.

Save and clone reauthorize capabilities against authenticated groups and reject
invalid mappings with bounded path-addressed issues. Capabilities are derived
from the existing validation registry, not a parallel implementation catalog.
The initial Alliance opt-ins are the existing gene-expression subject-gene and
source-reference bindings; neither implies submission readiness.

`GET /api/agent-studio/generic-profiles/validator-capabilities` lists versioned
slots/policy, selectable status and diagnostics with the existing profile page
limit. `POST .../validate` validates a complete unsaved draft without writes.
`GET .../{profile_id}/revisions/{revision}/validator-mappings` inspects the exact
saved revision and retained capability snapshots, reporting compatible,
unsupported or unmapped without executing validators or asserting readiness.

Migration `h5c6d7e8f9a0` adds immutable capability audit snapshots and normalized
foreign-key references. A deferred constraint rejects a saved profile mapping
without its matching capability receipt. A package cannot reuse the same
composite version for different capability bytes after it has been referenced.
Historical revisions remain readable if a package disappears; snapshots never
authorize executing an unavailable package. ALL-1036 owns reauthorization,
compilation and semantic execution through the existing dispatcher, followed by
transactional complete-record conformance.

## API and migration

Profile lifecycle endpoints live under `/api/agent-studio/generic-profiles`.
Custom-agent `/execution-revisions` lists saved configurations with keyset
pagination; `/execution-revisions/{revision_id}` reads an exact snapshot, and
POST `/execution-revisions/{revision_id}/restore` takes `expected_revision_id`.
The old `/revert/{version}` action is removed. Existing `/versions` records are
read-only prompt history marked `executable: false`.

Migrations `f3a4b5c6d7e8` and `g4b5c6d7e8f9` add these tables and create one truthful
baseline from each current custom-agent head, including archived heads. Template
baselines load the actual database prompt cache; unavailable templates or invalid
current configuration abort instead of inventing history. Legacy prompt-only
rows never acquire fabricated historical model/tool settings. A null schema on
migration remains `none`. Run migrations with the target deployment's package
configuration available. These migrations do not version flows or alter source
documents/results.

The persistence suites `test_generic_profile_persistence.py` and
`test_agent_execution_revision_persistence.py` exercise real PostgreSQL constraints,
baseline capture, lifecycle/auth, snapshot restore/clone, stale saves, complete
output transitions, and rollback in transactional private schemas.
