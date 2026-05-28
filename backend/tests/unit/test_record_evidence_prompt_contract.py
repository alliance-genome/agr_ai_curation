"""Regression checks for span-backed record_evidence wording."""

from pathlib import Path

import yaml

from src.lib.agent_studio import catalog_service
from src.lib.prompts import assembly


REPO_ROOT = Path(__file__).resolve().parents[3]
PILOT_PROMPT_PATH = REPO_ROOT / "packages/alliance/agents/gene_expression/prompt.yaml"

EXTRACTOR_PROMPT_PATHS = [
    REPO_ROOT / "packages/alliance/agents/allele_extractor/prompt.yaml",
    REPO_ROOT / "packages/alliance/agents/chemical_extractor/prompt.yaml",
    REPO_ROOT / "packages/alliance/agents/disease_extractor/prompt.yaml",
    REPO_ROOT / "packages/alliance/agents/gene_expression/prompt.yaml",
    REPO_ROOT / "packages/alliance/agents/gene_extractor/prompt.yaml",
    REPO_ROOT / "packages/alliance/agents/pdf/prompt.yaml",
    REPO_ROOT / "packages/alliance/agents/phenotype_extractor/prompt.yaml",
]

STALE_RECORD_EVIDENCE_PHRASES = [
    "claimed_quote",
    "verbatim or lightly trimmed",
    "performs fuzzy quote",
    "fuzzy quote matching",
    "matching against the stored chunk text",
    "Verify a claimed quote against a specific chunk",
    "exact contiguous source text copied from that chunk",
    "omitted, inserted, changed, paraphrased, or normalized quote text returns",
]


def _runtime_prompt_content(path: Path) -> str:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.relative_to(REPO_ROOT)} did not parse as YAML mapping"
    content = data.get("content")
    assert isinstance(content, str), f"{path.relative_to(REPO_ROOT)} has no runtime content field"
    return content


def _tool_policy_description(path: Path, tool_id: str) -> str:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.relative_to(REPO_ROOT)} did not parse as YAML mapping"
    policy = data.get("tool_policies", {}).get(tool_id, {})
    assert isinstance(policy, dict), f"{path.relative_to(REPO_ROOT)} has no {tool_id} policy"
    description = policy.get("description")
    assert isinstance(description, str), f"{path.relative_to(REPO_ROOT)} has no {tool_id} description"
    return description


def _effective_prompt_content(prompt_path: Path) -> str:
    agent_yaml = prompt_path.with_name("agent.yaml")
    data = yaml.safe_load(agent_yaml.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{agent_yaml.relative_to(REPO_ROOT)} did not parse as YAML mapping"
    agent_id = str(data["agent_id"])
    return "\n\n".join(
        [
            assembly.build_agent_core_prompt(agent_id).render(),
            _runtime_prompt_content(prompt_path),
        ]
    )


def _pilot_effective_prompt_content() -> str:
    return _effective_prompt_content(PILOT_PROMPT_PATH)


def _assert_no_stale_phrases(label: str, content: str, stale_hits: list[str]) -> None:
    content_lower = content.lower()
    for phrase in STALE_RECORD_EVIDENCE_PHRASES:
        if phrase.lower() in content_lower:
            stale_hits.append(f"{label}: {phrase}")


def test_gene_expression_prompt_contract_has_no_legacy_quote_recording_language():
    content = _pilot_effective_prompt_content()
    stale_hits: list[str] = []
    _assert_no_stale_phrases("gene_expression effective prompt", content, stale_hits)

    assert stale_hits == []


def test_extractor_prompts_have_no_legacy_quote_recording_language():
    stale_hits: list[str] = []
    for path in EXTRACTOR_PROMPT_PATHS:
        _assert_no_stale_phrases(
            f"{path.relative_to(REPO_ROOT)} effective prompt",
            _effective_prompt_content(path),
            stale_hits,
        )

    assert stale_hits == []


def test_extractor_prompts_state_span_evidence_workflow():
    missing: list[str] = []
    for path in EXTRACTOR_PROMPT_PATHS:
        content = " ".join(_effective_prompt_content(path).lower().split())
        label = str(path.relative_to(REPO_ROOT))
        for fragment in [
            "read_chunk.evidence_spans[].span_id",
            "record_evidence(span_ids=[...])",
            "truly disjoint evidence",
        ]:
            if fragment.lower() not in content:
                missing.append(f"{label}: {fragment}")

    assert missing == []


def test_gene_expression_prompt_contract_states_span_workspace_workflow():
    required_fragments = [
        "read_chunk.evidence_spans[].span_id",
        "record_evidence(span_ids=[...])",
        "list_recorded_evidence",
        "get_recorded_evidence",
        "attach_evidence_to_object",
        "detach_evidence_from_object",
        "discard_recorded_evidence",
        "update_recorded_evidence_metadata",
        "source quote and provenance fields are immutable",
        "Multiple `span_ids` in one `record_evidence` call produce one evidence record",
    ]

    missing = []
    content = " ".join(_pilot_effective_prompt_content().lower().split())
    for fragment in required_fragments:
        if fragment.lower() not in content:
            missing.append(fragment)

    assert missing == []


def test_record_evidence_tool_policy_surfaces_are_span_only():
    stale_hits: list[str] = []
    for path in [
        REPO_ROOT / "config/tool_policy_defaults.yaml",
        REPO_ROOT / "packages/core/config/tool_policy_defaults.yaml",
    ]:
        for tool_id in [
            "search_document",
            "read_chunk",
            "read_section",
            "read_subsection",
            "record_evidence",
            "list_recorded_evidence",
            "get_recorded_evidence",
            "attach_evidence_to_object",
            "detach_evidence_from_object",
            "discard_recorded_evidence",
            "update_recorded_evidence_metadata",
        ]:
            content = _tool_policy_description(path, tool_id)
            _assert_no_stale_phrases(
                f"{path.relative_to(REPO_ROOT)}:{tool_id}",
                content,
                stale_hits,
            )

    assert stale_hits == []


def test_agent_studio_catalog_tool_inventory_exposes_span_workspace_contract():
    required_by_tool = {
        "search_document": ["Discovery", "read_chunk"],
        "read_chunk": ["evidence_spans[].span_id"],
        "read_section": ["Survey", "read_chunk"],
        "read_subsection": ["Survey", "read_chunk"],
        "record_evidence": ["span_ids", "verified_quote"],
        "list_recorded_evidence": ["Review", "active-run evidence"],
        "get_recorded_evidence": ["detailed review"],
        "attach_evidence_to_object": ["intended curatable object"],
        "detach_evidence_from_object": ["wrong curatable object"],
        "discard_recorded_evidence": ["wrong or weak"],
        "update_recorded_evidence_metadata": ["editable"],
    }

    stale_hits: list[str] = []
    missing: list[str] = []
    for tool_id, fragments in required_by_tool.items():
        entry = catalog_service.TOOL_REGISTRY[tool_id]
        content = " ".join(
            [
                str(entry.get("description") or ""),
                str(entry.get("documentation", {}).get("summary") or ""),
            ]
        )
        _assert_no_stale_phrases(f"Agent Studio catalog:{tool_id}", content, stale_hits)
        normalized = content.lower()
        for fragment in fragments:
            if fragment.lower() not in normalized:
                missing.append(f"{tool_id}: {fragment}")

    assert stale_hits == []
    assert missing == []
