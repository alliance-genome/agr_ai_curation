# Runtime speed experiments

These controls support independent comparisons of package-tool startup,
validator completion, and fixed-field exports. They are disabled by default
until repeated measurements and correctness review establish which changes to
retain. Keep model, reasoning, input, prompt revision and source schema fixed.

| Experiment | Control | Candidate |
|---|---|---|
| Isolated package worker reuse | `PACKAGE_RUNNER_REUSE_WORKERS=false` | `true`, with `PACKAGE_RUNNER_WORKER_COUNT` sized for the host |
| Validator completion | `VALIDATOR_STOP_AFTER_ACCEPTED_FINALIZATION=false` | `true` |
| Validator concurrency | Existing `MAX_PARALLEL_VALIDATORS` | Compare explicit values with the other controls held constant |
| Fixed-field file exports | `FLOW_SELECTED_FIELDS_DIRECT_EXPORT=false` | `true` |

Worker reuse retains imports and initialized clients in isolated package Python
processes. Each worker handles one request at a time in a fresh Context; tool
factories are recreated for every request. The pool is bounded across package
environments. Package identity, environment fingerprint and inherited environment
changes prevent reuse of a mismatched worker. Restart the runtime after changing
package source code. Calls that time out, crash, exceed the response byte limit,
or return malformed protocol data discard the worker without replaying the
request. The timeout includes waiting for an available worker, after environment
preparation. Shutdown releases retained workers.

Async tool invocations still own separate event loops. A package that caches an
async client tied to one loop must be reviewed before enabling worker reuse;
retaining that client across calls would outlive its loop.

Accepted-finalizer completion uses the Agents SDK terminal-result callback.
Rejected finalizations still return feedback to the model for repair. The accepted
typed result becomes the SDK output, retaining output guardrails, usage and provider
cleanup. Both individual and batch validator runs support this control.

Direct exports apply only when a file node explicitly selects `export_execution_mode: direct` and has a `selected_fields`
projection plan. Runtime agent construction still checks access and resolves
configuration. The bound file finalizer validates the exact saved plan and source
fingerprints, creates the projection and persists the file through the existing
artifact/event path. Invalid plans or storage errors fail the step. Prompt-defined
exports continue through the formatter model. Empty selected-field exports retain
their headers (CSV/TSV) or empty array (JSON).

## Measurement requirements

Use repeated matched cases and record every attempt, including failures. Separate
cold startup from warm calls. Rotate case order and interleave or bracket candidate
blocks with baseline controls. Record host load, memory, model/tool calls, token
usage, cache usage and budget reservations alongside elapsed time. Ten repetitions
are a starting point, not sufficient evidence for a precise tail-latency claim.
Report medians, spread and uncertainty; extend inconclusive comparisons.

Compare correctness against source values and validation identities, not complete
model prose. A faster unresolved or failed result is not a successful optimization.
For file output, compare parsed cells/JSON values, column order, null handling,
nested values and zero-record behavior. Include real persistence and download
canaries before recommending deployment.

Measure each change separately. A combined worker/concurrency result is a separate
condition and must not be presented as the effect of concurrency alone. Background
work on the same host can overwhelm small improvements; retain its load evidence
and repeat controls before drawing conclusions.

Direct mode is separate from column selection. Existing nodes without a mode retain AI execution. The server flag is an availability gate: an explicitly direct node fails clearly when disabled. Workshop defaults are saved in immutable snapshots and copied only into new flow steps. Custom file formatter clones resolve their format from the authorized pinned template and required tools. No agent, group, or step prompts execute in direct mode; their text is preserved.
