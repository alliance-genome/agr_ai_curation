# Agentic Finalization and Self-Healing Inventory

Status: Draft for review.

Date: 2026-06-05

## Purpose

This note records an inventory of AI Curation runtime paths that can be made
more "agentic-friendly" in the same sense as the validator finalization change:
when a model produces an output that is almost correct but contract-incomplete,
the backend should return a precise tool result while the same agent is still
running, so the model can repair the exact problem before final output.

The immediate trigger was a validator failure from a weaker validation model.
The validator agent returned a semantically promising result, but the backend
could only reject it after the agent run had already ended because the final
result did not contain the required successful lookup attempt or explicit
non-lookup validation attempt. The new validator finalization design fixes that
class of failure by making validator finalization itself a mandatory tool call.
If the proposed result is rejected, the tool response gives repair instructions
inside the live run, and the validator can immediately call the finalization
tool again.

This document asks: where else do we still have late rejection, post-run repair,
or disconnected retry logic that should move into an in-run finalization tool?

## Scope Decision

The hierarchy classifier is deliberately excluded from this plan.

`backend/src/lib/pipeline/hierarchy_resolution.py` uses a one-shot structured
LLM call and falls back to the non-LLM hierarchy behavior when the classifier
returns empty output or errors. That is acceptable for now because:

- it is not creating curator-facing annotation objects;
- the fallback is already an intentional degraded behavior;
- the cost of building a live repair loop there is not justified by the
  current risk;
- hierarchy mistakes can be revisited separately if document navigation quality
  becomes the blocker.

Everything below focuses on curation, validation, extraction, lookup, and
structured answer paths where a model either creates or summarizes data that is
expected to satisfy a runtime contract.

## Executive Summary

Several important runtime paths are already in the desired shape:

- domain builder extractors finalize via tools;
- domain-pack validators now finalize via tools;
- evidence recording and evidence attachment tools return immediate repair
  feedback;
- database and document lookup tools return structured errors that are visible
  to the model inside the run.

The remaining self-healing opportunities are concentrated in older structured
output flows:

1. generic structured specialist handling in
   `backend/src/lib/openai_agents/streaming_tools.py`;
2. the general PDF extraction specialist, which has evidence tools and a
   structured result envelope but no explicit finalization tool;
3. lookup and shared validator-result specialists when they are run directly
   through supervisor routing or Agent Studio instead of through domain-pack
   validator dispatch;
4. the top-level streamed runner path that can still reject structured results
   for missing evidence after the agent has ended;
5. stale or misleading curation-prep agent configuration, which is less urgent
   because the active curation-prep path is deterministic service code.

The highest leverage implementation is a reusable "structured specialist
finalization" mechanism. It should let agents with `output_schema` and ordinary
tools opt into a mandatory finalization tool, with schema-specific and
evidence-specific checks. PDF extraction should be the first concrete adopter.
The generic mechanism should then cover lookup/provenance specialists, direct
use of shared validator-result specialists, and future custom structured
specialists without another bespoke patch for each agent.

## Design Goal

The design goal is not to make every error recoverable. Some failures should
remain fatal, especially infrastructure failures, missing configuration, missing
auth, invalid domain-pack metadata, or code bugs.

The design goal is narrower:

When an agent has enough context to fix its own proposed output, the contract
check should be exposed as a tool result during the live run, not as a backend
exception after final output.

That principle gives weaker models a clearer path:

1. gather context through tools;
2. propose a structured result to a finalization tool;
3. receive `accepted` or `rejected` with exact repair instructions;
4. repair only the listed issues;
5. call the finalization tool again;
6. finish only after the backend accepted the finalized result.

This is stronger than a post-run retry because the repair stays inside the
agent's tool-use loop. The model sees the same tool ledger, the same system
instructions, the same evidence records, and the exact validation failure. It
does not require a second "retry agent" to infer what the first agent meant.

## Terminology

### Late Rejection

A late rejection happens when the model has already ended its run and only then
the backend decides that the result is unusable. Typical examples:

- `result.final_output` is missing;
- a structured result lacks required evidence references;
- a result is valid JSON but semantically incomplete;
- a result says something was resolved but does not include the required lookup
  attempt provenance.

Late rejection is sometimes correct, but it is a poor shape for model-repairable
mistakes because the model cannot see the error and fix it in the same run.

### Post-Run Retry

A post-run retry is a backend-initiated second model invocation after the first
agent run finished. The current empty-output retry in `streaming_tools.py` is an
example. It asks a simplified retry agent to synthesize structured output from
the previous conversation history.

This can recover some failures, but it is less reliable than in-run tool
finalization because:

- the retry agent is not the original live tool-using agent;
- the retry agent may not have the same guardrails or allowed tools;
- repair feedback is indirect;
- every retry is another orchestration branch to debug;
- the retry pattern does not generalize cleanly to semantic validation errors.

### Agentic Finalization Tool

An agentic finalization tool is a function tool whose only purpose is to accept
or reject the model's proposed result before the model finishes.

A finalization tool should:

- receive the proposed structured payload or result fragment;
- validate the proposal against schema, provenance, evidence, and domain rules;
- return `status: accepted` only when the backend is willing to use that result;
- return `status: rejected` with compact, exact repair instructions otherwise;
- store the accepted payload in run-scoped state;
- be mandatory before final answer for that agent type;
- be idempotent when called repeatedly with the same accepted payload.

The validator finalization implementation is the current reference pattern.

### Repair-Loop Turn Budgeting

Any in-run repair loop needs an explicit turn-budget policy. This applies to
new finalization tools and to the repair loops that already exist.

The generic rule:

- if the backend requires a model-facing tool handshake before final answer,
  the runtime should add enough `max_turns` headroom for that handshake;
- if the handshake can reject and ask for repair, the budget should cover at
  least one rejected proposal and one accepted repair;
- if the agent is expected to stage records, attach evidence, then finalize,
  the budget should cover the normal staging path plus one local correction;
- if a repair loop is optional rather than mandatory, the runtime should still
  avoid setting the default so low that a single tool rejection consumes the
  whole run.

This is not a license to make runs unbounded. The goal is to prevent false
`MaxTurnsExceeded` failures in exactly the cases where the backend has become
more agentic-friendly and asked the model to self-correct.

Existing repair loops to audit:

- builder extractor staging/finalization loops;
- domain-pack validator finalization loops;
- evidence recording and `attach_evidence_to_object` retry loops;
- direct lookup retry loops when prompts ask the model to retry with a
  corrected query;
- future generic structured-specialist finalization loops.

