# Chat Validator Payload and Latency Plan

Date: 2026-05-20

## Diagnosis

A local chat run for `What gene is the focus of this publication?` took about
192 seconds. The trace was `cfbde6e2ee03e3ce870045c148918a5f`.

The latency split into three separate phases:

- Gene extractor post-tool synthesis: about 112 seconds after the final
  `agr_species_context_lookup` completed. No backend tool was active in this
  interval; the model was streaming final JSON text.
- Active validator dispatch: about 50 seconds. Four extracted
  `gene_mention_evidence` objects triggered four package validator runs.
- Supervisor final synthesis: about 4 seconds, but with a very large tool
  result as input.

The huge JSON payload was caused by the supervisor reducer path. It built a
compact `metadata.supervisor_summary`, but returned the full validated domain
envelope with that summary inserted. In the trace, the useful supervisor summary
was about 958 characters while the tool result sent back to the supervisor was
89,562 characters. The supervisor then consumed a 119,780-character input.

Most of the 89KB payload was audit material that belongs in the envelope, not in
the supervisor handoff:

- `history`: about 40KB
- `validation_findings`: about 33KB
- `objects`: about 7.6KB
- `metadata`: about 3.3KB

The audit bloat is mostly repeated validator binding metadata, validation
request details, validation result details, and lookup attempts copied into
findings and history.

## LinkML Requirement Check

The pinned Alliance LinkML schema remains authoritative. At commit
`1b11d0888f19eba4ca72022200bb7d96b30d4a52`:

- `Gene.slot_usage.primary_external_id.required` is `true`.
- `gene_symbol.required` is `true`.
- `BiologicalEntity.slot_usage.taxon.required` is `true`, and `Gene` inherits
  from `GenomicEntity` / `BiologicalEntity`.

So the fix should not relax real model requirements. The issue is the handoff
shape and runtime fanout, not the existence of validated identity fields.

## Fix Plan

1. Keep full validated envelopes available for audit/persistence, but return
   only the compact supervisor summary from specialist tool calls.
2. Preserve required validator policy. Required means the validator must run or
   produce a controlled unresolved result; it does not imply sequential
   execution.
3. Run independent validator requests in a bounded parallel pool.
4. Deduplicate equivalent validator requests before execution using stable
   selector inputs, then remap the result back to each original target before
   materialization.
5. Keep envelope materialization sequential and ordered, so object patching,
   findings, and history remain deterministic.
6. Follow up separately on extractor precision for non-gene phrases like
   `apical spectrin cytoskeleton`; parallelism and payload compaction reduce
   cost, but they do not fix over-extraction by themselves.

## Follow-up: Validator Target Context Drift

A later run, trace `44c29ee61e8a6e82fb4b13221e7956b3`, confirmed that the
parallel/deduped validator path was faster, but surfaced a false
`invalid_schema` result. The gene validator correctly resolved `crumbs` to
`crb` / `FB:FBgn0259685`; the rejection happened because the validator copied
the request target's `input_values.evidence_quote` with a mangled `±` escape.

`target.input_values` is validator context, not materialization identity. The
identity guard should continue to reject mismatched request IDs, binding IDs,
validator agents, object IDs, object types, roles, field paths, and expected
fields, but should not reject a good lookup solely because copied context text
drifted. Accepted validator results should be canonicalized back to the
dispatcher-owned request identity before materialization.

## Next-Day Plan: Batch Validator Dispatch Sessions

The next implementation step should be final-envelope batch validation. This is
not a literal long-running LLM process waiting for messages. The current
OpenAI Agents runtime is request-oriented, and `validator_dispatch.py` currently
calls `Runner.run_sync(...)` once per deduped validator request. The practical
target is a validator dispatch session: build all final
`DomainValidationRequest` objects, group them by validator agent or binding
family, run one validator batch per group, then materialize one
`DomainValidatorResultBase` per original request back into the envelope.

This keeps validation on the validator-owned path while avoiding one full LLM
validator run per gene mention.

Current code anchors:

- Chat-time validation starts in
  `backend/src/lib/openai_agents/streaming_tools.py::_dispatch_domain_envelope_validators_for_chat`.
- Final requests are built in
  `backend/src/lib/domain_packs/validator_dispatch.py::dispatch_active_validator_bindings`.
- Current dedupe and bounded parallel execution lives in
  `backend/src/lib/domain_packs/validator_dispatch.py::_run_validator_jobs`.
