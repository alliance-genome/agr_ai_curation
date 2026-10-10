"""Tests for typed gene, precursor, and mature-RNA product resolution."""

from __future__ import annotations

import json
from pathlib import Path

import requests

from agr_ai_curation_alliance.tools import gene_product_resolution as resolver_module
from agr_ai_curation_alliance.tools.gene_product_resolution import (
    GeneProductCandidate,
    resolve_gene_product,
)

REPO_ROOT = Path(__file__).resolve().parents[6]
FIXTURE_PATH = (
    REPO_ROOT
    / "backend/tests/fixtures/alliance/gene_product_resolution/rno_mir_124_3p.json"
)


class _Response:
    def __init__(self, status_code: int, payload: object):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _candidate(row: dict) -> GeneProductCandidate:
    return resolver_module._candidate_from_row(row)


def test_non_rgd_candidate_provenance_links_to_its_alliance_gene_record():
    candidate = _candidate(
        {
            "gene_id": "MGI:1923928",
            "gene_symbol": "Tmem67",
            "gene_name": "transmembrane protein 67",
            "gene_type": "protein_coding_gene",
            "taxon_id": "NCBITaxon:10090",
            "rnacentral_ids": [],
        }
    )

    assert candidate.provenance[0].source_url == (
        "https://www.alliancegenome.org/gene/MGI:1923928"
    )
    assert candidate.provenance[0].source_record_id == "MGI:1923928"


def _recorded_requester(fixture: dict, calls: list[tuple[str, dict]]):
    def requester(url: str, **kwargs):
        calls.append((url, kwargs))
        if "ebisearch" in url:
            return _Response(200, fixture["rnacentral_search"])
        if url.endswith("/rna/URS000020BE6A/xrefs/10116/"):
            return _Response(200, fixture["rnacentral_mature_xrefs"])
        for rnacentral_id, payload in fixture["rnacentral_precursor_xrefs"].items():
            urs = rnacentral_id.split(":", 1)[1]
            if url.endswith(f"/rna/{urs}/xrefs/10116/"):
                return _Response(200, payload)
        raise AssertionError(f"unexpected URL: {url}")

    return requester


def test_recorded_rat_mature_product_returns_every_current_mapping_without_selection(
    monkeypatch,
):
    monkeypatch.delenv("RNA_GENE_PRODUCT_REQUEST_TIMEOUT_SECONDS", raising=False)
    fixture = _fixture()
    candidates = [_candidate(row) for row in fixture["curation_db_candidates"]]
    lookup_calls: list[dict] = []
    request_calls: list[tuple[str, dict]] = []

    def curation_lookup(**kwargs):
        lookup_calls.append(kwargs)
        return [] if kwargs["rnacentral_id"] is None else candidates

    result = resolve_gene_product(
        "miR-124-3p",
        "NCBITaxon:10116",
        "RGD",
        "rno",
        requester=_recorded_requester(fixture, request_calls),
        curation_lookup=curation_lookup,
        use_cache=False,
    )

    assert result.status == "success"
    assert "resolved_gene_id" not in result.model_dump()
    assert len(result.product_candidates) == 1
    assert result.product_candidates[0].rnacentral_id == "RNAcentral:URS000020BE6A_10116"
    assert result.product_candidates[0].mirbase_ids == ["miRBase:MIMAT0000828"]
    assert {candidate.gene_id for candidate in result.candidate_mappings} == {
        "RGD:2325336",
        "RGD:2325458",
        "RGD:2325576",
    }
    assert result.candidate_mappings[0].provenance[0].source_url.startswith(
        "https://rgd.mcw.edu/rgdweb/report/gene/main.html?id="
    )
    assert {
        hairpin_id
        for candidate in result.candidate_mappings
        for hairpin_id in candidate.mirbase_hairpin_ids
    } == {"miRBase:MI0000892", "miRBase:MI0000893", "miRBase:MI0000894"}
    assert lookup_calls[1]["rnacentral_id"] == "RNAcentral:URS000020BE6A"
    assert request_calls[0][1]["timeout"] == 10.0
    assert {item.source for item in result.provenance} == {
        "Alliance curation database",
        "RNAcentral",
        "miRBase",
    }


