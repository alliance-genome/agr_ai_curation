"""Unit tests for AGR curation provider mapping guard behavior."""

import importlib
import json
from pathlib import Path

import pytest

from src.lib import identifier_validation
from src.lib.openai_agents.tools import agr_curation


@pytest.fixture(autouse=True)
def _seed_runtime_identifier_prefixes(monkeypatch, tmp_path: Path):
    identifier_validation.load_prefixes.cache_clear()
    prefix_file = (
        tmp_path / "runtime" / "state" / "identifier_prefixes" / "identifier_prefixes.json"
    )
    prefix_file.parent.mkdir(parents=True, exist_ok=True)
    prefix_file.write_text(json.dumps({"prefixes": ["FB", "WB"]}), encoding="utf-8")
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(tmp_path / "runtime"))
    yield
    identifier_validation.load_prefixes.cache_clear()


def test_non_provider_method_does_not_require_mapping(monkeypatch):
    """Methods that do not fan out by provider should not fail on missing map."""
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {})
    monkeypatch.setattr(agr_curation, "_GROUP_MAPPING_LOAD_ERROR", "missing file")

    result = agr_curation._ensure_provider_mappings("get_gene_by_id")

    assert result is None


def test_provider_method_requires_mapping(monkeypatch):
    """Provider-fanout methods should return a clear error when map is missing."""
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {})
    monkeypatch.setattr(agr_curation, "_GROUP_MAPPING_LOAD_ERROR", "missing file")

    result = agr_curation._ensure_provider_mappings("search_genes")

    assert result is not None
    assert result.status == "error"
    assert "Provider mappings are unavailable" in result.message
    assert "missing file" in result.message


def test_provider_bulk_method_requires_mapping(monkeypatch):
    """Bulk provider methods should also require provider mappings."""
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {})
    monkeypatch.setattr(agr_curation, "_GROUP_MAPPING_LOAD_ERROR", "missing file")

    result = agr_curation._ensure_provider_mappings("search_genes_bulk")

    assert result is not None
    assert result.status == "error"
    assert "Provider mappings are unavailable" in result.message


def test_provider_method_succeeds_when_mapping_present(monkeypatch):
    """Provider-fanout methods should proceed when mapping is available."""
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {"WB": "NCBITaxon:6239"})
    monkeypatch.setattr(agr_curation, "_GROUP_MAPPING_LOAD_ERROR", None)

    result = agr_curation._ensure_provider_mappings("search_alleles")

    assert result is None


def test_tool_schema_requires_only_method():
    """With strict_mode=False, only 'method' is required (optional params are truly optional)."""
    schema = getattr(agr_curation.agr_curation_query, "params_json_schema", {}) or {}
    assert schema.get("required") == ["method"]


def test_optional_arg_keys_are_derived_from_tool_contract():
    """Forwarded payload keys should be derived, not manually duplicated."""
    assert "gene_symbol" in agr_curation._AGR_QUERY_OPTIONAL_ARG_KEYS
    assert "allele_symbol" in agr_curation._AGR_QUERY_OPTIONAL_ARG_KEYS
    assert "method" not in agr_curation._AGR_QUERY_OPTIONAL_ARG_KEYS


def test_groq_wrapper_schema_uses_compact_required_fields():
    """Groq wrapper should expose an all-required compact schema."""
    tool = agr_curation.create_groq_agr_curation_query_tool()
    schema = getattr(tool, "params_json_schema", {}) or {}
    assert sorted((schema.get("properties") or {}).keys()) == ["method", "payload_json"]
    assert schema.get("required") == ["method", "payload_json"]


