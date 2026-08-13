"""Tests for provider-neutral document-source provenance helpers."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.lib.document_sources.provenance import (
    build_document_source_provenance,
    find_existing_document_by_source,
    sanitize_document_source_provenance,
)


def test_build_document_source_provenance_returns_none_for_local_document() -> None:
    document = SimpleNamespace(
        source_provider=None,
        source_provider_reference_id=None,
        source_provider_reference_curie=None,
        source_provider_source_file_id=None,
        source_provider_converted_artifact_id=None,
        source_provider_pdf_artifact_id=None,
        source_md5=None,
        source_access_scope=None,
        viewer_mode=None,
    )

    assert build_document_source_provenance(document) is None


def test_build_document_source_provenance_requires_provider() -> None:
    document = SimpleNamespace(
        source_provider=None,
        source_provider_reference_id=None,
        source_provider_reference_curie=None,
        source_provider_source_file_id=None,
        source_provider_converted_artifact_id=None,
        source_provider_pdf_artifact_id=None,
        source_md5=None,
        source_access_scope="restricted",
        viewer_mode="local_pdf",
    )

    assert build_document_source_provenance(document) is None


def test_build_document_source_provenance_uses_neutral_non_secret_fields() -> None:
    document = SimpleNamespace(
        source_provider="abc_literature",
        source_provider_reference_id="101",
        source_provider_reference_curie="AGRKB:101",
        source_provider_source_file_id="source-file-1",
        source_provider_pdf_artifact_id="pdf-file-1",
        source_provider_converted_artifact_id="converted-file-1",
        source_external_ids={"pmid": "12345"},
        source_md5="abc123",
        source_file_class="converted_merged_main",
        source_file_extension="md",
        source_artifact_status="available",
        source_import_status="completed",
        source_imported_at=datetime(2026, 6, 24, 12, 0, 0),
        source_payload_path="/internal/raw.json",
        source_markdown_path="/internal/paper.md",
        source_access_scope="restricted",
        source_access_mods={"mods": ["FB"], "raw_mod_objects": [{"secret": "drop"}]},
        viewer_mode="local_pdf",
    )

    provenance = build_document_source_provenance(document)

    assert provenance is not None
    assert provenance == {
        "provider": "abc_literature",
        "reference_id": "101",
        "reference_curie": "AGRKB:101",
        "source_file_id": "source-file-1",
        "pdf_artifact_id": "pdf-file-1",
        "converted_artifact_id": "converted-file-1",
        "external_ids": {"pmid": "12345"},
        "source_md5": "abc123",
        "file_class": "converted_merged_main",
        "file_extension": "md",
        "artifact_status": "available",
        "import_status": "completed",
        "imported_at": "2026-06-24T12:00:00",
        "access_scope": "restricted",
        "access_mods": {"mods": ["FB"]},
        "viewer_mode": "local_pdf",
    }
    assert "source_payload_path" not in provenance
    assert "source_markdown_path" not in provenance


def test_sanitize_document_source_provenance_drops_unknown_nested_raw_values() -> None:
    provenance = sanitize_document_source_provenance(
        {
            "provider": "abc_literature",
            "reference_id": 101,
            "reference_curie": "AGRKB:101",
            "source_payload_path": "/internal/raw.json",
            "source_markdown_path": "/internal/paper.md",
            "curator_token": "secret-token",
            "client_secret": "secret-client",
            "full_markdown": "# Full content",
            "external_ids": {
                "pmid": 12345,
                "doi": ["10.1/example", {"raw": "drop"}],
                "raw_crossref": {"drop": True},
                "client_secret": "drop-me",
                "bearer_token": "drop-me-too",
            },
            "access_mods": {
                "mods": ["FB", {"drop": True}, "WB"],
                "referencefile_mods": [{"secret": "drop"}],
            },
        }
    )

    assert provenance == {
        "provider": "abc_literature",
        "reference_id": "101",
        "reference_curie": "AGRKB:101",
        "external_ids": {
            "pmid": "12345",
            "doi": ["10.1/example"],
        },
        "access_mods": {"mods": ["FB", "WB"]},
    }


def test_find_existing_document_by_source_returns_none_without_match_keys() -> None:
    db = MagicMock()

    result = find_existing_document_by_source(
        db,
        user_id=1,
        source_provider="abc_literature",
    )

    assert result is None
    db.execute.assert_not_called()


def test_find_existing_document_by_source_executes_query_for_match_keys() -> None:
    expected = object()
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = expected

    result = find_existing_document_by_source(
        db,
        user_id=1,
        source_provider="abc_literature",
        reference_id="101",
        reference_curie="AGRKB:101",
        converted_artifact_id="converted-file-1",
        source_md5="abc123",
    )

    assert result is expected
    db.execute.assert_called_once()
