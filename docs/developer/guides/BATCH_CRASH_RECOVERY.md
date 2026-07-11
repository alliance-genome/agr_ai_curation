# Batch Crash Recovery

Batch execution uses a durable queue/lease policy. Application startup scans for
all `PENDING` batches and `RUNNING` batches whose lease is missing or expired,
then dispatches workers. Every worker must atomically acquire the batch lease;
simultaneous startup scans are safe because only one owner can claim a row.

The lease owner heartbeats while flow execution is active. Its duration and
heartbeat interval are configured by `BATCH_WORKER_LEASE_SECONDS` and
`BATCH_WORKER_HEARTBEAT_SECONDS`. Cancellation and completion clear the lease.
An expired owner cannot heartbeat, persist a document result, update counters,
or complete the batch.

## Resume policy

- `COMPLETED` and `FAILED` documents are terminal. Recovery preserves their
  artifacts, extraction results, and review-session references and never runs
  their flows again.
- `PENDING` documents are eligible for execution by the new lease owner.
- `PROCESSING` means the previous process may have performed external side
  effects without reaching its final database commit. Recovery marks that row
  `FAILED` with an interruption reason and does not re-run it.
- A recovered batch completes normally after every document is terminal. The SSE
  endpoint observes the persisted terminal state and emits its existing
  `DOCUMENT_STATUS` and `BATCH_COMPLETE` events even when the original process
  and its in-memory broadcaster no longer exist.

Progress counters are recomputed from the durable document rows in the same
transactions that claim or finalize work. `total_documents` is derived from the
rows as well, so `completed_documents + failed_documents` cannot exceed it and
recovery cannot double-increment progress.

This policy intentionally favors at-most-once document flow execution after an
ambiguous crash. A curator may explicitly start a new batch for an interrupted
document after inspecting any external side effects.
