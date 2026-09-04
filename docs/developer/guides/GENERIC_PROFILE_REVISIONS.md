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
Operational limits in `.env.example`. Nonempty `validator_mappings` are rejected
until the separate mapping contract is implemented; no executable expressions
or arbitrary JSON Schema are accepted.

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
