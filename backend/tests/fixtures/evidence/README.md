# Evidence Fixture Format

Each fixture file is a reusable paper-level test case for the tool-based evidence pipeline. The format is intentionally domain-agnostic: values such as `entity_type`, `normalized_id`, `adapter_key`, and `agent_key` are opaque data, not branch keys for the harness.

## Required top-level keys

- `fixture_id`: Stable fixture identifier.
- `paper`: Human-readable metadata used when seeding integration tests.
- `chunks`: Known Weaviate chunk payloads keyed by `id` and shaped like the `record_evidence` tool input source.
- `tool_cases`: Individual `record_evidence` calls with `tool_input` and `expected_tool_result`.
- `extraction`: Structured extraction fixture data that references tool cases by `evidence_case_ids`.
- `expected_candidates`: Downstream prep/workspace expectations after evidence gating.
- `expected_gating`: Expected warning/error messages for evidence gating assertions.

## `tool_cases`

Each tool case defines one `record_evidence` invocation:

```json
{
  "case_id": "verified_exact",
  "tool_input": {
    "entity": "crumb",
    "chunk_id": "chunk-1",
    "claimed_quote": "Quoted paper text."
  },
  "expected_tool_result": {
    "status": "verified",
    "verified_quote": "Quoted paper text.",
    "page": 4,
    "section": "Results"
  }
}
```

Use `status: "verified"` for accepted evidence and `status: "not_found"` for rejected quotes or bad chunk ids.

## `extraction`

The extraction section describes the persisted structured output without duplicating verified evidence payloads. Instead, each item lists `evidence_case_ids`, and the harness expands those ids into extractor `evidence` records from the corresponding verified tool cases.

```json
{
  "tool_name": "ask_domain_specialist",
  "agent_key": "domain_extractor",
  "adapter_key": "domain_adapter",
  "scope_confirmation": {
    "confirmed": true,
    "adapter_keys": ["domain_adapter"],
    "notes": ["Confirmed fixture scope."]
  },
  "items": [
    {
      "label": "entity label",
      "entity_type": "domain",
      "normalized_id": "ID:1",
      "source_mentions": ["entity label"],
      "evidence_case_ids": ["verified_exact"]
    }
  ],
  "top_level_evidence_case_ids": ["verified_exact"],
  "run_summary": {
    "candidate_count": 1
  }
}
```

Some integration tests still inject legacy raw payload fields such as `profile_key` or
`scope_confirmation.profile_keys` on purpose to verify that shared prep/workspace transport drops
them. Those legacy fields are not part of the normal fixture contract anymore.

## `expected_candidates`

Each expected candidate captures the post-gate prep payload plus the `field_paths` and `evidence_case_ids` that should survive into prep `evidence_records` and workspace `evidence_anchors`.

## Extending the corpus

1. Add a new JSON file beside the existing fixtures.
2. Define new `chunks` and `tool_cases` for the paper.
3. Reference those cases from `extraction.items[*].evidence_case_ids`.
4. Add the expected downstream candidate payloads in `expected_candidates`.

Non-gene fixtures should reuse the same structure; only the data changes.
