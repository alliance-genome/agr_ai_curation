"""Query-path tests for agr_curation_query gene/allele branches."""

from types import SimpleNamespace

from src.lib.openai_agents.tools import agr_curation


def _unwrap_query_function(tool):
    """Extract wrapped agr_curation_query callable for direct unit testing."""
    return agr_curation._unwrap_function_tool_callable(tool, "agr_curation_query")


def _valid_validation():
    return SimpleNamespace(is_valid=True, warning_message=None)


def _invalid_validation(msg):
    return SimpleNamespace(is_valid=False, warning_message=msg)


def test_get_gene_by_exact_symbol_branches(monkeypatch):
    query_fn = _unwrap_query_function(agr_curation.agr_curation_query)

    class _Display:
        def __init__(self, text):
            self.displayText = text

    gene = SimpleNamespace(
        primaryExternalId="BAD:0001",
        geneSymbol=_Display("crb"),
        geneFullName=_Display("crumbs"),
        geneType={"name": "protein_coding_gene"},
    )

    class FakeDb:
        @staticmethod
        def map_entity_names_to_curies(entity_type, entity_names, taxon_curie):
            if entity_type == "gene" and taxon_curie == "NCBITaxon:7227":
                return [{"entity_curie": "BAD:0001", "entity": entity_names[0]}]
            return []

        @staticmethod
        def get_gene(_curie):
            return gene

    class Resolver:
        @staticmethod
        def get_db_client():
            return FakeDb()

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {"FB": "NCBITaxon:7227", "WB": "NCBITaxon:6239"})
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda curie: curie.startswith("FB:"))

    missing = query_fn(method="get_gene_by_exact_symbol")
    assert missing.status == "error"
    assert "requires gene_symbol" in (missing.message or "")

    unknown_provider = query_fn(
        method="get_gene_by_exact_symbol",
        gene_symbol="crb",
        data_provider="BAD",
    )
    assert unknown_provider.status == "error"
    assert "Unknown data_provider" in (unknown_provider.message or "")

    prefixed = query_fn(method="get_gene_by_exact_symbol", gene_symbol="FB:crb")
    assert prefixed.status == "ok"
    assert prefixed.count == 1
    assert prefixed.data[0]["taxon"] == "NCBITaxon:7227"
    assert "invalid_curie_prefixes:1" in (prefixed.warnings or [])


def test_get_gene_by_exact_symbol_detail_fetch_failure_is_transient(monkeypatch):
    query_fn = _unwrap_query_function(agr_curation.agr_curation_query)

    class FakeDb:
        @staticmethod
        def map_entity_names_to_curies(entity_type, entity_names, taxon_curie):
            if entity_type == "gene" and taxon_curie == "NCBITaxon:6239":
                return [{"entity_curie": "WB:WBGene00000001", "entity": entity_names[0]}]
            return []

        @staticmethod
        def get_gene(_curie):
            raise TimeoutError("detail timeout")

    class Resolver:
        @staticmethod
        def get_db_client():
            return FakeDb()

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {"WB": "NCBITaxon:6239"})
    monkeypatch.setattr(agr_curation, "TAXON_TO_PROVIDER", {"NCBITaxon:6239": "WB"})

    result = query_fn(
        method="get_gene_by_exact_symbol",
        gene_symbol="unc-54",
        data_provider="WB",
    )

    assert result.status == "ok"
    assert result.lookup_status == "transient"
    assert result.failure_classification == "transient"
    assert result.lookup_attempts[0]["lookup_status"] == "success"
    assert result.lookup_attempts[0]["resolved_id"] == "WB:WBGene00000001"
    assert result.lookup_attempts[0]["resolved_label"] == "unc-54"
    detail_attempt = result.lookup_attempts[1]
    assert detail_attempt["lookup_status"] == "transient"
    assert detail_attempt["attempted_query"]["lookup_stage"] == "fetch_gene_details"
    assert detail_attempt["attempted_query"]["gene_id"] == "WB:WBGene00000001"
    assert detail_attempt["target_projection"]["resolved_id"] == "WB:WBGene00000001"
    assert detail_attempt["error"]["type"] == "TimeoutError"


