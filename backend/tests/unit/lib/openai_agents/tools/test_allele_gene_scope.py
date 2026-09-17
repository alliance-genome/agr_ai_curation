"""Gene aliases constrain allele discovery only after exact, scoped resolution."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agr_ai_curation_alliance.tools import agr_curation as tool


@pytest.fixture
def context(monkeypatch):
    db = Mock()
    db.search_allele_candidates.return_value = {
        "candidates": [], "coverage": {"discovered_count": 0, "returned_count": 0,
        "discovery_capped": False, "display_capped": False, "detail_missing_count": 0},
    }
    monkeypatch.setattr(tool, "get_curation_resolver", lambda: SimpleNamespace(get_db_client=lambda: db))
    monkeypatch.setattr(tool, "_GROUP_MAPPING_LOAD_ERROR", None)
    monkeypatch.setattr(tool, "PROVIDER_TO_TAXON", {"MGI": "NCBITaxon:10090", "FB": "NCBITaxon:7227", "WB": "NCBITaxon:6239"})
    return tool._unwrap_function_tool_callable(tool.agr_curation_query, "agr_curation_query"), db


def record(identifier, taxon):
    return SimpleNamespace(primaryExternalId=identifier, taxon=taxon, obsolete=False, internal=False)


def match(identifier, text, kind="exact"):
    return {"entity_curie": identifier, "entity": text, "match_type": kind, "is_obsolete": False}


@pytest.mark.parametrize("provider,taxon,alias,identifier", [
    ("MGI", "NCBITaxon:10090", "Rosa26", "MGI:104735"),
    ("FB", "NCBITaxon:7227", "rut", "FB:FBgn0003301"),
    ("WB", "NCBITaxon:6239", "unc-1", "WB:WBGene00006742"),
])
@pytest.mark.parametrize("method", ["search_alleles", "search_alleles_bulk"])
def test_exact_alias_scopes_literal_allele_query(context, provider, taxon, alias, identifier, method):
    query, db = context
    db.search_entities.return_value = [match(identifier, alias.upper())]
    db.get_gene.return_value = record(identifier, taxon)
    args = {"allele_symbol": "LSL-DTR"} if method == "search_alleles" else {"allele_symbols": ["LSL-DTR"]}
    result = query(method=method, gene_symbol=alias, data_provider=provider, discovery_limit=25, **args)
    assert result.status == "ok"
    db.search_entities.assert_called_once_with(entity_type="gene", search_pattern=alias,
        taxon_curie=taxon, include_synonyms=True, limit=26)
    assert db.search_allele_candidates.call_args.args == ("LSL-DTR",)
    assert db.search_allele_candidates.call_args.kwargs["gene_identifier"] == identifier
    assert db.search_allele_candidates.call_args.kwargs["taxon_curie"] == taxon
    item = result.model_dump() if method == "search_alleles" else result.data["items"][0]
    assert item["coverage"]["gene_scope"]["canonical_id"] == identifier
    assert item["lookup_status"] == "not_found"  # Scope succeeded; allele query was empty.


@pytest.mark.parametrize("clue", ["MGI:104735", "Gt(ROSA)26Sor"])
def test_canonical_id_and_official_symbol(context, clue):
    query, db = context
    db.search_entities.return_value = [match("MGI:104735", clue)]
    db.get_gene.return_value = record("MGI:104735", "NCBITaxon:10090")
    result = query(method="search_alleles", allele_symbol="DTR", gene_id=clue, data_provider="MGI")
    assert result.status == "ok"
    assert db.search_allele_candidates.call_args.kwargs["gene_identifier"] == "MGI:104735"


def test_identifier_can_establish_taxon_but_conflicts_are_blocked(context):
    query, db = context
    db.get_gene.return_value = record("MGI:104735", "NCBITaxon:10090")
    assert query(method="search_alleles", allele_symbol="DTR", gene_id="MGI:104735").status == "ok"
    assert db.search_allele_candidates.call_args.kwargs["taxon_curie"] == "NCBITaxon:10090"
    db.search_allele_candidates.reset_mock()
    result = query(method="search_alleles", allele_symbol="DTR", gene_id="MGI:104735", data_provider="FB")
    assert result.coverage["gene_scope"]["status"] == "conflict"
    db.search_allele_candidates.assert_not_called()


@pytest.mark.parametrize("case,status", [("missing", "unresolved"), ("partial", "unresolved"),
    ("ambiguous", "ambiguous"), ("capped", "incomplete"), ("inactive", "unresolved"),
    ("no_species", "unresolved"), ("missing_detail", "incomplete")])
@pytest.mark.parametrize("method", ["search_alleles", "search_alleles_bulk"])
def test_unestablished_scope_never_runs_allele_search(context, case, status, method):
    query, db = context
    db.search_entities.return_value = [match("MGI:1", "Rosa26")]
    db.get_gene.side_effect = lambda identifier: record(identifier, "NCBITaxon:10090")
    if case == "missing": db.search_entities.return_value = []
    if case == "partial": db.search_entities.return_value = [match("MGI:1", "Rosa26-like", "starts_with")]
    if case == "ambiguous": db.search_entities.return_value += [match("MGI:2", "Rosa26")]
    if case == "capped": db.search_entities.return_value *= 3
    if case == "inactive":
        inactive = record("MGI:1", "NCBITaxon:10090"); inactive.obsolete = True
        db.get_gene.side_effect = lambda _: inactive
    if case == "missing_detail": db.get_gene.side_effect = lambda _: None
    args = {"allele_symbol": "DTR"} if method == "search_alleles" else {"allele_symbols": ["DTR"]}
    result = query(method=method, gene_symbol="Rosa26", data_provider=None if case == "no_species" else "MGI",
                   discovery_limit=2, **args)
    item = result.model_dump() if method == "search_alleles" else result.data["items"][0]
    assert item["coverage"]["gene_scope"]["status"] == status
    assert item["coverage"]["allele_search_performed"] is False
    assert result.lookup_status != "not_found"
    assert item["lookup_status"] != "not_found"
    assert item["lookup_attempts"][0]["coverage"] == item["coverage"]
    db.search_allele_candidates.assert_not_called()


def test_gene_source_outage_is_transient(context):
    query, db = context
    db.search_entities.side_effect = TimeoutError("source unavailable")
    result = query(method="search_alleles", allele_symbol="DTR", gene_symbol="Rosa26", data_provider="MGI")
    assert result.lookup_status == "transient"
    db.search_allele_candidates.assert_not_called()


def test_partial_fill_does_not_hide_a_unique_exact_scope(context):
    query, db = context
    db.search_entities.return_value = [match("MGI:1", "Rosa26"),
        match("MGI:2", "Rosa26-like", "starts_with"), match("MGI:3", "Rosa26-other", "contains")]
    db.get_gene.return_value = record("MGI:1", "NCBITaxon:10090")
    result = query(method="search_alleles", allele_symbol="DTR", gene_symbol="Rosa26",
                   data_provider="MGI", discovery_limit=2)
    assert result.coverage["gene_scope"]["status"] == "resolved"
    assert result.coverage["gene_scope"]["discovery_capped"] is False
    db.search_allele_candidates.assert_called_once()
