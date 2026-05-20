"""Query-path tests for package-owned agr_curation_query gene/allele branches."""

from types import SimpleNamespace

from agr_ai_curation_alliance.tools import agr_curation


def _unwrap_query_function(tool):
    """Extract wrapped agr_curation_query callable for direct unit testing."""
    return agr_curation._unwrap_function_tool_callable(tool, "agr_curation_query")


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


def test_get_gene_by_exact_symbol_filters_obsolete_internal_gene_rows(monkeypatch):
    query_fn = _unwrap_query_function(agr_curation.agr_curation_query)

    class _Display:
        def __init__(self, text):
            self.displayText = text

    # Keep the retired/internal Crumbs row in the fixture to mirror live FlyBase
    # history. The resolver must ignore it and return the current corpus-backed
    # crb identity, FB:FBgn0259685.
    old_gene = SimpleNamespace(
        primaryExternalId="FB:FBgn0000368",
        geneSymbol=_Display("crb"),
        geneFullName=_Display("crumbs"),
        geneType={"name": "gene"},
        obsolete=True,
        internal=True,
    )
    current_gene = SimpleNamespace(
        primaryExternalId="FB:FBgn0259685",
        geneSymbol=_Display("crb"),
        geneFullName=_Display("crumbs"),
        geneType={"name": "protein_coding_gene"},
        obsolete=False,
        internal=False,
    )

    class FakeDb:
        @staticmethod
        def map_entity_names_to_curies(entity_type, entity_names, taxon_curie):
            _ = entity_names
            if entity_type == "gene" and taxon_curie == "NCBITaxon:7227":
                return [
                    {"entity_curie": "FB:FBgn0000368", "entity": "crb"},
                    {"entity_curie": "FB:FBgn0259685", "entity": "crb"},
                ]
            return []

        @staticmethod
        def get_gene(curie):
            return {
                "FB:FBgn0000368": old_gene,
                "FB:FBgn0259685": current_gene,
            }.get(curie)

    class Resolver:
        @staticmethod
        def get_db_client():
            return FakeDb()

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {"FB": "NCBITaxon:7227"})
    monkeypatch.setattr(agr_curation, "TAXON_TO_PROVIDER", {"NCBITaxon:7227": "FB"})
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)

    result = query_fn(
        method="get_gene_by_exact_symbol",
        gene_symbol="crb",
        data_provider="FB",
    )

    assert result.status == "ok"
    assert result.count == 1
    assert result.lookup_status == "success"
    assert result.data[0]["curie"] == "FB:FBgn0259685"


def test_search_genes_detail_fetch_failure_is_transient(monkeypatch):
    query_fn = _unwrap_query_function(agr_curation.agr_curation_query)

    class FakeDb:
        @staticmethod
        def search_entities(entity_type, search_pattern, taxon_curie, include_synonyms, limit):
            _ = include_synonyms, limit
            if entity_type == "gene" and taxon_curie == "NCBITaxon:6239":
                return [
                    {
                        "entity_curie": "WB:WBGene00000001",
                        "entity": search_pattern,
                        "match_type": "exact",
                    }
                ]
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
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)
    result = query_fn(
        method="search_genes",
        gene_symbol="unc-54",
        data_provider="WB",
    )

    assert result.status == "ok"
    assert result.count == 0
    assert result.lookup_status == "transient"
    assert result.failure_classification == "transient"
    assert result.lookup_attempts[0]["lookup_status"] == "success"
    detail_attempt = result.lookup_attempts[1]
    assert detail_attempt["lookup_status"] == "transient"
    assert detail_attempt["attempted_query"]["lookup_stage"] == "fetch_gene_details"
    assert detail_attempt["attempted_query"]["gene_id"] == "WB:WBGene00000001"
    assert detail_attempt["target_projection"]["resolved_id"] == "WB:WBGene00000001"
    assert detail_attempt["target_projection"]["resolved_label"] == "unc-54"
    assert detail_attempt["error"]["type"] == "TimeoutError"


