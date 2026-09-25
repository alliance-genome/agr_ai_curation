# Workshop guidance coverage: September hotfix

ALL-1297 / KANBAN-1854, reviewed against the integrated hotfix beginning at
`00f5305c3` (production base `3d34ed09b`). This is a coverage inventory, not a
production-deployment claim. The release record holds final commit, full gates,
paid-run evidence and independent review verdicts.

Curator-facing explanation: [Workshop results and outputs](../../curator/WORKSHOP_OUTPUT_GUIDE.md).
Detailed assistant guidance stays in searchable `studio_guide_topic` sections,
not the always-sent prompt. Core guidance is project-neutral; biological examples
and current Alliance domain limitations belong to the Alliance package.

## Feature-to-guidance inventory

| Scope | Curator-visible effect / limitation | Guidance and tool route | Acceptance coverage |
|---|---|---|---|
| ALL-1275 | Application-owned chat/file outputs; compact default rows; output branches, not passing model-authored rows onward | `flow_design`, `output_values_and_columns`; real source fields, complete projection schema, proposal then Apply/Save | Flow tools; formatter projection and value-display tests; no invented Studio formatter-preview tool |
| ALL-1277 | Scoped contract discovery, summary then detail; invalid field selectors do not return the registry | `bounded_results_and_contracts`; runtime contract tool versus Studio live catalog/domain plan | Runtime agent-contract tests; guide scope tests |
| ALL-1278 | Evidence, document, query and chat-history results are bounded; exact evidence remains accessible | `bounded_results_and_contracts`, existing evidence/trace topics; returned continuations and revision/hash identity | Bounded guide reads; existing document/evidence/result tests |
| ALL-1279 | Input ceilings can stop a model request before dispatch | `tool_discovery_and_limits`; explain returned measurement/limit, narrow request, no disabling guards | Existing request-measurement tests; guide limit scenario |
| ALL-1280 delivered phases | Hosted search loads rarely used extractor tools; Studio namespaces remain scoped | `tool_discovery_and_limits`; callable-tool discovery is distinct from authenticated resource catalog | Existing hosted tool/runtime authorization tests; guide discovery scenario. Broader specialist rollout is not claimed |
| ALL-1282 | Declared structured display, default layouts, one list item per named column or whole-list cell; scalar accepted as one item | `output_values_and_columns`; source refs/types/cardinality; split_list schema; static semantic checks reused by canonical authoring | New packaged/custom rejection matrix and source-fields → proposal tests; existing runtime split rendering/overflow tests |
| ALL-1283 / ALL-1299 | Paper wording, proposed identities and resolved identities are separate; no fallback columns; decisive validation outcomes differ from transient failures | `domain_envelopes`, `output_values_and_columns`, `domain_workflows`; inspect field state/finding/history, do not infer from a quote | Existing resolvable-state/override/display suites; new guide assertions |
| ALL-1300 | Expression values use shared identity rules; legacy missing wording may require re-extraction | `domain_workflows`; corrected expression help includes measured perturbations and distinguishes inactive reagent checks | Existing expression contract/materializer tests; corrected help assertions |
| ALL-1301 | Disease subtype/subject routing and phenotype shared value semantics | `domain_workflows`; corrected disease relations, phenotype subject/reference limitations | Existing subject routing/phenotype materializer tests; guide/help scenarios |
| ALL-1302 | GO and custom-profile identities follow the same state/override rules | `domain_workflows`, custom profile and validator topics; fixed local mappings are not identity lookups | Existing GO/profile conformance and override tests; helper-exception regression assertion |
| ALL-1303 | Gene/allele state and species identity rules; extractors cannot carry lookup tools | `domain_envelopes`, `tool_discovery_and_limits`; supported allele-only flow example; retained customizations | Existing inherited-tool/pinned-revision guard tests; new no-helper-exception assertion |
| ALL-1304 | Review shows paper wording versus validated values and supports deliberate curator overrides | `domain_workflows`; distinguish override from lookup confirmation and preserve history/protected fields | Existing review-grid/atomic identity override tests; curator guide |
| ALL-1284 | Stable prompt-cache identity; no changed scientific behavior or guaranteed speedup | `flow_design`, measurement guidance; no curator action or invented cache setting | Existing prompt cache tests; explicitly no configuration advice |
| ALL-1285 / ALL-1291 | Lean lookup candidate view; shared available-fields metadata once per response | `bounded_results_and_contracts`, `tool_discovery_and_limits`; compact metadata does not mean omitted biological answers | Existing lookup-response tests; exact-read guidance |
| ALL-1286 / ALL-1293 | Figure/hierarchy classifier uses short references and real body previews; retries distinguished from final failures | `tool_discovery_and_limits`, trace workflow; no claim that figure classification verifies evidence | Existing ingestion/classifier tests; source-evidence and retry guidance |
| ALL-1287 | Chat can summarize, filter and inspect retained results without re-extraction | `bounded_results_and_contracts`; Studio uses envelope/review/readiness tools, NOT direct inspect_results | Existing result-explorer tests; new callable-scope/withheld-value guide tests |
| ALL-1288 / ALL-1294 | Usage capture and explicit missing/partial/inconsistent measurement states | `bounded_results_and_contracts`; missing price is not free, estimates are not invoices | Existing usage/TraceReview tests; guide measurement assertion |
| ALL-1289 | Full phenotype-term list exported, not only primary/[0] label; term-local findings | `domain_workflows`, corrected phenotype help, full-list/split guidance | Existing multi-term/default-layout/display tests; guide/help assertions |
| ALL-1292 | Compact always-sent instructions and on-demand exact guide reads | New topics remain indexed/searchable, bounded and hash-pinned | Existing unchanged instruction-size ceilings plus new topic search/chunk tests |
| ALL-1295 | Custom agents can read their own saved contract | `bounded_results_and_contracts`; exact custom revision/profile/prompt_manifest; core prompt no longer directs ca_ IDs to built-in-only tools | Existing contract ownership tests; new guidance scope scenario |
| ALL-1298 | Per-item extractor rationale displayed read-only, not generated by formatter | `domain_envelopes`, `domain_workflows`, output and curator guides | Existing rationale/finalize/display tests; no claim saved older records gain rationale |
| Model migration and validator TLC | Supported catalog models/reasoning only; frozen flow pins can be stale; lookups belong to validators | `tool_discovery_and_limits`, `domain_workflows`; live catalog, exact pins, per-domain schedule | Existing unsupported model/reasoning/lookup guard tests; guide scenario |

