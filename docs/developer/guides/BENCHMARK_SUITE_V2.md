# Execution-only benchmark suites

Benchmark suite schema version 2 describes reproducible execution experiments.
It does not define biological correctness, gold records, scoring, adjudication,
or provider-specific model matrices. Checked-in YAML and ad hoc JSON requests
are validated through the same strict `BenchmarkSuite` model.

## Suite contract

```yaml
schema_version: 2
suite_id: gene-expression-model-comparison
cases:
  - case_id: paper-1
    target: {kind: flow, id: Gene Expression Analysis}
    input:
      resolver: local_document
      reference: DOCUMENT_ID
      version: VERSION
      digest: sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
configurations:
  - configuration_id: all-sol
    routes:
      supervisor: {provider: openai, model: gpt-5.6-sol, reasoning_effort: high}
      agent:gene_expression: {provider: openai, model: gpt-5.6-sol, reasoning_effort: high}
repetitions: 1
```

Each case has its own agent or flow target and immutable input reference. Input
references identify a registered resolver plus an exact reference, version, and
SHA-256 digest. They never accept a request-time URL or executable import path.
Resolver registration and materialization are separate runtime concerns.

Configurations are explicit named experiment arms. A list of models does not
create a Cartesian product. `repetitions` defaults to one.
Names such as `all-sol` do not imply routing behavior: a whole-target arm must
explicitly name every applicable model-backed slot.

The schema forbids unknown fields. In particular, `expected`, `gold`,
`scorers`, and `adjudicator` are invalid at every suite boundary.

## Route catalog and frozen plans

The deployment catalog publishes model capabilities, agent/flow targets, and
stable model-bearing route slots:

- `supervisor`
- `agent:<agent-id>`
- `validator:<validator-id>` for model-backed validators only

Deterministic validators do not have model routes and are absent from the route
catalog. Models and reasoning capabilities come from the dynamic deployment
catalog; clients must not hardcode model or provider assumptions.

Every target identifies its applicable slots. When a named configuration omits
one of those slots, planning freezes the catalog's checked-in default route.
The normalized plan therefore contains complete provider, model, and reasoning
values for every applicable slot in every cell. Planning rejects unknown
targets, slots, provider/model pairs, and unsupported reasoning efforts before
execution.

Suite, catalog, and resolved-plan objects are deeply immutable, including their
targets and route maps. `suite_digest` hashes canonical suite JSON.
`catalog_digest` hashes the catalog used for resolution. `plan_digest` hashes
the complete normalized plan,
including immutable input provenance, resolved routes, repetitions, and cell
identities. Mapping keys are sorted and compact canonical JSON is used, so YAML
and equivalent ad hoc JSON produce identical digests.

The plan bounds are environment-configurable:

- `BENCHMARK_MAX_CASES` (default `50`)
- `BENCHMARK_MAX_CONFIGURATIONS` (default `10`)
- `BENCHMARK_MAX_REPETITIONS` (default `5`)
- `BENCHMARK_MAX_CELLS` (default `250`)

Checked-in suites live in `packages/<package>/benchmarks/suites/`. The Alliance
synthetic suites demonstrate explicit named arms without correctness data.
