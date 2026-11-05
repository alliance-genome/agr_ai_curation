"""The effective reference of a document (ALL-1482)."""

from types import SimpleNamespace

import pytest

from src.lib.curation_workspace.document_reference import effective_document_reference
from src.schemas.curation_workspace import CurationDocumentReferenceSource


def _document(*, imported_id=None, imported_curie=None, linked_curie=None):
    return SimpleNamespace(
        source_provider_reference_id=imported_id,
        source_provider_reference_curie=imported_curie,
        linked_reference_curie=linked_curie,
    )


def test_a_document_with_neither_has_no_reference():
    assert effective_document_reference(_document()) is None


def test_an_imported_reference_is_reported_as_imported():
    reference = effective_document_reference(_document(imported_curie="SOURCE:1"))

    assert reference.curie == "SOURCE:1"
    assert reference.source is CurationDocumentReferenceSource.IMPORTED


def test_a_curator_link_is_reported_as_a_link():
    reference = effective_document_reference(_document(linked_curie="SOURCE:2"))

    assert reference.curie == "SOURCE:2"
    assert reference.source is CurationDocumentReferenceSource.CURATOR_LINK


@pytest.mark.parametrize("imported_curie", [None, ""])
def test_an_import_with_only_a_reference_id_has_no_curie_to_offer(imported_curie):
    assert effective_document_reference(
        _document(imported_id="11", imported_curie=imported_curie)
    ) is None
