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
    "backend/src/api/agent_studio_system_prompt.md",
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
    "config/agents/chat_output/prompt.yaml",
    "config/agents/curation_prep/prompt.yaml",
    "docs/curator/AGENT_STUDIO.md",
    "docs/curator/CURATION_FLOWS.md",
    "docs/curator/README.md",
    "docs/developer/README.md",
    "docs/developer/TEST_STRATEGY.md",
    "docs/developer/guides/DOMAIN_ENVELOPES.md",
    "docs/design/domain-pack-migration/18-validator-dispatch-contract.md",
    "frontend/src/content/changelog/entries/2026-05-11-v0.7.0.ts",
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
        "docs/developer/guides/SYMPHONY_FLOW_AND_OPTIMIZATION.md",
        "Linear workflow state names include Blocked outside validator capability metadata",
        re.compile(r"`Blocked`"),
    ),
    (
        "docs/curator/AGENT_STUDIO.md",
        "lookup attempt statuses may be blocked without being validator buckets",
        re.compile(r"ambiguous, not found, transient, blocked, or under development"),
    ),
)


def _read_repo_text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _validator_dispatch_cleanup_surface_paths() -> tuple[str, ...]:
    paths = set(VALIDATOR_DISPATCH_CLEANUP_SURFACE_PATHS)
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
    prompt = _read_repo_text("backend/src/api/agent_studio_system_prompt.md")

    assert "domain envelopes are the semantic source of truth" in prompt
    assert "call the relevant tools" in prompt
    assert "get_domain_envelope_state" in prompt
    assert "bounded validator request/result summaries" in prompt
    assert "materialization paths" in prompt
    assert "get_domain_pack_validation_plan" in prompt
    assert "get_export_submission_readiness" in prompt
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
    assert "get_prompt(agent_id=<validator agent id>)" in prompt
    assert "Extractor and validator responsibilities are deliberately separate" in prompt
    assert "First-pass extractors must not use broad database/entity lookup tools" in prompt
    assert "`agr_species_context_lookup` is the shared narrow context tool" in prompt
    assert "`get_domain_field_term_options` may provide controlled-vocabulary options" in prompt
    assert "helper output remains candidate guidance, not validator authority" in prompt
    assert "Validators receive `DomainValidationRequest` payloads" in prompt
    assert "Materialized/resolved fields belong to validator results" in prompt
    assert "Do not infer that an extractor called a validator directly" in prompt
    assert "Domain-envelope extractors" in prompt
    assert "gene_expression_extraction" in prompt
    assert "Gene-expression prompt and validation-plan inspection accepts both" in prompt
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


def test_agent_studio_system_prompt_canonical_and_packaged_copies_match():
    canonical_path = REPO_ROOT / "alliance_config" / "agent_studio_system_prompt.md"
    if not canonical_path.exists():
        # The backend unit-test image may include only /app/backend. Local and
        # full-repo runs still guard the canonical runtime copy.
        return

    canonical_prompt = canonical_path.read_text(encoding="utf-8")
    packaged_prompt = _read_repo_text("backend/src/api/agent_studio_system_prompt.md")

    assert canonical_prompt == packaged_prompt


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


def test_chat_output_prompts_match_and_preserve_domain_envelope_refs():
    config_prompt = _read_repo_text("config/agents/chat_output/prompt.yaml")
    package_prompt_paths = _package_agent_prompt_paths("chat_output")

    assert package_prompt_paths
    for package_prompt_path in package_prompt_paths:
        assert config_prompt == _read_repo_text(package_prompt_path)
    assert "domain_envelope.objects" in config_prompt
    assert "review rows" in config_prompt
    assert "lookup attempts" in config_prompt
    assert "export/submission blockers" in config_prompt
    assert "`lookup_attempts` as an audit trail" in config_prompt
    assert "`annotation_drafts`" in config_prompt
    assert "use envelope references as truth" in config_prompt
    assert "flow validator replacements/skips" in config_prompt
    assert "opt-outs" not in config_prompt


def test_non_opus_runtime_prompts_reject_stale_validator_dispatch_wording():
    surface_paths = (
        "config/agents/supervisor/prompt.yaml",
        "config/agents/chat_output/prompt.yaml",
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
