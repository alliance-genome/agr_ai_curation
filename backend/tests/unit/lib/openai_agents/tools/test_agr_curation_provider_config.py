"""Unit tests for AGR curation provider mapping guard behavior."""

import importlib

from src.lib.openai_agents.tools import agr_curation


def test_non_provider_method_does_not_require_mapping(monkeypatch):
    """Methods that do not fan out by provider should not fail on missing map."""
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {})
    monkeypatch.setattr(agr_curation, "_PROVIDER_MAPPING_LOAD_ERROR", "missing file")

    result = agr_curation._ensure_provider_mappings("get_gene_by_id")

    assert result is None


def test_provider_method_requires_mapping(monkeypatch):
    """Provider-fanout methods should return a clear error when map is missing."""
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {})
    monkeypatch.setattr(agr_curation, "_PROVIDER_MAPPING_LOAD_ERROR", "missing file")

    result = agr_curation._ensure_provider_mappings("search_genes")

    assert result is not None
    assert result.status == "error"
    assert "Provider mappings are unavailable" in result.message
    assert "missing file" in result.message


def test_provider_method_succeeds_when_mapping_present(monkeypatch):
    """Provider-fanout methods should proceed when mapping is available."""
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {"WB": "NCBITaxon:6239"})
    monkeypatch.setattr(agr_curation, "_PROVIDER_MAPPING_LOAD_ERROR", None)

    result = agr_curation._ensure_provider_mappings("search_alleles")

    assert result is None


def _unwrap_function_tool(tool):
    """Extract decorated function from FunctionTool wrapper for unit testing.

    The OpenAI Agents SDK wrapper shape can vary by environment/version, so
    we avoid hard-coded closure indexes and recursively search for the first
    callable named ``agr_curation_query``.
    """

    visited_ids = set()

    def _walk_callable(candidate):
        if not callable(candidate):
            return None
        obj_id = id(candidate)
        if obj_id in visited_ids:
            return None
        visited_ids.add(obj_id)

        if getattr(candidate, "__name__", "") == "agr_curation_query":
            return candidate

        for cell in getattr(candidate, "__closure__", ()) or ():
            found = _walk_callable(cell.cell_contents)
            if found is not None:
                return found
        return None

    found = _walk_callable(tool.on_invoke_tool)
    assert found is not None, "Unable to locate underlying agr_curation_query callable"
    return found


def test_module_load_fallback_on_missing_provider_file(monkeypatch):
    """Module should not crash when provider config path is invalid at import time."""
    monkeypatch.setenv("PROVIDERS_CONFIG_PATH", "/tmp/does-not-exist-providers.yaml")
    reloaded = importlib.reload(agr_curation)

    assert reloaded.PROVIDER_TO_TAXON == {}
    assert reloaded._PROVIDER_MAPPING_LOAD_ERROR is not None
    monkeypatch.delenv("PROVIDERS_CONFIG_PATH", raising=False)
    importlib.reload(agr_curation)


def test_query_returns_db_not_configured_error(monkeypatch):
    """agr_curation_query should return clear error when DB resolver has no client."""
    query_fn = _unwrap_function_tool(agr_curation.agr_curation_query)

    class Resolver:
        @staticmethod
        def get_db_client():
            return None

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    result = query_fn(method="search_genes", gene_symbol="abc")

    assert result.status == "error"
    assert "not configured" in (result.message or "").lower()


def test_query_returns_mapping_error_for_provider_methods(monkeypatch):
    """agr_curation_query should fail fast on provider methods when mapping is unavailable."""
    query_fn = _unwrap_function_tool(agr_curation.agr_curation_query)

    class Resolver:
        @staticmethod
        def get_db_client():
            return object()

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())
    monkeypatch.setattr(agr_curation, "PROVIDER_TO_TAXON", {})
    monkeypatch.setattr(agr_curation, "_PROVIDER_MAPPING_LOAD_ERROR", "missing providers.yaml")

    result = query_fn(method="search_genes", gene_symbol="abc")

    assert result.status == "error"
    assert "provider mappings are unavailable" in (result.message or "").lower()
