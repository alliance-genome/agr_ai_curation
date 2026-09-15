# Recovering review after Curation Handoff fails

An extraction can finish before handoff fails. Do not infer that its output is
durable from a trace or finalization acknowledgment alone.

## Preconditions

- Verify the fix is deployed and use the intended runner's authenticated account.
  Chat uses current authenticated groups; batch execution uses the authenticated
  internal-group snapshot saved when the batch was created. Never supply group
  claims from model output or document text.
- Identify the document, flow run, and extraction adapter. Confirm persisted
  canonical extraction results or a persisted curation-prep result exist for that
  exact scope. Required pinned agent/profile revisions and source envelopes must
  still be available and conform to their saved contracts.
- Check the document's bootstrap availability through the existing Curation
  workspace entry point. A trace alone is not a supported recovery source.

## Supported retry

Use **Review & Curate** for the affected document/run/adapter, or the existing
authenticated POST /api/curation-workspace/documents/{document_id}/bootstrap
endpoint with adapter_key, flow_run_id, and, when known, origin_session_id.
Use an adapter-specific request when the run has multiple extraction adapters.
Do not retry the batch or extraction flow merely to prepare review.

Bootstrap replays the matching persisted prep result. If only canonical flow
extraction results exist, it performs deterministic prep first. It then refreshes
validation/materialization with the current caller's trusted user/group context
and reuses the prepared session instead of duplicating it. Verify the returned
session ID, candidate count, assigned curator, and validation findings.

This does not rerun the extraction model. It can run validators when saved
envelopes have not already completed inline validation. Missing group context
grants no group access; explicit empty membership likewise grants none. Profiles
without validator mappings remain reviewable. Unavailable scoped mappings retain
controlled findings and their readiness policy, rather than being treated as
successful validation.

If no durable source survived the failed transaction, stop: this route cannot
reconstruct extraction from a transcript. Obtain separate approval for any new
model run or other recovery work. Do not edit curator flows, substitute revisions,
or directly insert review rows to bypass the failure.

## September 15 incident

ALL-1230 / KANBAN-1750 fixes lost group context and the subsequent tuple(None)
failure. Tests cover a real saved custom profile with 35 synthetic records,
runner-owned review preparation, and replay without duplicate candidates.
They do not establish whether Gillian's historical run retained recoverable
database rows; that requires a read-only scope check before recovery.
No historical run is modified by this fix.
