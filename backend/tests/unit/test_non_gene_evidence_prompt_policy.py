import re
from pathlib import Path

import pytest
import yaml

from src.lib.config.agent_loader import load_agent_definitions, reset_cache
from src.lib.config.agent_sources import resolve_agent_config_sources
from src.lib.prompts import assembly
from src.schemas.models.base import ExclusionReasonCode


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_prompt_content(folder_name: str) -> str:
    source = next(
        source
        for source in resolve_agent_config_sources(_repo_root() / "packages")
        if source.folder_name == folder_name
    )
    prompt_path = source.prompt_yaml
    assert prompt_path is not None
    data = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))
    editable_content = str(data.get("content") or "")
    try:
        agents = load_agent_definitions(_repo_root() / "packages", force_reload=True)
        agent = next(agent for agent in agents.values() if agent.folder_name == folder_name)
        generated_content = assembly.build_agent_core_prompt(agent.agent_id).render()
    finally:
        reset_cache()
    return "\n\n".join([generated_content, editable_content])


def _extractor_prompt_sources():
    return sorted(
        [
            source
            for source in resolve_agent_config_sources(_repo_root() / "packages")
            if source.folder_name == "gene_expression" or source.folder_name.endswith("_extractor")
        ],
        key=lambda source: source.folder_name,
    )


def _listed_reason_codes(content: str) -> set[str]:
    codes: set[str] = set()
    collecting = False

    for line in content.splitlines():
        stripped = line.strip()
        if (
            stripped == "<exclusion_reason_codes>"
            or stripped == "# Exclusion reason codes"
            or stripped == "Exclude with canonical reason_code when applicable:"
        ):
            collecting = True
            continue

        if not collecting:
            continue

        if (
            stripped.startswith("</")
            or stripped.startswith("<")
            or (stripped.startswith("# ") and stripped != "# Exclusion reason codes")
        ):
            collecting = False
            continue

        match = re.match(r"-\s+`?(?P<code>[a-z_]+)`?(?:\s|$)", stripped)
        if match:
            codes.add(match.group("code"))

    return codes


@pytest.mark.parametrize(
    ("folder_name", "domain_specific_snippet"),
    [
        ("allele_extractor", "Strong allele evidence usually does one or more of the following:"),
        ("disease_extractor", "The disease is mentioned only as motivation, background, or population context"),
        ("chemical_extractor", "Vehicle/control mentions without a chemical-specific biological result"),
        ("phenotype_extractor", "A phenotype term appears only in a heading, keyword list, or background sentence"),
        ("gene_expression", "Rescue or ectopic overexpression statements where \"expression\" is only the experimental tool"),
    ],
)
def test_non_gene_extractor_prompts_include_record_evidence_domain_guidance(
    folder_name: str,
    domain_specific_snippet: str,
):
    content = _load_prompt_content(folder_name)

    assert "record_evidence" in content
    assert "Strong quote examples:" in content
    assert "Weak quote examples:" in content
    assert "curatable_objects[]" in content
    assert "`read_chunk.evidence_spans[].span_id` values" in content
    assert "`record_evidence` with `span_ids`" in content
    assert "active-run evidence workspace" in content
    assert "evidence_record_ids" in content
    assert re.search(
        r"Do not emit top-level legacy semantic lists:\s+`items\[\]`,\s+`annotations\[\]`,\s+"
        r"`genes\[\]`,\s+`alleles\[\]`,\s+`diseases\[\]`,\s+"
        r"`chemicals\[\]`,\s+(?:or\s+)?`phenotypes\[\]`",
        content,
    )
    assert domain_specific_snippet in content


@pytest.mark.parametrize(
    "source",
    _extractor_prompt_sources(),
    ids=lambda source: source.folder_name,
)
def test_extractor_prompt_reason_codes_match_schema_contract(source):
    prompt_path = source.prompt_yaml
    assert prompt_path is not None
    data = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))
    content = str(data.get("content") or "")

    prompt_reason_codes = _listed_reason_codes(content)

    schema_reason_codes = {reason_code.value for reason_code in ExclusionReasonCode}
    assert prompt_reason_codes
    assert prompt_reason_codes <= schema_reason_codes
