# Full Backend Suite Blockers

## 2026-06-02: Full-suite order pollution remains after genuine clusters were fixed

### Status

- Latest VM full-suite command:
  `docker compose -f docker-compose.test.yml run --rm backend-tests bash -lc "cd /app/backend && python -m pytest tests/ -p no:cacheprovider -q --tb=no ..."`
- Latest result after this repair batch:
  `53 failed, 3998 passed, 147 skipped, 13 warnings in 500.51s`
- The remaining 53 failures are order-dependent pollution, not isolated test failures.

### Evidence

The following targeted reruns passed after the full-suite failure list was collected:

- `test_gene_expression_builder_tools.py` + `test_alliance_agr_curation_vocabulary_helpers.py`: `45 passed`
- `test_chat_stream_endpoint.py` + `test_chat_misc_endpoints.py`: `80 passed`
- Mixed unit batch covering custom agent service, Groq compatibility, validator dispatch/metadata, assembly callsite parity, literature reference, and streaming tool helpers: `166 passed, 1 skipped` after rebuilding the stale `backend-tests` image
- Evidence SSE/pipeline/durable integration batch: `29 passed`
- Remaining full-failure file batch:
  `test_agent_studio_workshop_refresh.py`, `test_session_service.py`, `test_config.py`,
  `test_project_agnostic_runtime_guardrails.py`, `test_assembly_callsite_parity.py`,
  `test_domain_envelope_repair_prompt_contract.py`,
  `test_phenotype_extractor_domain_envelope_contract.py`, and `test_routing_consistency.py`:
  `141 passed`

The current full-suite-only failing nodes are:

