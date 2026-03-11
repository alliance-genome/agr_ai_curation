"""Unit tests for merged package-declared tool registry construction."""

from pathlib import Path

import pytest

from src.lib.packages.models import ExportKind, RuntimeOverrideSelection, RuntimeOverrides
from src.lib.packages.registry import load_package_registry
from src.lib.packages.tool_registry import (
    ToolRegistryValidationError,
    build_tool_registry,
    load_tool_registry,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
CORE_TOOLS_DIR = REPO_ROOT / "packages" / "core" / "python" / "src" / "agr_ai_curation_core" / "tools"


def _write_package(
    packages_dir: Path,
    *,
    directory_name: str,
    package_id: str,
    export_name: str = "default",
    export_description: str = "Default tool bindings",
    bindings_text: str,
) -> None:
    package_dir = packages_dir / directory_name
    (package_dir / "tools").mkdir(parents=True)
    (package_dir / "package.yaml").write_text(
        f"""package_id: {package_id}
display_name: {package_id} package
version: 1.0.0
package_api_version: 1.0.0
min_runtime_version: 1.0.0
max_runtime_version: 2.0.0
python_package_root: src/{package_id.replace('.', '_')}
requirements_file: requirements/runtime.txt
exports:
  - kind: tool_binding
    name: {export_name}
    path: tools/bindings.yaml
    description: {export_description}
""",
        encoding="utf-8",
    )
    (package_dir / "tools" / "bindings.yaml").write_text(
        bindings_text,
        encoding="utf-8",
    )


def _write_overrides(path: Path, *, package_id: str, export_name: str = "default") -> None:
    path.write_text(
        f"""overrides_api_version: 1.0.0
package_precedence:
  - {package_id}
selections:
  - export_kind: tool_binding
    name: {export_name}
    package_id: {package_id}
""",
        encoding="utf-8",
    )


def test_load_tool_registry_merges_unique_package_exports(tmp_path):
    packages_dir = tmp_path / "packages"
    _write_package(
        packages_dir,
        directory_name="agr-base",
        package_id="agr.base",
        export_description="Shared AGR tool bindings",
        bindings_text="""package_id: agr.base
bindings_api_version: 1.0.0
tools:
  - tool_id: agr_curation_query
    binding_kind: static
    callable: agr_base.tools.agr:agr_curation_query
    required_context: []
    description: AGR database query binding
    source_file: src/agr_base/tools/agr.py
""",
    )
    _write_package(
        packages_dir,
        directory_name="org-custom",
        package_id="org.custom",
        export_description="Document-scoped custom tools",
        bindings_text="""package_id: org.custom
bindings_api_version: 1.0.0
tools:
  - tool_id: search_document
    binding_kind: context_factory
    callable_factory: org_custom.tools.documents:create_search_document_tool
    required_context:
      - document_id
      - user_id
    source_file: src/org_custom/tools/documents.py
""",
    )

    registry = load_tool_registry(
        packages_dir,
        runtime_version="1.5.0",
        supported_package_api_version="1.0.0",
    )

    assert registry.validation_errors == ()
    assert set(registry.bindings_by_tool_id) == {"agr_curation_query", "search_document"}

    agr_binding = registry.get("agr_curation_query")
    assert agr_binding is not None
    assert agr_binding.binding_kind.value == "static"
    assert agr_binding.import_attribute_kind == "callable"
    assert agr_binding.import_path == "agr_base.tools.agr:agr_curation_query"
    assert agr_binding.required_context == ()
    assert agr_binding.description == "AGR database query binding"
    assert agr_binding.source.package_id == "agr.base"
    assert agr_binding.source.source_file == "src/agr_base/tools/agr.py"

    document_binding = registry.get("search_document")
    assert document_binding is not None
    assert document_binding.binding_kind.value == "context_factory"
    assert document_binding.import_attribute_kind == "callable_factory"
    assert document_binding.required_context == ("document_id", "user_id")
    assert document_binding.description == "Document-scoped custom tools"
    assert document_binding.source.package_id == "org.custom"


def test_load_tool_registry_rejects_conflicting_exports_without_override(tmp_path):
    packages_dir = tmp_path / "packages"
    for directory_name, package_id in (
        ("agr-base", "agr.base"),
        ("org-custom", "org.custom"),
    ):
        _write_package(
            packages_dir,
            directory_name=directory_name,
            package_id=package_id,
            bindings_text=f"""package_id: {package_id}
bindings_api_version: 1.0.0
tools:
  - tool_id: shared_tool
    binding_kind: static
    callable: {package_id.replace('.', '_')}.tools.shared:shared_tool
    required_context: []
""",
        )

    with pytest.raises(ToolRegistryValidationError) as exc_info:
        load_tool_registry(
            packages_dir,
            runtime_version="1.5.0",
            supported_package_api_version="1.0.0",
        )

    message = str(exc_info.value)
    assert "Conflicting tool binding 'shared_tool'" in message
    assert "agr.base:default" in message
    assert "org.custom:default" in message
    assert "export_kind 'tool_binding'" in message


def test_load_tool_registry_resolves_conflicts_with_explicit_override(tmp_path):
    packages_dir = tmp_path / "packages"
    overrides_path = tmp_path / "overrides.yaml"

    _write_package(
        packages_dir,
        directory_name="agr-base",
        package_id="agr.base",
        bindings_text="""package_id: agr.base
bindings_api_version: 1.0.0
tools:
  - tool_id: shared_tool
    binding_kind: static
    callable: agr_base.tools.shared:shared_tool
    required_context: []
    description: Base binding
""",
    )
    _write_package(
        packages_dir,
        directory_name="org-custom",
        package_id="org.custom",
        bindings_text="""package_id: org.custom
bindings_api_version: 1.0.0
tools:
  - tool_id: shared_tool
    binding_kind: context_factory
    callable_factory: org_custom.tools.shared:create_shared_tool
    required_context:
      - user_id
    description: Custom binding
""",
    )
    _write_overrides(overrides_path, package_id="org.custom")

    registry = load_tool_registry(
        packages_dir,
        overrides_path=overrides_path,
        runtime_version="1.5.0",
        supported_package_api_version="1.0.0",
    )

    assert registry.validation_errors == ()
    assert len(registry.collisions) == 1

    selected = registry.get("shared_tool")
    assert selected is not None
    assert selected.source.package_id == "org.custom"
    assert selected.binding_kind.value == "context_factory"
    assert selected.import_attribute_kind == "callable_factory"
    assert selected.required_context == ("user_id",)
    assert registry.collisions[0].selected == selected


def test_build_tool_registry_soft_fails_for_multiple_matching_override_selections(tmp_path):
    packages_dir = tmp_path / "packages"

    _write_package(
        packages_dir,
        directory_name="agr-base",
        package_id="agr.base",
        bindings_text="""package_id: agr.base
bindings_api_version: 1.0.0
tools:
  - tool_id: shared_tool
    binding_kind: static
    callable: agr_base.tools.shared:shared_tool
    required_context: []
""",
    )
    _write_package(
        packages_dir,
        directory_name="org-custom",
        package_id="org.custom",
        bindings_text="""package_id: org.custom
bindings_api_version: 1.0.0
tools:
  - tool_id: shared_tool
    binding_kind: static
    callable: org_custom.tools.shared:shared_tool
    required_context: []
""",
    )

    package_registry = load_package_registry(
        packages_dir,
        runtime_version="1.5.0",
        supported_package_api_version="1.0.0",
        fail_on_validation_error=False,
    )
    invalid_overrides = RuntimeOverrides.model_construct(
        overrides_api_version="1.0.0",
        package_precedence=[],
        disabled_packages=[],
        selections=[
            RuntimeOverrideSelection(
                export_kind=ExportKind.TOOL_BINDING,
                name="default",
                package_id="agr.base",
            ),
            RuntimeOverrideSelection(
                export_kind=ExportKind.TOOL_BINDING,
                name="default",
                package_id="org.custom",
            ),
        ],
    )

    registry = build_tool_registry(
        package_registry,
        runtime_overrides=invalid_overrides,
        fail_on_validation_error=False,
    )

    assert registry.get("shared_tool") is None
    assert len(registry.collisions) == 1
    assert registry.collisions[0].selected is None
    assert any(
        "Multiple override selections match conflicting tool 'shared_tool'" in error
        for error in registry.validation_errors
    )


def test_repo_core_package_exports_current_built_in_tools():
    registry = load_tool_registry(REPO_ROOT / "packages")

    expected_bindings = {
        "agr_curation_query": ("static", (), "agr.core"),
        "search_document": ("context_factory", ("document_id", "user_id"), "agr.core"),
        "read_section": ("context_factory", ("document_id", "user_id"), "agr.core"),
        "read_subsection": ("context_factory", ("document_id", "user_id"), "agr.core"),
        "curation_db_sql": ("context_factory", ("database_url",), "agr.core"),
        "chebi_api_call": ("static", (), "agr.core"),
        "quickgo_api_call": ("static", (), "agr.core"),
        "go_api_call": ("static", (), "agr.core"),
        "alliance_api_call": ("static", (), "agr.core"),
        "save_csv_file": ("static", (), "agr.core"),
        "save_tsv_file": ("static", (), "agr.core"),
        "save_json_file": ("static", (), "agr.core"),
    }

    assert set(expected_bindings).issubset(registry.bindings_by_tool_id)

    for tool_id, (binding_kind, required_context, package_id) in expected_bindings.items():
        binding = registry.get(tool_id)
        assert binding is not None
        assert binding.binding_kind.value == binding_kind
        assert binding.required_context == required_context
        assert binding.source.package_id == package_id


def test_repo_core_package_copies_tool_implementations_locally():
    copied_modules = [
        "agr_curation.py",
        "search_helpers.py",
        "weaviate_search.py",
        "sql_query.py",
        "rest_api.py",
        "file_output_tools.py",
    ]

    for module_name in copied_modules:
        module_path = CORE_TOOLS_DIR / module_name
        assert module_path.exists()
        source = module_path.read_text(encoding="utf-8")
        assert "src.lib.openai_agents.tools" not in source
