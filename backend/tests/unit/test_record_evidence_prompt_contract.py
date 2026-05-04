"""Regression checks for record_evidence wording across shipped prompts."""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
PROMPT_ROOTS = ["config", "packages", "alliance_agents"]

STALE_RECORD_EVIDENCE_PHRASES = [
    "performs fuzzy quote",
    "fuzzy quote matching",
    "matching against the stored chunk text",
    "Verify a claimed quote against a specific chunk",
]


def _record_evidence_prompt_files() -> list[Path]:
    prompt_files = []
    for root in PROMPT_ROOTS:
        prompt_files.extend((REPO_ROOT / root).rglob("prompt.yaml"))
    return sorted(
        path
        for path in prompt_files
        if "record_evidence" in _runtime_prompt_content(path)
    )


def _runtime_prompt_content(path: Path) -> str:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.relative_to(REPO_ROOT)} did not parse as YAML mapping"
    content = data.get("content")
    assert isinstance(content, str), f"{path.relative_to(REPO_ROOT)} has no runtime content field"
    return content


def _record_evidence_tool_policy_description(path: Path) -> str:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.relative_to(REPO_ROOT)} did not parse as YAML mapping"
    record_evidence = data.get("tool_policies", {}).get("record_evidence", {})
    assert isinstance(record_evidence, dict), f"{path.relative_to(REPO_ROOT)} has no record_evidence policy"
    description = record_evidence.get("description")
    assert isinstance(description, str), f"{path.relative_to(REPO_ROOT)} has no record_evidence description"
    return description


def test_record_evidence_prompt_contract_has_no_stale_fuzzy_source_matching_language():
    searched_prompts = _record_evidence_prompt_files()
    assert searched_prompts, "Expected at least one runtime prompt to mention record_evidence"

    stale_hits = []
    for path in searched_prompts:
        content = _runtime_prompt_content(path)
        content_lower = content.lower()
        for phrase in STALE_RECORD_EVIDENCE_PHRASES:
            if phrase.lower() in content_lower:
                stale_hits.append(f"{path.relative_to(REPO_ROOT)}: {phrase}")

    for path in [
        REPO_ROOT / "config/tool_policy_defaults.yaml",
        REPO_ROOT / "packages/core/config/tool_policy_defaults.yaml",
    ]:
        content_lower = _record_evidence_tool_policy_description(path).lower()
        for phrase in STALE_RECORD_EVIDENCE_PHRASES:
            if phrase.lower() in content_lower:
                stale_hits.append(f"{path.relative_to(REPO_ROOT)}: {phrase}")

    assert stale_hits == []


def test_record_evidence_runtime_prompts_state_exact_source_text_contract():
    required_fragments = [
        "verifies only exact",
        "contiguous source text copied from that chunk",
        "omitted, inserted",
        "changed, paraphrased, or normalized quote text returns `not_found`",
    ]

    missing = []
    for path in _record_evidence_prompt_files():
        content = " ".join(_runtime_prompt_content(path).lower().split())
        for fragment in required_fragments:
            if fragment.lower() not in content:
                missing.append(f"{path.relative_to(REPO_ROOT)}: {fragment}")

    assert missing == []
