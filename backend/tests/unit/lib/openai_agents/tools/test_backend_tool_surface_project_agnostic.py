"""Guardrails preventing backend-owned Alliance curation tool wrappers."""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[6]
BACKEND_TOOLS_DIR = REPO_ROOT / "backend/src/lib/openai_agents/tools"
PACKAGE_BINDINGS_PATH = REPO_ROOT / "packages/alliance/tools/bindings.yaml"
PACKAGE_TOOL_PATH = (
    REPO_ROOT
    / "packages/alliance/python/src/agr_ai_curation_alliance/tools/agr_curation.py"
)


def _direct_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = "." * node.level + module
            imports.add(module)
    return imports


def test_backend_openai_agent_tools_do_not_expose_alliance_curation_surface():
    forbidden_files = {
        "agr_curation.py",
        "agr_lookup.py",
        "search_helpers.py",
    }

    present_files = {path.name for path in BACKEND_TOOLS_DIR.glob("*.py")}

    assert present_files.isdisjoint(forbidden_files)


def test_backend_tools_package_does_not_export_or_import_agr_curation_query():
    init_path = BACKEND_TOOLS_DIR / "__init__.py"
    init_source = init_path.read_text(encoding="utf-8")

    assert "agr_curation_query" not in init_source
    assert ".agr_curation" not in _direct_imports(init_path)


def test_alliance_curation_query_is_package_owned_through_bindings():
    bindings_source = PACKAGE_BINDINGS_PATH.read_text(encoding="utf-8")

    assert PACKAGE_TOOL_PATH.exists()
    assert "tool_id: agr_curation_query" in bindings_source
    assert (
        "callable: agr_ai_curation_alliance.tools.agr_curation:agr_curation_query"
        in bindings_source
    )
    for tool_id in (
        "search_domain_field_terms",
        "inspect_ontology_term",
        "resolve_domain_field_term",
    ):
        assert f"tool_id: {tool_id}" in bindings_source
        assert (
            f"callable: agr_ai_curation_alliance.tools.agr_curation:{tool_id}"
            in bindings_source
        )
    assert "backend/src/lib/openai_agents/tools/agr_curation.py" not in bindings_source
