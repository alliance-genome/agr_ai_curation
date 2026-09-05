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

Updates can instead send `revise_generic_profile: {base, contract}`, where `base`
is the full existing profile pin (profile UUID, revision UUID, number, fingerprint).
This is an explicit third output transition, mutually exclusive with the other
output fields. The profile service verifies the pin, current head and ownership;
the new profile revision and new agent revision commit or roll back together.
Profile conflicts return HTTP 409. Agents and flows already pinned to an older
revision keep that pin. Create/clone requests cannot revise a source profile.

Workshop loads the exact saved output revision and uses one authorable output
draft for editing, dirty state, Save and AI fingerprints. The profile detail API
exposes authenticated `can_edit` for UI guidance; Save still checks authorization.
An unchanged profile retains its pin. Editing an owned profile on an existing
agent sends the explicit revision transition; editing a shared/noneditable profile
or saving an edited structure as a new agent creates a separate profile copy.
Validation and save errors preserve the local draft.

`POST /generic-profiles/{id}/revisions/{revision}/compare` compares a local
contract against an authorized immutable revision without saving. Its response
includes the exact base revision, proposed fingerprint and the same compatibility
findings used by revision saves. Workshop marks comparisons stale after draft
edits, requires confirmation before loading a compared revision, and offers a
separate-copy action that preserves the edited contract until explicit Save.
Compatibility is not semantic validation or submission readiness.

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

## Saved consumer impact

The Workshop revision panel loads `GET .../{profile_id}/consumers` on demand.
It lists immutable agent configurations (current and historical) and normalized
flow-node references across all revisions of that profile. Archived references
remain inspectable. Current agent visibility, profile visibility, flow ownership,
and each saved agent revision's group restrictions apply before keyset pagination;
the ordinary exact-revision reader also verifies integrity. No inaccessible names
or totals are returned. An empty authorized page does not prove global non-use.
`GENERIC_PROFILE_LIST_PAGE_SIZE` bounds this read, as it does profile/revision lists.
The view has no retarget action: saving a revision never updates other consumers.

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

The manual editor uses `POST .../validator-options` with a typed draft to obtain
canonical input/output field choices. It reuses the save-time path resolver and
schema assignability rules, including inherited requiredness/nullability, explicit
array domains and bounded provider fields. Catalog pagination uses the same
configured page size. Options are suggestions for slots, not complete-mapping
approval: the existing validation endpoint remains authoritative for required
alternatives, shared array domain, provider scope, policy and overlapping writes.
The UI invalidates choices after fields change and never rewrites an existing
capability fingerprint. Constant values use typed controls, context selectors
stay package-owned, and unavailable mappings remain inspectable/removable.

Migration `h5c6d7e8f9a0` adds immutable capability audit snapshots and normalized
foreign-key references. A deferred constraint rejects a saved profile mapping
without its matching capability receipt. A package cannot reuse the same
composite version for different capability bytes after it has been referenced.
Historical revisions remain readable if a package disappears; snapshots never
authorize executing an unavailable package.

### Runtime validation and write-back

`domain_packs/profile_validation.py` compiles a request-local registry from the
exact receipt and live authorized package capabilities. Chat, Flow, Workspace,
curation preparation, inspection and readiness share this context. It does not
modify the global generic pack. Missing or revoked capabilities and denied
group/provider scopes produce explicit findings under the saved mapping policy;
they do not discard an otherwise conforming extraction. Cached validation does
not cache capability access. Blocking mappings use the existing required/blocking
readiness contract; conformance alone is not semantic validation or submission
approval.

The existing dispatcher still owns requests, batching and validator jobs.
`profile_materialization.py` stages all mapped outputs for one record, checks
typed slots and destination conflicts, and invokes the shared closed-profile
patch once for the complete record. One invalid output leaves that record
unchanged. Source mirrors, undeclared outputs and implicit resolved-object
side channels are not approved profile write destinations. Ordinary packaged
validators retain their existing materialization behavior.

Flow validation groups must cover each saved mapping exactly once; omission,
replacement, supplementation or opt-out cannot alter the pinned profile. Catalog
and inspection projections retain saved mapping identity while reporting live
availability separately. An unavailable mapping is not an under-development
feature and must not count as an executable automatic check.

