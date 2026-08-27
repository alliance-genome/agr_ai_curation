"""Tests for typed gene, precursor, and mature-RNA product resolution."""

from __future__ import annotations

import json
from pathlib import Path

import requests

from agr_ai_curation_alliance.tools import gene_product_resolution as resolver_module
from agr_ai_curation_alliance.tools.gene_product_resolution import (
    GeneProductCandidate,
    ResolutionProvenance,
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
    return GeneProductCandidate(
        gene_id=row["gene_id"],
        symbol=row["gene_symbol"],
        name=row["gene_name"],
        identity_kind="precursor_locus",
        organism_taxon_id=row["taxon_id"],
        gene_type=row["gene_type"],
        rnacentral_ids=row["rnacentral_ids"],
        provenance=[
            ResolutionProvenance(
                source="Alliance curation database",
                source_url=(
                    "https://rgd.mcw.edu/rgdweb/report/gene/main.html?id="
                    + row["gene_id"].split(":", 1)[1]
                ),
                source_record_id=row["gene_id"],
                evidence="Recorded current read-only curation identity",
            )
        ],
    )


def _recorded_requester(fixture: dict, calls: list[tuple[str, dict]]):
    def requester(url: str, **kwargs):
        calls.append((url, kwargs))
        if "ebisearch" in url:
            return _Response(200, fixture["rnacentral_search"])
        if url.endswith("/rna/URS000020BE6A/xrefs/10116/"):
            return _Response(200, fixture["rnacentral_mature_xrefs"])
        raise AssertionError(f"unexpected URL: {url}")

    return requester


def test_recorded_rat_mature_product_returns_every_current_mapping_as_ambiguous(
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

    assert result.status == "ambiguous"
    assert result.identity_kind == "mature_product"
    assert result.resolved_gene_id is None
    assert result.mature_product is not None
    assert result.mature_product.rnacentral_id == "RNAcentral:URS000020BE6A_10116"
    assert result.mature_product.mirbase_id == "miRBase:MIMAT0000828"
    assert {candidate.gene_id for candidate in result.candidate_mappings} == {
        "RGD:2325336",
        "RGD:2325458",
        "RGD:2325576",
    }
    assert {candidate.identity_kind for candidate in result.candidate_mappings} == {
        "precursor_locus"
    }
    assert lookup_calls[1]["rnacentral_id"] == "RNAcentral:URS000020BE6A"
    assert request_calls[0][1]["timeout"] == 10.0
    assert {item.source for item in result.provenance} == {
        "Alliance curation database",
        "RNAcentral",
        "miRBase",
    }


def test_mapping_cardinality_is_data_driven_not_fixed_by_rat_fixture(monkeypatch):
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

    assert one.status == "resolved"
    assert one.resolved_gene_id == candidates[0].gene_id
    assert one.identity_kind == "mature_product"
    assert two.status == "ambiguous"
    assert two.resolved_gene_id is None
    assert len(two.candidate_mappings) == 2

    monkeypatch.setenv("RNA_GENE_PRODUCT_MAX_CANDIDATES", "1")
    bounded = resolve_with(candidates[:2])
    assert bounded.status == "ambiguous"
    assert len(bounded.candidate_mappings) == 1
    assert bounded.candidate_limit_reached is True


def test_exact_precursor_and_ordinary_gene_are_distinct_and_skip_rna_sources():
    fixture = _fixture()
    precursor = _candidate(fixture["curation_db_candidates"][0])
    ordinary = GeneProductCandidate(
        gene_id="RGD:1594961",
        symbol="Cttn",
        name="cortactin",
        identity_kind="ordinary_gene",
        organism_taxon_id="NCBITaxon:10116",
        gene_type="protein_coding_gene",
    )

    def no_network(*_args, **_kwargs):
        raise AssertionError("exact curation identity must not call RNA sources")

    for query, candidate, expected_kind in (
        ("Mir124-1", precursor, "precursor_locus"),
        ("Cttn", ordinary, "ordinary_gene"),
    ):
        result = resolve_gene_product(
            query,
            "NCBITaxon:10116",
            "RGD",
            "rno",
            requester=no_network,
            curation_lookup=lambda **_kwargs: [candidate],
            use_cache=False,
        )

        assert result.status == "resolved"
        assert result.identity_kind == expected_kind
        assert result.resolved_gene_id == candidate.gene_id


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
    assert result.identity_kind == "unknown"
    assert result.resolved_gene_id is None
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

    assert result.status == "not_found"
    assert result.resolved_gene_id is None
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
    assert result.resolved_gene_id is None
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
        identity_kind="unknown",
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