## Current Good Patterns

This section names runtime areas that already look self-healing enough for the
current design. These should not be the first targets unless a specific trace
shows a failure.

### Domain Builder Extractors

The builder extractors already use staging and finalization tools rather than
trusting model-authored final envelopes.

Relevant files and tools:

- `packages/alliance/tools/bindings.yaml`
- `packages/alliance/python/src/agr_ai_curation_alliance/tools/gene_builder_tools.py`
- `packages/alliance/python/src/agr_ai_curation_alliance/tools/allele_builder_tools.py`
- `packages/alliance/python/src/agr_ai_curation_alliance/tools/phenotype_builder_tools.py`
- `packages/alliance/python/src/agr_ai_curation_alliance/tools/disease_builder_tools.py`
- `packages/alliance/python/src/agr_ai_curation_alliance/tools/agr_curation.py`

The builder finalization tools currently include:

- `finalize_gene_extraction`
- `finalize_gene_expression_extraction`
- `finalize_allele_extraction`
- `finalize_phenotype_extraction`
- `finalize_disease_extraction`

These tools are marked with `builder_finalization: true` in the package binding
metadata. Runtime detection in `streaming_tools.py` uses that metadata rather
than a hard-coded list, which is the desired direction.

These extractors still can fail, but the failures are generally in the right
place: staging and finalization tools return actionable feedback while the
extractor is running, and the backend builds the domain envelope from accepted
tool state.

Budget follow-up:

Even though the builder flow already has the right repair shape, it should be
audited for turn-budget headroom. A realistic builder run may need to read the
paper, record evidence, stage several objects, attach evidence, receive one
tool rejection, repair that object, and then call the builder finalizer. The
runtime should not treat the finalizer as "free" when setting `max_turns`.

### Domain-Pack Validators

The validator dispatch path now uses mandatory in-run validator finalization.

Relevant file:

- `backend/src/lib/domain_packs/validator_dispatch.py`

Important pieces:

- `_ValidatorFinalizationState`
- `_ValidatorFinalizationFeedback`
- `_build_finalize_validator_result_tool`
- `_build_finalize_validator_batch_results_tool`
- `_append_validator_finalization_instructions`
- `run_package_scoped_validator_agent`
- `run_package_scoped_validator_agent_batch`

The key invariant is now:

- a validator agent run is accepted only if the mandatory finalization tool
  stored an accepted result in run state;
- if the tool rejects a proposal, the model receives repair instructions before
  final answer;
- if the agent never calls the finalization tool successfully, the run fails
  with a clear error.

This directly addresses the original weaker-model validator failure.

Budget follow-up:

Validator dispatch already has a dedicated finalization turn-budget adjustment.
Keep that as the reference behavior, but add tests that both single-result and
batch validator runs survive a rejected-then-accepted finalization cycle under
the configured defaults. If new validator-adjacent loops are added later, they
should reuse the same budgeting principle instead of relying on prompt length
or model luck.

### Evidence Recording and Evidence Attachment

Evidence tools are already useful self-healing surfaces.

For example:

- `record_evidence` returns retry instructions when span inputs are invalid;
- builder tools can validate evidence IDs against the active evidence registry;
- `attach_evidence_to_object` can reject invalid object refs or pending refs and
  let the extractor retry with the intended staged object.

This area may need future refinements, but it already follows the right
interaction model: the model learns about evidence problems while it is still
able to repair them.

Budget follow-up:

Evidence repair is often nested inside builder or PDF extraction work. The
outer agent budget should include at least one evidence-recording or
evidence-attachment rejection and retry, especially when finalization is also
mandatory. Otherwise a successful repair sequence can still fail at the run
level because the extra evidence tool turn was not budgeted.

### Database and Document Lookup Tools

The tool layer usually returns visible structured failure data rather than
hiding all failures as backend exceptions. That is good because the model can
respond with unresolved status, retry with a corrected query, or preserve the
lookup error for curator review.

This does not mean every external-service failure is model-repairable. For
example, missing Elasticsearch configuration for literature lookup is not a
model reasoning error. The agent should not "repair" missing infrastructure.
But the current tool event shape is still useful because it gives the agent and
curator a visible reason.

Budget follow-up:

Lookup retry is usually not mandatory in the same way as finalization, but
lookup-oriented agents often need one corrected query after a rejected,
ambiguous, or over-broad lookup. Direct lookup specialists that gain mandatory
finalization should budget for the lookup call, one corrected lookup when
reasonable, and the finalization proposal/repair cycle.

## Inventory of Remaining Candidates

### Candidate 1: Generic Structured Specialist Finalization

Priority: highest.

Relevant file:

- `backend/src/lib/openai_agents/streaming_tools.py`

Current behavior:

- `run_specialist_with_events` streams a specialist run and then extracts
  `result.final_output`.
- If no final output exists for an agent with `output_type`, the runtime starts
  a post-run retry agent with a nudge prompt.
- After output extraction, the runtime canonicalizes structured output,
  optionally stages extraction payload, emits evidence summaries, and dispatches
  domain-envelope validators.
- If evidence is required but missing, `_emit_specialist_evidence_summary_or_raise`
  raises `SpecialistOutputError` after the agent has ended.
- Required package tool-call enforcement can also reject after streaming
  completes through `_required_tool_failure_message`. For package tools such as
  `agr_curation_query`, that can catch missing required lookups, but it is still
  a late rejection rather than an in-run repair loop.

Why this is not agentic-friendly enough:

- the model can finish without calling a finalization tool;
- empty-output recovery uses a separate retry agent rather than the original
  agent receiving a tool verdict;
- evidence requirements can still fail after final output;
- required lookup-tool requirements can still fail after final output;
- generic structured agents have no standardized "backend accepts this payload"
  handshake;
- every new structured specialist has to rely on prompt discipline plus
  post-run checks.

Target behavior:

Structured specialists that are not builder extractors and not domain-pack
validators should be able to opt into a mandatory finalization tool. The tool
should validate the proposed structured result while the agent is still running.

The generic finalization tool might be named one of:

- `finalize_specialist_result`
- `finalize_structured_result`
- `finalize_agent_result`

The exact name matters less than the invariant:

- the agent must call it before final answer;
- the backend must store the accepted payload;
- the runtime should prefer the accepted payload over free-form final output;
- if no accepted payload exists, the specialist should fail with a clear
  required-finalization error.

The generic tool should support pluggable validators:

- Pydantic schema validation for the agent's `output_schema`;
- evidence checks using existing evidence summary helpers;
- required tool-call checks where package metadata declares a lookup tool;
- optional schema-specific finalization adapters for PDF, GO, orthologs, and
  future custom agents.

