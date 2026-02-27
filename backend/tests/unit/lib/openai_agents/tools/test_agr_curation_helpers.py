"""Additional helper/branch tests for AGR curation tool."""

from types import SimpleNamespace

import pytest

from src.lib.openai_agents.tools import agr_curation


def _unwrap_query_function(tool):
    """Extract wrapped agr_curation_query callable for direct unit testing."""
    visited_ids = set()

    def _walk(candidate):
        if not callable(candidate):
            return None
        obj_id = id(candidate)
        if obj_id in visited_ids:
            return None
        visited_ids.add(obj_id)
        if getattr(candidate, "__name__", "") == "agr_curation_query":
            return candidate
        for cell in getattr(candidate, "__closure__", ()) or ():
            found = _walk(cell.cell_contents)
            if found is not None:
                return found
        return None

    found = _walk(getattr(tool, "on_invoke_tool", None))
    assert found is not None
    return found


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
