"""Prompt policy checks for Agent Studio domain-envelope grounding."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]

VALIDATOR_DISPATCH_CLEANUP_SURFACE_PATHS = (
    # Backend/domain-pack metadata and dispatch contract surfaces.
    "backend/src/schemas/domain_pack_metadata.py",
    "backend/src/schemas/flows.py",
    "backend/src/api/agent_studio.py",
    "backend/src/api/agent_studio_opus_tools.py",
    "backend/src/lib/agent_studio/domain_envelope_metadata.py",
    "backend/src/lib/agent_studio/domain_envelope_tools.py",
    "backend/src/lib/agent_studio/flow_tools.py",
    "backend/src/lib/agent_studio/prompt_builder.py",
    "backend/src/lib/agent_studio/diagnostic_tools/tool_definitions.py",
    "backend/src/lib/domain_packs/validation_registry.py",
    "backend/src/lib/domain_packs/validator_dispatch.py",
    "backend/src/lib/flows/validation_attachments.py",
    # Flow Builder/API/UI contract surfaces.
    "frontend/src/components/AgentStudio/DomainEnvelopeMetadataPanel.tsx",
    "frontend/src/components/AgentStudio/FlowBuilder/FlowBuilder.tsx",
    "frontend/src/components/AgentStudio/FlowBuilder/FlowNode.tsx",
    "frontend/src/components/AgentStudio/FlowBuilder/NodeEditor.tsx",
    "frontend/src/components/AgentStudio/FlowBuilder/types.ts",
    "frontend/src/features/curation/contracts.ts",
    "frontend/src/features/curation/types.ts",
    "frontend/src/features/curation/unavailableValidatorCapabilities.ts",
    "frontend/src/services/agentStudioService.ts",
    # Curator/non-Opus prompts, user-facing changelog, and design docs.
    "config/agents/supervisor/prompt.yaml",
    "docs/curator/AGENT_STUDIO.md",
    "docs/curator/CURATION_FLOWS.md",
    "docs/curator/README.md",
    "docs/developer/README.md",
    "docs/developer/TEST_STRATEGY.md",
    "docs/developer/guides/DOMAIN_ENVELOPES.md",
    "docs/design/domain-pack-migration/18-validator-dispatch-contract.md",
    "frontend/src/content/changelog/entries/2026-06-09-v0.7.0.ts",
)

VALIDATOR_DISPATCH_CLEANUP_SURFACE_GLOBS = (
    "packages/*/agents/*/prompt.yaml",
    "packages/*/domain_packs/*/domain_pack.yaml",
)

FORBIDDEN_VALIDATOR_DISPATCH_CLEANUP_PATTERNS = {
    "legacy planned validator bucket wording": re.compile(r"\bplanned validators?\b"),
    "legacy blocked validator bucket wording": re.compile(r"\bblocked validators?\b"),
    "legacy mixed availability wording": re.compile(
        r"\bplanned (?:or|/) blocked validators?\b"
    ),
    "legacy planned/blocked metadata wording": re.compile(
        r"\bplanned(?: or |/)blocked metadata\b"
    ),
    "legacy validator_state planned value": re.compile(
        r"\bvalidator_state\s*:\s*planned\b"
    ),
    "legacy validator_state blocked value": re.compile(
        r"\bvalidator_state\s*:\s*blocked\b"
    ),
    "legacy opt-out reason field": re.compile(r"\bopt_out_reason\b"),
    "legacy opt-out reason wording": re.compile(r"\bopt-out reasons?\b"),
    "legacy opt-out reason requirement": re.compile(
        r"\brequires an opt-out reason\b"
    ),
    "legacy reason-required wording": re.compile(r"\bwhether a reason is required\b"),
    "legacy export-locking wording": re.compile(
        r"\bexport-blocking or explicitly locked\b"
    ),
    "validation supervisor remnants": re.compile(
        r"\b(?:run_validation_supervisor|validation_supervisor|validation supervisor)\b"
    ),
    "repair mode/action/hint remnants": re.compile(
        r"\b(?:repair_action|repair_mode|repair_hints)\b"
    ),
}

ALLOWED_VALIDATOR_DISPATCH_CLEANUP_CONTEXTS = (
    (
        "backend/src/lib/domain_packs/validation_registry.py",
        "negative loader checks reject legacy planned/blocked buckets",
        re.compile(r'legacy_state_keys = \{"planned", "blocked"\}'),
    ),
    (
        "backend/src/schemas/curation_workspace.py",
        "curator-facing validation finding summaries still use planned/blocked statuses",
        re.compile(r'PLANNED = "planned".*BLOCKED = "blocked"', re.DOTALL),
    ),
    (
        "frontend/src/features/curation/contracts.ts",
        "frontend validation finding summary contract mirrors planned/blocked statuses",
        re.compile(r"'planned',\s*'blocked'", re.DOTALL),
    ),
    (
        "frontend/src/features/curation/submission/SubmissionPreviewDialog.tsx",
        "export/submission readiness blockers are legitimate blocked states",
        re.compile(r"blockedCount|Blocked"),
    ),
    (
        "docs/curator/AGENT_STUDIO.md",
        "lookup attempt statuses may be blocked without being validator buckets",
        re.compile(r"ambiguous, not found, transient, blocked, or under development"),
    ),
)


def _read_repo_text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _display_prompt_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _read_runtime_agent_studio_system_prompt() -> tuple[str, str]:
    from src.lib.packages import load_installed_agent_studio_prompt

    loaded = load_installed_agent_studio_prompt(
        REPO_ROOT / "packages",
        overrides_path=(
            REPO_ROOT
            / "packages"
            / "alliance"
            / "config"
            / "runtime_overrides.yaml"
        ),
    )
    return _display_prompt_path(loaded.source.prompt_path), loaded.content


def _validator_dispatch_cleanup_surface_paths() -> tuple[str, ...]:
    paths: set[str] = set(VALIDATOR_DISPATCH_CLEANUP_SURFACE_PATHS)
    for pattern in VALIDATOR_DISPATCH_CLEANUP_SURFACE_GLOBS:
        paths.update(
            str(path.relative_to(REPO_ROOT))
            for path in (REPO_ROOT).glob(pattern)
            if path.is_file()
        )
    return tuple(sorted(paths))


def _package_agent_prompt_paths(agent_name: str | None = None) -> tuple[str, ...]:
    prompt_glob = (
        f"packages/*/agents/{agent_name}/prompt.yaml"
        if agent_name
        else "packages/*/agents/*/prompt.yaml"
    )
    return tuple(
        sorted(
            str(path.relative_to(REPO_ROOT))
            for path in REPO_ROOT.glob(prompt_glob)
            if path.is_file()
        )
    )


def test_agent_studio_system_prompt_grounded_in_domain_envelope_tools():
    source_path, prompt = _read_runtime_agent_studio_system_prompt()

    assert "domain envelopes are the semantic source of truth" in prompt
    assert "domain_envelope.extracted_objects" in prompt, source_path
    assert "domain_envelope.objects" not in prompt, source_path
    assert "call the relevant tools" in prompt
    assert "get_domain_envelope_state" in prompt
    assert (
        "get_domain_envelope_state(envelope_id, revision, section, object_id, "
        "field_path, query, include_object_payload, limit, cursor)"
    ) in prompt, source_path
    assert "bounded validator request/result summaries" in prompt
    assert "materialization paths" in prompt
    assert "get_domain_pack_validation_plan" in prompt
    assert "get_export_submission_readiness" in prompt
    assert (
        "get_domain_envelope_review_rows(envelope_id, revision, section, object_id, "
        "query, limit, cursor)"
    ) in prompt, source_path
    assert (
        "get_export_submission_readiness(session_id, candidate_ids, "
        "expected_envelope_revisions, mode, section, candidate_id, envelope_id, "
        "object_id, field_path, code, query, limit, cursor)"
    ) in prompt, source_path
    assert "Omit `section` for a compact runtime summary" in prompt, source_path
    assert "follow `next_request` until complete" in prompt, source_path
    assert "history_limit" not in prompt, source_path
    assert "get_tool_inventory" in prompt
    assert "get_tool_details" in prompt
    assert "`lookup_attempts` is an audit trail" in prompt
    assert "`normalized_payload`" in prompt
    assert "are not semantic truth for new domain-envelope runs" in prompt
    assert "Active default validators are the only validators scheduled automatically" in prompt
    assert "Under-development validator bindings remain explanatory metadata" in prompt
    assert "should not be asked to call validators directly" in prompt
    assert "Validator-agent inspection workflow" in prompt
    assert "read `validator_bindings[].validator_agent.agent_id`" in prompt
    assert "validation_attachments[].validator_agent_id" in prompt
    assert 'get_prompt(agent_id, group_id, view="summary")' in prompt
    assert "Extractor and validator responsibilities are deliberately separate" in prompt
    assert "First-pass extractors must not use broad database/entity lookup tools" in prompt
    assert "`agr_species_context_lookup` is the shared narrow context tool" in prompt
    assert "Domain-pack-declared extractor helper tools may provide" in prompt
    assert "controlled-vocabulary options or slot-routing hints" in prompt
    assert "helper output remains candidate guidance, not validator authority" in prompt
    assert "Validators receive `DomainValidationRequest` payloads" in prompt
    assert "Materialized/resolved fields belong to validator results" in prompt
    assert "Do not infer that an extractor called a validator directly" in prompt
    assert "Domain-envelope extractors" in prompt
    assert "gene_expression_extraction" in prompt
    assert "`gene_expression` is the flow/prompt alias" in prompt
    assert "Validator/Resolver Agents" in prompt
    assert "phenotype_extractor" in prompt
    assert "controlled_vocabulary_validation" in prompt
    assert "data_provider_validation" in prompt
    assert "reference_validation" in prompt
    assert "experimental_condition_validation" in prompt
    assert "tools deliberately unavailable" in prompt
    assert "what fields it proposes or preserves as hints" in prompt
    assert "what fields it materializes or validates authoritatively" in prompt
    assert "what a specialist, extractor, or validator can do" in prompt


def test_agent_studio_system_prompt_uses_bounded_trace_review_contracts():
    source_path, prompt = _read_runtime_agent_studio_system_prompt()

    assert "get_trace_reconstruction(trace_id, section, limit, offset)" in prompt, source_path
    assert "get_trace_payloads(trace_id, sort, section, limit, offset)" in prompt, source_path
    assert "get_tool_calls_summary(trace_id, page, page_size)" in prompt, source_path
    assert "get_trace_conversation(trace_id, field, start, max_chars)" in prompt, source_path
    assert "get_tool_call_detail(trace_id, call_id, field, start, max_chars)" in prompt, source_path
    assert "Follow `next_call` until `complete=true`" in prompt, source_path
    assert "include_payloads" not in prompt, source_path
    assert "include_values" not in prompt, source_path


def test_installed_agent_studio_prompts_require_targeted_flow_verification():
    prompt_paths = (
        "packages/core/config/agent_studio_system_prompt.md",
        "packages/alliance/config/agent_studio_system_prompt.md",
    )

    for relative_path in prompt_paths:
        prompt = _read_repo_text(relative_path)
        assert "Call `get_current_flow()` first" in prompt, relative_path
        assert 'get_available_agents(category="Output")' in prompt, relative_path
        assert 'get_prompt(agent_id, group_id, view="summary")' in prompt, relative_path
        assert 'view="effective_prompt"' in prompt, relative_path
        assert "`compacted_tool_result`" in prompt, relative_path
        assert "every present `custom_instructions`" in prompt, relative_path
        assert "returned `next_call` until `complete=true`" in prompt, relative_path
        assert "`truncated=false` and no `next_cursor` remains" in prompt, relative_path
        assert (
            "`next_cursor` until `complete=true` for every required field" not in prompt
        ), relative_path
        assert "`scheduled_validators`" in prompt, relative_path
        assert "method/PDF-level `get_tool_details(tool_id, agent_id)`" in prompt, relative_path
        assert "Output agents are attachment branches with ordered" in prompt, relative_path
        assert "duplicate `output_key` as HIGH" in prompt, relative_path
        assert (
            "get_domain_pack_validation_plan(agent_id, domain_pack_id, section, "
            "object_type, field_path, validator_id, binding_id, state, query, limit, cursor)"
        ) in prompt, relative_path


def test_alliance_prompt_documents_catalog_and_tool_continuation_contracts():
    prompt = _read_repo_text("packages/alliance/config/agent_studio_system_prompt.md")

    assert (
        "get_flow_templates(template_query, query, category, section, "
        "template_cursor, cursor)"
    ) in prompt
    assert "call `validate_flow` before\n`create_flow`" in prompt
    assert (
        "get_tool_inventory(agent_id, category, include_method_tools, query, "
        "limit, cursor)"
    ) in prompt
    assert "get_tool_details(tool_id, agent_id, section, cursor, max_chars)" in prompt


def test_agent_studio_system_prompt_grounded_in_pdf_evidence_span_tools():
    _, prompt = _read_runtime_agent_studio_system_prompt()

    assert "PDF evidence is span-backed" in prompt
    assert "Do not tell curators or prompt authors that extraction agents should invent" in prompt
    assert "`search_document` for candidate chunks" in prompt
    assert "`read_chunk` for exact chunk text plus deterministic `evidence_spans[].span_id`" in prompt
    assert "`record_evidence(span_ids=[...])`" in prompt
    assert "`search_document.search_mode` supports `auto`, `hybrid`, `lexical`, and `hybrid_lexical_first`" in prompt
    assert "Prefer lexical-heavy modes for exact biomedical symbols" in prompt
    assert "Active-run evidence workspace tools" in prompt
    assert "Do not recommend fuzzy quote repair" in prompt


def test_agent_studio_system_prompt_is_owned_by_selected_alliance_package():
    from src.api import agent_studio as api_module

    canonical_path = (
        REPO_ROOT
        / "packages"
        / "alliance"
        / "config"
        / "agent_studio_system_prompt.md"
    )
    backend_core_path = REPO_ROOT / "backend/src/api/agent_studio_system_prompt.md"

    assert canonical_path.exists()
    assert not backend_core_path.exists()
    assert not hasattr(api_module, "AGENT_STUDIO_SYSTEM_PROMPT_TEMPLATE_CANDIDATES")


def test_validator_dispatch_cleanup_guardrail_rejects_stale_active_surface_terms():
    """Guard old validator-dispatch terms without banning real blocked/planned states."""

    violations: list[str] = []
    for relative_path in _validator_dispatch_cleanup_surface_paths():
        text = _read_repo_text(relative_path).lower()
        for reason, pattern in FORBIDDEN_VALIDATOR_DISPATCH_CLEANUP_PATTERNS.items():
            match = pattern.search(text)
            if match:
                violations.append(
                    f"{relative_path}: {reason}: {match.group(0)!r}"
                )

    assert violations == []


def test_validator_dispatch_cleanup_allowlist_documents_legitimate_contexts():
    missing_allowlist_entries = []
    for relative_path, reason, pattern in ALLOWED_VALIDATOR_DISPATCH_CLEANUP_CONTEXTS:
        if not pattern.search(_read_repo_text(relative_path)):
            missing_allowlist_entries.append(f"{relative_path}: {reason}")

    assert missing_allowlist_entries == []


def test_package_chat_output_prompt_preserves_domain_envelope_refs():
    package_prompt_paths = _package_agent_prompt_paths("chat_output")

    assert package_prompt_paths
    package_prompt = _read_repo_text(package_prompt_paths[0])
    assert "domain_envelope.extracted_objects" in package_prompt
    assert "review rows" in package_prompt
    assert "lookup attempts" in package_prompt
    assert "export/submission blockers" in package_prompt
    assert "`lookup_attempts` as an audit trail" in package_prompt
    assert "`annotation_drafts`" in package_prompt
    assert "use envelope references as truth" in package_prompt
    assert "flow validator replacements/skips" in package_prompt
    assert "opt-outs" not in package_prompt


def test_non_opus_runtime_prompts_reject_stale_validator_dispatch_wording():
    surface_paths = (
        "config/agents/supervisor/prompt.yaml",
        *_package_agent_prompt_paths(),
    )
    stale_phrases = [
        "planned " + "validators",
        "blocked " + "validators",
        "planned or blocked " + "validators",
        "opt-out " + "reason",
        "requires an opt-out " + "reason",
        "blocked_validator",
        "mark_under_development",
    ]

    for relative_path in surface_paths:
        text = _read_repo_text(relative_path).lower()
        for phrase in stale_phrases:
            assert phrase not in text, f"{phrase!r} returned in {relative_path}"
