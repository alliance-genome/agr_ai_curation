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


def test_allele_extractor_mgi_overlay_includes_lab_code_disambiguation_workflow():
    content = _load_allele_extractor_mgi_group_rule()

    assert "tm1.1Hko" in content
    assert "em1Cya" in content
    assert "em1Gpt" in content
    assert "fullname_attribution" in content
    assert "`search_alleles` substring matching" in content
    assert "cross-check that the candidate's gene matches the experimental gene(s) discussed in the current paper" in content
    assert "ambiguities[]" in content
