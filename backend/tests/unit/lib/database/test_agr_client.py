"""Tests for AGR curation client convenience helpers."""

import pytest

from src.lib.database import agr_client


@pytest.mark.parametrize(
    "fn_name,kwargs",
    [
        ("get_genes_for_species", {"taxon_id": "NCBITaxon:9606", "limit": 5}),
        ("get_disease_annotations_for_species", {"taxon_id": "NCBITaxon:9606"}),
        ("get_expression_annotations_for_species", {"taxon_id": "NCBITaxon:9606"}),
        ("get_available_species", {}),
    ],
)
def test_convenience_functions_fail_fast_when_db_not_configured(monkeypatch, fn_name, kwargs):
    """Convenience helpers should raise clear error when DB is unavailable."""
    monkeypatch.setattr(agr_client, "get_agr_db_client", lambda force_new=False: None)

    fn = getattr(agr_client, fn_name)
    with pytest.raises(RuntimeError, match="AGR curation DB is not configured"):
        fn(**kwargs)


def test_get_genes_for_species_delegates_to_db(monkeypatch):
    """Convenience helper should delegate to DatabaseMethods when configured."""

    class FakeDb:
        @staticmethod
        def get_genes_by_taxon(taxon_id, limit=None):
            return [{"taxon_id": taxon_id, "limit": limit}]

    monkeypatch.setattr(agr_client, "get_agr_db_client", lambda force_new=False: FakeDb())

    result = agr_client.get_genes_for_species("NCBITaxon:10090", limit=10)

    assert result == [{"taxon_id": "NCBITaxon:10090", "limit": 10}]
