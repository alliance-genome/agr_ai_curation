"""Focused public API schema contract tests."""

from src.models.api_schemas import DocumentSourceProvenance


def test_document_source_provenance_uses_flat_access_group_ids() -> None:
    provenance = DocumentSourceProvenance(
        provider="genome_archive",
        access_scope="restricted",
        access_group_ids=["research-team", "lab-2"],
    )

    assert provenance.model_dump(exclude_none=True) == {
        "provider": "genome_archive",
        "access_scope": "restricted",
        "access_group_ids": ["research-team", "lab-2"],
    }
    assert "access_mods" not in DocumentSourceProvenance.model_fields