The first implementation does not need to replace every old post-run check.
It can start by adding an opt-in flag or runtime detection path for a narrow
set of agents. But the implementation should be generic enough that adding the
next structured specialist is mostly configuration, not another bespoke code
path.

Suggested runtime shape:

1. At specialist startup, inspect the runtime agent:
   - if it is a builder materializer, keep the existing builder finalization path;
   - if it is a validator agent, keep the validator dispatch finalization path;
   - if it has `output_type` and is configured for structured finalization,
     append a finalization tool and instructions.
2. Initialize a run-scoped finalization state object.
3. The finalization tool accepts a `dict[str, Any]` proposal.
4. The tool validates the proposal against the agent's output schema.
5. The tool runs evidence checks when the schema requires evidence.
6. The tool runs any schema-specific finalization adapter.
7. On success, the tool stores the canonical payload and returns
   `status: accepted`.
8. On failure, the tool returns:
   - `status: rejected`;
   - `message`;
   - `repair_instructions`;
   - `field_errors`;
   - `missing_evidence_record_ids`;
   - `missing_tool_calls`, when applicable.
9. After streaming ends, `run_specialist_with_events` uses the accepted payload
   if one exists.
10. If no accepted payload exists for an agent that requires finalization, the
    runtime raises `SpecialistOutputError` without trying to salvage free-form
    final JSON.

The finalization-enabled runtime should also expand the agent's `max_turns`
budget, analogous to validator dispatch. Mandatory finalization costs at least
one tool turn, and a useful repair loop usually costs two: rejected proposal,
then accepted repair. Without this budget adjustment, weaker or more cautious
models can fail with `MaxTurnsExceeded` exactly when the new tool loop is doing
the right thing.

State-binding rule:

The finalization tool must close over the same run-scoped evidence registry,
tool-call ledger, builder/resolver state, and event sinks as the other tools
bound for that specialist run. If `run_specialist_with_events` rebuilds tools
with run-state wrappers before streaming, the finalization tool must be added
as part of that same binding pass or explicitly receive the same state object.
A finalizer that inspects a different evidence registry could reject valid
evidence IDs or accept invented IDs.

Suggested instruction layer:

The model should receive locked instructions similar to validator finalization:

> Structured result finalization is mandatory. Before your final answer, call
> `finalize_specialist_result` with your proposed result. If the tool returns
> `status: rejected`, repair only the reported issues, call the finalization
> tool again, and do not send the final answer until the tool returns
> `status: accepted`.

Compatibility concern:

Some existing agents still rely on SDK structured output. The rollout should be
opt-in at first. Do not flip every `output_schema` agent at once.

Recommended first adopters:

1. PDF extraction;
2. GO term lookup;
3. GO annotations lookup;
4. ortholog lookup.

What to do with the old empty-output retry:

The old retry can remain temporarily for agents that have not opted into
finalization. For finalization-enabled agents, an empty final output should not
trigger a separate synthesis agent if the accepted finalization payload exists.
The accepted tool payload is the output.

Eventually, the retry path should shrink or disappear for structured
specialists. It is a compatibility bridge, not the preferred architecture.

### Candidate 2: General PDF Extraction Finalization

Priority: highest concrete adopter.

Relevant files:

- `packages/alliance/agents/pdf/agent.yaml`
- `packages/alliance/agents/pdf/prompt.yaml`
- `backend/src/schemas/models/pdf_extraction.py`
- `backend/src/lib/openai_agents/evidence_summary.py`
- `backend/src/lib/openai_agents/streaming_tools.py`

Current behavior:

- the PDF extraction specialist has document retrieval tools;
- it has `record_evidence`;
- it declares `output_schema: PdfExtractionResultEnvelope`;
- the prompt says `kept_count > 0` requires `evidence_records[]`;
- evidence is checked after output via shared evidence-summary helpers.

Why this should be adjusted:

The PDF agent is exactly the kind of agent that benefits from a finalization
tool. It often gathers enough evidence correctly, but the final structured
result can still omit evidence IDs, mismatch `kept_count`, forget to reference
recorded evidence from retained items, or answer in a way that is curator-useful
but not contract-complete.

A post-run error here is frustrating because the model probably had the live
evidence records available seconds earlier. The backend should hand it the
specific missing evidence-reference report before the run ends.

Proposed tool:

`finalize_pdf_extraction`

Input:

- the proposed `PdfExtractionResultEnvelope` payload;
- optional concise completion note;
- optional model-reported coverage notes.

Validation should check:

- payload validates as `PdfExtractionResultEnvelope`;
- the agent called at least one document retrieval tool before finalization;
- if `run_summary.kept_count > 0`, evidence records are present;
- if retained `items[]`, `raw_mentions[]`, `exclusions[]`, or `ambiguities[]`
  include evidence-reference fields, those references resolve to the active
  evidence registry or to evidence records included in the proposal;
- recorded evidence IDs are not invented;
- proposed `evidence_records[]` entries match active-run evidence records by
  ID, verified quote, source location, and source fragments where those fields
  exist; retained `items[]` should not be expected to carry quotes unless the
  schema actually provides quote fields there;
- `kept_count`, `excluded_count`, and `ambiguous_count` are internally
  consistent with the relevant arrays when the schema provides those fields;
- empty-result answers explain what was searched or why nothing relevant was
  found.

Suggested accepted output:

```json
{
  "status": "accepted",
  "message": "PDF extraction result accepted.",
  "summary": {
    "kept_count": 3,
    "evidence_record_count": 3,
    "warning_count": 0
  }
}
```

Suggested rejected output:

```json
{
  "status": "rejected",
  "message": "PDF extraction result has retained items but no verified evidence references.",
  "repair_instructions": [
    "Use evidence_record_ids returned by record_evidence.",
    "Attach at least one verified evidence record to each retained item.",
    "Call finalize_pdf_extraction again with the repaired payload."
  ],
  "field_errors": [
    {
      "path": "items[0].evidence_record_ids",
      "message": "No evidence record IDs were supplied for a retained item."
    }
  ]
}
```

Why PDF first:

- the code already has evidence-specific checks;
- the agent already has `record_evidence`, so repair is practical;
- it is supervisor-routable and curator-facing;
- improvements here help broad document questions, not only domain-specific
  extraction;
- it is a clean proving ground for generic structured-specialist finalization
  before touching lookup agents.

Implementation notes:

- Use the generic structured finalization framework if it exists.
- If PDF lands first, keep the PDF finalization logic factored so it can become
  a plugin to the generic framework rather than a permanent special case.