def test_search_genes_filters_obsolete_internal_gene_rows(monkeypatch):
    query_fn = _unwrap_query_function(agr_curation.agr_curation_query)

    class _Display:
        def __init__(self, text):
            self.displayText = text

    # Keep the retired/internal Crumbs row in the fixture to mirror live FlyBase
    # history. The resolver must ignore it and return the current corpus-backed
    # crb identity, FB:FBgn0259685.
    old_gene = SimpleNamespace(
        primaryExternalId="FB:FBgn0000368",
        geneSymbol=_Display("crb"),
        geneFullName=_Display("crumbs"),
        taxon="NCBITaxon:7227",
        geneType={"name": "gene"},
        obsolete=True,
        internal=True,
    )
    current_gene = SimpleNamespace(
        primaryExternalId="FB:FBgn0259685",
        geneSymbol=_Display("crb"),
        geneFullName=_Display("crumbs"),
        taxon="NCBITaxon:7227",
        geneType={"name": "protein_coding_gene"},
        obsolete=False,
        internal=False,
    )

    class FakeDb:
        @staticmethod
        def search_entities(entity_type, search_pattern, taxon_curie, include_synonyms, limit):
            _ = search_pattern, include_synonyms, limit
            if entity_type == "gene" and taxon_curie == "NCBITaxon:7227":
                return [
                    {
                        "entity_curie": "FB:FBgn0000368",
                        "entity": "crumbs",
                        "match_type": "exact",
                    },
                    {
                        "entity_curie": "FB:FBgn0259685",
                        "entity": "crumbs",
                        "match_type": "exact",
                    },
                ]
            return []

        @staticmethod
        def get_gene(curie):
            return {
                "FB:FBgn0000368": old_gene,
                "FB:FBgn0259685": current_gene,
            }.get(curie)

    class Resolver:
        @staticmethod
        def get_db_client():
            return FakeDb()

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {"FB": "NCBITaxon:7227"})
    monkeypatch.setattr(agr_curation, "TAXON_TO_PROVIDER", {"NCBITaxon:7227": "FB"})
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)
    result = query_fn(
        method="search_genes",
        gene_symbol="Crumbs",
        data_provider="FB",
    )

    assert result.status == "ok"
    assert result.count == 1
    assert result.lookup_status == "success"
    assert result.data[0]["curie"] == "FB:FBgn0259685"


def test_search_genes_records_batch_setup_failure_when_retry_succeeds(monkeypatch):
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
    )

    class FakeDb:
        @staticmethod
        def search_entities(entity_type, search_pattern, taxon_curie, include_synonyms, limit):
            _ = include_synonyms, limit
            if entity_type == "gene" and taxon_curie == "NCBITaxon:6239":
                return [
                    {
                        "entity_curie": "WB:WBGene00000001",
                        "entity": search_pattern,
                        "match_type": "exact",
                    }
                ]
            return []

        @staticmethod
        def create_session():
            raise RuntimeError("session factory down")

        @staticmethod
        def get_gene(_curie):
            return gene

    class Resolver:
        @staticmethod
        def get_db_client():
            return FakeDb()

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {"WB": "NCBITaxon:6239"})
    monkeypatch.setattr(agr_curation, "TAXON_TO_PROVIDER", {"NCBITaxon:6239": "WB"})
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)
    result = query_fn(
        method="search_genes",
        gene_symbol="unc-54",
        data_provider="WB",
    )

    assert result.status == "ok"
    assert result.count == 1
    assert result.lookup_status == "success"
    assert result.data[0]["symbol"] == "unc-54"
    detail_attempt = result.lookup_attempts[1]
    assert detail_attempt["lookup_status"] == "transient"
    assert detail_attempt["attempted_query"]["lookup_stage"] == "batch_setup_gene_details"
    assert detail_attempt["attempted_query"]["retry_strategy"] == "per_curie"
    assert detail_attempt["attempted_query"]["gene_id"] == "WB:WBGene00000001"
    assert detail_attempt["error"]["type"] == "RuntimeError"


def test_search_genes_bulk_detail_fetch_failure_is_transient(monkeypatch):
    query_fn = _unwrap_query_function(agr_curation.agr_curation_query)

    class FakeDb:
        @staticmethod
        def search_entities(entity_type, search_pattern, taxon_curie, include_synonyms, limit):
            _ = include_synonyms, limit
            if entity_type == "gene" and taxon_curie == "NCBITaxon:6239":
                return [
                    {
                        "entity_curie": "WB:WBGene00000001",
                        "entity": search_pattern,
                        "match_type": "exact",
                    }
                ]
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
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)
    result = query_fn(
        method="search_genes_bulk",
        gene_symbols=["unc-54"],
        data_provider="WB",
    )

    item = result.data["items"][0]
    assert result.data["resolution_status"] == "detail_failure"
    assert item["status"] == "detail_failure"
    assert item["count"] == 0
    assert item["lookup_status"] == "transient"
    assert item["failure_classification"] == "detail_failure"
    detail_attempt = item["lookup_attempts"][1]
    assert detail_attempt["lookup_status"] == "transient"
    assert detail_attempt["attempted_query"]["lookup_stage"] == "fetch_gene_details"
    assert detail_attempt["attempted_query"]["gene_id"] == "WB:WBGene00000001"
    assert detail_attempt["error"]["type"] == "TimeoutError"


