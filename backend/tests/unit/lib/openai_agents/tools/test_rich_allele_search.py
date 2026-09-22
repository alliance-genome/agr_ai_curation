"""Rich allele client integration; candidates are never paper identity confirmations."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from agr_ai_curation_alliance.tools import agr_curation as tool


def response(count=1, match_type="starts_with"):
    rows = [
        {
            "curie": f"MGI:{i}",
            "symbol": f"H2-Ab1 allele {i}",
            "name": "mutation, Cyagen Biosciences",
            "taxon": "NCBITaxon:10090",
            "synonyms": ["em1flox"],
            "genes": [{"curie": "MGI:103070", "relation": "is_allele_of"}],
            "functional_impacts": ["conditional_ready"],
            "mutation_types": [{"curie": "SO:0000667", "name": "insertion"}],
            "annotations_capped": [],
            "match_reasons": [
                "full_name_attribution_text",
                "structured_functional_impact",
            ],
            "match_type": match_type,
            "identity_status": "unconfirmed",
        }
        for i in range(count)
    ]
    return {
        "candidates": rows,
        "coverage": {
            "discovered_count": count,
            "returned_count": count,
            "discovery_limit": 200,
            "display_limit": count,
            "discovery_capped": False,
            "display_capped": False,
            "database_total": None,
            "detail_missing_count": 0,
        },
    }


@pytest.fixture
def context(monkeypatch):
    calls = []

    class DB:
        result = response()

        def search_allele_candidates(self, symbol, **kwargs):
            calls.append((symbol, kwargs))
            if symbol == "failed":
                raise TimeoutError("fixture source outage")
            return deepcopy(self.result)

        def get_gene(self, identifier):
            return SimpleNamespace(primaryExternalId=identifier, taxon="NCBITaxon:10090",
                                   obsolete=False, internal=False)

        def get_allele_candidate_details(self, identifiers):
            return [r for r in self.result["candidates"] if r["curie"] in identifiers]

    db = DB()
    monkeypatch.setattr(
        tool, "get_curation_resolver", lambda: SimpleNamespace(get_db_client=lambda: db)
    )
    monkeypatch.setattr(tool, "PROVIDER_TO_TAXON", {"MGI": "NCBITaxon:10090"})
    monkeypatch.setattr(tool, "TAXON_TO_PROVIDER", {"NCBITaxon:10090": "MGI"})
    monkeypatch.setattr(tool, "_GROUP_MAPPING_LOAD_ERROR", None)
    monkeypatch.setattr(tool, "is_valid_curie", lambda _: True)
    return (
        tool._unwrap_function_tool_callable(
            tool.agr_curation_query, "agr_curation_query"
        ),
        db,
        calls,
    )


@pytest.mark.parametrize(
    "symbol", ["H2-Ab1 f/f", "N fa-g", "Nfa-g", "upd3∆", "crb 11A22", "MGI:7584221"]
)
@pytest.mark.parametrize("method", ["search_alleles", "search_alleles_bulk"])
def test_literal_inputs_separate_clues_and_compact_candidates(context, method, symbol):
    query, db, calls = context
    db.result["coverage"].update(discovered_count=31, display_capped=True)
    args = (
        {"allele_symbol": symbol}
        if method == "search_alleles"
        else {"allele_symbols": [symbol]}
    )
    result = query(
        method=method,
        data_provider="MGI",
        gene_id="MGI:103070",
        allele_attribution="Cyagen",
        allele_functional_impact="conditional_ready",
        discovery_limit=200,
        limit=20,
        **args,
    )
    assert result.status == "ok"
    assert calls[0][0] == symbol
    assert calls[0][1]["gene_identifier"] == "MGI:103070"
    assert calls[0][1]["attribution_hint"] == "Cyagen"
    assert calls[0][1]["functional_impact_hint"] == "conditional_ready"
    item = (
        result.model_dump() if method == "search_alleles" else result.data["items"][0]
    )
    row = item["data"][0] if method == "search_alleles" else item["results"][0]
    assert row["functional_impacts"] == ["conditional_ready"]
    assert row["synonyms"] == ["em1flox"]
    assert item["coverage"]["discovered_count"] == 31
    assert item["lookup_attempts"][0]["coverage"]["display_capped"] is True
    assert item["lookup_status"] == "ambiguous"
    assert item["result_projections"][0]["projection_status"] == "candidate"
    assert "synonyms" not in item["result_projections"][0].get("provider_data", {})
    assert "not an exact database total" in item["explanation"]


def test_detail_on_demand_exposes_distinguishing_facts(context):
    query, _, _ = context
    result = query(method="get_allele_by_id", allele_id="MGI:0")
    assert result.data["genes"][0]["relation"] == "is_allele_of"
    assert result.data["functional_impacts"] == ["conditional_ready"]
    assert (
        query(method="get_allele_by_id", allele_id="MGI:absent").lookup_status
        == "not_found"
    )


@pytest.mark.parametrize("method", ["search_alleles", "search_alleles_bulk"])
def test_search_supplies_detail_facts_without_requiring_confirmation(context, method):
    query, db, _ = context
    args = {"allele_symbol": "H2-Ab1"} if method == "search_alleles" else {"allele_symbols": ["H2-Ab1"]}
    search = query(method=method, **args)
    item = search.model_dump() if method == "search_alleles" else search.data["items"][0]
    rows = item["data"] if method == "search_alleles" else item["results"]
    detail = query(method="get_allele_by_id", allele_id=rows[0]["curie"])
    for field in ("curie", "symbol", "name", "taxon", "synonyms", "genes", "functional_impacts", "mutation_types"):
        assert rows[0][field] == detail.data[field]
    assert "Do not reread an already returned record" in item["explanation"]
    assert "missing required facts" in item["explanation"]
    assert "conflicting records" in item["explanation"]
    assert item["lookup_status"] == "ambiguous"
    assert rows[0]["identity_status"] == "unconfirmed"


def test_missing_search_details_do_not_claim_complete_identity(context):
    query, db, _ = context
    db.result["coverage"]["detail_missing_count"] = 1
    db.result["candidates"][0]["annotations_capped"] = ["synonyms"]
    result = query(method="search_alleles", allele_symbol="H2-Ab1")
    assert result.coverage["detail_missing_count"] == 1
    assert result.data[0]["annotations_capped"] == ["synonyms"]
    assert "capped annotations are not complete evidence" in result.explanation
    assert result.lookup_status == "ambiguous"


def test_identifier_collision_preserves_all_candidates(context, monkeypatch):
    query, db, _ = context
    monkeypatch.setattr(
        db, "get_allele_candidate_details", lambda _: response(2)["candidates"]
    )
    result = query(method="get_allele_by_id", allele_id="MGI:overlap")
    assert result.lookup_status == "ambiguous"
    assert result.count == 2
    assert len(result.data) == 2
    assert all(row["identity_status"] == "unconfirmed" for row in result.data)
    assert all(p["projection_status"] == "candidate" for p in result.result_projections)
    assert result.lookup_attempts[0]["lookup_status"] == "ambiguous"
    assert "resolved_id" not in result.lookup_attempts[0]


def test_source_failure_is_not_no_match_and_bulk_retains_other_candidates(context):
    query, _, _ = context
    assert (
        query(method="search_alleles", allele_symbol="failed").lookup_status
        == "transient"
    )
    result = query(method="search_alleles_bulk", allele_symbols=["H2-Ab1", "failed"])
    assert [i["status"] for i in result.data["items"]] == [
        "ambiguous",
        "transient_failure",
    ]
    assert result.data["resolution_status"] == "transient_failure"
    assert result.data["total_matches"] == 1


def test_bulk_candidates_are_ambiguous_not_resolved_or_empty(context):
    query, _, _ = context
    result = query(method="search_alleles_bulk", allele_symbols=["one", "two"])
    assert result.lookup_status == "ambiguous"
    assert result.data["resolution_status"] == "ambiguous"
    assert result.data["resolved_count"] == 0
    assert result.data["total_matches"] == 2
    assert len(result.lookup_attempts) == 2
    assert all(a["coverage"]["returned_count"] == 1 for a in result.lookup_attempts)


def test_all_failed_bulk_is_transient_with_original_attempts(context):
    query, _, _ = context
    result = query(method="search_alleles_bulk", allele_symbols=["failed"])
    assert result.lookup_status == "transient"
    assert result.data["resolution_status"] == "transient_failure"
    assert result.lookup_attempts[0]["lookup_status"] == "transient"
    assert result.lookup_attempts[0]["error"]["type"] == "TimeoutError"


def test_fuzzy_session_has_transaction_local_timeout(monkeypatch):
    from unittest.mock import MagicMock

    session = MagicMock()
    session.execute.return_value.fetchall.return_value = []
    monkeypatch.setattr(tool, "create_db_session", lambda _: session)
    monkeypatch.setenv("AGR_ALLELE_QUERY_TIMEOUT_MS", "1234")
    tool._search_alleles_fuzzy_via_db(
        object(),
        search_pattern="missing",
        taxon_curie=None,
        include_synonyms=True,
        limit=20,
    )
    statement, params = session.execute.call_args_list[0].args
    assert "set_config('statement_timeout', :timeout, true)" in str(statement)
    assert params == {"timeout": "1234"}
    session.close.assert_called_once()


@pytest.mark.parametrize("provider", [None, "MGI"])
def test_fuzzy_discovery_retains_synonym_and_never_confirms_identity(
    context, monkeypatch, provider
):
    query, db, _ = context
    db.result = response(0)
    fuzzy_calls = []

    def fuzzy(_db, **kwargs):
        fuzzy_calls.append(kwargs)
        return [
            {
                "entity_curie": "MGI:1",
                "entity": "NFAT-GFP",
                "taxon_curie": "NCBITaxon:10090",
                "match_type": "fuzzy_synonym",
            }
        ]

    monkeypatch.setattr(tool, "_search_alleles_fuzzy_via_db", fuzzy)
    monkeypatch.setattr(
        tool,
        "_fetch_allele_details_bulk",
        lambda *_: ({"MGI:1": {"curie": "MGI:1", "symbol": "NFAT insertion"}}, {}),
    )
    result = query(
        method="search_alleles", allele_symbol="Nfa-g", data_provider=provider
    )
    assert len(fuzzy_calls) == 1
    assert fuzzy_calls[0]["taxon_curie"] == ("NCBITaxon:10090" if provider else None)
    assert result.data[0]["matched_on"] == "NFAT-GFP"
    assert result.data[0]["identity_status"] == "unconfirmed"
    assert result.lookup_status == "ambiguous"
    assert (
        result.lookup_attempts[0]["target_projection"]["projection_status"]
        == "candidate"
    )
    assert "resolved_id" not in result.lookup_attempts[0]


def test_no_match_and_fuzzy_failure_distinct(context, monkeypatch):
    query, db, _ = context
    db.result = response(0)
    monkeypatch.setattr(tool, "_search_alleles_fuzzy_via_db", lambda *_a, **_kw: [])
    assert (
        query(method="search_alleles", allele_symbol="absent").lookup_status
        == "not_found"
    )

    def failure(*_a, **_kw):
        raise TimeoutError("fixture")

    monkeypatch.setattr(tool, "_search_alleles_fuzzy_via_db", failure)
    assert (
        query(method="search_alleles", allele_symbol="absent").lookup_status
        == "transient"
    )
    assert (
        query(
            method="search_alleles", allele_symbol="absent", gene_id="MGI:103070"
        ).lookup_status
        == "not_found"
    )


def test_provider_scope_and_limits_are_checked_before_query(context):
    query, _, calls = context
    for extra in (
        {"data_provider": "BAD"},
        {"data_provider": "MGI", "taxon_id": "NCBITaxon:6239"},
        {"discovery_limit": 1001},
        {"gene_id": "MGI:1", "gene_symbol": "Gene"},
    ):
        assert (
            query(method="search_alleles", allele_symbol="Gene", **extra).lookup_status
            == "blocked"
        )
    assert calls == []


def test_bulk_display_cap_preserves_coverage(context):
    query, db, _ = context
    db.result = response(250)
    result = query(
        method="search_alleles_bulk", allele_symbols=["one", "two", "three"], limit=250
    )
    assert result.data["bulk_match_totals"]["total_count"] == 750
    assert result.data["bulk_match_totals"]["truncated"] is True
    item = result.data["items"][-1]
    assert item["coverage"]["returned_count"] == item["count"]
    assert item["coverage"]["bulk_display_capped"] is True
    assert all(a["coverage"] == item["coverage"] for a in item["lookup_attempts"])


def test_bulk_empty_results_and_symbol_soft_cap(context, monkeypatch):
    query, db, calls = context
    db.result = response(0)
    fuzzy_calls = []

    def fuzzy(*_a, **kwargs):
        fuzzy_calls.append(kwargs["search_pattern"])
        return []

    monkeypatch.setattr(tool, "_search_alleles_fuzzy_via_db", fuzzy)
    monkeypatch.setenv("AGR_BULK_SYMBOL_SOFT_CAP", "2")
    result = query(
        method="search_alleles_bulk", allele_symbols=["alpha", "beta", "gamma"]
    )
    assert [c[0] for c in calls] == fuzzy_calls == ["alpha", "beta"]
    assert "bulk_symbol_cap_applied:2:3" in result.warnings
    assert result.data["resolution_status"] == "no_matches"
    assert result.data["status_counts"] == {"no_matches": 2}
    assert result.data["resolved_count"] == 0


def test_detail_batch_preserves_rich_facts_and_reports_failures():
    from agr_ai_curation_alliance.tools.agr_lookup import fetch_allele_details_bulk

    calls = []

    class DB:
        def get_allele_candidate_details(self, identifiers):
            calls.append(identifiers)
            return response()["candidates"]

    details, failures = fetch_allele_details_bulk(
        DB(), ["MGI:0", "MGI:0", "MGI:missing"]
    )
    assert calls == [["MGI:0", "MGI:missing"]]
    assert details["MGI:0"]["functional_impacts"] == ["conditional_ready"]
    assert failures["MGI:missing"][0]["lookup_status"] == "not_found"

    class FailedDB:
        def get_allele_candidate_details(self, _):
            raise TimeoutError("fixture")

    details, failures = fetch_allele_details_bulk(FailedDB(), ["MGI:0"])
    assert details == {}
    assert failures["MGI:0"][0]["lookup_status"] == "transient"