- The accepted PDF payload should flow into the same output path currently used
  by `final_output`.
- The evidence summary event should use accepted finalization payload evidence,
  not stale raw final output.

### Candidate 3: Lookup and Shared Validator-Result Specialists

Priority: medium.

Relevant files:

- `packages/alliance/agents/chemical/agent.yaml`
- `packages/alliance/agents/chemical/prompt.yaml`
- `packages/alliance/agents/gene_ontology/agent.yaml`
- `packages/alliance/agents/gene_ontology/prompt.yaml`
- `packages/alliance/agents/go_annotations/agent.yaml`
- `packages/alliance/agents/go_annotations/prompt.yaml`
- `packages/alliance/agents/orthologs/agent.yaml`
- `packages/alliance/agents/orthologs/prompt.yaml`
- `packages/alliance/agents/reference/agent.yaml`
- `packages/alliance/agents/reference/prompt.yaml`
- `packages/alliance/agents/gene/agent.yaml`
- `packages/alliance/agents/allele/agent.yaml`
- `packages/alliance/agents/disease/agent.yaml`
- `packages/alliance/agents/ontology_term/agent.yaml`
- `packages/alliance/agents/controlled_vocabulary/agent.yaml`
- `packages/alliance/agents/data_provider/agent.yaml`
- `packages/alliance/agents/subject_entity/agent.yaml`
- `packages/alliance/agents/agm/agent.yaml`
- `packages/alliance/agents/experimental_condition/agent.yaml`
- `packages/alliance/tools/bindings.yaml`

Current behavior:

Several supervisor-routable or Agent Studio-routable agents have lookup tools
and structured output schemas:

- chemical lookup uses `chebi_api_call` and a structured chemical validation
  result;
- GO term lookup uses `quickgo_api_call` and `GOTermResultEnvelope`;
- GO annotation lookup uses `go_api_call` and `GOAnnotationsResult`;
- ortholog lookup uses `alliance_api_call` and `OrthologsResult`;
- reference lookup uses `agr_literature_reference_lookup` and
  `ReferenceValidationResult`;
- shared validator-result agents such as gene, allele, disease, ontology term,
  controlled vocabulary, data provider, subject entity, AGM, and experimental
  condition use the same validator-result schema family when called directly.

Some of these agents are also used by domain-pack validator dispatch. In that
mode, they are already protected by `finalize_validator_result` or
`finalize_validator_batch_results`. The gap is the direct-use mode: when the
same agent is called as an ordinary specialist from supervisor routing, Agent
Studio, or a custom agent clone, validator-dispatch finalization is not
automatically involved.

Why they should be adjusted:

These agents are lookup-oriented. If a direct specialist call reports a
resolved fact without an API-backed lookup attempt, the result is not
trustworthy. If an API call fails, the structured output should preserve an
unresolved or error state rather than inventing a positive result.

The prompts already say variations of this, but prompt-only enforcement is the
fragile part. A finalization tool would let the backend reject a resolved result
that lacks the required lookup provenance while the agent is still running.

Possible generic checks:

- if a result has status `resolved`, require at least one successful lookup/tool
  attempt;
- require the accepted result to faithfully copy or reference lookup attempts
  from domain, literature, document, or API tools. "A tool was called" and "the
  final result preserved the successful lookup attempt" are separate checks;
- if a lookup tool returned an upstream error, require `unresolved` or `error`
  status unless a later successful lookup attempt supersedes it;
- if the query requested multiple terms or genes, require coverage accounting:
  resolved, unresolved, not found, or error for each requested input;
- require `missing_expected_fields` or equivalent when the agent cannot satisfy
  a field expected by a binding or user request;
- ensure lookup attempts are copied from tool outputs, not invented in final
  JSON;
- ensure the finalization tool itself does not count as lookup provenance;
  provenance checks must count domain, literature, document, or API lookup
  tools only.

Implementation options:

Option A: generic `finalize_specialist_result` with schema-specific adapters.

This is preferred. Lookup and validator-result agents become configuration
entries that tell the finalizer which tool calls count as lookup provenance,
which status fields imply successful resolution, and which schema family is
being finalized.

The first adapter metadata should name the concrete provenance tools rather
than relying on package-level defaults:

- `chebi_api_call` for chemical lookup;
- `quickgo_api_call` for GO term lookup;
- `go_api_call` for GO annotation lookup;
- `alliance_api_call` for ortholog lookup;
- `agr_literature_reference_lookup` for reference lookup;
- `agr_curation_query` for curation database lookup specialists.

Only `agr_curation_query` currently has broad `required_tool_call` metadata in
`packages/alliance/tools/bindings.yaml`. The REST/API-style tools above still
need explicit provenance adapter configuration even if they are mandatory in
their prompts.

Option B: individual finalization tools:

- `finalize_chemical_lookup`
- `finalize_go_term_lookup`
- `finalize_go_annotations_lookup`
- `finalize_ortholog_lookup`
- `finalize_reference_lookup`
- `finalize_direct_validator_result`

This is more explicit but risks duplicating the validator finalization pattern
many more times.

Recommendation:

Use Option A unless a schema difference makes the generic adapter too contorted.
The point of doing PDF first is to learn the minimal generic surface before
adding these lookup agents.

The rollout should phase this bucket:

1. GO term, GO annotations, and orthologs, because they are clearly direct-use
   lookup specialists and are less entangled with domain-pack validator
   dispatch.
2. Chemical and reference, because they expose the same provenance problem but
   may have more production-data or infrastructure edge cases.
3. Shared validator-result agents in direct-use mode, with explicit mode
   detection so validator dispatch remains on its specialized finalization
   path.

Direct-use validator-result caveat:

The domain-pack validator-dispatch path has a real `DomainValidationRequest`
with request IDs, binding IDs, targets, and selected inputs. A supervisor-routed
or Agent Studio direct lookup may not. Direct-use validator-result finalization
therefore needs an explicit direct-call request envelope, or a documented
mapping that synthesizes request identity fields from the user query and tool
ledger. It should not blindly reuse dispatcher identity checks and then force
models to invent `request_id`, `validator_binding_id`, or `target` values.

### Candidate 4: Top-Level Streamed Runner Structured Evidence Guardrail

Priority: medium, but verify live usage before editing.

Relevant file:

- `backend/src/lib/openai_agents/runner.py`

Current behavior:

`run_agent_streamed` has a structured-result evidence guardrail after
`result.final_output` is captured. If the structured result requires evidence
and the evidence records or references are missing, it emits a `RUN_ERROR` and
returns.