def test_groq_wrapper_forwards_payload_json(monkeypatch):
    """Groq wrapper should decode payload_json and pass kwargs to AGR query callable."""
    captured = {}

    def _fake_query(method, **kwargs):
        captured["method"] = method
        captured["kwargs"] = kwargs
        return agr_curation.AgrQueryResult(status="ok", data={"method": method})

    monkeypatch.setattr(agr_curation, "_AGR_QUERY_CALLABLE", _fake_query)
    tool = agr_curation.create_groq_agr_curation_query_tool()
    wrapped = agr_curation._unwrap_function_tool_callable(tool, "agr_curation_query_groq")

    result = wrapped(
        method="search_genes",
        payload_json='{"gene_symbol":"norpA","data_provider":"FB","limit":10}',
    )

    assert result.status == "ok"
    assert captured["method"] == "search_genes"
    assert captured["kwargs"]["gene_symbol"] == "norpA"
    assert captured["kwargs"]["data_provider"] == "FB"
    assert captured["kwargs"]["limit"] == 10
    assert captured["kwargs"]["allele_symbol"] is None


def _unwrap_function_tool(tool):
    """Extract decorated function from FunctionTool wrapper for unit testing."""
    return agr_curation._unwrap_function_tool_callable(tool, "agr_curation_query")


def test_module_load_fallback_on_missing_groups_file(monkeypatch):
    """Module should not crash when groups config path is invalid at import time."""
    from src.lib.config import groups_loader as gl_module

    module = importlib.import_module("src.lib.openai_agents.tools.agr_curation")
    monkeypatch.setenv("GROUPS_CONFIG_PATH", "/tmp/does-not-exist-groups.yaml")
    gl_module.reset_cache()
    # Force DEFAULT_GROUPS_PATH to update for the reload
    monkeypatch.setattr(gl_module, "DEFAULT_GROUPS_PATH", gl_module._get_default_groups_path())
    reloaded = importlib.reload(module)

    assert reloaded.PROVIDER_TO_TAXON == {}
    assert reloaded._GROUP_MAPPING_LOAD_ERROR is not None
    monkeypatch.delenv("GROUPS_CONFIG_PATH", raising=False)
    gl_module.reset_cache()
    importlib.reload(module)


def test_query_returns_db_not_configured_error(monkeypatch):
    """agr_curation_query should return clear error when DB resolver has no client."""
    query_fn = _unwrap_function_tool(agr_curation.agr_curation_query)

    class Resolver:
        @staticmethod
        def get_db_client():
            return None

        @staticmethod
        def get_connection_url():
            return None

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    result = query_fn(method="search_genes", gene_symbol="abc")

    assert result.status == "error"
    assert "not configured" in (result.message or "").lower()


def test_query_returns_runtime_unavailable_error_when_db_is_configured(monkeypatch):
    """agr_curation_query should distinguish configured DBs from missing runtime dependencies."""
    query_fn = _unwrap_function_tool(agr_curation.agr_curation_query)

    class Resolver:
        @staticmethod
        def get_db_client():
            return None

        @staticmethod
        def get_connection_url():
            return "postgresql://db.invalid:5432/agr"

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    result = query_fn(method="search_genes", gene_symbol="abc")

    assert result.status == "error"
    assert "configured" in (result.message or "").lower()
    assert "runtime" in (result.message or "").lower()


def test_query_returns_mapping_error_for_provider_methods(monkeypatch):
    """agr_curation_query should fail fast on provider methods when mapping is unavailable."""
    query_fn = _unwrap_function_tool(agr_curation.agr_curation_query)

    class Resolver:
        @staticmethod
        def get_db_client():
            return object()

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {})
    monkeypatch.setattr(agr_curation, "_GROUP_MAPPING_LOAD_ERROR", "missing groups.yaml")

    result = query_fn(method="search_genes", gene_symbol="abc")

    assert result.status == "error"
    assert "provider mappings are unavailable" in (result.message or "").lower()