def test_search_genes_bulk_search_failure_is_transient_failure(monkeypatch):
    query_fn = _unwrap_query_function(agr_curation.agr_curation_query)

    class FakeDb:
        @staticmethod
        def search_entities(entity_type, search_pattern, taxon_curie, include_synonyms, limit):
            _ = entity_type, search_pattern, taxon_curie, include_synonyms, limit
            raise TimeoutError("search timeout")

        @staticmethod
        def get_gene(_curie):
            raise AssertionError("search failure should not fetch details")

    class Resolver:
        @staticmethod
        def get_db_client():
            return FakeDb()

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {"WB": "NCBITaxon:6239"})
    monkeypatch.setattr(agr_curation, "TAXON_TO_PROVIDER", {"NCBITaxon:6239": "WB"})
    result = query_fn(
        method="search_genes_bulk",
        gene_symbols=["unc-54"],
        data_provider="WB",
    )

    item = result.data["items"][0]
    assert result.count == 0
    assert result.lookup_status == "transient"
    assert result.data["resolution_status"] == "transient_failure"
    assert result.data["status_counts"] == {"transient_failure": 1}
    assert item["status"] == "transient_failure"
    assert item["count"] == 0
    assert item["lookup_status"] == "transient"
    assert item["failure_classification"] == "transient"
    assert item["lookup_attempts"][0]["error"]["type"] == "TimeoutError"


def test_search_genes_queries_symbols_without_local_validation(monkeypatch):
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

    spaced = query_fn(method="search_genes", gene_symbol="bad symbol", data_provider="WB")
    assert spaced.status == "ok"
    assert spaced.count == 1
    assert spaced.lookup_attempts[0]["attempted_query"]["gene_symbol"] == "bad symbol"

    unknown_provider = query_fn(
        method="search_genes",
        gene_symbol="unc-54",
        data_provider="BAD",
    )
    assert unknown_provider.status == "error"
    assert "Unknown data_provider" in (unknown_provider.message or "")

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
    assert exact.data[0]["matched_variant"] == "Arx<tm1Gldn>"
    assert "invalid_curie_prefixes:1" in (exact.warnings or [])

    spaced_search = query_fn(method="search_alleles", allele_symbol="bad allele", data_provider="MGI", limit=5)
    assert spaced_search.status == "ok"
    assert spaced_search.count == 1
    assert spaced_search.lookup_attempts[0]["attempted_query"]["allele_symbol"] == "bad allele"

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


def test_search_alleles_detail_fetch_failure_is_transient(monkeypatch):
    query_fn = _unwrap_query_function(agr_curation.agr_curation_query)

    class FakeDb:
        @staticmethod
        def search_entities(entity_type, search_pattern, taxon_curie, include_synonyms, limit):
            _ = include_synonyms, limit
            if entity_type == "allele" and taxon_curie == "NCBITaxon:6239":
                return [
                    {
                        "entity_curie": "WB:WBVar00000001",
                        "entity": search_pattern,
                        "match_type": "exact",
                    }
                ]
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
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)

    result = query_fn(
        method="search_alleles",
        allele_symbol="e1370",
        data_provider="WB",
    )

    assert result.status == "ok"
    assert result.count == 0
    assert result.lookup_status == "transient"
    assert result.failure_classification == "transient"
    assert result.lookup_attempts[0]["lookup_status"] == "success"
    detail_attempt = result.lookup_attempts[1]
    assert detail_attempt["lookup_status"] == "transient"
    assert detail_attempt["attempted_query"]["lookup_stage"] == "fetch_allele_details"
    assert detail_attempt["attempted_query"]["allele_id"] == "WB:WBVar00000001"
    assert detail_attempt["target_projection"]["resolved_id"] == "WB:WBVar00000001"
    assert detail_attempt["target_projection"]["resolved_label"] == "e1370"
    assert detail_attempt["error"]["type"] == "TimeoutError"