Why it may need adjustment:

This is another late rejection pattern. The model has already finished, and the
backend says the extraction completed without required verified evidence. If
this path is still used for structured extraction or structured answer agents,
the model cannot repair the missing evidence references inside the same run.

This path may be less central for ordinary chat extraction now that
supervisor-routed specialists use `run_specialist_with_events` and builder
extractors use builder tools. It is not safe to treat it as dead, though:
Agent Studio direct runs and custom-agent execution can pass prebuilt agents
into `run_agent_streamed`, and those agents may carry `output_schema_key`.
Before changing behavior, instrument or test which live top-level and custom
agents can still reach this guardrail with `structured_result is not None`.

Potential target behavior:

- If the top-level agent has a structured `output_type` and requires evidence,
  add the same generic finalization tool used by specialist agents.
- The accepted finalization payload becomes `structured_result`.
- The old post-run `RUN_ERROR` remains as a fail-safe, not the primary contract.

Recommended approach:

Do not build a separate top-level finalization system. Reuse the same structured
finalization helper introduced for specialists. The runner and specialist
wrappers should share validation helpers and payload formats so evidence rules
do not fork.

Questions to answer before implementation:

- Which top-level agents currently have `output_type` in production?
- Do Agent Studio custom agents use this path for structured output?
- Are any top-level structured outputs curator-facing, or is this mostly legacy?
- Can the finalization helper be attached at `Agent` construction time in
  `catalog_service.py`, or does it need to happen in the runner because it
  depends on runtime evidence state?

### Candidate 5: Curation Prep Agent Configuration Cleanup

Priority: low.

Relevant files:

- `config/agents/curation_prep/agent.yaml`
- `config/agents/curation_prep/prompt.yaml`
- `backend/src/lib/curation_workspace/curation_prep_service.py`
- `backend/src/schemas/curation_prep.py`

Initial suspicion:

`curation_prep` declares `output_schema: CurationPrepAgentOutput` and has no
tools. At first glance that looks like a structured-output agent that could
produce invalid envelope refs and only be rejected after the run.

Current finding:

The active curation prep service path is deterministic. `run_curation_prep`
does not ask an LLM to author `CurationPrepAgentOutput`. It filters extraction
results, materializes persisted domain envelopes, computes `review_row_count`,
and creates the output in service code.

That makes curation prep a poor first target for agentic finalization. There is
no model loop to repair in the main path.

What still needs cleanup:

- The config and prompt may now be misleading because they describe an agent
  authoring `CurationPrepAgentOutput`.
- If any legacy flow path still invokes `curation_prep` as a normal LLM agent,
  that path should either be removed or redirected to deterministic service
  code.
- Runtime validation should ensure curation prep remains deterministic if that
  is now the intended architecture.

Recommendation:

Do not build `finalize_curation_prep` unless a real trace shows an LLM-authored
curation-prep output is still in use. Instead, schedule a cleanup pass:

- confirm all live curation prep invocations call `run_curation_prep`;
- update or simplify `config/agents/curation_prep/prompt.yaml` if it is no
  longer used as an LLM prompt;
- consider changing agent metadata to more clearly mark it deterministic;
- add a regression test if there is risk that future loader work reintroduces an
  LLM curation-prep path.

## Proposed Architecture

### Runtime Finalization State

Add a small run-scoped state object for structured specialist finalization.

It should track:

- whether finalization is required;
- finalization tool name;
- agent name and agent id;
- output schema type;
- accepted payload;
- canonical model dump of accepted payload;
- last rejection payload;
- validation warnings;
- evidence record IDs referenced by the accepted payload;
- whether finalization was called at least once;
- accepted and rejected finalization call events, including timestamp, tool
  name, rejection reason, compact repair summary, and payload digest;
- the accepted payload source, so downstream audit can distinguish
  tool-accepted structured output from SDK `final_output` or text-JSON
  recovery.

The state should be local to one specialist run. It should not leak across
parallel tool calls or concurrent chats.

The builder finalization state and validator finalization state should remain
separate because they have different domain responsibilities. The new generic
state should not replace builder or validator state.

Finalization events should be visible in specialist and trace/audit streams.
Accepted finalization should preserve the accepting tool name and canonical
payload metadata. Rejected finalization should remain diagnostic repair
feedback and should not be persisted as the canonical result.

### Tool Ledger Normalization

Generic finalization should not depend on one incidental representation of
tool calls. Different runtime paths expose different ledgers:

- specialist streaming has the run's `tool_calls` and emitted tool events;
- Agent Studio and top-level runner paths can involve `ToolCallTracker` and
  guardrail state;
- validator dispatch result objects carry `lookup_attempts`;
- REST/API-style lookup tools may have successful tool events but no package
  `required_tool_call` metadata.

The finalizer should receive a normalized view of provenance:

- tool name;
- call status;
- query/input digest;
- result status or upstream error;
- result payload digest or structured lookup attempt;
- whether the tool is allowed to count as lookup provenance for this schema.

This normalized ledger lets adapters reject three different failure classes
cleanly: no lookup tool was called, the wrong lookup tool was called, or a
lookup was called but the proposed result failed to preserve the successful
lookup attempt.

### Runtime Mode Matrix

The finalization choice should be explicit by runtime mode, not inferred only
from schema class names.

| Runtime mode | Example agents | Canonical finalization | Generic structured finalization? |
| --- | --- | --- | --- |
| Builder extractor / materializer | gene expression, gene, allele, phenotype, disease extractors | `finalize_*_extraction` builder tool | No |
| Domain-pack validator dispatch | gene validation, ontology term validation, reference validation when called by active validator binding | `finalize_validator_result` or `finalize_validator_batch_results` | No |
| Direct structured specialist | PDF extraction, GO term lookup, GO annotations, orthologs, chemical/reference direct lookup | Generic/specialized `finalize_*` structured-specialist tool | Yes, opt-in |
| Direct shared validator-result specialist | gene/allele/disease/ontology/data-provider/etc. when called directly by supervisor or Agent Studio | Generic direct-specialist finalizer that understands validator-result provenance | Yes, opt-in and mode-aware |
| Deterministic service path | curation prep, curation handoff | Service code validation | No |
| Plain text/chat path | supervisor final prose, chat output | None beyond existing stream/tool guardrails | No |

The important subtlety is that a `DomainValidatorResultBase` schema does not
automatically mean "validator-dispatch mode." The same package agent can be
called by active validator dispatch or directly by a user/supervisor. Only the
dispatch path should use validator-dispatch finalization. Direct-use paths need
their own generic finalization only when deliberately enabled.

