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
    # The metadata-framing cleanup reworded the ambiguity line off the impossible
    # `metadata.ambiguities[]` write (AlleleStageInput has no such param) onto the
    # builder-accurate "leave that field for the validator" framing; the curation
    # DECISION (uncertain same-gene candidates are not guessed) is preserved.
    assert "the validator rather than guessing" in content


def test_allele_extractor_prompt_declares_allele_domain_envelope_contract():
    content = _load_allele_extractor_prompt()

    # Builder-pattern contract: the agent stages observations and the backend
    # materializes the 4-object AllelePaperEvidenceAssociation graph.
    assert "Do not hand-author `curatable_objects[]`" in content
    assert "`AllelePaperEvidenceAssociation`" in content
    assert "`AlleleMention`" in content
    assert "`EvidenceQuote`" in content
    assert "stage_allele_observation" in content
    assert "finalize_allele_extraction" in content
    assert "BLOCKED write/export behavior" in content
    assert "AlleleExtractionResultEnvelope" in content


def test_allele_extractor_prompt_uses_validator_dispatch_for_unresolved_values():
    content = _load_allele_extractor_prompt()

    assert (
        "Active validator bindings declared by the allele domain pack are the "
        "authority for final normalized allele identity" in content
    )
    assert "the active allele validator owns final allele identity" in content
    assert "does not repair validator failures" in content
    assert "repair_mode" not in content
    assert "repair_notes" not in content
