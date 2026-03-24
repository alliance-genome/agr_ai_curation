"""Regression guard: package __init__.py files must not import heavy runtime deps.

Importing ``agent_studio`` or ``curation_workspace`` for lightweight use (models,
validation, extraction results) must NOT pull in the OpenAI Agents SDK
(``agents.Agent``, ``agents.Runner``, etc.) or trigger flow-tool registration
side effects.  If this test fails it means someone added an eager import of a
heavy submodule back into one of the ``__init__.py`` files.

We use AST inspection rather than runtime imports to avoid SQLAlchemy MetaData
conflicts and missing-dependency issues in CI isolation.
"""

import ast
from pathlib import Path

_BACKEND_SRC = Path(__file__).resolve().parents[3] / "src"

# Submodules known to pull in the OpenAI Agents SDK (agents.Agent / Runner / RunConfig)
_AGENT_STUDIO_HEAVY = {"catalog_service", "flow_tools"}
_CURATION_WS_HEAVY = {"curation_prep_service", "pipeline"}


def _get_eager_relative_imports(init_path: Path) -> dict[str, list[str]]:
    """Return ``{submodule: [names]}`` for top-level ``from .submodule import ...`` statements.

    Only top-level (non-lazy, non-conditional) imports are captured.  Imports
    inside ``if`` blocks, ``try`` blocks, or function bodies are excluded.
    """
    source = init_path.read_text()
    tree = ast.parse(source, filename=str(init_path))
    result: dict[str, list[str]] = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            names = [alias.name for alias in node.names]
            result[node.module] = names
    return result


def _has_auto_registration_block(init_path: Path) -> bool:
    """Return True if the __init__.py has a top-level call to register_flow_tools().

    Uses ``ast.iter_child_nodes`` (top-level statements only) so that calls
    inside ``__getattr__`` or other functions are not falsely detected.
    """
    source = init_path.read_text()
    tree = ast.parse(source, filename=str(init_path))
    for node in ast.iter_child_nodes(tree):
        for inner in ast.walk(node) if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else []:
            if isinstance(inner, ast.Call):
                func = inner.func
                if isinstance(func, ast.Name) and func.id == "register_flow_tools":
                    return True
                if isinstance(func, ast.Attribute) and func.attr == "register_flow_tools":
                    return True
    return False


def _extract_lazy_import_keys(init_path: Path) -> set[str]:
    """Extract the string keys from the ``_LAZY_IMPORTS`` dict in *init_path*."""
    source = init_path.read_text()
    tree = ast.parse(source, filename=str(init_path))
    keys: set[str] = set()
    for node in ast.walk(tree):
        # Plain assignment: _LAZY_IMPORTS = { ... }
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_LAZY_IMPORTS":
                    if isinstance(node.value, ast.Dict):
                        for key in node.value.keys:
                            if isinstance(key, ast.Constant):
                                keys.add(key.value)
        # Annotated assignment: _LAZY_IMPORTS: dict[...] = { ... }
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == "_LAZY_IMPORTS":
                if isinstance(node.value, ast.Dict):
                    for key in node.value.keys:
                        if isinstance(key, ast.Constant):
                            keys.add(key.value)
    return keys


def _has_lazy_getattr(init_path: Path) -> bool:
    """Return True if the module defines a ``__getattr__`` function."""
    source = init_path.read_text()
    tree = ast.parse(source, filename=str(init_path))
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "__getattr__":
                return True
    return False


# -- agent_studio ---------------------------------------------------------

class TestAgentStudioThinInit:
    """``agent_studio/__init__.py`` must stay free of heavy eager imports."""

    INIT = _BACKEND_SRC / "lib" / "agent_studio" / "__init__.py"

    def test_no_heavy_eager_imports(self):
        """Heavy submodules (catalog_service, flow_tools) must not be eagerly imported."""
        imports = _get_eager_relative_imports(self.INIT)
        heavy_found = _AGENT_STUDIO_HEAVY & set(imports.keys())
        assert not heavy_found, (
            f"agent_studio/__init__.py eagerly imports heavy submodules: {heavy_found}"
        )

    def test_no_auto_registration_side_effect(self):
        """Flow tool registration must not run as a side effect of import."""
        assert not _has_auto_registration_block(self.INIT), (
            "agent_studio/__init__.py calls register_flow_tools() at module level"
        )

    def test_lazy_getattr_present(self):
        """A __getattr__ function must exist for lazy loading of heavy names."""
        assert _has_lazy_getattr(self.INIT), (
            "agent_studio/__init__.py is missing __getattr__ for lazy imports"
        )

    def test_lazy_imports_cover_heavy_names(self):
        """The _LAZY_IMPORTS dict must include all previously-exported heavy names."""
        lazy_keys = _extract_lazy_import_keys(self.INIT)

        expected_heavy = {
            "PromptCatalogService", "get_prompt_catalog", "get_prompt_key_for_agent",
            "register_flow_tools", "set_workflow_user_context", "clear_workflow_user_context",
            "get_current_user_id", "get_current_user_email",
            "set_current_flow_context", "clear_current_flow_context", "FLOW_AGENT_IDS",
        }
        missing = expected_heavy - lazy_keys
        assert not missing, (
            f"_LAZY_IMPORTS is missing entries for: {missing}"
        )


# -- curation_workspace ---------------------------------------------------

class TestCurationWorkspaceThinInit:
    """``curation_workspace/__init__.py`` must stay free of heavy eager imports."""

    INIT = _BACKEND_SRC / "lib" / "curation_workspace" / "__init__.py"

    def test_no_heavy_eager_imports(self):
        """Heavy submodules (curation_prep_service, pipeline) must not be eagerly imported."""
        imports = _get_eager_relative_imports(self.INIT)
        heavy_found = _CURATION_WS_HEAVY & set(imports.keys())
        assert not heavy_found, (
            f"curation_workspace/__init__.py eagerly imports heavy submodules: {heavy_found}"
        )

    def test_lazy_getattr_present(self):
        """A __getattr__ function must exist for lazy loading of heavy names."""
        assert _has_lazy_getattr(self.INIT), (
            "curation_workspace/__init__.py is missing __getattr__ for lazy imports"
        )

    def test_lazy_imports_cover_heavy_names(self):
        """The _LAZY_IMPORTS dict must include all previously-exported heavy names."""
        lazy_keys = _extract_lazy_import_keys(self.INIT)

        expected_heavy = {
            "CurationPrepPersistenceContext", "run_curation_prep",
            "run_post_curation_pipeline", "execute_post_curation_pipeline",
            "PipelineExecutionMode", "PipelineRunStatus",
        }
        missing = expected_heavy - lazy_keys
        assert not missing, (
            f"_LAZY_IMPORTS is missing entries for: {missing}"
        )