### Finalization Tool Response Contract

Use one common response shape across finalization tools:

```json
{
  "status": "accepted",
  "message": "Result accepted.",
  "summary": {},
  "warnings": []
}
```

or:

```json
{
  "status": "rejected",
  "message": "Result rejected.",
  "repair_instructions": [],
  "field_errors": [],
  "warnings": []
}
```

The tool response should be compact but specific. The model does not need a
dump of the full schema. It needs exact repair actions.

Good repair instruction:

- "Add `evidence_record_ids: [\"evidence-record-1\"]` to `items[0]`, because
  the item is retained and the recorded evidence registry contains that ID."

Weak repair instruction:

- "Output did not match schema."

### Required Finalization Enforcement

For agents opted into structured finalization, post-run enforcement should be
strict:

- accepted finalization exists: use accepted payload;
- finalization called but rejected: fail with last rejection summary;
- finalization never called: fail with missing required finalization error;
- final output exists but no accepted finalization: do not trust final output.

This mirrors validator behavior.

### Relationship to SDK Structured Output

The OpenAI SDK structured output schema can still be useful, but it should not
be the only contract boundary for these agents.

Preferred shape:

- the finalization tool input schema is permissive enough to receive model
  proposals;
- function-tool schemas should use permissive dict input and avoid strict
  provider-specific shapes when provider adapters cannot reliably satisfy them;
- backend code validates the proposal with Pydantic and domain checks;
- accepted payload is canonicalized with `model_dump`;
- the model's final answer can be a short acknowledgment or summary;
- runtime uses the accepted payload as the structured result.

For finalization-enabled Groq or text-JSON compatibility paths, prefer
`output_type=None` or an acknowledgment-style final output and trust the
accepted finalization state. Provider-specific text JSON recovery should remain
a fallback for agents that are not yet using finalization, not a parallel
canonical-output path for enabled agents.

This avoids the brittle situation where the final output has to be both a
conversation answer and the backend-trusted data artifact.

### Relationship to Required Tool Calls

`packages/alliance/tools/bindings.yaml` already supports `required_tool_call`
metadata for package tools such as curation database lookup. The generic
finalizer should not duplicate that entire system.

Instead:

- keep required-tool enforcement as a broad guardrail;
- let finalization adapters check whether required tool provenance is reflected
  in the proposed result;
- for lookup agents, finalization should know which tool calls are acceptable
  evidence of lookup provenance;
- recognize that `_required_tool_failure_message` can still reject after
  streaming completes; finalization is the in-run repair mechanism, while
  required-tool enforcement remains a late fail-safe;
- add explicit adapter metadata for REST/API tools that do not currently have
  package `required_tool_call` declarations, including `chebi_api_call`,
  `quickgo_api_call`, `go_api_call`, `alliance_api_call`, and
  `agr_literature_reference_lookup`.

Example:

If a GO term lookup result says `status: resolved`, finalization can require
that `quickgo_api_call` appears in the tool ledger and that the result includes
or references at least one successful attempt from that ledger.

### Relationship to Evidence Summary

Evidence summary should become a consumer of accepted finalization payloads,
not only a post-run checker of arbitrary final output.

Current evidence helper behavior is valuable and should be reused:

- `structured_result_requires_evidence`;
- `structured_result_missing_evidence_record_refs`;
- `structured_result_evidence_reference_report`;
- `extract_evidence_records_from_structured_result`;
- `canonicalize_structured_result_payload`.

The new finalization tool should call these helpers before the agent stops.

Post-run evidence checks should remain as a fail-safe while rollout is in
progress, but for finalization-enabled agents the primary feedback should come
from the tool.

## Rollout Plan

### Phase 0: Existing Repair-Loop Budget Audit

Before adding new finalization adopters, audit the repair loops that already
exist and make sure their `max_turns` defaults match the repair contract they
advertise.

1. Builder extractor loops:
   - identify where builder extractor `max_turns` are computed;
   - account for staging, evidence recording, evidence attachment, one local
     repair, and finalization;
   - add or update tests for rejected builder tool calls followed by successful
     repair and finalization.
2. Validator dispatch loops:
   - confirm the existing finalization budget helper covers both single and
     batch validator runs;
   - add regression tests for rejected-then-accepted finalization under the
     configured default model settings;
   - ensure future validator-adjacent loops call the same budgeting helper or a
     shared equivalent.
3. Evidence repair loops:
   - test that one invalid `record_evidence` or `attach_evidence_to_object`
     call can be corrected before the outer agent exhausts turns;
   - include one case where evidence repair happens before mandatory
     finalization.
4. Direct lookup retry loops:
   - document the expected retry budget for lookup specialists;
   - when a lookup specialist becomes finalization-enabled, include lookup
     retry plus finalization repair in its budget.

### Phase 1: Design and Test Harness

1. Add unit tests around a small finalization helper with fake schemas:
   - accepts schema-valid payload;
   - rejects invalid Pydantic shape;
   - rejects retained item without evidence;
   - accepts repaired payload on second call;
   - stores accepted payload in run-scoped state.
2. Add tests for instruction injection:
   - finalization-enabled agent receives mandatory finalization instructions;
   - builder agents do not receive generic finalization instructions;
   - validator agents keep validator-specific finalization instructions.
3. Add tests for post-run enforcement:
   - accepted payload is used;
   - missing finalization fails;
   - rejected-only finalization fails with useful message.
4. Add tests for runtime mechanics:
   - finalization-enabled agents receive an expanded `max_turns` budget;
   - one rejected-then-accepted finalization cycle completes without
     `MaxTurnsExceeded`;
   - expanded `max_turns` composes with existing builder, validator, evidence,
     and lookup repair loops instead of only covering the new finalizer call;
   - accepted and rejected finalization events are emitted with finalization
     tool name and compact diagnostic metadata;
   - finalization sees the same live evidence registry as `record_evidence`;
   - two simultaneous specialist runs cannot see or accept each other's
     evidence IDs.
5. Add lookup-provenance harness tests before enabling lookup agents:
   - finalizer-only call is rejected as missing lookup provenance;
   - wrong lookup tool is rejected for the schema adapter;
   - REST/API lookup tool without `required_tool_call` metadata can still be
     accepted when adapter metadata permits it;
   - upstream tool error plus `unresolved` status is accepted;
   - later successful lookup supersedes an earlier upstream error.

### Phase 2: PDF Extraction Adopter