def test_search_alleles_queries_flattened_flybase_notation_as_supplied(monkeypatch):
    query_fn = _unwrap_query_function(agr_curation.agr_curation_query)
    captured = {"patterns": []}

    class FakeDb:
        @staticmethod
        def search_entities(entity_type, search_pattern, taxon_curie, include_synonyms, limit):
            _ = include_synonyms, limit
            captured["patterns"].append(search_pattern)
            if (
                entity_type == "allele"
                and taxon_curie == "NCBITaxon:7227"
                and search_pattern == "N fa-g"
            ):
                return [
                    {
                        "entity_curie": "FB:FBal0012868",
                        "entity": "N fa-g",
                        "match_type": "exact",
                    }
                ]
            return []

    class Resolver:
        @staticmethod
        def get_db_client():
            return FakeDb()

    def _fake_batch_fetch(_db, curies):
        assert curies == ["FB:FBal0012868"]
        return {
            "FB:FBal0012868": {
                "curie": "FB:FBal0012868",
                "symbol": "N<sup>fa-g</sup>",
                "name": "Notch facet-glossy",
                "taxon": "NCBITaxon:7227",
            }
        }, {}

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {"FB": "NCBITaxon:7227"})
    monkeypatch.setattr(agr_curation, "TAXON_TO_PROVIDER", {"NCBITaxon:7227": "FB"})
    monkeypatch.setattr(agr_curation, "_GROUP_MAPPING_LOAD_ERROR", None)
    monkeypatch.setattr(agr_curation, "_fetch_allele_details_bulk", _fake_batch_fetch)
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)

    result = query_fn(
        method="search_alleles",
        allele_symbol="N fa-g",
        data_provider="FB",
        limit=5,
    )

    assert result.status == "ok"
    assert result.count == 1
    assert captured["patterns"] == ["N fa-g"]
    assert result.data[0]["curie"] == "FB:FBal0012868"
    assert result.data[0]["symbol"] == "N<sup>fa-g</sup>"


def test_search_alleles_queries_collapsed_flybase_notation_as_supplied(monkeypatch):
    query_fn = _unwrap_query_function(agr_curation.agr_curation_query)
    captured = {"patterns": []}

    class FakeDb:
        @staticmethod
        def search_entities(entity_type, search_pattern, taxon_curie, include_synonyms, limit):
            _ = include_synonyms, limit
            captured["patterns"].append(search_pattern)
            if (
                entity_type == "allele"
                and taxon_curie == "NCBITaxon:7227"
                and search_pattern == "Nfa-g"
            ):
                return [
                    {
                        "entity_curie": "FB:FBal0012868",
                        "entity": "Nfa-g",
                        "match_type": "exact",
                    }
                ]
            return []

    class Resolver:
        @staticmethod
        def get_db_client():
            return FakeDb()

    def _fake_batch_fetch(_db, curies):
        assert curies == ["FB:FBal0012868"]
        return {
            "FB:FBal0012868": {
                "curie": "FB:FBal0012868",
                "symbol": "N<sup>fa-g</sup>",
                "name": "Notch facet-glossy",
                "taxon": "NCBITaxon:7227",
            }
        }, {}

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {"FB": "NCBITaxon:7227"})
    monkeypatch.setattr(agr_curation, "TAXON_TO_PROVIDER", {"NCBITaxon:7227": "FB"})
    monkeypatch.setattr(agr_curation, "_GROUP_MAPPING_LOAD_ERROR", None)
    monkeypatch.setattr(agr_curation, "_fetch_allele_details_bulk", _fake_batch_fetch)
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)

    result = query_fn(
        method="search_alleles",
        allele_symbol="Nfa-g",
        data_provider="FB",
        limit=5,
    )

    assert result.status == "ok"
    assert result.count == 1
    assert captured["patterns"] == ["Nfa-g"]
    assert result.data[0]["curie"] == "FB:FBal0012868"


def test_search_alleles_uses_generic_db_fuzzy_fallback_when_api_search_misses(monkeypatch):
    query_fn = _unwrap_query_function(agr_curation.agr_curation_query)
    fallback_calls = []

    class FakeDb:
        @staticmethod
        def search_entities(entity_type, search_pattern, taxon_curie, include_synonyms, limit):
            _ = entity_type, search_pattern, taxon_curie, include_synonyms, limit
            return []

    class Resolver:
        @staticmethod
        def get_db_client():
            return FakeDb()

    def _fake_fuzzy_fallback(_db, *, search_pattern, taxon_curie, include_synonyms, limit):
        fallback_calls.append((search_pattern, taxon_curie, include_synonyms, limit))
        return [
            {
                "entity_curie": "FB:FBal0001817",
                "entity": "crb<sup>11A22</sup>",
                "match_type": "fuzzy_symbol",
                "score": 0.71,
            }
        ]

    def _fake_batch_fetch(_db, curies):
        assert curies == ["FB:FBal0001817"]
        return {
            "FB:FBal0001817": {
                "curie": "FB:FBal0001817",
                "symbol": "crb<sup>11A22</sup>",
                "name": None,
                "taxon": "NCBITaxon:7227",
            }
        }, {}

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {"FB": "NCBITaxon:7227"})
    monkeypatch.setattr(agr_curation, "TAXON_TO_PROVIDER", {"NCBITaxon:7227": "FB"})
    monkeypatch.setattr(agr_curation, "_GROUP_MAPPING_LOAD_ERROR", None)
    monkeypatch.setattr(agr_curation, "_fetch_allele_details_bulk", _fake_batch_fetch)
    monkeypatch.setattr(agr_curation, "_search_alleles_fuzzy_via_db", _fake_fuzzy_fallback)
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)

    result = query_fn(
        method="search_alleles",
        allele_symbol="crb 11A22",
        data_provider="FB",
        limit=5,
    )

    assert result.status == "ok"
    assert result.count == 1
    assert fallback_calls == [("crb 11A22", "NCBITaxon:7227", True, 5)]
    assert result.data[0]["curie"] == "FB:FBal0001817"
    assert result.data[0]["match_type"] == "fuzzy_symbol"


