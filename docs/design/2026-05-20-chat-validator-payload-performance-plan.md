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
