"""Unit tests for search helper validation/enrichment utilities."""

import importlib.util
import logging
from pathlib import Path

import pytest


def _load_search_helpers_module():
    module_path = Path(__file__).resolve().parents[5] / "src/lib/openai_agents/tools/search_helpers.py"
    spec = importlib.util.spec_from_file_location("test_search_helpers_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


search_helpers = _load_search_helpers_module()


def test_validate_search_symbol_rejects_empty_input():
    """Empty/whitespace symbols should be rejected with a clear message."""
    result = search_helpers.validate_search_symbol("   ", "gene")

    assert result.is_valid is False
    assert "Empty gene symbol provided" in (result.warning_message or "")


def test_validate_search_symbol_rejects_whitespace_genotype_notation():
    """Whitespace in symbols should be treated as likely genotype notation."""
    result = search_helpers.validate_search_symbol("PIMT fl/fl", "gene")

    assert result.is_valid is False
    assert "contains whitespace" in (result.warning_message or "")


def test_validate_search_symbol_rejects_genotype_slash_patterns():
    """Genotype slash tokens outside parentheses should be rejected."""
    result = search_helpers.validate_search_symbol("PIMT+/-", "gene")

    assert result.is_valid is False
    assert "genotype notation" in (result.warning_message or "")


def test_validate_search_symbol_allows_slash_inside_parentheses():
    """Slash notation inside parentheses should remain valid."""
    result = search_helpers.validate_search_symbol("Tg(Vil1-cre/ERT2)", "gene")

    assert result.is_valid is True
    assert result.warning_message is None


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


@pytest.mark.parametrize(
    ("force", "force_reason", "expected_ok"),
    [
        (False, None, True),
        (True, "intentional override for external alias", True),
    ],
)
def test_check_force_parameters_accepts_valid_combinations(force, force_reason, expected_ok):
    """Valid force/force_reason combinations should pass."""
    is_valid, message = search_helpers.check_force_parameters(force=force, force_reason=force_reason)

    assert is_valid is expected_ok
    assert message is None


def test_check_force_parameters_requires_reason_when_forced():
    """force=True without a reason should fail fast."""
    is_valid, message = search_helpers.check_force_parameters(force=True, force_reason=None)

    assert is_valid is False
    assert message == "force=True requires force_reason explaining why validation should be skipped"


def test_log_validation_override_writes_expected_log_entry(caplog):
    """Override logging should include symbol/entity/reason details."""
    with caplog.at_level(logging.INFO, logger=search_helpers.__name__):
        search_helpers.log_validation_override(
            symbol="PIMT+/-",
            entity_type="gene",
            force_reason="curated source uses genotype suffix",
        )

    assert "[search_helpers] Validation override:" in caplog.text
    assert "entity_type='gene'" in caplog.text
    assert "symbol='PIMT+/-'" in caplog.text
    assert "reason='curated source uses genotype suffix'" in caplog.text
