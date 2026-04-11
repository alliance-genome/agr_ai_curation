from pathlib import Path

import pytest
import yaml

from src.lib.config.agent_sources import resolve_agent_config_sources


def _repo_root() -> Path:
    # backend/tests/unit/<this_file>.py -> repo root is parents[3]
    return Path(__file__).resolve().parents[3]


pytestmark = pytest.mark.skipif(
    not (_repo_root() / "packages").is_dir(),
    reason="requires full repository checkout (packages/ at repo root)",
)


def _load_gene_expression_source():
    return next(
        source
        for source in resolve_agent_config_sources(_repo_root() / "packages")
        if source.folder_name == "gene_expression"
    )


def test_gene_expression_prompt_includes_daniela_policy_gates():
    prompt_path = _load_gene_expression_source().prompt_yaml
    assert prompt_path is not None
    data = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))
    content = str(data.get("content") or "")

    assert "Return JSON only, matching GeneExpressionEnvelope." in content
    assert "previously_reported" in content
    assert "non_experimental_claim" in content
    assert "marker_only_visualization" in content
    assert "promoter_driven_marker_localization" in content
    assert "mutant_background_only" in content
    assert "structural_label_or_fusion_only" in content
    assert "Capture reagent genotype strings exactly as written" in content
    assert "midbrain-hindbrain boundary at 18 hpf" in content
    assert "Tg(kdrl:EGFP)" in content


def test_gene_expression_wb_overlay_includes_wormbase_examples():
    wb_path = next(
        path
        for path in _load_gene_expression_source().group_rule_files
        if path.stem == "wb"
    )
    data = yaml.safe_load(wb_path.read_text(encoding="utf-8"))
    content = str(data.get("content") or "")

    assert "dendrite` over `dendritic tree" in content
    assert "F49H12.4p::GFP" in content
    assert "SAX-7/MNR-1" in content
    assert "TIAM-1::GFP" in content
    assert "tagRFP::TBA-1" in content
    assert "UtrCH" in content


def test_gene_expression_zfin_overlay_includes_zebrafish_curation_rules():
    zfin_path = next(
        path
        for path in _load_gene_expression_source().group_rule_files
        if path.stem == "zfin"
    )
    data = yaml.safe_load(zfin_path.read_text(encoding="utf-8"))
    content = str(data.get("content") or "")

    assert "ZFA-compatible anatomy label" in content
    assert "ZFS-compatible stage labels" in content
    assert "fgf8a" in content
    assert "Tg(kdrl:EGFP)" in content
    assert "morpholino" in content
    assert "rescue_experiment_not_expression" in content
