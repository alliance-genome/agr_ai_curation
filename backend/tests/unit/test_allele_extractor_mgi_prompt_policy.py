from pathlib import Path

import yaml

from src.lib.config.agent_sources import resolve_agent_config_sources


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_allele_extractor_mgi_group_rule() -> str:
    source = next(
        source
        for source in resolve_agent_config_sources(_repo_root() / "packages")
        if source.folder_name == "allele_extractor"
    )
    mgi_path = next(path for path in source.group_rule_files if path.stem == "mgi")
    data = yaml.safe_load(mgi_path.read_text(encoding="utf-8"))
    return str(data.get("content") or "")


def _load_allele_extractor_prompt() -> str:
    source = next(
        source
        for source in resolve_agent_config_sources(_repo_root() / "packages")
        if source.folder_name == "allele_extractor"
    )
    assert source.prompt_yaml is not None
    data = yaml.safe_load(source.prompt_yaml.read_text(encoding="utf-8"))
    return str(data.get("content") or "")


def test_allele_extractor_mgi_overlay_includes_lab_code_disambiguation_workflow():
    content = _load_allele_extractor_mgi_group_rule()

    assert "tm1.1Hko" in content
    assert "em1Cya" in content
    assert "em1Gpt" in content
    assert "Active allele" in content
    assert "paper-backed context that can help validation" in content
    assert "do not" in content
    assert "ambiguities[]" in content


def test_allele_extractor_prompt_declares_allele_domain_envelope_contract():
    content = _load_allele_extractor_prompt()

    assert "`curatable_objects[]` is the only semantic object list" in content
    assert "`AllelePaperEvidenceAssociation`" in content
    assert '`object_role: "curatable_unit"`' in content
    assert "`AlleleMention`" in content
    assert "`EvidenceQuote`" in content
    assert '`definition_state: "in_development"`' in content
    assert '`metadata.write_behavior.status: "blocked"`' in content
    assert "Do not emit any top-level helper list of retained alleles" in content
    assert "`CurationPrepCandidate`" in content


def test_allele_extractor_prompt_uses_validator_dispatch_for_unresolved_values():
    content = _load_allele_extractor_prompt()

    assert "# Unresolved validation" in content
    assert "Active validator bindings own final allele identity" in content
    assert "validator result fields and envelope validation findings" in content
    assert "repair_mode" not in content
    assert "repair_notes" not in content