def test_search_genes_validation_force_and_success_paths(monkeypatch):
    query_fn = _unwrap_query_function(agr_curation.agr_curation_query)

    class _Display:
        def __init__(self, text):
            self.displayText = text

    gene = SimpleNamespace(
        primaryExternalId="BAD:0002",
        geneSymbol=_Display("unc-54"),
        geneFullName=_Display("myosin heavy chain"),
    )

    class FakeDb:
        @staticmethod
        def search_entities(entity_type, search_pattern, taxon_curie, include_synonyms, limit):
            _ = include_synonyms, limit
            if entity_type != "gene":
                return []
            if taxon_curie == "NCBITaxon:6239":
                return [{"entity_curie": "BAD:0002", "entity": search_pattern, "match_type": "exact"}]
            raise RuntimeError("taxon failed")

        @staticmethod
        def get_gene(curie):
            if curie == "BAD:0002":
                return gene
            return None

    class Resolver:
        @staticmethod
        def get_db_client():
            return FakeDb()

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {"WB": "NCBITaxon:6239", "FB": "NCBITaxon:7227"})
    monkeypatch.setattr(agr_curation, "enrich_with_match_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda curie: curie.startswith("WB:"))

    monkeypatch.setattr(agr_curation, "validate_search_symbol", lambda *_args, **_kwargs: _invalid_validation("bad symbol"))
    validation_warn = query_fn(method="search_genes", gene_symbol="bad symbol")
    assert validation_warn.status == "validation_warning"

    monkeypatch.setattr(agr_curation, "validate_search_symbol", lambda *_args, **_kwargs: _valid_validation())
    monkeypatch.setattr(agr_curation, "check_force_parameters", lambda *_args, **_kwargs: (False, "force reason required"))
    force_error = query_fn(method="search_genes", gene_symbol="unc-54", force=True, force_reason=None)
    assert force_error.status == "error"
    assert "force reason required" in (force_error.message or "")

    override_calls = []
    monkeypatch.setattr(agr_curation, "check_force_parameters", lambda *_args, **_kwargs: (True, None))
    monkeypatch.setattr(agr_curation, "log_validation_override", lambda *args, **_kwargs: override_calls.append(args))
    unknown_provider = query_fn(
        method="search_genes",
        gene_symbol="unc-54",
        force=True,
        force_reason="intentional",
        data_provider="BAD",
    )
    assert unknown_provider.status == "error"
    assert "Unknown data_provider" in (unknown_provider.message or "")
    assert override_calls

    success = query_fn(method="search_genes", gene_symbol="unc-54", include_synonyms=True, limit=5)
    assert success.status == "ok"
    assert success.count == 1
    assert success.data[0]["symbol"] == "unc-54"
    assert "invalid_curie_prefixes:1" in (success.warnings or [])


def test_allele_exact_and_search_paths(monkeypatch):
    query_fn = _unwrap_query_function(agr_curation.agr_curation_query)

    class _Display:
        def __init__(self, text):
            self.displayText = text

    allele = SimpleNamespace(
        primaryExternalId="BAD:0003",
        alleleSymbol=_Display("Arx<sup>tm1Gldn</sup>"),
        alleleFullName=_Display("targeted mutation, Jackson"),
        taxon="NCBITaxon:10090",
    )

    class FakeDb:
        @staticmethod
        def map_entity_names_to_curies(entity_type, entity_names, taxon_curie):
            if entity_type == "allele" and taxon_curie == "NCBITaxon:10090":
                return [
                    {"entity_curie": "BAD:0003", "entity": entity_names[0]},
                    {"entity_curie": "BAD:0003", "entity": entity_names[0]},
                ]
            return []

        @staticmethod
        def search_entities(entity_type, search_pattern, taxon_curie, include_synonyms, limit):
            _ = include_synonyms, limit
            if entity_type == "allele" and taxon_curie == "NCBITaxon:10090":
                return [{"entity_curie": "BAD:0003", "entity": search_pattern, "match_type": "exact"}]
            return []

        @staticmethod
        def get_allele(curie):
            if curie == "BAD:0003":
                return allele
            return None

    class Resolver:
        @staticmethod
        def get_db_client():
            return FakeDb()

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {"MGI": "NCBITaxon:10090"})
    monkeypatch.setattr(agr_curation, "TAXON_TO_PROVIDER", {"NCBITaxon:10090": "MGI"})
    monkeypatch.setattr(agr_curation, "enrich_with_match_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda curie: curie.startswith("MGI:"))

    missing_exact = query_fn(method="get_allele_by_exact_symbol")
    assert missing_exact.status == "error"

    unknown_provider = query_fn(
        method="get_allele_by_exact_symbol",
        allele_symbol="Arx<tm1Gldn>",
        data_provider="BAD",
    )
    assert unknown_provider.status == "error"
    assert "Unknown data_provider" in (unknown_provider.message or "")

    exact = query_fn(method="get_allele_by_exact_symbol", allele_symbol="Arx<tm1Gldn>", data_provider="MGI")
    assert exact.status == "ok"
    assert exact.count == 1
    assert exact.data[0]["matched_variant"] in {"Arx<tm1Gldn>", "Arx<sup>tm1Gldn</sup>", "Arxtm1Gldn"}
    assert "invalid_curie_prefixes:1" in (exact.warnings or [])

    monkeypatch.setattr(agr_curation, "validate_search_symbol", lambda *_args, **_kwargs: _invalid_validation("bad allele"))
    warn = query_fn(method="search_alleles", allele_symbol="bad allele", data_provider="MGI")
    assert warn.status == "validation_warning"

    monkeypatch.setattr(agr_curation, "validate_search_symbol", lambda *_args, **_kwargs: _valid_validation())
    monkeypatch.setattr(agr_curation, "check_force_parameters", lambda *_args, **_kwargs: (False, "reason needed"))
    force_err = query_fn(method="search_alleles", allele_symbol="e1370", force=True, data_provider="MGI")
    assert force_err.status == "error"
    assert "reason needed" in (force_err.message or "")

    monkeypatch.setattr(agr_curation, "check_force_parameters", lambda *_args, **_kwargs: (True, None))
    monkeypatch.setattr(agr_curation, "log_validation_override", lambda *_args, **_kwargs: None)
    search = query_fn(method="search_alleles", allele_symbol="e1370", data_provider="MGI", limit=5)
    assert search.status == "ok"
    assert search.count == 1
    assert search.data[0]["symbol"] == "Arx<sup>tm1Gldn</sup>"
    assert "invalid_curie_prefixes:1" in (search.warnings or [])


def test_get_allele_by_exact_symbol_detail_fetch_failure_is_transient(monkeypatch):
    query_fn = _unwrap_query_function(agr_curation.agr_curation_query)

    class FakeDb:
        @staticmethod
        def map_entity_names_to_curies(entity_type, entity_names, taxon_curie):
            if entity_type == "allele" and taxon_curie == "NCBITaxon:6239":
                return [{"entity_curie": "WB:WBVar00000001", "entity": entity_names[0]}]
            return []

        @staticmethod
        def get_allele(_curie):
            raise TimeoutError("detail timeout")

    class Resolver:
        @staticmethod
        def get_db_client():
            return FakeDb()

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {"WB": "NCBITaxon:6239"})
    monkeypatch.setattr(agr_curation, "TAXON_TO_PROVIDER", {"NCBITaxon:6239": "WB"})

    result = query_fn(
        method="get_allele_by_exact_symbol",
        allele_symbol="e1370",
        data_provider="WB",
    )

    assert result.status == "ok"
    assert result.lookup_status == "transient"
    assert result.failure_classification == "transient"
    assert result.lookup_attempts[0]["lookup_status"] == "success"
    assert result.lookup_attempts[0]["resolved_id"] == "WB:WBVar00000001"
    assert result.lookup_attempts[0]["resolved_label"] == "e1370"
    detail_attempt = result.lookup_attempts[1]
    assert detail_attempt["lookup_status"] == "transient"
    assert detail_attempt["attempted_query"]["lookup_stage"] == "fetch_allele_details"
    assert detail_attempt["attempted_query"]["allele_id"] == "WB:WBVar00000001"
    assert detail_attempt["target_projection"]["resolved_id"] == "WB:WBVar00000001"
    assert detail_attempt["error"]["type"] == "TimeoutError"
