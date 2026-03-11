"""Unit tests for merged package-declared tool registry construction."""

from pathlib import Path

import pytest

from src.lib.packages.tool_registry import (
    ToolRegistryValidationError,
    load_tool_registry,
)


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