def test_mapping_counts_never_select_identity_and_bounds_are_explicit(monkeypatch):
    fixture = _fixture()
    candidates = [_candidate(row) for row in fixture["curation_db_candidates"]]

    def resolve_with(mapped):
        return resolve_gene_product(
            "miR-124-3p",
            "NCBITaxon:10116",
            "RGD",
            "rno",
            requester=_recorded_requester(fixture, []),
            curation_lookup=lambda **kwargs: (
                [] if kwargs["rnacentral_id"] is None else mapped
            ),
            use_cache=False,
        )

    one = resolve_with(candidates[:1])
    two = resolve_with(candidates[:2])

    assert one.status == "success"
    assert "resolved_gene_id" not in one.model_dump()
    assert two.status == "success"
    assert "resolved_gene_id" not in two.model_dump()
    assert len(two.candidate_mappings) == 2

    monkeypatch.setenv("RNA_GENE_PRODUCT_MAX_CANDIDATES", "1")
    bounded = resolve_with(candidates[:2])
    assert bounded.status == "success"
    assert len(bounded.candidate_mappings) == 1
    assert bounded.candidate_limit_reached is True


def test_exact_records_retain_source_gene_types_and_cross_references():
    fixture = _fixture()
    precursor = _candidate(fixture["curation_db_candidates"][0])
    ordinary = GeneProductCandidate(
        gene_id="RGD:1594961",
        symbol="Cttn",
        name="cortactin",
        organism_taxon_id="NCBITaxon:10116",
        gene_type="protein_coding_gene",
    )

    precursor_result = resolve_gene_product(
        "Mir124-1",
        "NCBITaxon:10116",
        "RGD",
        "rno",
        requester=_recorded_requester(fixture, []),
        curation_lookup=lambda **_kwargs: [precursor],
        use_cache=False,
    )

    assert precursor_result.status == "success"
    assert "resolved_gene_id" not in precursor_result.model_dump()
    assert precursor_result.candidate_mappings[0].mirbase_hairpin_ids == [
        fixture["curation_db_candidates"][0]["mirbase_hairpin_id"]
    ]
    assert {item.source for item in precursor_result.provenance} == {
        "Alliance curation database",
        "RNAcentral",
        "miRBase",
    }

    ordinary_result = resolve_gene_product(
        "Cttn",
        "NCBITaxon:10116",
        "RGD",
        "rno",
        requester=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ordinary curation identity must not call RNA sources")
        ),
        curation_lookup=lambda **_kwargs: [ordinary],
        use_cache=False,
    )
    assert ordinary_result.status == "success"
    assert "resolved_gene_id" not in ordinary_result.model_dump()


def test_truncated_rnacentral_search_reports_incomplete_coverage():
    fixture = _fixture()
    truncated_search = {**fixture["rnacentral_search"], "hitCount": 7}

    def requester(url: str, **_kwargs):
        if "ebisearch" in url:
            return _Response(200, truncated_search)
        if url.endswith("/rna/URS000020BE6A/xrefs/10116/"):
            return _Response(200, fixture["rnacentral_mature_xrefs"])
        raise AssertionError(f"unexpected URL: {url}")

    result = resolve_gene_product(
        "miR-124-3p",
        "NCBITaxon:10116",
        "RGD",
        "rno",
        requester=requester,
        curation_lookup=lambda **_kwargs: [],
        use_cache=False,
    )

    assert result.status == "success"
    assert result.candidate_limit_reached is True
    assert "resolved_gene_id" not in result.model_dump()


def test_truncated_rnacentral_xrefs_report_incomplete_coverage():
    fixture = _fixture()
    truncated_xrefs = {
        **fixture["rnacentral_mature_xrefs"],
        "next": "https://rnacentral.org/api/v1/rna/URS000020BE6A/xrefs/10116/?page=2",
    }

    def requester(url: str, **_kwargs):
        if "ebisearch" in url:
            return _Response(200, fixture["rnacentral_search"])
        if url.endswith("/rna/URS000020BE6A/xrefs/10116/"):
            return _Response(200, truncated_xrefs)
        raise AssertionError(f"unexpected URL: {url}")

    result = resolve_gene_product(
        "miR-124-3p",
        "NCBITaxon:10116",
        "RGD",
        "rno",
        requester=requester,
        curation_lookup=lambda **_kwargs: [],
        use_cache=False,
    )

    assert result.status == "success"
    assert result.candidate_limit_reached is True
    assert "resolved_gene_id" not in result.model_dump()


