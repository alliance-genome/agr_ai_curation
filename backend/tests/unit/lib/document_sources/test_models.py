"""Contract tests for provider-neutral document-source models."""

from __future__ import annotations

from dataclasses import fields

from src.lib.document_sources.models import SourceAccessPolicy, SourceConversionResult


def test_source_access_policy_exposes_only_neutral_group_ids() -> None:
    assert [field.name for field in fields(SourceAccessPolicy)] == ["scope", "group_ids"]


def test_source_conversion_result_does_not_expose_provider_group_diagnostics() -> None:
    assert "per_mod_status" not in {
        field.name for field in fields(SourceConversionResult)
    }