def test_query_search_genes_bulk_returns_per_symbol_results(monkeypatch):
    """search_genes_bulk should return list-in/list-out payload in one tool call."""
    query_fn = _unwrap_function_tool(agr_curation.agr_curation_query)

    class _Display:
        def __init__(self, text):
            self.displayText = text

    class _Gene:
        def __init__(self, curie, symbol, name):
            self.primaryExternalId = curie
            self.geneSymbol = _Display(symbol)
            self.geneFullName = _Display(name)
            self.geneType = None

    gene_rows = {
        "FB:FBgn0000117": _Gene("FB:FBgn0000117", "crb", "crumbs"),
        "FB:FBgn0002942": _Gene("FB:FBgn0002942", "ninaE", "neither inactivation nor afterpotential E"),
    }

    class FakeDb:
        @staticmethod
        def search_entities(entity_type, search_pattern, taxon_curie, include_synonyms, limit):
            if entity_type != "gene" or taxon_curie != "NCBITaxon:7227":
                return []
            if search_pattern == "crb":
                return [{"entity_curie": "FB:FBgn0000117", "entity": "crb", "match_type": "exact"}]
            if search_pattern == "ninaE":
                return [{"entity_curie": "FB:FBgn0002942", "entity": "ninaE", "match_type": "exact"}]
            return []

        @staticmethod
        def get_gene(curie):
            return gene_rows.get(curie)

    class Resolver:
        @staticmethod
        def get_db_client():
            return FakeDb()

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {"FB": "NCBITaxon:7227"})
    monkeypatch.setattr(agr_curation, "_GROUP_MAPPING_LOAD_ERROR", None)

    result = query_fn(
        method="search_genes_bulk",
        gene_symbols=["crb", "ninaE"],
        data_provider="FB",
        limit=10,
    )

    assert result.status == "ok"
    assert result.data["method"] == "search_genes_bulk"
    assert result.data["requested_count"] == 2
    assert len(result.data["items"]) == 2
    assert result.data["items"][0]["status"] == "ok"
    assert result.data["items"][0]["count"] == 1
    assert result.data["items"][1]["status"] == "ok"
    assert result.data["items"][1]["count"] == 1


def test_query_search_alleles_bulk_includes_validation_warning_items(monkeypatch):
    """search_alleles_bulk should surface validation warnings per input item."""
    query_fn = _unwrap_function_tool(agr_curation.agr_curation_query)

    class _Display:
        def __init__(self, text):
            self.displayText = text

    class _Allele:
        def __init__(self, curie, symbol, name):
            self.primaryExternalId = curie
            self.alleleSymbol = _Display(symbol)
            self.alleleFullName = _Display(name)
            self.taxon = "NCBITaxon:7227"

    allele_rows = {
        "FB:FBal0000001": _Allele("FB:FBal0000001", "e1370", "e1370"),
    }

    class FakeDb:
        @staticmethod
        def search_entities(entity_type, search_pattern, taxon_curie, include_synonyms, limit):
            if entity_type != "allele" or taxon_curie != "NCBITaxon:7227":
                return []
            if search_pattern == "e1370":
                return [{"entity_curie": "FB:FBal0000001", "entity": "e1370", "match_type": "exact"}]
            return []

        @staticmethod
        def get_allele(curie):
            return allele_rows.get(curie)

    class Resolver:
        @staticmethod
        def get_db_client():
            return FakeDb()

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {"FB": "NCBITaxon:7227"})
    monkeypatch.setattr(agr_curation, "_GROUP_MAPPING_LOAD_ERROR", None)

    result = query_fn(
        method="search_alleles_bulk",
        allele_symbols=["e1370", "w +/+"],
        data_provider="FB",
        limit=10,
    )

    assert result.status == "ok"
    assert result.data["method"] == "search_alleles_bulk"
    assert len(result.data["items"]) == 2
    assert result.data["items"][0]["status"] == "ok"
    assert result.data["items"][1]["status"] == "validation_warning"