def test_truncated_precursor_xrefs_report_incomplete_coverage():
    fixture = _fixture()
    precursor = _candidate(fixture["curation_db_candidates"][0])
    complete_requester = _recorded_requester(fixture, [])

    def requester(url: str, **kwargs):
        if url.endswith("/rna/URS000075A939/xrefs/10116/"):
            return _Response(
                200,
                {
                    **fixture["rnacentral_precursor_xrefs"][
                        "RNAcentral:URS000075A939"
                    ],
                    "next": "https://rnacentral.org/api/v1/rna/URS000075A939/xrefs/10116/?page=2",
                },
            )
        return complete_requester(url, **kwargs)

    result = resolve_gene_product(
        "Mir124-1",
        "NCBITaxon:10116",
        "RGD",
        "rno",
        requester=requester,
        curation_lookup=lambda **_kwargs: [precursor],
        use_cache=False,
    )

    assert result.status == "success"
    assert result.candidate_limit_reached is True
    assert "resolved_gene_id" not in result.model_dump()


def test_not_found_has_no_synthetic_identity_or_candidates():
    result = resolve_gene_product(
        "definitely-not-a-rat-product",
        "NCBITaxon:10116",
        "RGD",
        "rno",
        requester=lambda *_args, **_kwargs: _Response(
            200, {"hitCount": 0, "entries": []}
        ),
        curation_lookup=lambda **_kwargs: [],
        use_cache=False,
    )

    assert result.status == "not_found"
    assert "resolved_gene_id" not in result.model_dump()
    assert result.product_candidates == []
    assert result.candidate_mappings == []


def test_upstream_errors_are_explicit_for_database_and_rna_sources():
    def database_error(**_kwargs):
        raise RuntimeError("read-only database unavailable")

    db_result = resolve_gene_product(
        "Cttn",
        "NCBITaxon:10116",
        "RGD",
        "rno",
        curation_lookup=database_error,
        use_cache=False,
    )

    def connection_error(*_args, **_kwargs):
        raise requests.ConnectionError("RNAcentral offline")

    rna_result = resolve_gene_product(
        "miR-124-3p",
        "NCBITaxon:10116",
        "RGD",
        "rno",
        requester=connection_error,
        curation_lookup=lambda **_kwargs: [],
        use_cache=False,
    )

    assert db_result.status == "upstream_error"
    assert "database" in db_result.message
    assert rna_result.status == "upstream_error"
    assert "RNAcentral offline" in rna_result.message


