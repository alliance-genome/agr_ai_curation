# Custom benchmark source revisions

Custom-agent benchmark targets use two distinct identities:

- `source_execution_receipts` binds each custom model slot to its authorized saved agent revision and output/profile contract.
- The existing frozen `routes` bind the experimental provider, model and reasoning setting. Invocation telemetry records requested and actual routing separately.

The source receipt does not claim that an experiment used the saved revision's original model. The saved prompt, tools, temperature, output contract and profile remain unchanged; only the three experiment route dimensions vary. Neither the saved revision nor its fingerprint is rewritten.

Catalog generation resolves the initiating curator's current authorized source revision, including exact custom-validator pins in profile mappings. Preview hashes these source receipts into catalog, cell and plan identities. Admission rebuilds the authoritative catalog and rejects a stale preview before accepting work. The accepted plan retains its source receipts in existing job JSON; the worker uses those receipts rather than selecting a newer head. Failed-cell rerun copies the same frozen sources and inputs.

The explicit benchmark constructor requires both a frozen cell source and an active server-owned model route. It rechecks current source visibility, saved group restrictions and current executable tool policy through the normal pinned constructor. Ordinary chat/flow construction still rejects model or other setting overrides for immutable custom revisions. Benchmark execution does not opt into direct CHAT extraction persistence.

System targets have no custom source receipt, so their existing plan serialization is unchanged. Historical custom jobs without a recoverable frozen source refuse execution; no current-head fallback or invented historical revision is allowed. Fresh preview/admission creates the required source binding.

Offline coverage is in `backend/tests/unit/lib/benchmarks/test_source_revisions.py`, `test_runtime_catalog.py`, and `backend/tests/integration/persistence/test_benchmark_source_revisions.py`. These are owning regression tests, not an additional live release gate. The PostgreSQL test uses the existing isolated transactional profile/revision fixture and never calls a provider.
