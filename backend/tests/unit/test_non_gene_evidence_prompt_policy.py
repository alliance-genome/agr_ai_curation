from pathlib import Path

import pytest
import yaml

from src.lib.config.agent_sources import resolve_agent_config_sources


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


pytestmark = pytest.mark.skipif(
    not (_repo_root() / "packages").is_dir(),
    reason="requires full repository checkout (packages/ at repo root)",
)


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