1. Add `finalize_pdf_extraction`.
2. Mark the PDF agent as finalization-enabled in config or runtime metadata.
3. Keep its existing document and evidence tools.
4. Update prompt instructions to call finalization before final answer.
5. Ensure accepted payload drives:
   - specialist final output;
   - evidence summary event;
   - supervisor summary reduction.
6. Add tests with:
   - kept item plus evidence accepted;
   - kept item without evidence rejected, then accepted after repair;
   - empty result accepted only with honest no-hit summary;
   - invented evidence ID rejected.

### Phase 3: Lookup Agents

1. Add generic lookup-provenance finalization adapter.
2. Apply to:
   - GO term lookup;
   - GO annotations lookup;
   - ortholog lookup.
3. Add tests:
   - resolved result without lookup attempt rejected;
   - unresolved result with tool error accepted;
   - multi-query result missing one requested input rejected;
   - repaired coverage accepted.
4. Add direct-use validator-result tests:
   - direct call without a dispatcher request uses the documented direct-call
     request envelope or identity mapping;
   - models are not required to invent dispatcher-only identity fields.

### Phase 4: Top-Level Runner Review

1. Instrument or test which top-level structured agents still hit the
   `runner.py` evidence guardrail.
2. If live, attach the same generic finalization helper.
3. If not live, leave the guardrail as a fail-safe and document it as legacy.

### Phase 5: Cleanup

1. Remove or shrink the post-run empty-output retry for finalization-enabled
   agents.
2. Clean up curation-prep config if confirmed deterministic-only.
3. Add docs to `docs/developer/guides/ADDING_NEW_AGENT.md` explaining when a
   new structured agent must use finalization.
4. Add docs explaining how to estimate `max_turns` for any new repair-capable
   tool loop, including builder staging, validator finalization, evidence
   attachment, lookup retry, and generic structured finalization.

## Acceptance Criteria

The first implementation slice should be considered successful when:

- PDF extraction cannot finish with retained items but missing verified evidence
  refs without first receiving an in-run finalization rejection;
- a repaired PDF finalization call can be accepted in the same run;
- accepted finalization payload is the payload used by specialist output and
  evidence summary;
- builder extractors remain on their existing builder finalization path;
- domain-pack validators remain on their existing validator finalization path;
- a finalization-enabled agent with an accepted tool payload does not invoke
  the old empty-output retry path;
- a finalization-enabled agent with an accepted tool payload does not depend on
  Groq/text-JSON recovery to produce canonical structured output;
- finalization-enabled agents receive enough `max_turns` budget for one
  rejected-then-accepted repair cycle;
- existing builder extractor and validator finalization loops also have
  tested `max_turns` headroom for one local rejection and repair;
- evidence repair can occur inside an outer extraction run without exhausting
  the run before finalization;
- lookup specialists that become finalization-enabled have enough budget for a
  corrected lookup plus one finalization rejection and repair;
- accepted and rejected finalization events are emitted to specialist/trace
  audit streams with finalization tool name and diagnostic metadata;
- the finalizer validates against the same live evidence registry used by
  `record_evidence`, including concurrent runs with distinct evidence IDs;
- the finalization tool itself cannot satisfy lookup-provenance requirements;
- lookup-provenance adapters reject wrong-tool and missing-tool cases, but can
  accept configured REST/API tools that lack package `required_tool_call`
  metadata;
- direct-use validator-result agents have a defined identity behavior when no
  dispatcher `DomainValidationRequest` exists;
- agents that are not opted into generic finalization continue to behave as
  before;
- Agent Studio/custom structured agents are opt-in: non-opted custom agents
  remain unchanged, and opted agents receive exactly one finalization tool;
- tests cover accepted, rejected, missing-finalization, and repaired cases.

## Risks

### Risk: Double Finalization Systems Collide

Builder extractors and validators already have specialized finalization systems.
The generic structured finalization path must not wrap those agents again.

Mitigation:

- explicitly detect and exclude builder materializer agents;
- keep validator dispatch finalization local to validator dispatch;
- add tests that builder and validator agents do not receive generic
  finalization tools.

### Risk: Tool Names or Instructions Become Confusing

If an agent has both `finalize_pdf_extraction` and generic instructions that say
`finalize_specialist_result`, the model may call the wrong tool.

Mitigation:

- each finalization-enabled agent should have exactly one model-facing
  finalization tool name;
- instruction text should use the exact tool name;
- audit events should report that exact tool name.

### Risk: Finalization Payloads Become Too Large

Large PDF or lookup results might make repeated finalization calls expensive.

Mitigation:

- repair responses should be compact;
- finalization should avoid echoing the entire proposal back to the model;
- if needed, allow finalization to accept references to staged records rather
  than full payloads in future phases.

### Risk: SDK Output Schema Fights Tool Finalization

If the agent still has a strict `output_type`, the SDK may pressure it to
produce final structured output even though the backend wants the tool-accepted
payload.

Mitigation:

- for finalization-enabled agents, consider a model-facing acknowledgment output
  or relaxed output schema;
- where provider adapters are sensitive to strict function-tool schemas, use
  permissive dict input and backend-side validation;
- ensure runtime prefers accepted tool payload over final output;
- avoid prompting the model to hand-author the trusted object graph twice.

### Risk: Repair Loops Exhaust Turn Budget

Mandatory finalization adds tool calls, but it is not the only repair loop that
does. Builder staging, evidence recording, evidence attachment, validator
finalization, lookup retries, and future generic finalization all spend turns
when they do the right thing and let the model correct a precise tool rejection.

A healthy self-repair cycle can require one rejected proposal and one accepted
proposal before the agent gives its final acknowledgment. In extractor runs,
that cycle may happen after several ordinary staging and evidence calls.

Mitigation:

- increase `max_turns` for finalization-enabled agents, as validator dispatch
  already does for validator finalization;
- audit builder, validator, evidence, and lookup repair loops for their own
  default turn budgets;
- define a small shared budgeting helper or policy so every repair-capable loop
  adds predictable headroom instead of each caller guessing;
- include tests for rejected-then-accepted repair cycles under the default
  configured turn budget;
- include nested repair tests, such as evidence repair followed by mandatory
  finalization;
- make rejection responses compact so the repair turn stays focused.

### Risk: Groq/Text-JSON Compatibility Paths Fork Behavior

`streaming_tools.py` has compatibility handling for providers that return
structured output as text rather than SDK `final_output`. A generic finalization
path must decide whether that compatibility path still disables SDK structured
output, or whether tool finalization replaces it for enabled agents.

Mitigation:

- for finalization-enabled agents, prefer accepted tool payload over recovered
  JSON text;
