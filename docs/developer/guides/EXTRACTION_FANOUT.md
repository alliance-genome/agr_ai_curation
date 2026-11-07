# Fan-out extraction

Fan-out extraction lets a builder extraction agent plan its records first and hand the
plan to several writers that stage records in parallel. It is off by default. The design
and its 12-paper measurements are tracked under ALL-1483 (speed and cost hotfix, W1).

## When it runs

`EXTRACTION_FANOUT_MIN_ITEMS` turns it on. With the default `0`, every extraction runs as
one writer, exactly as before. With a positive value, every builder extraction agent
plans first, and the plan size decides the path:

| Planned records | Path |
|---|---|
| 0 | The planner explains the empty result; code records it through the finalize tool, as a single writer's empty finalization does. |
| 1 to `EXTRACTION_FANOUT_MIN_ITEMS` - 1 | The planner's own thread is resumed with staging enabled and stages its plan. |
| `EXTRACTION_FANOUT_MIN_ITEMS` or more | Parallel writers stage the plan, then the planner's thread checks the result. |

A builder extraction agent is any agent carrying a tool with `builder_finalization: true`
binding metadata. Fan-out also needs exactly one such finalize tool, at least one tool
with `builder_stage: true` (a tool that adds one new draft record), and the document scope
its document tools were built for. Agents without these run as one writer; the run
records an `extraction_fanout.not_applicable` trace event with the reason.

Settings (all in `.env.example` under Operational limits, forwarded to `backend` and
`benchmark_worker`):

| Setting | Default | Meaning |
|---|---|---|
| `EXTRACTION_FANOUT_MIN_ITEMS` | `0` (off) | Planned records at which the plan goes to parallel writers. Production uses 15. |
| `EXTRACTION_FANOUT_ITEMS_PER_WRITER` | `5` | Planned records per writer. Writers = ceil(records / this), capped below. |
| `EXTRACTION_FANOUT_MAX_WRITERS` | `8` | Most writers one extraction may start; each opens its own model connection. |

The planner, the writers and the check pass all run at the agent's saved reasoning
effort. There is no separate planning effort, which also keeps the check pass on the
planner's prompt cache.

## Who decides what

The split follows `AGENTS.md` section 8: code does mechanics, the model does meaning.

- **Planner (the agent itself).** Reads the paper and calls `submit_extraction_plan`, a
  strict-schema tool: a short label and the evidence `chunk_index` values per record, plus
  up to 5 paper-wide notes. No field values. When the agent carries a tool whose binding
  metadata declares `builder_catalog: true` (a tool that lists the record types it can
  stage, such as `list_generic_object_classes`), the planning message names that tool and
  tells the planner to call it before reading, so each planned item is one record of the
  type that fits. Agents without one get the same message without that step. Code checks
  the plan's structure and that every cited chunk exists in the document (one
  tenant-scoped query); a plan citing a missing chunk is rejected with the missing numbers
  so the planner corrects it in the same thread, and a failed query fails the step.
  Staging is refused while it plans (`ExtractionBuilderWorkspace.planning`).
- **What the plan holds.** The planning message tells the planner to plan only records
  that answer the request under its instructions. Anything it will name only as a
  contrast or an exclusion (a control or other item outside the requested category)
  goes in its final explanation, not in items, even when the request or the
  supervisor's handoff asks it to distinguish them. An item inside the category, such as
  a transgenic line used as a control when transgenic lines are requested, is a record. This is guidance to the model; code never reads labels or notes to
  decide what belongs in the plan.
- **Code.** Counts the plan, slices it in order into balanced contiguous batches, reads each
  cited chunk once through the agent's own `read_chunk` tool and renders it exactly as the
  model sees a `read_chunk` result, starts the writers, and counts what each writer staged.
- **Writers.** `agent.clone()` with the agent's instructions and four tools: `read_chunk`,
  `search_document`, `record_evidence` and the stage tool. Each runs on its own provider
  connection (`run_agent_with_owned_openai_resources`) with a developer-role assignment:
  its items, the whole plan for context, the planner's notes and the prefetched chunks.
  A writer ends itself with a short final reply; code does not stop it on a count.
- **Check pass.** The planner's own thread is resumed with the full listing of staged
  records (candidate ID and exact staged fields) and, for any writer that staged fewer
  records than it was assigned, that writer's assigned items, staged records and final
  reply. The model decides what is missing, duplicated or wrong, and is asked to make
  every fix in one response with parallel tool calls. Code never matches labels or merges
  records. The planner thread and the writers run with parallel tool calls on wherever
  the provider's capability gates allow them.
- **Finalize.** Code calls the agent's own finalize tool with every retained record
  (writers' records in plan order, then the rest). Validation problems go back to the
  planner's thread once; if the second attempt also fails, the step fails exactly as a
  single-writer run that never finalized.

The planner's thread cannot finalize on its own: its finalize tool answers that the system
finalizes. Its last reply is the specialist explanation saved with the result.

## Concurrency contracts

- Each writer task binds a staging owner (`set_staging_owner`). New candidates are
  attributed to it, and a writer cannot overwrite another writer's draft through a reused
  `pending_ref_id` (`ExtractionBuilderOwnershipError`). The planner's thread keeps the
  single-writer semantics.
- Writers share the run's workspace (lock-protected). A writer's verified
  `record_evidence` result joins the run's live evidence list as soon as the call returns,
  so the writer can update its own new record by ID. Writers' tool calls are replayed
  through the specialist stream loop after they finish, which merges the same evidence
  records by ID, so the audit, the evidence summary and the tool-call record match a
  single-writer run.
- Extraction trace event sequence numbers are allocated under a lock.

## Failures

- Provider and runtime errors in any writer fail the step, as they do for one writer; the
  other writers are cancelled. Document read failures during prefetch fail the step, as a
  failed `read_chunk` call does.
- A planner that ends without a plan gets one reminder; without a plan after that, the step
  fails with `extraction_plan_missing`.
- There is no fallback to a single writer.

## What curators see

Chat shows plain-language phase lines (`AGENT_GENERATING` events with
`details.extractionPhase`), for example "Reading the paper and planning the records",
"Writing 38 records in 8 parallel batches", "Checking the 37 written records" (the staged
count) and, when validation sends problems back, "Fixing the problems validation found
before saving". The planner's reading streams live as today. Writers' tool calls reach the
audit when the writers finish, with their recorded start and end times.

## Observability

- Extraction trace events: `extraction_fanout.plan_submitted` (path, counts, the plan),
  `extraction_fanout.writer_completed` (assigned and staged counts per writer) and
  `extraction_fanout.finalize_attempt`. TraceReview groups them under `extraction_fanout`,
  which is absent when fan-out did not run.
- Langfuse shows the planner, check pass and each writer as separate agent runs in one
  trace.

## Compatibility

Saved agents and flows are unchanged and curators re-save nothing: the plan tool and the
writers exist only at run time. Package authors who add a builder data type mark its stage
tool with `builder_stage: true` next to `builder_run_state: true`, and mark a tool that
lists the record types the agent can stage with `builder_catalog: true`.