def test_search_alleles_records_batch_fetch_failure_when_retry_succeeds(monkeypatch):
    query_fn = _unwrap_query_function(agr_curation.agr_curation_query)
    closed = {"value": False}

    class _Display:
        def __init__(self, text):
            self.displayText = text

    allele = SimpleNamespace(
        primaryExternalId="WB:WBVar00000001",
        alleleSymbol=_Display("e1370"),
        alleleFullName=_Display("e1370"),
        taxon="NCBITaxon:6239",
    )

    class FakeSession:
        @staticmethod
        def execute(_query, _params):
            raise TimeoutError("batch execute down")

        @staticmethod
        def close():
            closed["value"] = True

    class FakeDb:
        @staticmethod
        def search_entities(entity_type, search_pattern, taxon_curie, include_synonyms, limit):
            _ = include_synonyms, limit
            if entity_type == "allele" and taxon_curie == "NCBITaxon:6239":
                return [
                    {
                        "entity_curie": "WB:WBVar00000001",
                        "entity": search_pattern,
                        "match_type": "exact",
                    }
                ]
            return []

        @staticmethod
        def create_session():
            return FakeSession()

        @staticmethod
        def get_allele(_curie):
            return allele

    class Resolver:
        @staticmethod
        def get_db_client():
            return FakeDb()

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {"WB": "NCBITaxon:6239"})
    monkeypatch.setattr(agr_curation, "TAXON_TO_PROVIDER", {"NCBITaxon:6239": "WB"})
    monkeypatch.setattr(agr_curation, "is_valid_curie", lambda _curie: True)

    result = query_fn(
        method="search_alleles",
        allele_symbol="e1370",
        data_provider="WB",
    )

    assert result.status == "ok"
    assert result.count == 1
    assert result.lookup_status == "success"
    assert result.data[0]["symbol"] == "e1370"
    assert closed["value"] is True
    detail_attempt = result.lookup_attempts[1]
    assert detail_attempt["lookup_status"] == "transient"
    assert detail_attempt["attempted_query"]["lookup_stage"] == "batch_fetch_allele_details"
    assert detail_attempt["attempted_query"]["retry_strategy"] == "per_curie"
    assert detail_attempt["attempted_query"]["allele_id"] == "WB:WBVar00000001"
    assert detail_attempt["error"]["type"] == "TimeoutError"


def test_search_alleles_bulk_detail_fetch_failure_is_transient(monkeypatch):
    query_fn = _unwrap_query_function(agr_curation.agr_curation_query)

    class FakeDb:
        @staticmethod
        def search_entities(entity_type, search_pattern, taxon_curie, include_synonyms, limit):
            _ = include_synonyms, limit
            if entity_type == "allele" and taxon_curie == "NCBITaxon:6239":
                return [
                    {
                        "entity_curie": "WB:WBVar00000001",
                        "entity": search_pattern,
                        "match_type": "exact",
                    }
                ]
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
        method="search_alleles_bulk",
        allele_symbols=["e1370"],
        data_provider="WB",
    )

    item = result.data["items"][0]
    assert result.data["resolution_status"] == "detail_failure"
    assert item["status"] == "detail_failure"
    assert item["count"] == 0
    assert item["lookup_status"] == "transient"
    assert item["failure_classification"] == "detail_failure"
    detail_attempt = item["lookup_attempts"][1]
    assert detail_attempt["lookup_status"] == "transient"
    assert detail_attempt["attempted_query"]["lookup_stage"] == "fetch_allele_details"
    assert detail_attempt["attempted_query"]["allele_id"] == "WB:WBVar00000001"
    assert detail_attempt["error"]["type"] == "TimeoutError"
