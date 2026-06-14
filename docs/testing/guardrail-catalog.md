# Guardrail Catalog

This catalog lists invariant, scan, and smoke guards that protect release-critical
behavior from silent deletion or drift. Keep each row machine-parseable: one
guard per row, one repo-relative path in the `Test module / guard file` column,
and no Markdown table nesting.

When adding a new invariant guard, add it here in the same change as the test.

| Guard ID | Guard name | Protects | Trace | Test module / guard file |
|---|---|---|---|---|
| 0.7.x | `normalize_reasoning_effort` normalization | Invalid reasoning-effort values are dropped before model settings are built, so provider-specific settings cannot crash the runtime. | 0.7.x release hardening | `backend/tests/unit/lib/openai_agents/test_config.py` |
| 0.7.x | TSV canonical-only export | Curation TSV rows are generated only from canonical object rows, not prose answers, legacy semantic lists, artifact summaries, or raw step payload guesses. | 0.7.x release hardening | `backend/tests/unit/lib/flows/test_output_projection.py` |
| 0.7.x | Alembic single-head migration graph | The migration graph has exactly one head so deploys do not encounter ambiguous upgrade targets. | 0.7.x release hardening | `backend/tests/unit/test_alembic_migration_graph.py` |
| 0.7.x | PDF.js `toHex` polyfill before worker load | Browser compatibility polyfills load before the PDF.js worker wrapper so older browser runtimes can still initialize the viewer. | 0.7.x release hardening | `frontend/src/components/pdfViewer/pdfJsCompatibility.test.ts` |
| 0.7.x | Validator `max_turns` pinning | Package-scoped validator agents and validator batches receive explicit turn ceilings derived from the configured tool-call budget. | 0.7.x release hardening | `backend/tests/unit/lib/domain_packs/test_validator_dispatch.py` |
| 0.7.x | Supervisor call ledger | Repeated supervisor specialist calls are deduplicated or replayed with clear guidance while enforcing per-specialist and total call budgets. | 0.7.x release hardening | `backend/tests/unit/lib/openai_agents/agents/test_supervisor_call_ledger.py` |
| 0.7.x | Layer-2 forced-tool finalization | Layer-2 specialist runs force the expected finalization tool and end only after accepted finalization, with a documented kill switch. | 0.7.x release hardening | `backend/tests/unit/lib/openai_agents/test_streaming_tools_layer2_finalization.py` |
| G1 | Agent envelope-output vs finalize-tool invariant | Shipped active agents cannot combine direct output schemas with builder finalization tools, and extractor/materializer agents must expose the expected finalize tool. | ALL-599 / wave-74 | `backend/tests/unit/lib/agent_studio/test_agent_finalize_tool_invariant.py` |
| G2 | Runner `max_turns` scan and budget defaults | Every backend `Runner` call pins `max_turns`, and supervisor, single-shot, and validator finalization turn ceilings stay positive and documented. | ALL-600 / wave-74 | `backend/tests/unit/lib/openai_agents/test_runner_max_turns_invariant.py` |
| G3 | Projection planner full turn budget forwarding | The output projection planner forwards the configured `get_max_turns()` value and does not clamp back to a stale literal. | ALL-601 / wave-74 | `backend/tests/unit/lib/flows/test_tsv_formatter_flow_export.py` |
| G4 | Supervisor routing map specialist-only invariant | Supervisor prompt routing references only registered specialist tools, and pipeline validators remain non-routable through the supervisor map. | ALL-602 / wave-74 | `backend/tests/unit/lib/config/test_supervisor_routing_invariants.py` |
| G8 | Single palette-visible auto-push curation terminal | `curation_prep` stays hidden from the Flow Builder palette, and only `curation_handoff` is palette-visible as the auto-push curation terminal. | ALL-603 / wave-74 | `backend/tests/unit/lib/config/test_curation_palette_invariants.py` |
| S6 | `openai-agents` SDK pin drift smoke | Smoke and CI checks fail when the installed `openai-agents` package drifts from the backend lockfile pin. | ALL-604 / wave-74 | `backend/tests/unit/scripts/test_dev_release_smoke.py` |
| S7 | `openai-agents` SDK upgrade smoke evidence gate | PRs that change the `openai-agents` pin fail the Agent PR Gate unless the PR body records a passing `dev_release_smoke.py` evidence line. | ALL-595 / KANBAN-1377 | `scripts/testing/check_openai_agents_upgrade_gate.sh` |
| I4 | Allele fuzzy DB lookup integration | The Alliance allele fuzzy lookup uses real PostgreSQL trigram behavior, taxon filtering, synonym matching, score thresholds, and transaction-local threshold isolation. | ALL-605 / wave-74 | `backend/tests/integration/alliance/test_allele_fuzzy_db_lookup.py` |
| U2 | Experimental-condition status-only batch envelope | The experimental-condition batch validation envelope accepts per-result `status` without reintroducing legacy `condition_status`. | ALL-606 / wave-74 | `backend/tests/unit/lib/config/test_experimental_condition_validation_agent.py` |
| G10 | Guardrail catalog enumeration | Every catalogued guard path remains present on disk, making guard deletion visible as a cheap structural unit-test failure. | ALL-607 / wave-75 | `backend/tests/unit/test_guardrail_catalog.py` |
