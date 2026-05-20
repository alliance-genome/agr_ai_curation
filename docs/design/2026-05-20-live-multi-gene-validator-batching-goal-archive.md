# Goal: Live Multi-Gene Validator Batching

Updated: 2026-05-20

This file is a self-contained handoff goal. Assume the next agent has no chat
history. Read this file first, then read `AGENTS.md`, then inspect the current
worktree.

## One-Sentence Objective

Find an open-access paper/trial that actually produces multiple gene validator
requests in a live gene extraction flow, prove where those requests appear
inside the runtime, then implement and benchmark the smallest safe batching
change that reduces live validator latency.

## Why This Goal Exists

The previous task implemented validator batching for this case:

```text
one final domain envelope
  -> multiple compatible DomainValidationRequest objects
  -> one gene_validation batch run
  -> one DomainValidatorResultBase per original request
```

That implementation works in unit tests. However, the real-PDF gene corpus run
did not exercise it. The live flow produced separate single-request gene
validator dispatches, so the post-change benchmark still had:

```text
validator_agent_run_count = 2
batch_validator_run_count = 0
```

The next task is to batch the live shape that really happens, not just the
idealized shape.

## Current Repo / Branch Context

The previous work was pushed to `main`.

Important commits:

- `e300d1e1` - `Add batch validator dispatch for gene validation`
- `0956f164` - `Record validator batch corpus comparison`

Start with:

```bash
git status --short --branch
git log -3 --oneline
```

Expected branch after the previous work:

```text
main...origin/main
0956f164 Record validator batch corpus comparison
e300d1e1 Add batch validator dispatch for gene validation
```

There may be unrelated local dirty files in this checkout. Do not reset, stash,
clean, or revert unrelated local changes unless Chris explicitly asks.

## Previous Durable Evidence

Committed artifacts from the previous task:

- Baseline corpus:
  `docs/design/pdf-corpus-trials/baseline-2026-05-20/`
- Focused gene baseline rerun:
  `docs/design/pdf-corpus-trials/baseline-2026-05-20-gene-rerun-1/`
- Post-change corpus:
  `docs/design/pdf-corpus-trials/batch-validator-2026-05-21/`
- Comparison JSON:
  `docs/design/pdf-corpus-trials/validator-batch-comparison-2026-05-21.json`
- Human note:
  `docs/design/2026-05-21-validator-batch-comparison.md`

Key previous benchmark facts:

- Focused gene baseline active validator dispatch: `17.435s`
- Focused gene post-change active validator dispatch: `20.525s`
- Focused gene post-change validator agent runs: `2`
- Focused gene post-change batch validator runs: `0`
- Post-change validator problem events: `0`
- Conclusion: the batch executor is implemented, but the live flow did not hand
  it multiple gene requests at once.

## Core Questions To Answer

Do not implement blindly. First answer these from code inspection and live
evidence:

1. When the live gene extractor finds multiple genes, does it return:
   - one domain envelope containing multiple `gene_mention_evidence` objects, or
   - multiple separate domain envelopes/tool outputs, each with one or a few
     gene objects?
2. Does `_dispatch_domain_envelope_validators_for_chat` run once per extractor
   output, causing multiple single-request validator dispatches?
3. If multiple gene requests are available at one time, does the existing batch
   dispatcher already produce one `gene_validation` batch?
4. If requests arrive across multiple consecutive chat/tool outputs, where can
   they be safely coalesced without moving biological validation into the
   extractor or supervisor?
5. Which option actually improves wall time in the sandbox:
   - one validator agent run with many `DomainValidationRequest` objects,
   - one validator agent run issuing several lookup tool calls,
   - one validator agent run using `search_genes_bulk`,
   - request-scoped batching across consecutive gene extraction envelopes?

## Existing Implementation Anchors

Batch-capable validator dispatch:

- `backend/src/lib/domain_packs/validator_dispatch.py`
- `dispatch_active_validator_bindings`
- `_run_validator_jobs`
- `_plan_validator_run_groups`
- `run_package_scoped_validator_agent_batch`
- `_validated_results_from_agent_batch_output`

