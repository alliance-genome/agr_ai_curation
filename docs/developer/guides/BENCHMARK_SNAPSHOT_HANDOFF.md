# Workspace benchmark snapshots

The curation workspace's **Send snapshot to Benchmark** dialog exports the
selected persisted envelope revision. Biological validation and approval do
not control eligibility. Unsaved edits must be saved first; a server revision
conflict requires refreshing the workspace. The source envelope is not changed.

Authenticated `GET /api/curation-workspace/benchmark-destinations` returns only
configured destination IDs and curator-facing labels. Each entry in
`BENCHMARK_SNAPSHOT_HANDOFF_DESTINATIONS_JSON` requires a nonempty `label` in
addition to the existing sink/token URLs, OAuth settings and redirect policy.
Neither credentials nor the registry's outbound URLs are returned by discovery.
When `BENCHMARK_SNAPSHOT_HANDOFF_ENABLED=false`, discovery returns an empty list.

The secondary **Prepare benchmark bundle JSON for download** action works even
when handoff is disabled or destination discovery fails. It creates an immutable
snapshot using the same expected-revision check and reveals its authenticated
download link; it does not send the bundle anywhere.

Send creates or reuses that exact revision's snapshot, then calls the existing
server-allowlisted handoff endpoint. Selection is disabled while work is pending.
Receipts are bound to session, envelope, revision and destination, so a workspace
refresh cannot attach a previous operation's result to new content. Closing and
reopening the dialog retains receipts while the workspace remains mounted.
Server-side durable replay protection continues to apply after page reloads.

Only successful handoff exposes **Open Benchmark**, a keyboard-accessible link
with opener isolation. The response's `redirect_path` contains the approved
absolute destination URL: the server combines its configured HTTPS origin with
the previously validated receipt path. The stored receipt remains a path, so
no migration or browser-supplied redirect origin is needed. Unknown delivery
remains explicitly unconfirmed and is not automatically retried; the JSON
snapshot remains downloadable. Delivery success does not mean scoring completed.
Long-running comparison progress and accuracy belong to the private portal.

## Extraction and document provenance

New flow extractions and builder-finalized chat extractions retain a server-owned
`envelope.execution_context` with schema version `extraction-execution-context/v1`.
It records the query supplied to the specialist before execution, the agent key,
capture time, flow/step identity where applicable, and available document identity.
This is the specialist query, not a claim to capture every system prompt, tool
response, provider setting, or instruction in the conversation.

Document identity includes the original document UUID and, when available, the
provider, canonical reference CURIE, converted-artifact ID and SHA256. The hash
covers exact downloaded converted-artifact bytes, before UTF-8 decoding, figure
enrichment or image stripping. It does not hash the PDF, parsed chunks, figure
sidecars or extraction output. Migration `m0n1o2p3q4r5` adds the nullable source
digest; it does not backfill historical documents or perform downloads.

The initial envelope checkpoint accepts only explicitly supplied server context.
Later validator and curator checkpoints preserve it; model payloads cannot replace
it. Snapshot export uses that saved context, not current flow or document metadata.
The existing outer `curation-benchmark-snapshot/v1` contract carries it inside its
envelope JSON, so no second outer bundle format is needed. The envelope digest
includes exported context; the extraction-output hash remains a separate identity.

Historical snapshots remain byte-identical. Missing context or reference identity
stays missing, including custom/local PDF uploads without an imported artifact.
Consumers must not infer requested species or sections from output, turn a flow ID
into a benchmark task version, or fill missing fields from selected reference data.
This capture is evidence for comparison applicability, not a complete benchmark
task binding or approval of a reference set. Portal-side paper association and
reference review remain separate from source identity.

## Initiating curator identity (transport)

Handoffs require the initiating curator's issuer and subject from validated
authentication claims. Development and API-key identities without a verified
issuer cannot send a handoff. The server checks current snapshot ownership and
session access before sending or recovering a receipt.

The immutable JSON body and its digest are unchanged. Alongside the existing
service OAuth authorization, the sender supplies these server-generated headers:

- `X-Curation-Benchmark-Sender-Version: 1`
- `X-Curation-Benchmark-Sender-Issuer`: validated issuer
- `X-Curation-Benchmark-Sender-Subject`: validated subject

No human bearer token, email, or other raw authentication claims are forwarded.
`BENCHMARK_HANDOFF_MAX_IDENTITY_BYTES` bounds the combined issuer/subject header
size; both values must contain only non-whitespace printable ASCII characters.
The receiver must accept these assertions only from an authorized sending
service, then bind access to the exact issuer and subject. A receipt URL does not
grant ownership. Receiver enforcement is a separate private-portal integration.

Durable attempts record the sender identity, which also participates in the
delivery idempotency key. Re-exporting the same envelope revision after a reload
can create a new snapshot UUID; the same sender recovers the original receipt
and snapshot ID without resending. A different sender or a historical attempt
without recorded identity fails closed with a replay conflict. The migration
retains historical receipts without assigning them to whoever visits next.
