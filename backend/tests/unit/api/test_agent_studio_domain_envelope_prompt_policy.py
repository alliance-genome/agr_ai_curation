"""Prompt policy checks for Agent Studio domain-envelope grounding."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]


def test_agent_studio_system_prompt_grounded_in_domain_envelope_tools():
    prompt = (
        REPO_ROOT / "backend/src/api/agent_studio_system_prompt.md"
    ).read_text(encoding="utf-8")

    assert "domain envelopes are the semantic source of truth" in prompt
    assert "call the relevant tools" in prompt
    assert "get_domain_envelope_state" in prompt
    assert "get_domain_pack_validation_plan" in prompt
    assert "get_export_submission_readiness" in prompt
    assert "`lookup_attempts` is an audit trail" in prompt
    assert "`normalized_payload`" in prompt
    assert "are not semantic truth for new domain-envelope runs" in prompt
    assert "Active default validators are the only validators scheduled automatically" in prompt
    assert "Under-development validator bindings remain explanatory metadata" in prompt
    assert "should not be asked to call validators directly" in prompt
    assert "Validator-agent inspection workflow" in prompt
    assert "get_prompt(agent_id=<validator agent id>)" in prompt


def test_opus_validation_surfaces_reject_stale_validator_dispatch_wording():
    surface_paths = [
        "backend/src/api/agent_studio_system_prompt.md",
        "backend/src/api/agent_studio_opus_tools.py",
        "backend/src/lib/agent_studio/domain_envelope_tools.py",
        "backend/src/lib/agent_studio/flow_tools.py",
        "backend/src/lib/agent_studio/prompt_builder.py",
        "backend/src/lib/agent_studio/diagnostic_tools/tool_definitions.py",
    ]
    stale_phrases = [
        "planned " + "validators",
        "blocked " + "validators",
        "planned or blocked " + "validators",
        "planned or blocked " + "metadata",
        "planned/blocked " + "metadata",
        "opt-out " + "reason",
        "requires an opt-out " + "reason",
        "whether a " + "reason is required",
        "export-blocking or explicitly " + "locked",
    ]

    for relative_path in surface_paths:
        text = (REPO_ROOT / relative_path).read_text(encoding="utf-8").lower()
        for phrase in stale_phrases:
            assert phrase not in text, f"{phrase!r} returned in {relative_path}"


def test_chat_output_prompts_match_and_preserve_domain_envelope_refs():
    config_prompt = (
        REPO_ROOT / "config/agents/chat_output/prompt.yaml"
    ).read_text(encoding="utf-8")
    package_prompt = (
        REPO_ROOT / "packages/alliance/agents/chat_output/prompt.yaml"
    ).read_text(encoding="utf-8")

    assert config_prompt == package_prompt
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
    surface_paths = [
        "config/agents/supervisor/prompt.yaml",
        "config/agents/chat_output/prompt.yaml",
        "packages/alliance/agents/chemical/prompt.yaml",
        "packages/alliance/agents/chat_output/prompt.yaml",
        "packages/alliance/agents/disease/prompt.yaml",
        "packages/alliance/agents/reference/prompt.yaml",
    ]
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
        text = (REPO_ROOT / relative_path).read_text(encoding="utf-8").lower()
        for phrase in stale_phrases:
            assert phrase not in text, f"{phrase!r} returned in {relative_path}"