## Tool sufficiency and the one authoring gap

`get_current_flow_projection_plan(view="source_fields")` already returns exact
authorized source refs, value types, array depth, requiredness and fingerprints.
`formatter_projection_plan` exposes the schema, and
`propose_flow_draft_update(update_step.projection_plan)` compiles a transient
candidate through canonical validation. No new projection authoring tool is needed.

The missing static split checks are now shared by canonical authoring and runtime:
JSON split refusal, field-ref requirement, mutually exclusive header choices,
numbered-template placeholder, blank/duplicate headers and configured column bounds.
Data-sized expansion and expanded-name collisions remain runtime checks; authoring
does not fabricate empty extraction rows or promise a successful future export.

Field display declarations are deliberately separate from saved source catalogs,
whose fingerprints identify layouts. Existing field/model inspection identifies
the subject; exact pack display declarations can be read through bounded source
inspection when needed. This change does not alter layout fingerprints or saved
agent/flow data. A previously saved invalid split plan will now be rejected earlier
and must be corrected explicitly, not silently rewritten.

## Representative acceptance and remaining release evidence

Deterministic tests exercise guide search → bounded complete reads for requests
about named columns, unresolved values, phenotype terms, expression scope, scoped
custom contracts, result inspection, hosted discovery and model/limit errors.
They test supplied guidance, not probabilistic model obedience.

The mocked Workshop authoring scenario uses real source discovery and canonical
proposal validation: inspect a pinned profile, choose its exact scalar field/ref
and fingerprint, propose numbered or explicit split names, retain execution mode,
then reject an invalid header template. The live draft is unchanged throughout;
approval remains pending for valid proposals. Packaged and custom paths both reject
invalid static split settings. Runtime split tests cover list/scalar values,
formatting, exact headers, lossless JSON and data-sized limits.

Before release, require independent review of this final diff, full backend and
frontend candidate gates, and deployed Workshop checks with representative curator
requests. A scripted test is not a paid-conversation transcript. Retain those
checks separately and do not mark the ticket delivered until the final release is
verified. Policy row-rule changes and the broader AGM/client work remain deferred;
this guidance must not imply they shipped.