Validating a manual profile candidate creates its canonical envelope through the
existing checkpoint service, using the session's selected saved revision and
explicit `curator_manual` origin. No agent extraction is fabricated. Subsequent
validation, edits and export use that envelope. Validator write-back refreshes
all affected sibling candidate drafts and revisions. Readiness uses the closed
profile contract rather than inherited generic field requirements. Real
PostgreSQL regressions in `test_profile_validator_workspace.py` cover receipt
linkage, policy-governed unavailable findings, export data and sibling drafts.

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
rows never acquire fabricated historical model/tool settings. A null response
schema alone does not imply either ordinary output or extraction: the baseline
uses the installed template's declared curation and actual builder tools. Run migrations with the target deployment's package
configuration available. These migrations do not version flows or alter source
documents/results.

The persistence suites `test_generic_profile_persistence.py` and
`test_agent_execution_revision_persistence.py` exercise real PostgreSQL constraints,
baseline capture, lifecycle/auth, snapshot restore/clone, stale saves, complete
output transitions, and rollback in transactional private schemas.

### Packaged builder output

Domain output selects exactly one model-response schema or a
`domain_extraction_ref` containing `package_id`, `agent_id`, and `domain_pack_id`.
Packaged builders retain a null model-response schema: the backend finalizer
materializes their envelope. Discovery and save use the exact installed
package/agent identity and its declared domain. Save requires matching finalizer
tools and preserves package access/tool policy; selecting a format does not
grant tools. Start from the matching template when its managed tools are needed.
Maturity labels remain advisory.

The snapshot freezes the selected curation/finalization configuration, not the
prompt parent's configuration. Tools remain deployment-owned. Absent builder
references are omitted from serialized contracts so earlier immutable
fingerprints remain unchanged. Fresh g4 bootstraps permit builder baselines
before i6 pins flows; m0 upgrades older constraints without rewriting existing
revisions or retargeting flow nodes.

No-output configurations containing builder finalizers are rejected on save and
execution. Historical contradictory revisions remain readable; repair by saving
an explicit matching output configuration and deliberately selecting the new
revision for each affected flow. Never reinterpret or rewrite an old pin.

## Output Structure dev walkthrough

Human UI feedback and Gillian's business semantics/UAT remain pending until
they review the combined dev deployment. Use a new test agent/profile, not an
original production flow. This checklist is not evidence of completed human UAT.

1. In Workshop Setup, keep **No structured output**, then inspect the three
   structured formats. A packaged builder may have a null response schema;
   maturity is advisory and choosing a format never grants tools.
2. Choose Custom Output Structure and edit name, record description and class.
   With JSON closed, add text, enum and repeating-group fields; add a nested
   child, duplicate, reorder and remove fields. Check required and nullable
   independently. Locked platform fields must remain separate.
3. Add **Synonyms / source labels (not output fields)**. The example must still
   show only canonical keys and must identify its values as placeholders.
4. Find compatible validators and explicitly select typed inputs, outputs and
   allowed unresolved policy. No available capability means structural checks
   still apply, not that the record is submission-ready.
5. Use **Review before saving** and its Change actions to revisit basics,
   nested fields and mappings. Edits must survive navigation with JSON closed.
   Trigger a validation error and follow its link to the affected control.
6. Save, reopen and edit a saved profile. Confirm the executable revision
   identifies model, tools and exact output/profile pin. Compare/copy or load a
   newer profile revision; other agents and flows must retain their pins.
   Test a stale save and verify local edits remain available for recovery.
7. Inspect consumer impact, including older revisions; archive copy must say
   history is retained. Exercise loading failure/retry and pending-save locking.
8. Repeat key actions using keyboard alone and at narrow and desktop widths:
   visible focus, field-linked errors, stacked panels, readable long values and
   no horizontal spreadsheet. Record actual browser dimensions and findings.
9. Profile candidate comparison/adapter has deterministic proposed, applied,
   canceled, stale and undone fixtures. Live AI Apply/Cancel/undo integration is
   ALL-1034 on the shared ALL-1051 lifecycle, not a second editor save path.