def test_search_genes_bulk_uses_batched_detail_lookup(monkeypatch):
    """Gene bulk query should aggregate CURIE detail lookup into one batched call per taxon."""
    query_fn = _unwrap_function_tool(agr_curation.agr_curation_query)
    captured = {"calls": []}

    class FakeDb:
        @staticmethod
        def search_entities(entity_type, search_pattern, taxon_curie, include_synonyms, limit):
            _ = include_synonyms, limit
            if entity_type != "gene" or taxon_curie != "NCBITaxon:7227":
                return []
            if search_pattern == "crb":
                return [{"entity_curie": "FB:FBgn0000117", "entity": "crb", "match_type": "exact"}]
            if search_pattern == "ninaE":
                return [{"entity_curie": "FB:FBgn0002942", "entity": "ninaE", "match_type": "exact"}]
            return []

        @staticmethod
        def get_gene(_curie):
            raise AssertionError("Per-CURIE get_gene should not be used in batched bulk path")

    class Resolver:
        @staticmethod
        def get_db_client():
            return FakeDb()

    def _fake_batch_fetch(_db, curies):
        captured["calls"].append(list(curies))
        return {
            "FB:FBgn0000117": {
                "curie": "FB:FBgn0000117",
                "symbol": "crb",
                "name": "crumbs",
                "taxon": "NCBITaxon:7227",
                "gene_type": "protein_coding_gene",
            },
            "FB:FBgn0002942": {
                "curie": "FB:FBgn0002942",
                "symbol": "ninaE",
                "name": "neither inactivation nor afterpotential E",
                "taxon": "NCBITaxon:7227",
                "gene_type": "protein_coding_gene",
            },
        }

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {"FB": "NCBITaxon:7227"})
    monkeypatch.setattr(agr_curation, "_GROUP_MAPPING_LOAD_ERROR", None)
    monkeypatch.setattr(agr_curation, "_fetch_gene_details_bulk", _fake_batch_fetch)

    result = query_fn(
        method="search_genes_bulk",
        gene_symbols=["crb", "ninaE"],
        data_provider="FB",
        limit=10,
    )

    assert result.status == "ok"
    assert len(captured["calls"]) == 1
    assert set(captured["calls"][0]) == {"FB:FBgn0000117", "FB:FBgn0002942"}
    assert result.data["items"][0]["results"][0]["name"] == "crumbs"
    assert result.data["items"][1]["results"][0]["symbol"] == "ninaE"


def test_search_alleles_bulk_uses_batched_detail_lookup(monkeypatch):
    """Allele bulk query should aggregate CURIE detail lookup into one batched call per taxon."""
    query_fn = _unwrap_function_tool(agr_curation.agr_curation_query)
    captured = {"calls": []}

    class FakeDb:
        @staticmethod
        def search_entities(entity_type, search_pattern, taxon_curie, include_synonyms, limit):
            _ = include_synonyms, limit
            if entity_type != "allele" or taxon_curie != "NCBITaxon:7227":
                return []
            if search_pattern == "e1370":
                return [{"entity_curie": "FB:FBal0000001", "entity": "e1370", "match_type": "exact"}]
            if search_pattern == "let-23":
                return [{"entity_curie": "FB:FBal0000002", "entity": "let-23", "match_type": "exact"}]
            return []

        @staticmethod
        def get_allele(_curie):
            raise AssertionError("Per-CURIE get_allele should not be used in batched bulk path")

    class Resolver:
        @staticmethod
        def get_db_client():
            return FakeDb()

    def _fake_batch_fetch(_db, curies):
        captured["calls"].append(list(curies))
        return {
            "FB:FBal0000001": {
                "curie": "FB:FBal0000001",
                "symbol": "e1370",
                "name": "e1370",
                "taxon": "NCBITaxon:7227",
            },
            "FB:FBal0000002": {
                "curie": "FB:FBal0000002",
                "symbol": "let-23",
                "name": "let-23",
                "taxon": "NCBITaxon:7227",
            },
        }

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {"FB": "NCBITaxon:7227"})
    monkeypatch.setattr(agr_curation, "_GROUP_MAPPING_LOAD_ERROR", None)
    monkeypatch.setattr(agr_curation, "_fetch_allele_details_bulk", _fake_batch_fetch)

    result = query_fn(
        method="search_alleles_bulk",
        allele_symbols=["e1370", "let-23"],
        data_provider="FB",
        limit=10,
    )

    assert result.status == "ok"
    assert len(captured["calls"]) == 1
    assert set(captured["calls"][0]) == {"FB:FBal0000001", "FB:FBal0000002"}
    assert result.data["items"][0]["results"][0]["curie"] == "FB:FBal0000001"
    assert result.data["items"][1]["results"][0]["symbol"] == "let-23"