Chat-time validation:

- `backend/src/lib/openai_agents/streaming_tools.py`
- `_dispatch_domain_envelope_validators_for_chat`

Gene validation metadata:

- `packages/alliance/domain_packs/gene/domain_pack.yaml`
- `packages/alliance/agents/gene/agent.yaml`
- `packages/alliance/agents/gene/prompt.yaml`

Benchmark runner:

- `scripts/testing/domain_envelope_pdf_corpus.py`

Existing focused unit tests:

- `backend/tests/unit/lib/domain_packs/test_validator_dispatch.py`
- `backend/tests/unit/lib/openai_agents/test_streaming_tools_helpers.py`

## Important Existing Behavior

The existing batch dispatcher is deliberately not used for one unique request.
That was intentional because singleton batch execution adds overhead and would
only make metrics look batched without a real latency benefit.

Do not change this just to make `batchValidatorRunCount` nonzero. Only batch
when there are multiple useful requests or a measured benefit.

## Step 1: Inspect Current Runtime Path

Read the code enough to trace this path:

```text
extractor tool final output
  -> _dispatch_domain_envelope_validators_for_chat
  -> build extraction envelope candidate
  -> DomainEnvelope
  -> dispatch_active_validator_bindings
  -> build DomainValidationRequest values
  -> _run_validator_jobs
  -> one or more validator agent runs
```

Use commands like:

```bash
rg -n "_dispatch_domain_envelope_validators_for_chat|dispatch_active_validator_bindings|run_package_scoped_validator_agent_batch" backend/src
rg -n "batchValidatorRunCount|validatorAgentRunCount|dispatch_active_validator_batch" backend/src backend/tests
```

Write down where multiple envelopes could be coalesced, if they appear across
separate calls.

## Step 2: Find An Open-Access Multi-Gene Paper

Use web search. The paper must be open access and have a PDF reachable without
login.

Good candidate criteria:

- The paper includes several concrete gene symbols in abstract/results.
- Genes are from a supported Alliance species/provider:
  Drosophila, mouse, zebrafish, C. elegans, yeast, rat, or human.
- The paper text has explicit evidence for multiple genes, not just a pathway
  name or broad gene family.
- It is likely to produce multiple retained `gene_mention_evidence` objects.
- Avoid papers where most terms are reagents, strains, protein complexes,
  pathways, or non-gene abbreviations.

Useful search queries:

```text
site:pmc.ncbi.nlm.nih.gov Drosophila multiple genes crb ninaE open access PDF
site:pmc.ncbi.nlm.nih.gov "Drosophila melanogaster" "genes" "PDF" "open access"
site:pmc.ncbi.nlm.nih.gov zebrafish flcn multiple genes open access PDF
site:pmc.ncbi.nlm.nih.gov C. elegans mus-81 multiple genes open access PDF
site:pmc.ncbi.nlm.nih.gov mouse gene expression multiple genes open access PDF
```

Prefer PubMed Central / PMC articles because PDFs are usually stable and open.

Record the chosen paper in a note or artifact with:

- title
- URL
- PDF URL
- species/provider
- candidate gene symbols
- why it should trigger multiple gene validator requests

## Step 3: Add Or Run A Focused Trial

Use the corpus runner if possible. It already downloads PDFs and records timing
JSON. Either:

1. Add a new `CorpusTrial` to `scripts/testing/domain_envelope_pdf_corpus.py`,
   if the trial is generally useful, or
2. Temporarily/manual-run a focused trial without committing a permanent corpus
   addition, if the paper is only exploratory.

Preferred artifact directories:

- Candidate/probe run:
  `docs/design/pdf-corpus-trials/gene-multi-validator-candidate-2026-05-20/`
- Post-change batching run:
  `docs/design/pdf-corpus-trials/gene-multi-validator-batching-2026-05-20/`
- Comparison JSON:
  `docs/design/pdf-corpus-trials/gene-multi-validator-batching-comparison-2026-05-20.json`

If adding a permanent trial, keep it generic. Do not hardcode expected answers
or paper-specific regexes.

