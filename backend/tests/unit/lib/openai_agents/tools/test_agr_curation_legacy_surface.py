"""Guardrails for the frozen legacy backend AGR curation tool surface."""

import ast
import inspect
from pathlib import Path

from src.lib.openai_agents.tools import agr_curation


def _agr_query_method_literals() -> set[str]:
    source = Path(agr_curation.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    query_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "agr_curation_query"
    )

    methods: set[str] = set()
    for node in ast.walk(query_function):
        if not isinstance(node, ast.Compare):
            continue
        if not isinstance(node.left, ast.Name) or node.left.id != "method":
            continue
        for op, comparator in zip(node.ops, node.comparators):
            if isinstance(op, ast.Eq) and isinstance(comparator, ast.Constant):
                if isinstance(comparator.value, str):
                    methods.add(comparator.value)
            elif isinstance(op, ast.In) and isinstance(comparator, (ast.Set, ast.Tuple)):
                methods.update(
                    item.value
                    for item in comparator.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                )
    return methods


def test_backend_agr_curation_module_is_marked_legacy_compatibility_only():
    module_doc = agr_curation.__doc__ or ""

    assert "Legacy compatibility-only" in module_doc
    assert (
        agr_curation.LEGACY_AGR_CURATION_QUERY_PACKAGE_TOOL
        == "packages/alliance/python/src/agr_ai_curation_alliance/tools/agr_curation.py"
    )
    assert (
        agr_curation.LEGACY_AGR_CURATION_QUERY_PACKAGE_BINDINGS
        == "packages/alliance/tools/bindings.yaml"
    )


def test_backend_agr_curation_dispatch_is_frozen_to_legacy_allowlist():
    """Adding backend method dispatch requires changing this explicit allowlist."""
    assert _agr_query_method_literals() == set(
        agr_curation.LEGACY_AGR_CURATION_QUERY_SUPPORTED_METHODS
    )


def test_all458_helper_methods_are_package_owned_not_backend_dispatch_methods():
    assert _agr_query_method_literals().isdisjoint(
        agr_curation.PACKAGE_OWNED_AGR_CURATION_HELPER_METHODS
    )


def test_package_owned_helper_call_returns_legacy_boundary_error(monkeypatch):
    query_fn = agr_curation._unwrap_function_tool_callable(
        agr_curation.agr_curation_query,
        "agr_curation_query",
    )

    class Resolver:
        @staticmethod
        def get_db_client():
            return object()

    monkeypatch.setattr(agr_curation, "get_curation_resolver", lambda: Resolver())

    result = query_fn(method="search_ontology_terms", term="neuron")

    assert result.status == "error"
    assert "package-owned" in (result.message or "")
    assert agr_curation.LEGACY_AGR_CURATION_QUERY_PACKAGE_TOOL in (result.message or "")
    assert agr_curation.LEGACY_AGR_CURATION_QUERY_PACKAGE_BINDINGS in (result.message or "")


def test_all458_helper_inputs_are_not_added_to_legacy_backend_tool_signature():
    signature = inspect.signature(agr_curation._AGR_QUERY_CALLABLE)

    assert set(signature.parameters).isdisjoint(
        agr_curation.PACKAGE_OWNED_AGR_CURATION_HELPER_ARGS
    )


def test_all458_helper_methods_remain_in_package_owned_alliance_tool():
    repo_root = Path(agr_curation.__file__).resolve().parents[5]
    package_tool_path = repo_root / agr_curation.LEGACY_AGR_CURATION_QUERY_PACKAGE_TOOL
    package_bindings_path = repo_root / agr_curation.LEGACY_AGR_CURATION_QUERY_PACKAGE_BINDINGS

    package_source = package_tool_path.read_text(encoding="utf-8")
    package_bindings = package_bindings_path.read_text(encoding="utf-8")

    for method in agr_curation.PACKAGE_OWNED_AGR_CURATION_HELPER_METHODS:
        assert method in package_source
    assert (
        "callable: agr_ai_curation_alliance.tools.agr_curation:agr_curation_query"
        in package_bindings
    )