def test_invalid_synthetic_rgd_curie_is_rejected_without_source_dispatch():
    calls = 0

    def must_not_call(**_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("invalid synthetic CURIE must not reach a source")

    result = resolve_gene_product(
        "RGD:miR-124",
        "NCBITaxon:10116",
        "RGD",
        "rno",
        requester=must_not_call,
        curation_lookup=must_not_call,
        use_cache=False,
    )

    assert result.status == "invalid_input"
    assert "resolved_gene_id" not in result.model_dump()
    assert result.candidate_mappings == []
    assert "Rejected synthetic or invalid RGD gene CURIE" == result.message
    assert calls == 0


def test_invalid_rnacentral_contract_is_upstream_error_not_empty_success():
    result = resolve_gene_product(
        "miR-124-3p",
        "NCBITaxon:10116",
        "RGD",
        "rno",
        requester=lambda *_args, **_kwargs: _Response(200, {"entries": "invalid"}),
        curation_lookup=lambda **_kwargs: [],
        use_cache=False,
    )

    assert result.status == "upstream_error"
    assert result.candidate_mappings == []


def test_unsafe_curation_mapping_is_upstream_error_and_never_emitted():
    fixture = _fixture()
    unsafe = _candidate(fixture["curation_db_candidates"][0]).model_copy(
        update={"gene_id": "RGD:miR-124"}
    )

    result = resolve_gene_product(
        "miR-124-3p",
        "NCBITaxon:10116",
        "RGD",
        "rno",
        requester=_recorded_requester(fixture, []),
        curation_lookup=lambda **kwargs: (
            [] if kwargs["rnacentral_id"] is None else [unsafe]
        ),
        use_cache=False,
    )

    assert result.status == "upstream_error"
    assert "resolved_gene_id" not in result.model_dump()
    assert result.candidate_mappings == []
    assert "not accepted by go_api_call" in result.message


def test_cache_ttl_capacity_and_backend_configuration_share_environment(monkeypatch):
    from src.lib.openai_agents import config

    monkeypatch.setenv("RNA_GENE_PRODUCT_REQUEST_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setenv("RNA_GENE_PRODUCT_CACHE_TTL_SECONDS", "5")
    monkeypatch.setenv("RNA_GENE_PRODUCT_CACHE_MAX_ENTRIES", "1")
    monkeypatch.setenv("RNA_GENE_PRODUCT_MAX_CANDIDATES", "9")

    assert resolver_module._request_timeout_seconds() == 7.5
    assert config.get_rna_gene_product_request_timeout_seconds() == 7.5
    assert config.get_rna_gene_product_cache_ttl_seconds() == 5.0
    assert config.get_rna_gene_product_cache_max_entries() == 1
    assert config.get_rna_gene_product_max_candidates() == 9

    now = 100.0
    monkeypatch.setattr(resolver_module.time, "monotonic", lambda: now)
    resolver_module.clear_gene_product_resolution_cache()
    first_key = ("first", "NCBITaxon:10116", "RGD", "rno")
    second_key = ("second", "NCBITaxon:10116", "RGD", "rno")
    result = resolver_module._base_result(
        status="not_found",
        query="first",
        organism_taxon_id="NCBITaxon:10116",
        provider_prefix="RGD",
        message="not found",
    )

    resolver_module._store_cached(first_key, result)
    assert resolver_module._cached(first_key) == result
    resolver_module._store_cached(second_key, result)
    assert resolver_module._cached(first_key) is None
    assert resolver_module._cached(second_key) == result
    now = 106.0
    assert resolver_module._cached(second_key) is None


def test_single_candidate_is_lookup_data_not_an_identity_verdict():
    candidate = _candidate(_fixture()["curation_db_candidates"][0])
    result = resolve_gene_product(
        "Mir124-1", "NCBITaxon:10116", "RGD", "rno",
        requester=_recorded_requester(_fixture(), []),
        curation_lookup=lambda **kwargs: [candidate], use_cache=False,
    )
    assert result.status == "success"
    assert result.candidate_mappings[0].gene_id == candidate.gene_id
    assert "resolved_gene_id" not in result.model_dump()
    assert "identity_kind" not in result.model_dump()
    assert "identity_kind" not in result.candidate_mappings[0].model_dump()


def test_search_returns_candidates_without_description_type_or_source_veto():
    fixture = _fixture()
    entry = fixture["rnacentral_search"]["entries"][0]
    entry["fields"] = {
        "description": ["Different wording for a potentially relevant RNA"],
        "rna_type": ["other RNA"], "expert_db": ["another source"],
    }
    result = resolve_gene_product(
        "miR-124-3p", "NCBITaxon:10116", "RGD", "rno",
        requester=_recorded_requester(fixture, []),
        curation_lookup=lambda **kwargs: [], use_cache=False,
    )
    assert result.status == "success"
    assert result.product_candidates[0].descriptions == entry["fields"]["description"]
    assert result.product_candidates[0].rna_types == ["other RNA"]
    assert result.product_candidates[0].expert_databases == ["another source"]
    assert "resolved_gene_id" not in result.model_dump()


def test_multiple_products_keep_all_source_mappings_even_without_mirbase_match():
    from copy import deepcopy
    fixture = _fixture()
    first = fixture["rnacentral_search"]["entries"][0]
    second = deepcopy(first)
    second["id"] = "URS0000000001_10116"
    second["fields"]["description"] = ["Another possible RNA"]
    fixture["rnacentral_search"] = {"hitCount": 2, "entries": [first, second]}
    fixture["rnacentral_mature_xrefs"] = {"count": 0, "next": None, "results": []}
    recorded = _recorded_requester(fixture, [])
    calls = []
    candidate = _candidate(fixture["curation_db_candidates"][0])

    def requester(url, **kwargs):
        if url.endswith('/rna/URS0000000001/xrefs/10116/'):
            return _Response(200, {"count": 0, "next": None, "results": []})
        return recorded(url, **kwargs)

    def lookup(**kwargs):
        calls.append(kwargs["rnacentral_id"])
        return [candidate] if kwargs["rnacentral_id"] else []

    result = resolve_gene_product(
        "miR-124-3p", "NCBITaxon:10116", "RGD", "rno",
        requester=requester, curation_lookup=lookup, use_cache=False,
    )
    assert result.status == "success"
    assert len(result.product_candidates) == 2
    assert all(product.mirbase_ids == [] for product in result.product_candidates)
    assert calls == [None, "RNAcentral:URS000020BE6A", "RNAcentral:URS0000000001"]
    assert len(result.candidate_mappings) == 1
    relations = [p.evidence for p in result.candidate_mappings[0].provenance]
    assert any("URS000020BE6A_10116" in text for text in relations)
    assert any("URS0000000001_10116" in text for text in relations)
    assert "resolved_gene_id" not in result.model_dump()
