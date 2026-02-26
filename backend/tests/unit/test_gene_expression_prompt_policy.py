from pathlib import Path

import yaml


def _repo_root() -> Path:
    # backend/tests/unit/<this_file>.py -> repo root is parents[3]
    return Path(__file__).resolve().parents[3]


def test_gene_expression_prompt_includes_daniela_policy_gates():
    prompt_path = _repo_root() / "config" / "agents" / "gene_expression" / "prompt.yaml"
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


def test_gene_expression_wb_overlay_includes_wormbase_examples():
    wb_path = (
        _repo_root()
        / "config"
        / "agents"
        / "gene_expression"
        / "group_rules"
        / "wb.yaml"
    )
    data = yaml.safe_load(wb_path.read_text(encoding="utf-8"))
    content = str(data.get("content") or "")

    assert "dendrite` over `dendritic tree" in content
    assert "F49H12.4p::GFP" in content
    assert "SAX-7/MNR-1" in content
    assert "TIAM-1::GFP" in content
    assert "tagRFP::TBA-1" in content
    assert "UtrCH" in content
