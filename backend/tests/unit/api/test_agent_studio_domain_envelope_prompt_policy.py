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
    assert "repair attempts" in config_prompt
    assert "export/submission blockers" in config_prompt
    assert "`lookup_attempts` as an audit trail" in config_prompt
    assert "`annotation_drafts`" in config_prompt
    assert "use envelope references as truth" in config_prompt
