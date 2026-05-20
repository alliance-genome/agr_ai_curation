"""Unit tests for package-owned search helper enrichment utilities."""

import importlib.util
from pathlib import Path


def _load_search_helpers_module():
    module_path = (
        Path(__file__).resolve().parents[6]
        / "packages/alliance/python/src/agr_ai_curation_alliance/tools/search_helpers.py"
    )
    spec = importlib.util.spec_from_file_location("test_search_helpers_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


search_helpers = _load_search_helpers_module()


def test_enrich_with_match_context_adds_synonym_metadata():
    """Matched synonym should add matched_on/note context fields."""
    result = {"entity_id": "FB:FBgn0000008"}

    enriched = search_helpers.enrich_with_match_context(
        result=result,
        matched_entity="serpent",
        primary_symbol="srp",
        entity_type="gene",
    )

    assert enriched is result
    assert enriched["matched_on"] == "serpent"
    assert "official gene symbol is 'srp'" in enriched["note"]


def test_enrich_with_match_context_skips_when_primary_symbol_matches():
    """No enrichment should occur when matched entity already equals primary."""
    result = {"entity_id": "FB:FBgn0000008"}

    enriched = search_helpers.enrich_with_match_context(
        result=result,
        matched_entity="srp",
        primary_symbol="srp",
        entity_type="gene",
    )

    assert enriched == {"entity_id": "FB:FBgn0000008"}
