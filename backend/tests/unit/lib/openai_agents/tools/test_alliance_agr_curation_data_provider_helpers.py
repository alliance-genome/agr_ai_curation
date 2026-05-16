"""Package-owned AGR curation data provider helper tests."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[6]
ALLIANCE_PACKAGE_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
sys.path.insert(0, str(ALLIANCE_PACKAGE_SRC))

from agr_ai_curation_alliance.tools import agr_curation  # noqa: E402


def _query_fn():
    return agr_curation._unwrap_function_tool_callable(
        agr_curation.agr_curation_query,
        "agr_curation_query",
    )


class _Resolver:
    def __init__(self, db):
        self._db = db

    def get_db_client(self):
        return self._db


class _ProviderDb:
    @staticmethod
    def get_data_providers():
        return [
            ("WB", "NCBITaxon:6239"),
            SimpleNamespace(
                abbreviation="FB",
                taxon_id="NCBITaxon:7227",
                display_name="FlyBase",
            ),
        ]


def test_get_data_providers_returns_abbreviation_taxon_and_display_name(monkeypatch):
    monkeypatch.setattr(
        agr_curation,
        "PROVIDER_METADATA",
        {
            "WB": {
                "display_name": "WormBase",
                "taxon_id": "NCBITaxon:6239",
                "species": "Caenorhabditis elegans",
            }
        },
    )
    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(_ProviderDb()),
    )

    result = _query_fn()(method="get_data_providers")

    assert result.status == "ok"
    assert result.lookup_status == "success"
    assert result.data[0] == {
        "abbreviation": "WB",
        "taxon_id": "NCBITaxon:6239",
        "display_name": "WormBase",
        "species": "Caenorhabditis elegans",
    }
    assert result.data[1]["display_name"] == "FlyBase"
    assert result.result_projections[0]["projection_type"] == "data_provider_reference"
    assert result.result_projections[0]["provider_data"]["taxon_id"] == "NCBITaxon:6239"


def test_get_data_provider_resolves_exact_abbreviation_and_taxon(monkeypatch):
    monkeypatch.setattr(
        agr_curation,
        "PROVIDER_METADATA",
        {"WB": {"display_name": "WormBase", "taxon_id": "NCBITaxon:6239"}},
    )
    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(_ProviderDb()),
    )

    result = _query_fn()(
        method="get_data_provider",
        abbreviation="wb",
        taxon_id="NCBITaxon:6239",
    )

    assert result.status == "ok"
    assert result.lookup_status == "success"
    assert result.count == 1
    assert result.data["matches"][0]["abbreviation"] == "WB"
    assert result.data["matches"][0]["taxon_id"] == "NCBITaxon:6239"
    assert result.lookup_attempts[0]["lookup_status"] == "success"
    assert result.result_projections[0]["resolved_id"] == "WB"


def test_get_data_provider_preserves_unknown_provider_candidates(monkeypatch):
    monkeypatch.setattr(
        agr_curation,
        "PROVIDER_METADATA",
        {"WB": {"display_name": "WormBase", "taxon_id": "NCBITaxon:6239"}},
    )
    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(_ProviderDb()),
    )

    result = _query_fn()(method="get_data_provider", abbreviation="NOPE", limit=2)

    assert result.status == "ok"
    assert result.lookup_status == "not_found"
    assert result.failure_classification == "not_found"
    assert result.count == 0
    assert [candidate["abbreviation"] for candidate in result.data["candidates"]] == [
        "WB",
        "FB",
    ]
    assert result.lookup_attempts[0]["candidate_count"] == 2


def test_get_data_provider_reports_provider_taxon_mismatch(monkeypatch):
    monkeypatch.setattr(
        agr_curation,
        "PROVIDER_METADATA",
        {"WB": {"display_name": "WormBase", "taxon_id": "NCBITaxon:6239"}},
    )
    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(_ProviderDb()),
    )

    result = _query_fn()(
        method="get_data_provider",
        abbreviation="WB",
        taxon_id="NCBITaxon:7227",
    )

    assert result.status == "ok"
    assert result.lookup_status == "not_found"
    assert result.count == 0
    assert result.warnings == ["provider_taxon_mismatch"]
    candidate = result.data["candidates"][0]
    assert candidate["abbreviation"] == "WB"
    assert "Taxon 'NCBITaxon:7227' does not match provider 'WB'" in candidate[
        "mismatch_explanation"
    ]
    assert result.candidate_matches[0]["projection"]["object_type"] == "DataProvider"


def test_get_data_provider_reports_missing_api_helper(monkeypatch):
    monkeypatch.setattr(
        agr_curation,
        "get_curation_resolver",
        lambda: _Resolver(object()),
    )

    result = _query_fn()(method="get_data_provider", abbreviation="WB")

    assert result.status == "error"
    assert result.lookup_status == "under_development"
    assert "get_data_providers" in (result.message or "")