## Step 4: Deploy The Sandbox Before Live Runs

Use the Symphony main sandbox. Confirm its commit before each benchmark.

Check sandbox state:

```bash
export SYMPHONY_INCUS_PROJECT="${SYMPHONY_INCUS_PROJECT:-user-1000}"
incus --project "${SYMPHONY_INCUS_PROJECT}" exec symphony-main -- \
  sudo --login --user ctabone bash -lc '
    cd /home/ctabone/.symphony/sandboxes/agr_ai_curation/main
    git status --short --branch
    git rev-parse --short HEAD
    git log -1 --oneline
  '
```

If code or trial changes are committed, push them, then prepare the sandbox from
Git. Do not hot-patch normal app code into the VM/container.

```bash
export SYMPHONY_INCUS_PROJECT="${SYMPHONY_INCUS_PROJECT:-user-1000}"
incus --project "${SYMPHONY_INCUS_PROJECT}" exec symphony-main -- \
  sudo --login --user ctabone bash -lc '
    set -euo pipefail
    cd /home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation
    git fetch origin main
    ./scripts/utilities/symphony_main_sandbox.sh prepare --branch main
  '
```

Expected backend URL from prior work:

```text
http://10.79.64.167:8900
```

Verify health:

```bash
curl -fsS http://10.79.64.167:8900/health
```

## Step 5: Run The Focused Benchmark

Use the selected trial. Example command shape:

```bash
python3 scripts/testing/domain_envelope_pdf_corpus.py \
  --base-url http://10.79.64.167:8900 \
  --allow-dev-mode-fallback \
  --allow-duplicate-reuse \
  --trial <trial-id-or-domain> \
  --output-dir docs/design/pdf-corpus-trials/gene-multi-validator-candidate-2026-05-20
```

After it runs, inspect the per-trial JSON and summary:

```bash
jq '.trial_timing_summary' docs/design/pdf-corpus-trials/gene-multi-validator-candidate-2026-05-20/summary.json
jq '{status, duration_seconds, flow_execution_duration_seconds, active_validator_dispatch_duration_seconds, validator_agent_run_count, batch_validator_run_count, validator_lookup_event_count, validator_problem_event_count, observed_validator_lookup_counts, validator_dispatch_completion_details}' docs/design/pdf-corpus-trials/gene-multi-validator-candidate-2026-05-20/<trial>.json
```

The key evidence is:

- Did one final envelope contain multiple gene objects?
- How many `DomainValidationRequest` values were built?
- Did `validatorAgentRunCount` exceed `1`?
- Did `batchValidatorRunCount` become `1` or more?
- Did `validatorBatchGroups` list the gene batch?
- Did lookup events show `search_genes_bulk`?

## Step 6: Decide The Implementation Strategy

Use this decision tree:

### Case A: One Envelope Has Multiple Gene Requests

If one envelope contains multiple gene requests but `batchValidatorRunCount` is
still `0`, the existing dispatch grouping has a bug. Fix
`backend/src/lib/domain_packs/validator_dispatch.py`.

Expected fix area:

- `_batch_group_key_for_deduped_job_group`
- `_plan_validator_run_groups`
- `_execute_validator_run_group`
- gene binding metadata in `packages/alliance/domain_packs/gene/domain_pack.yaml`

### Case B: Multiple Envelopes Arrive Consecutively

If the live flow emits multiple separate gene envelopes, each with one request,
the current dispatcher cannot batch because it only sees one envelope at a time.

Investigate a request-scoped coalescing layer around chat-time validation:

- likely near `_dispatch_domain_envelope_validators_for_chat`
- possibly in the specialist/tool-result handling code that calls it
- must preserve original tool output ordering and final envelope ownership

The safe design is probably:

```text
same chat/flow run + same validator agent/batch family
  -> collect compatible validation requests for a short bounded window
  -> run one validator batch
  -> remap results back to each original envelope
  -> emit audit events for batch start/complete and per-result lookup
```

Keep the window deterministic and bounded. Avoid introducing a daemon or
unbounded global cache.

