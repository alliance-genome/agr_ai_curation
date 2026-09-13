# Custom benchmark source revisions

Custom-agent benchmark targets use two distinct identities:

- `source_execution_receipts` binds each custom model slot to its authorized saved agent revision and output/profile contract.
- The existing frozen `routes` bind the experimental provider, model and reasoning setting. Invocation telemetry records requested and actual routing separately.

The source receipt does not claim that an experiment used the saved revision's original model. The saved prompt, tools, temperature, output contract and profile remain unchanged; only the three experiment route dimensions vary. Neither the saved revision nor its fingerprint is rewritten.

Catalog generation resolves the initiating curator's current authorized source revision, including exact custom-validator pins in profile mappings. Preview hashes these source receipts into catalog, cell and plan identities. Admission rebuilds the authoritative catalog and rejects a stale preview before accepting work. The accepted plan retains its source receipts in existing job JSON; the worker uses those receipts rather than selecting a newer head. Failed-cell rerun copies the same frozen sources and inputs.

The explicit benchmark constructor requires both a frozen cell source and an active server-owned model route. It rechecks current source visibility, saved group restrictions and current executable tool policy through the normal pinned constructor. Ordinary chat/flow construction still rejects model or other setting overrides for immutable custom revisions. Benchmark execution does not opt into direct CHAT extraction persistence.

System targets have no custom source receipt. Newly prepared targets carry separate system snapshots; historical records without those fields keep their original serialization and must not be reported as having complete provenance. Historical custom jobs without a recoverable frozen source refuse execution; no current-head fallback or invented historical revision is allowed.

## Saved flows and installed recipes

Saved-flow selection supplies `source_kind: saved_flow`, the flow UUID and the
revision returned by discovery. The server captures the authorized definition,
resolved custom receipts and declared node output contracts. It does not accept
an executable definition or prompt from the client. Installed recipes use their
catalog identity and the same normal recipe hydration/revision resolver.

Preparation captures system-agent prompt layers, tool choices, temperature,
schema and curation metadata. Flow supervisors retain their generated
instructions, settings and expected step tools. Execution rechecks access but
uses the captured content; unavailable steps fail instead of being skipped.
Catalog and durable-cell validation reject a snapshot belonging to another flow
or selected revision. Source snapshots contribute to catalog, cell and plan
digests and are retained in existing job JSON, without backfilling old jobs.

## Dependency checks and reproducibility limits

Preparation records a digest of the deployment's package/tool-binding registry,
metadata digests for the selected agents' domain packs, and named execution
settings read through the normal configuration getters. Runtime checks these
before agent or flow execution. A change requires a new prepared experiment
revision; it does not silently substitute new settings into an old run.

This check intentionally covers the shared deployment registry, so changing an
unrelated registered tool can also invalidate an old prepared selection. It
does not construct tools, read database contents or store credentials. Package
versions and binding metadata do **not** pin transitive Python implementation
bytes. Tool code remains deployment-owned, external source data remains live,
and provider revisions remain unpinned. These limits are explicit fields in
the dependency receipt, not a claim of identical biological results on rerun.

Planned cells are ordered by case, configuration and repetition. Worker
scheduling and configured concurrency determine actual dispatch; neither is
randomization. The receipt records that distinction for downstream analysis.

Offline coverage is in `backend/tests/unit/lib/benchmarks/test_source_revisions.py`, `test_runtime_catalog.py`, and `backend/tests/integration/persistence/test_benchmark_source_revisions.py`. These are owning regression tests, not an additional live release gate. The PostgreSQL test uses the existing isolated transactional profile/revision fixture and never calls a provider.
