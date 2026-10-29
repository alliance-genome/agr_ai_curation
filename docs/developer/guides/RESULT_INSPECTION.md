# Result inspection across chat and flows

Chat, interactive flows, batch flows and preferred-flow follow-ups expose the
same `inspect_results` schema, descriptions and shared dispatcher. Actions include
search/list, object pages, exact fields, authored explanations, evidence, and
validator findings/originals. Returned `read` and `next_call` arguments preserve
the selected view and pagination identity.

Authorization determines the searchable collection. Chat uses its authorized
saved-result scopes. A fresh flow can inspect only successfully completed steps
in its own execution; preferred follow-ups also receive explicitly bound prior
results. The runtime rechecks stored ownership/session identity and current
PDF ownership for bound flow reads. Batch grouping IDs are not access grants:
capabilities also bind the full document ID, session and completed step.
This does not revoke ownership of saved results merely because sharing settings
on the originating agent or flow change.

`result_view="original"` reads the immutable extraction payload. Where flow
validation ran, `result_view="post_validation"` reads the committed validation
checkpoint used downstream. The extraction metadata stores only its envelope ID
and revision, in the same transaction as the checkpoint. It does not duplicate
or overwrite the original payload. Responses identify the view and revision;
pass the returned `envelope_revision` on subsequent reads. A superseded checkpoint
is explicitly unavailable: the envelope store does not provide historical JSON
snapshots, and inspection never silently substitutes the latest curator edits.
Older extractions without a recorded checkpoint pointer report that view as
unavailable. Discovery reports unavailable views separately.

Successful canonical extraction steps persist through existing idempotent FLOW storage
before returning their real result reference. A later formatter failure does not
remove the completed extraction. Failed/in-flight steps never publish inspection
capabilities. Inline CHAT persistence remains disabled for flow specialists.

Inspection is optional and read-only. It cannot advance configured step order,
run a specialist, or make a scientific judgment on behalf of an LLM. System-owned
runtime instructions explain view choice and exact reads consistently; curator
prompts do not own this contract. Existing operational pagination settings apply.

Noncanonical outputs retain their existing execution and persistence behavior;
they do not receive the new completed-step inspection capability. Canonical
manifest eligibility is checked before validation or extraction writes.

All benchmark runs are excluded from this new inspection registration, including
benchmark routes without a supervisor snapshot. Frozen runs retain their captured
tools and instructions. Extending benchmark inspection is separate work.

## Agent Studio review history

Studio uses `get_domain_envelope_review_history` for the durable validation ledger,
separately from the materialized `get_domain_envelope_state` history. It accepts an
owned envelope ID even when no curation-workspace candidate has been created.
Artifact/document/session authorization remains in the shared history store.

Start with `summary`: it returns fact-kind counts, an observed sequence boundary and the
typed baseline reference. Check `subject_kind` and `original_available`: a historical
baseline is a later snapshot, not an original extraction; a curator-authored
baseline is the curator's content. Use `list` with that `through_sequence` and a relevant
`kind`, then `detail` with the returned fact ID and SHA. Follow `next_call` to read
all exact JSON chunks. Baseline detail uses the same hash-pinned read contract.
The existing `VALIDATION_HISTORY_PAGE_SIZE` and `VALIDATION_REVIEW_PACKET_BYTES`
settings control pagination; the reader does not create a second history store.

A `target_id` list filter matches direct fact anchors only. Decisions may contain
multiple target references in their bodies: do not filter them out and claim a
complete per-record history. Use current envelope reads for current materialized
values and trace tools for the historical prompts/tool calls. Missing historical
metadata is reported explicitly rather than returned as empty history. Inspection
never creates candidates, writes review facts, reruns validation or restores records.

The sequence boundary excludes later sequence allocations, but is not a database
transaction snapshot: an in-flight earlier allocation may commit during paging.
For questions involving a concurrently active run, refresh the summary after it
settles rather than claiming that a paged read proves no later decision exists.
