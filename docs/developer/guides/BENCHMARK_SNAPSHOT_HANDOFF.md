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