- Current per-request validator execution is
  `backend/src/lib/domain_packs/validator_dispatch.py::run_package_scoped_validator_agent`.
- The gene binding is
  `packages/alliance/domain_packs/gene/domain_pack.yaml` /
  `alliance_gene_reference_lookup`.
- The gene validator agent is
  `packages/alliance/agents/gene/agent.yaml` / `gene_validation`.

### Desired Flow

```text
final domain envelope
  -> match active validator bindings
  -> build all DomainValidationRequest objects
  -> preflight missing required inputs
  -> dedupe equivalent requests
  -> group executable requests by validator agent and batch capability
  -> run one batch per group, concurrently across independent groups
  -> validate/canonicalize each returned DomainValidatorResultBase
  -> remap deduped results back to original requests
  -> materialize results sequentially and deterministically
```

For a gene-only envelope, this should become:

```text
crumbs/crb/ninaE/... final requests
  -> one gene_validation batch run
  -> validator uses bulk lookup where possible, such as search_genes_bulk
  -> validator returns one result per request_id
```

For a mixed envelope, the dispatcher can spin up independent batch runs:

```text
gene requests        -> gene_validation batch
phenotype requests   -> ontology/phenotype validator batch
condition requests   -> CHEBI or condition validator batch
other requests       -> package-specific validator batch
```

Those batches can run concurrently because each validator owns its own request
set and tool behavior. Envelope patching still happens after all results are
collected, in original dispatch order.

### Guardrails

- The extractor must not validate genes or call broad curation DB lookup tools.
- The supervisor must not decide validator policy.
- The dispatcher may batch, dedupe, cache, and schedule, but it must not invent
  biological resolutions.
- Required LinkML/domain-pack fields remain required. Missing or unresolved
  values should become controlled validator results and findings, not relaxed
  schema policy.
- The final extractor envelope remains authoritative for which objects are
  validated. Earlier speculative candidates are out of scope for the first
  batch implementation.
- Materialization must remain ordered and deterministic, even if validator
  batches run concurrently.
- Each returned validator result must match the dispatcher-owned request ID,
  binding, agent, target identity, and expected result fields. Context fields
  such as copied evidence quotes should be canonicalized from the request rather
  than used as strict identity.

### Implementation Slice

1. Add a batch runner abstraction alongside the current single-request runner.
   The input should be a list of `_DispatchJob` values for one compatible
   validator group, and the output should be a result per job/request.
2. Extend `_run_validator_jobs` to group deduped executable jobs by validator
   agent and binding family before falling back to the existing single-request
   path.
3. Add a package-scoped batch execution path for validators that opt in. For
   the first pass, gene validation is the important target.
4. Add a gene-specific batch prompt or runner contract that requires one
   `DomainValidatorResultBase` per request ID and instructs the validator to use
   bulk lookup for multiple gene mentions.
5. Preserve single-request execution as the fallback for validators without
   batch support.
6. Emit audit events at batch start and completion, plus per-result lookup
   summaries after materialization so the UI remains understandable.
7. Add timing logs around group construction, batch execution, and
   materialization so future slow runs identify the phase that is actually
   blocking.

### Test Plan

- Unit test that equivalent requests dedupe once and remap back to every
  original target.
- Unit test that mixed validator agents produce separate groups and preserve
  final materialization order.
- Unit test that a batch result with a wrong request ID or wrong target identity
  becomes a controlled unresolved result.
- Unit test that context-only drift in `target.input_values` does not reject an
  otherwise correct validator result.
- Contract test the gene batch path with `crumbs`, `crb`, `ninaE`, and an
  ambiguous broad term such as `Actin`.
- Smoke test chat extraction for the Drosophila `crb` paper and confirm the
  active validator phase is bounded and visibly audited.

### Later Option: Validator-Owned Prefetch

After final-envelope batch validation is stable, we can consider a pipelined
prefetch path. The extractor would call a narrow staging tool such as
`stage_domain_validation_candidate(...)` when it has validator-ready context.
That tool would only enqueue candidate input; it would not return validation
answers to the extractor. A validator-owned background worker could begin cheap
bulk lookups while extraction continues.

Final dispatch would still be authoritative. A staged candidate would be reused
only if it matches a final `DomainValidationRequest`; staged candidates that the
extractor later excludes would be discarded. This could reduce wall-clock time
further, but it is intentionally a second phase because it adds cache identity,
background lifecycle, and cancellation concerns.