### Case C: Validator Agent Gets Multiple Requests But Uses Single Lookups

If one batch agent run happens but the validator calls `search_genes` once per
gene instead of `search_genes_bulk`, fix the gene validator prompt or tool
contract.

Relevant files:

- `packages/alliance/agents/gene/prompt.yaml`
- `packages/alliance/agents/gene/agent.yaml`

### Case D: OpenAI Tool Calls Can Run In Parallel

If the runtime can perform multiple lookup tool calls concurrently and that is
easier/safer than cross-envelope batching, document evidence and compare timing.
Still prefer `search_genes_bulk` when several symbols share species/provider
because one bulk lookup is simpler to audit.

## Step 7: Tests To Add Or Update

For dispatcher changes:

```bash
docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc \
  "python -m pytest tests/unit/lib/domain_packs/test_validator_dispatch.py -v --tb=short"
```

For chat-time/coalescing changes:

```bash
docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc \
  "python -m pytest tests/unit/lib/openai_agents/test_streaming_tools_helpers.py -v --tb=short"
```

If implementing request-scoped batching across envelopes, add tests proving:

- two compatible gene envelopes in one chat/flow scope produce one batch run
- results remap back to the correct original envelope/request
- materialization order remains deterministic
- singleton requests still use the normal path unless batching has measured
  benefit
- bad batch output becomes controlled unresolved results
- context-only quote drift still does not reject otherwise valid identity

Then run the combined focused set:

```bash
docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc \
  "python -m pytest tests/unit/lib/domain_packs/test_validator_dispatch.py tests/unit/lib/openai_agents/test_streaming_tools_helpers.py -v --tb=short"
```

Run the broader backend unit target if changes are broad:

```bash
docker compose -f docker-compose.test.yml run --rm backend-unit-tests
```

Known unrelated broad-suite failures from 2026-05-20:

- retired ontology mapping route still exists
- literature helper dependency version `(0, 9, 0) >= (0, 10, 1)` failure
- project-agnostic allowlist debt
- old `scripts/test_dev_release_smoke.py` expectations

Do not spend time fixing those unless the user asks.

## Step 8: Save Evidence

Persist artifacts under `docs/design/pdf-corpus-trials/`. Do not rely on chat
history.

Required comparison JSON fields:

- selected paper title, URL, PDF URL
- sandbox commit and backend URL
- baseline/focused run directory
- post-change run directory
- per-run duration fields
- validator agent run counts
- batch validator run counts
- lookup event counts
- active validator dispatch duration
- pass/fail status
- short interpretation of whether live batching occurred

If the result needs human explanation, add a short Markdown note under
`docs/design/`, but keep JSON as the durable source of truth.

## Acceptance Criteria

The task is complete only when all of these are true:

- An open-access multi-gene paper/trial is identified and documented with a PDF
  URL.
- A focused real-PDF benchmark proves the runtime shape:
  one multi-request envelope, or multiple consecutive single-request envelopes.
- The chosen batching strategy is justified with evidence.
- If code changes are made, tests cover the exact live shape being batched.
- A post-change focused benchmark is run against the sandbox.
- JSON artifacts and a compact comparison are saved under
  `docs/design/pdf-corpus-trials/`.
- The final handoff states:
  - whether live batching occurred,
  - whether `search_genes_bulk` was used,
  - validator run count before/after,
  - active validator dispatch duration before/after,
  - speedup or regression,
  - any remaining bottleneck.

## Guardrails

- Do not hardcode paper-specific genes, organisms, expected answers, or regexes.
- Do not move biological validation into the extractor or supervisor.
- Do not relax required LinkML/domain-pack fields.
- Do not route singleton requests through batch just to make metrics look good.
- Preserve deterministic materialization order.
- Each returned validator result must match dispatcher-owned identity:
  request ID, binding, agent, target object ID/type, role, field path, and
  expected fields.
- Context fields such as evidence quotes should continue to be canonicalized
  from the request rather than used as strict identity.
- Keep changes domain-general and test-covered.