```text
FAILED tests/unit/api/test_agent_studio_workshop_refresh.py::test_prompt_sensitive_agent_workshop_chat_forces_refresh_before_review
FAILED tests/unit/lib/agent_studio/test_custom_agent_service.py::test_create_custom_agent_rejects_non_curator_visible_model
FAILED tests/unit/lib/agent_studio/test_custom_agent_service.py::test_normalize_custom_overlay_flags_mixed_exact_and_ambiguous_locked_copy
FAILED tests/unit/lib/agent_studio/test_custom_agent_service.py::test_normalize_custom_overlay_removes_exact_parent_layers
FAILED tests/unit/lib/curation_workspace/test_session_service.py::test_curator_corrected_workspace_candidate_validates_against_current_envelope
FAILED tests/unit/lib/curation_workspace/test_session_service.py::test_execute_submission_rejects_adapter_domain_envelope_readiness_blockers
FAILED tests/unit/lib/curation_workspace/test_session_service.py::test_execute_submission_rejects_domain_envelope_readiness_blockers
FAILED tests/unit/lib/curation_workspace/test_session_service.py::test_submission_export_blocks_missing_required_domain_field_without_allowed_override
FAILED tests/unit/lib/curation_workspace/test_session_service.py::test_submission_export_blocks_missing_required_host_context
FAILED tests/unit/lib/curation_workspace/test_session_service.py::test_submission_export_blocks_open_blocking_validation_findings
FAILED tests/unit/lib/curation_workspace/test_session_service.py::test_submission_export_blocks_unstable_definition_state
FAILED tests/unit/lib/curation_workspace/test_session_service.py::test_submission_export_blocks_waived_finding_with_alias_validation_metadata
FAILED tests/unit/lib/curation_workspace/test_session_service.py::test_submission_export_blocks_waived_finding_with_only_field_override_policy
FAILED tests/unit/lib/curation_workspace/test_session_service.py::test_submission_export_reports_stale_explicit_domain_envelope_revision_blocker
FAILED tests/unit/lib/curation_workspace/test_session_service.py::test_validate_candidate_reruns_after_waiver_advances_envelope_revision
FAILED tests/unit/lib/curation_workspace/test_session_service.py::test_workspace_validation_blocker_prevents_export_readiness
FAILED tests/unit/lib/curation_workspace/test_session_service.py::test_workspace_validation_dispatches_domain_pack_and_records_envelope_revision
FAILED tests/unit/lib/domain_packs/test_validation_registry_metadata.py::test_active_validator_agent_reference_validates_package_agent_and_dependency
FAILED tests/unit/lib/domain_packs/test_validator_dispatch.py::test_concrete_validator_envelope_projects_to_shared_result_contract
FAILED tests/unit/lib/domain_packs/test_validator_dispatch.py::test_dispatch_default_runner_uses_worker_thread_from_running_event_loop
FAILED tests/unit/lib/domain_packs/test_validator_dispatch.py::test_package_scoped_validator_agent_relaxes_domain_validator_output_schema
FAILED tests/unit/lib/domain_packs/test_validator_dispatch.py::test_package_scoped_validator_batch_agent_uses_batch_output_schema
FAILED tests/unit/lib/openai_agents/test_config.py::test_get_agent_config_env_override_beats_registry_model
FAILED tests/unit/lib/openai_agents/test_config.py::test_get_agent_config_prefers_registry_model_over_global_fallback
FAILED tests/unit/lib/openai_agents/test_streaming_tools_groq_compat.py::test_adapt_tools_for_groq_uses_package_declared_adapter
FAILED tests/unit/lib/openai_agents/test_streaming_tools_groq_compat.py::test_compute_adaptive_specialist_max_turns_honors_zero_minimum_entities
FAILED tests/unit/lib/openai_agents/test_streaming_tools_groq_compat.py::test_compute_adaptive_specialist_max_turns_requires_numeric_package_metadata
FAILED tests/unit/lib/openai_agents/test_streaming_tools_groq_compat.py::test_compute_adaptive_specialist_max_turns_scales_for_large_package_bulk_lists
FAILED tests/unit/lib/openai_agents/test_streaming_tools_groq_compat.py::test_required_tool_failure_message_for_missing_package_required_call
FAILED tests/unit/lib/openai_agents/test_streaming_tools_groq_compat.py::test_required_tool_failure_message_requires_package_declared_failure_message
FAILED tests/unit/lib/openai_agents/test_streaming_tools_groq_compat.py::test_tool_efficiency_instruction_requires_package_declared_text
FAILED tests/unit/lib/openai_agents/test_streaming_tools_helpers.py::test_chat_domain_envelope_dispatch_covers_launchable_active_validator_domains[allele]
FAILED tests/unit/lib/openai_agents/test_streaming_tools_helpers.py::test_chat_domain_envelope_dispatch_covers_launchable_active_validator_domains[disease]
FAILED tests/unit/lib/openai_agents/test_streaming_tools_helpers.py::test_chat_domain_envelope_dispatch_covers_launchable_active_validator_domains[gene-expression]
FAILED tests/unit/lib/openai_agents/test_streaming_tools_helpers.py::test_chat_domain_envelope_dispatch_covers_launchable_active_validator_domains[phenotype]
FAILED tests/unit/lib/openai_agents/tools/test_gene_expression_builder_tools.py::test_duplicate_finalize_conflicts_when_source_candidates_change
FAILED tests/unit/lib/openai_agents/tools/test_gene_expression_builder_tools.py::test_finalize_copies_resolver_provenance_from_ledger
FAILED tests/unit/lib/openai_agents/tools/test_gene_expression_builder_tools.py::test_finalize_preserves_multi_observation_source_candidate_identity
FAILED tests/unit/lib/openai_agents/tools/test_gene_expression_builder_tools.py::test_finalize_rejects_duplicate_candidate_ids_before_materialization
FAILED tests/unit/lib/openai_agents/tools/test_gene_expression_builder_tools.py::test_finalize_rejects_missing_evidence_records
FAILED tests/unit/lib/openai_agents/tools/test_gene_expression_builder_tools.py::test_finalize_rejects_null_relation_name
FAILED tests/unit/lib/openai_agents/tools/test_gene_expression_builder_tools.py::test_finalize_rejects_placeholder_pmid
FAILED tests/unit/lib/openai_agents/tools/test_gene_expression_builder_tools.py::test_finalize_returns_compact_builder_summary
FAILED tests/unit/lib/openai_agents/tools/test_gene_expression_builder_tools.py::test_patch_updates_reference_and_controlled_field_from_ledger
FAILED tests/unit/lib/openai_agents/tools/test_gene_expression_builder_tools.py::test_stage_gene_expression_observation_copies_resolver_provenance
FAILED tests/unit/lib/openai_agents/tools/test_gene_expression_builder_tools.py::test_stage_rejects_missing_resolver_provenance
FAILED tests/unit/lib/openai_agents/tools/test_gene_expression_builder_tools.py::test_stage_rejects_placeholder_reference
FAILED tests/unit/lib/packages/test_project_agnostic_runtime_guardrails.py::test_runtime_validation_accepts_synthetic_system_agent_without_alliance
FAILED tests/unit/lib/prompts/test_assembly_callsite_parity.py::test_catalog_preview_diagnostic_and_runtime_share_prompt_bundle
FAILED tests/unit/lib/prompts/test_assembly_callsite_parity.py::test_custom_agent_preview_treats_custom_prompt_as_overlay
FAILED tests/unit/test_domain_envelope_repair_prompt_contract.py::test_extractor_prompts_delegate_unresolved_state_to_validators
FAILED tests/unit/test_phenotype_extractor_domain_envelope_contract.py::test_phenotype_extractor_prompt_agent_and_group_rules_name_domain_contract
FAILED tests/unit/test_routing_consistency.py::TestRoutingConsistency::test_response_envelope_schemas_exist
```

### Root Cause

The remaining failures are caused by full-suite process pollution. Multiple test fixtures still delete and re-import
`main`/`src.*` to create a fresh DEV_MODE app. That changes module identity, cached config, package/agent registry state,
and schema class identity for later tests in the same pytest process. These later tests pass when run in isolation, but
fail after polluted imports/caches are left behind by earlier tests.

A previous attempt to remove the re-import pattern from the `test_files.py` and auth fixtures was reverted because it
regressed the full suite: those fixtures rely on a freshly imported DEV_MODE app, and there is no app factory/cache-reset
boundary that can safely provide that today.

### Recommendation

Do not patch these remaining assertions piecemeal. The safe fixes are structural:

- Preferred durable fix: introduce a FastAPI `create_app()`/test app factory plus explicit config and registry cache
  reset hooks, then refactor the re-import fixtures to use that boundary instead of deleting `sys.modules`.
- Low-risk CI unblocker: run the affected full-suite process with test isolation such as `pytest-forked` or split the
  polluted fixture groups into separate pytest processes.
- Avoid: more per-victim assertion rewrites unless a specific node fails in isolation too. Current evidence shows these
  53 are not isolated failures.

### Environment Note Resolved

The literature-reference dependency failure was not a code/test blocker after inspection. The VM test image was stale:
the worktree required `agr-curation-api-client==0.11.0`, while the image imported `0.9.0`. Rebuilding
`backend-tests` refreshed the image to `0.11.0`, and
`test_runtime_api_client_dependency_exposes_literature_helpers` then passed.
