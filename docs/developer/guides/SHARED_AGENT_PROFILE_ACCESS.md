# Shared agents and pinned output structures

An authorized saved-agent revision grants read/use access to its exact pinned output structure when that private structure belongs to the agent owner. Current project membership and the revision's group restrictions are checked first. This does not grant profile-library access, arbitrary revision access, editing rights, or another owner's private profile. Profile identity and contract fingerprints must match the immutable agent snapshot. Archived pinned structures remain readable, as with existing owner execution.

All execution routes use `get_execution_revision`; catalog construction and saved-resource inspection use `get_execution_profile_revision` for the same bounded dependency. The execution-revision output-profile endpoint serves Workshop clone-source hydration without listing the owner's profiles. Standalone profile-library endpoints retain their original permissions.

Cross-owner agent cloning creates a private recipient-owned profile from the exact pinned contract, validates its mappings for the recipient, and pins the new profile in the new agent revision. Source snapshots and profile history are unchanged. Same-owner agent clones retain their existing profile-reuse behavior. API transactions roll back the new profile and agent together on failure.

Flow cloning preserves its exact agent references, as before; it does not deep-copy every agent. Both shared-library and read-only editor clone actions use the same endpoint. The cloned flow requires continued access to those source agents. Revoking source access blocks subsequent flow execution; an independently cloned agent with its own private profile remains usable by its recipient.

No migration or saved-data rewrite is needed: existing authorized agent and flow pins use the new bounded read policy. No prompts, scientific fields, model settings, output-key mappings, or historical results change.
