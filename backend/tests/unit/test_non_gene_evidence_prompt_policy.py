import re
from pathlib import Path

import pytest
import yaml

from src.lib.config.agent_sources import resolve_agent_config_sources
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
    return str(data.get("content") or "")


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
    assert "items[].evidence" in content
    assert domain_specific_snippet in content


def test_disease_extractor_prompt_reason_codes_match_schema_contract():
    content = _load_prompt_content("disease_extractor")
    exclusion_block = re.search(
        r"<exclusion_reason_codes>(?P<body>.*?)</exclusion_reason_codes>",
        content,
        flags=re.DOTALL,
    )
    assert exclusion_block is not None

    prompt_reason_codes = {
        match.group("code")
        for match in re.finditer(
            r"^\s*-\s+(?P<code>[a-z_]+)\s+",
            exclusion_block.group("body"),
            flags=re.MULTILINE,
        )
    }

    schema_reason_codes = {reason_code.value for reason_code in ExclusionReasonCode}
    assert prompt_reason_codes
    assert prompt_reason_codes <= schema_reason_codes
