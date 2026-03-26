from pathlib import Path

import yaml


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_prompt(path: Path) -> str:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return str(data.get("content") or "")


def test_core_supervisor_prompt_stays_generic():
    content = _load_prompt(
        _repo_root() / "packages" / "core" / "agents" / "supervisor" / "prompt.yaml"
    )

    assert "QUERY REFORMULATION FOR SPECIALIST HANDOFFS" in content
    assert "RUNTIME TOOL AUTHORITY" in content
    assert "ask_pdf_extraction_specialist" not in content
    assert "ask_gene_extractor_specialist" not in content
    assert "Alliance Gene Database" not in content
    assert "Ready to prepare these for curation?" not in content


def test_config_supervisor_prompt_keeps_alliance_specific_handoffs():
    content = _load_prompt(
        _repo_root() / "config" / "agents" / "supervisor" / "prompt.yaml"
    )

    assert "QUERY REFORMULATION FOR SPECIALIST HANDOFFS" in content
    assert "ask_pdf_extraction_specialist" in content
    assert "ask_gene_extractor_specialist" in content
    assert "Ready to prepare these for curation?" in content
    assert "Alliance Gene Database" in content