- consider `output_type=None` or acknowledgment-style final output for enabled
  agents whose provider path struggles with SDK structured output;
- leave provider-specific text parsing as a fallback only for agents not yet
  using finalization;
- add tests that accepted finalization suppresses the empty-output retry and
  does not require final text JSON recovery.

### Risk: Finalization Tool Binds to the Wrong Runtime State

Specialist tools are rebound around run-scoped evidence, resolver, builder, and
event state. A finalization tool added too early or too late could inspect a
different registry than `record_evidence` or a different tool ledger than the
one used for emitted tool events.

Mitigation:

- add the finalization tool during the same run-state binding pass as the other
  specialist tools, or explicitly pass the same state object;
- test that accepted evidence IDs must exist in the current run's evidence
  registry;
- include a concurrency test with two simultaneous runs using different
  evidence IDs.

### Risk: Required-Tool Guardrails and Provenance Checks Blur Together

Existing output guardrails can prove that some tool was called. They do not
prove the final structured result faithfully carries lookup provenance or covers
all requested inputs. A finalization tool call itself is also a tool call and
must not satisfy "lookup happened" requirements.

Mitigation:

- finalization adapters should inspect the domain/document/API tool ledger, not
  only a total tool-call count;
- normalize ledger views across specialist streaming, Agent Studio/top-level
  tracker state, and validator-dispatch lookup attempts;
- finalization tool names must be excluded from lookup-provenance accounting;
- tests should include a result that calls only the finalizer and no lookup
  tool, and that result must be rejected.

### Risk: Evidence State Is Run-Scoped

Evidence IDs are meaningful only inside the active run's evidence workspace and
tool ledger. A finalizer that reads a different state container could reject
valid evidence or accept invented IDs.

Mitigation:

- initialize generic finalization state inside the same runtime scope that
  binds `record_evidence`;
- validate evidence IDs against the active evidence registry used by the
  specialist run;
- add concurrency tests for two simultaneous specialist runs with different
  evidence IDs.

### Risk: Custom Agent Studio Agents

Agent Studio custom agents may use `output_schema_key`. Some may benefit from
generic finalization, while others may be experimental or not curator-facing.

Mitigation:

- do not auto-enable generic finalization for all custom structured agents in
  the first slice;
- add a future opt-in toggle or metadata flag;
- document the recommended setting for curator-facing structured agents.

## Open Questions

1. What config flag should enable generic structured finalization?
   Possibilities:
   - `structured_finalization: true` in `agent.yaml`;
   - package binding metadata;
   - runtime hard-coded allow-list for the first rollout;
   - automatic detection for specific schema classes.
2. Should PDF use `finalize_pdf_extraction` as a named tool, or should it use a
   generic `finalize_specialist_result` tool with PDF-specific validation?
3. Should finalization tools accept a full proposed payload, or should they
   accept only a reference to staged state where possible?
4. Which top-level structured agents still use `runner.py` in production?
5. Should the old empty-output retry be removed immediately for PDF after
   finalization lands, or left as a temporary fail-safe?
6. How much of finalization should be visible in Agent Studio tool docs versus
   locked runtime instructions?

## Recommended Immediate Next Step

Implement the PDF slice first, but design it as the first adopter of a generic
structured-finalization helper.

The implementation should not begin by touching GO or ortholog agents. PDF is
the better proving ground because evidence is the known pain point and the
existing helper functions already know how to report evidence-reference
problems.

After PDF works, add lookup-provenance adapters for GO, GO annotations, and
orthologs. Then extend the same mechanism to chemical/reference lookup and
mode-aware direct-use validator-result agents.

## Second-Opinion Review

Review status: completed by GPT-5.5 high reviewers on 2026-06-05.

Reviewer verdict:

- The overall inventory and priority order are sound after intentionally
  excluding hierarchy.
- PDF extraction is the right first concrete adopter.
- Generic structured finalization is the right shared abstraction, but it must
  be mode-aware.
- The original narrow GO/GO-annotations/ortholog bucket missed chemical,
  reference, and direct-use shared validator-result specialists.
- Curation prep should remain low priority because the live path is
  deterministic service code.

Corrections folded into this document:

- Candidate 3 was renamed from "GO Term, GO Annotation, and Ortholog Lookup
  Specialists" to "Lookup and Shared Validator-Result Specialists."
- Chemical and reference lookup agents were added explicitly.
- Direct-use validator-result agents were added as phased follow-ons, distinct
  from validator-dispatch mode.
- A runtime mode matrix was added so builder finalization, validator-dispatch
  finalization, generic specialist finalization, deterministic service paths,
  and plain chat paths do not collide.
- Top-level runner risk was strengthened to mention Agent Studio direct/custom
  execution.
- Acceptance criteria now require accepted finalization payloads to bypass the
  empty-output retry path and require finalizer calls not to count as lookup
  provenance.
- Risks now call out SDK structured-output friction, Groq/text-JSON recovery,
  provenance accounting, run-scoped evidence state, turn-budget exhaustion,
  provider schema strictness, and custom-agent opt-in.
- Turn-budget planning was broadened from new generic finalization only to all
  existing and future repair-capable loops, including builder extractors,
  validator finalization, evidence repair, and lookup retry.
- Rollout now starts with a Phase 0 audit of existing repair-loop `max_turns`
  behavior before adding new finalization adopters.
- Runtime state now requires finalization event metadata and canonical payload
  source tracking.
- Tool-ledger normalization was added to cover specialist streaming, top-level
  Agent Studio execution, validator-dispatch lookup attempts, and REST/API
  lookup tools without package `required_tool_call` metadata.
- PDF evidence matching was narrowed to evidence records and schema fields that
  actually carry quote/source data.
- Candidate 3 now names `chebi_api_call`, `quickgo_api_call`, `go_api_call`,
  `alliance_api_call`, and `agr_literature_reference_lookup` as explicit
  provenance adapter inputs.
- Direct-use validator-result agents now require a direct-call identity
  envelope or documented mapping instead of inventing dispatcher-only request
  fields.
- Rollout and acceptance tests now include max-turn expansion, finalization
  event emission, same-run evidence registry checks, REST lookup adapter
  behavior, and Agent Studio opt-in behavior.

Residual reviewer cautions:

- A `DomainValidatorResultBase` output schema alone is not enough to infer
  validator-dispatch mode. Runtime context must decide the finalization path.
- Required tool-call guardrails prove a tool was called, not that the final
  structured result faithfully reflects the lookup.
- Agent Studio custom agents should not be auto-enabled in the first rollout.
