"""Additional helper/branch tests for the package-owned AGR curation tool."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.lib import identifier_validation

from agr_ai_curation_alliance.tools import agr_curation


@pytest.fixture(autouse=True)
def _seed_runtime_identifier_prefixes(monkeypatch, tmp_path: Path):
    identifier_validation.load_prefixes.cache_clear()
    prefix_file = (
        tmp_path / "runtime" / "state" / "identifier_prefixes" / "identifier_prefixes.json"
    )
    prefix_file.parent.mkdir(parents=True, exist_ok=True)
    prefix_file.write_text(json.dumps({"prefixes": ["WB", "FB"]}), encoding="utf-8")
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(tmp_path / "runtime"))
    yield
    identifier_validation.load_prefixes.cache_clear()


def _unwrap_query_function(tool):
    """Extract wrapped agr_curation_query callable for direct unit testing."""
    return agr_curation._unwrap_function_tool_callable(tool, "agr_curation_query")


def test_curie_validation_helpers(monkeypatch):
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda curie: curie.startswith("WB:"))

    result = agr_curation._validate_curie_in_result({"curie": "WB:WBGene00000001"})
    assert result["curie_validated"] is True

    invalid = agr_curation._validate_curie_in_result({"curie": "BAD:1"})
    assert invalid["curie_validated"] is False

    missing = agr_curation._validate_curie_in_result({})
    assert missing["curie_validated"] is False

    rows, invalid_count = agr_curation._validate_curie_list(
        [{"curie": "WB:1"}, {"curie": "BAD:2"}]
    )
    assert len(rows) == 2
    assert invalid_count == 1


def test_normalization_helpers(monkeypatch):
    assert agr_curation._normalize_allele_symbol_for_db("Arx<tm1Gldn>") == [
        "Arx<tm1Gldn>",
        "Arx<sup>tm1Gldn</sup>",
        "Arxtm1Gldn",
    ]
    assert agr_curation._normalize_allele_symbol_for_db("N fa-g") == [
        "N fa-g",
        "N[fa-g]",
        "N<sup>fa-g</sup>",
        "Nfa-g",
    ]
    assert agr_curation._normalize_allele_symbol_for_db("N[fa-g]") == [
        "N[fa-g]",
        "N<sup>fa-g</sup>",
        "Nfa-g",
    ]
    assert agr_curation._normalize_allele_symbol_for_db("Nfa-g") == [
        "Nfa-g",
        "N[fa-g]",
        "N<sup>fa-g</sup>",
        "N fa-g",
    ]
    assert agr_curation._normalize_allele_symbol_for_db("plain_symbol") == ["plain_symbol"]

    monkeypatch.setattr(agr_curation, "DEFAULT_LIMIT", 100)
    monkeypatch.setattr(agr_curation, "HARD_MAX", 500)

    assert agr_curation._normalize_limit(None) == (100, ["default_limit_applied:100"])
    assert agr_curation._normalize_limit("abc")[0] == 100
    assert "invalid_limit_defaulted:100" in agr_curation._normalize_limit("abc")[1]
    assert agr_curation._normalize_limit(0)[0] == 100
    assert "non_positive_limit_defaulted:100" in agr_curation._normalize_limit(0)[1]
    assert agr_curation._normalize_limit(999)[0] == 500
    assert "limit_capped_at:500" in agr_curation._normalize_limit(999)[1]


def test_fullname_attribution_extraction(monkeypatch):
    monkeypatch.setattr(agr_curation, "TAXON_TO_PROVIDER", {"NCBITaxon:10090": "MGI", "NCBITaxon:7227": "FB"})

    assert agr_curation._extract_fullname_attribution(None, "NCBITaxon:10090") is None
    assert agr_curation._extract_fullname_attribution("Name", "NCBITaxon:0000") is None
    assert agr_curation._extract_fullname_attribution("something", "NCBITaxon:7227") is None
    assert agr_curation._extract_fullname_attribution("wild type", "NCBITaxon:10090") is None
    assert agr_curation._extract_fullname_attribution("gene, Ab", "NCBITaxon:10090") is None

    probable = agr_curation._extract_fullname_attribution(
        "targeted mutation, Joshua Scallan", "NCBITaxon:10090"
    )
    assert probable == {
        "value": "Joshua Scallan",
        "confidence": "probable",
        "source": "fullname_suffix",
    }

    uncertain = agr_curation._extract_fullname_attribution(
        "targeted mutation, Jackson", "NCBITaxon:10090"
    )
    assert uncertain["value"] == "Jackson"
    assert uncertain["confidence"] == "uncertain"


def test_result_factories():
    ok = agr_curation._ok(data={"x": 1}, count=1, warnings=["w"], message="m")
    assert ok.status == "ok"
    assert ok.count == 1
    assert ok.warnings == ["w"]

    err = agr_curation._err("boom")
    assert err.status == "error"
    assert err.message == "boom"

    warning = agr_curation._validation_warning("bad symbol")
    assert warning.status == "validation_warning"
    assert warning.message == "bad symbol"


def test_lookup_response_serializes_attempts_candidates_and_projections():
    result = agr_curation._lookup_response(
        method="get_gene_by_id",
        data={
            "curie": "WB:WBGene00000298",
            "symbol": "cat-4",
            "name": "GTP cyclohydrolase I",
            "taxon": "NCBITaxon:6239",
        },
        attempted_query={
            "method": "get_gene_by_id",
            "gene_id": "WB:WBGene00000298",
        },
        exact_lookup=True,
    )

    assert result.lookup_status == "success"
    assert result.failure_classification is None
    assert result.lookup_attempts[0]["lookup_status"] == "success"
    assert result.lookup_attempts[0]["attempted_query"]["gene_id"] == "WB:WBGene00000298"
    assert result.candidate_matches[0]["candidate_id"] == "WB:WBGene00000298"
    projection = result.result_projections[0]
    assert projection["provider"] == "alliance_curation_db"
    assert projection["projection_type"] == "gene_reference"
    assert projection["resolved_label"] == "cat-4"
    assert projection["provider_data"]["taxon"] == "NCBITaxon:6239"
    assert "resolved" in result.explanation


def test_lookup_response_classifies_not_found_ambiguous_and_transient():
    missing = agr_curation._lookup_response(
        method="get_allele_by_id",
        data=None,
        count=0,
        attempted_query={"method": "get_allele_by_id", "allele_id": "WB:missing"},
        exact_lookup=True,
    )
    assert missing.lookup_status == "not_found"
    assert missing.failure_classification == "not_found"

    ambiguous = agr_curation._lookup_response(
        method="get_gene_by_exact_symbol",
        data=[
            {"curie": "WB:WBGene00000001", "symbol": "abc"},
            {"curie": "FB:FBgn0000001", "symbol": "abc"},
        ],
        count=2,
        attempted_query={"method": "get_gene_by_exact_symbol", "gene_symbol": "abc"},
        exact_lookup=True,
    )
    assert ambiguous.lookup_status == "ambiguous"
    assert ambiguous.failure_classification == "ambiguous"

    transient = agr_curation._err(
        "database timed out",
        method="search_genes",
        attempted_query={"method": "search_genes", "gene_symbol": "abc"},
        failure_classification="transient",
        error=TimeoutError("timeout"),
    )
    assert transient.lookup_status == "transient"
    assert transient.lookup_attempts[0]["error"]["type"] == "TimeoutError"


def test_unwrap_function_tool_callable_raises_for_missing_callable():
    fake_tool = SimpleNamespace(on_invoke_tool=lambda: None)
    with pytest.raises(RuntimeError, match="Unable to locate callable"):
        agr_curation._unwrap_function_tool_callable(fake_tool, "missing")


def test_derive_optional_arg_keys_paths(monkeypatch):
    monkeypatch.setattr(
        agr_curation.agr_curation_query,
        "params_json_schema",
        {"properties": {"method": {}, "gene_symbol": {}, "limit": {}}},
        raising=False,
    )
    keys_from_schema = agr_curation._derive_agr_query_optional_arg_keys()
    assert keys_from_schema == ("gene_symbol", "limit")

    monkeypatch.setattr(agr_curation.agr_curation_query, "params_json_schema", {}, raising=False)
    monkeypatch.setattr(agr_curation, "_AGR_QUERY_CALLABLE", lambda method, term=None: None)
    keys_from_signature = agr_curation._derive_agr_query_optional_arg_keys()
    assert "term" in keys_from_signature
    assert "method" not in keys_from_signature

    monkeypatch.setattr(
        agr_curation.inspect,
        "signature",
        lambda _obj: (_ for _ in ()).throw(RuntimeError("sig fail")),
    )
    assert agr_curation._derive_agr_query_optional_arg_keys() == ()


def test_groq_wrapper_payload_handling(monkeypatch):
    captured = {}

    def _fake_query(method, **kwargs):
        captured["method"] = method
        captured["kwargs"] = kwargs
        return agr_curation.AgrQueryResult(status="ok", data={"method": method})

    monkeypatch.setattr(agr_curation, "_AGR_QUERY_CALLABLE", _fake_query)
    monkeypatch.setattr(agr_curation, "_AGR_QUERY_OPTIONAL_ARG_KEYS", ("gene_symbol", "limit"))
    tool = agr_curation.create_groq_agr_curation_query_tool()
    wrapped = agr_curation._unwrap_function_tool_callable(tool, "agr_curation_query_groq")

    result = wrapped(method="search_genes", payload_json='{"gene_symbol":"abc","limit":3}')
    assert result.status == "ok"
    assert captured["kwargs"] == {"gene_symbol": "abc", "limit": 3}

    bad_json = wrapped(method="search_genes", payload_json="{")
    assert bad_json.status == "error"
    assert "payload_json must be valid JSON object string" in bad_json.message

    non_object = wrapped(method="search_genes", payload_json='["a"]')
    assert non_object.status == "error"
    assert "payload_json must decode to a JSON object" in non_object.message

    empty_payload = wrapped(method="search_genes", payload_json="")
    assert empty_payload.status == "ok"
    assert captured["kwargs"] == {"gene_symbol": None, "limit": None}


def test_query_branch_unknown_and_exception(monkeypatch):
    query_fn = _unwrap_query_function(agr_curation.agr_curation_query)

    class Resolver:
        @staticmethod
        def get_db_client():
            return object()

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {"WB": "NCBITaxon:6239"})

    unknown = query_fn(method="unknown_method")
    assert unknown.status == "error"
    assert "Unknown method" in (unknown.message or "")

    class FailingResolver:
        @staticmethod
        def get_db_client():
            raise RuntimeError("db init failed")

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: FailingResolver())
    failed = query_fn(method="search_genes")
    assert failed.status == "error"
    assert "Query error: db init failed" in (failed.message or "")


def test_transient_lookup_exception_preserves_attempted_query(monkeypatch):
    query_fn = _unwrap_query_function(agr_curation.agr_curation_query)

    class FakeDb:
        @staticmethod
        def get_gene(_gene_id):
            raise TimeoutError("gene lookup timed out")

        @staticmethod
        def search_go_terms(**_kwargs):
            raise TimeoutError("GO lookup timed out")

    class Resolver:
        @staticmethod
        def get_db_client():
            return FakeDb()

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {"WB": "NCBITaxon:6239"})

    gene_result = query_fn(
        method="get_gene_by_id",
        gene_id="WB:WBGene00000001",
    )
    assert gene_result.status == "error"
    assert gene_result.lookup_status == "transient"
    assert gene_result.lookup_attempts[0]["attempted_query"] == {
        "method": "get_gene_by_id",
        "gene_id": "WB:WBGene00000001",
    }

    go_result = query_fn(
        method="search_go_terms",
        term="kinase activity",
        go_aspect="molecular_function",
        exact_match=True,
        include_synonyms=False,
        limit=7,
    )
    assert go_result.status == "error"
    assert go_result.lookup_status == "transient"
    assert go_result.lookup_attempts[0]["attempted_query"] == {
        "method": "search_go_terms",
        "term": "kinase activity",
        "go_aspect": "molecular_function",
        "exact_match": True,
        "include_synonyms": False,
        "limit": 7,
    }


def test_query_simple_methods(monkeypatch):
    query_fn = _unwrap_query_function(agr_curation.agr_curation_query)

    class _Display:
        def __init__(self, text):
            self.displayText = text

    gene = SimpleNamespace(
        primaryExternalId="WB:WBGene00000001",
        geneSymbol=_Display("unc-54"),
        geneFullName=_Display("myosin heavy chain"),
        taxon="NCBITaxon:6239",
        geneType={"name": "protein_coding_gene"},
        genomeLocations=[
            SimpleNamespace(chromosome="I", start=1, end=100, strand="+", assembly="WBcel235")
        ],
    )
    allele = SimpleNamespace(
        primaryExternalId="WB:WBVar00000001",
        alleleSymbol=_Display("e1370"),
        alleleFullName=_Display("e1370"),
        taxon="NCBITaxon:6239",
    )

    class FakeDb:
        @staticmethod
        def get_gene(gene_id):
            if gene_id == "WB:WBGene00000001":
                return gene
            return None

        @staticmethod
        def get_allele(allele_id):
            if allele_id == "WB:WBVar00000001":
                return allele
            return None

        @staticmethod
        def get_species():
            return [SimpleNamespace(abbreviation="WB", display_name="WormBase")]

        @staticmethod
        def get_data_providers():
            return [("WB", "NCBITaxon:6239")]

    class Resolver:
        @staticmethod
        def get_db_client():
            return FakeDb()

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {"WB": "NCBITaxon:6239"})

    assert query_fn(method="get_gene_by_id").status == "error"
    missing_gene = query_fn(method="get_gene_by_id", gene_id="WB:missing")
    assert missing_gene.status == "ok"
    assert "Gene not found" in (missing_gene.message or "")
    got_gene = query_fn(method="get_gene_by_id", gene_id="WB:WBGene00000001")
    assert got_gene.status == "ok"
    assert got_gene.data["genomic_location"]["chromosome"] == "I"

    assert query_fn(method="get_allele_by_id").status == "error"
    missing_allele = query_fn(method="get_allele_by_id", allele_id="WB:missing")
    assert missing_allele.status == "ok"
    assert "Allele not found" in (missing_allele.message or "")
    got_allele = query_fn(method="get_allele_by_id", allele_id="WB:WBVar00000001")
    assert got_allele.status == "ok"
    assert got_allele.data["symbol"] == "e1370"

    species = query_fn(method="get_species")
    assert species.status == "ok"
    assert species.count == 1
    providers = query_fn(method="get_data_providers")
    assert providers.status == "ok"
    assert providers.count == 1


def test_query_ontology_search_methods(monkeypatch):
    query_fn = _unwrap_query_function(agr_curation.agr_curation_query)

    class _Term:
        def __init__(self, curie, name, ontology_type=None, namespace=None):
            self.curie = curie
            self.name = name
            self.ontology_type = ontology_type
            self.namespace = namespace

    class FakeDb:
        @staticmethod
        def search_anatomy_terms(**_kwargs):
            return [_Term("WBbt:0001", "neuron", ontology_type="anatomy")]

        @staticmethod
        def search_life_stage_terms(**_kwargs):
            return [_Term("WBls:0001", "L4", ontology_type="life_stage")]

        @staticmethod
        def search_go_terms(**_kwargs):
            return [_Term("GO:0003674", "molecular function", namespace="molecular_function")]

    class Resolver:
        @staticmethod
        def get_db_client():
            return FakeDb()

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {"WB": "NCBITaxon:6239"})

    assert query_fn(method="search_anatomy_terms").status == "error"
    assert query_fn(method="search_life_stage_terms").status == "error"
    assert query_fn(method="search_go_terms").status == "error"

    anatomy = query_fn(method="search_anatomy_terms", term="neuron", data_provider="WB")
    assert anatomy.status == "ok"
    assert anatomy.count == 1
    life_stage = query_fn(method="search_life_stage_terms", term="L4", data_provider="WB")
    assert life_stage.status == "ok"
    assert life_stage.count == 1
    go = query_fn(method="search_go_terms", term="molecular")
    assert go.status == "ok"
    assert go.count == 1


def test_query_get_ontology_term_uses_package_lookup(monkeypatch):
    query_fn = _unwrap_query_function(agr_curation.agr_curation_query)

    class FakeDb:
        @staticmethod
        def get_ontology_term(curie):
            if curie == "DOID:0050434":
                return SimpleNamespace(
                    curie="DOID:0050434",
                    name="Andersen-Tawil syndrome",
                    ontology_type="DOTerm",
                )
            return None

    class Resolver:
        @staticmethod
        def get_db_client():
            return FakeDb()

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {"WB": "NCBITaxon:6239"})
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)

    result = query_fn(
        method="get_ontology_term",
        term="DOID:0050434",
        ontology_term_type="DOTerm",
    )
    assert result.status == "ok"
    assert result.lookup_status == "success"
    assert result.data == {
        "curie": "DOID:0050434",
        "curie_validated": True,
        "name": "Andersen-Tawil syndrome",
        "ontology_type": "DOTerm",
    }
    assert result.result_projections[0]["projection_type"] == "ontology_term_reference"
    assert result.lookup_attempts[0]["resolved_id"] == "DOID:0050434"

    missing = query_fn(
        method="get_ontology_term",
        term="DOID:0050434",
        ontology_term_type="CHEBITerm",
    )
    assert missing.status == "ok"
    assert missing.lookup_status == "not_found"
    assert missing.failure_classification == "not_found"
