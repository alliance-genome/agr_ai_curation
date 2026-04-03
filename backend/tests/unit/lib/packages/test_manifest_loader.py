"""Unit tests for runtime package contract parsing."""

from pathlib import Path

import pytest

from src.lib.packages.manifest_loader import (
    PackageManifestError,
    RuntimeOverridesError,
    ToolBindingsError,
    load_package_manifest,
    load_runtime_overrides,
    load_tool_bindings,
)
from src.lib.packages.models import ExportKind, ToolBindingKind

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_load_package_manifest_parses_representative_fixture():
    manifest = load_package_manifest(FIXTURES_DIR / "valid_package.yaml")

    assert manifest.package_id == "agr.base"
    assert manifest.display_name == "AGR Base Package"
    assert manifest.python_package_root == "src/agr_base"
    assert manifest.requirements_file == "requirements/runtime.txt"
    assert [export.kind for export in manifest.exports] == [
        ExportKind.AGENT,
        ExportKind.PROMPT,
        ExportKind.TOOL_BINDING,
    ]
    assert manifest.exports[0].path == "agents/supervisor"


def test_load_package_manifest_expands_agent_bundles_shorthand(tmp_path: Path):
    manifest_path = tmp_path / "package.yaml"
    manifest_path.write_text(
        """
package_id: agr.core
display_name: AGR Core Package
version: 1.0.0
package_api_version: 1.0.0
min_runtime_version: 1.0.0
max_runtime_version: 2.0.0
python_package_root: python/src/agr_ai_curation_core
requirements_file: requirements/runtime.txt
exports:
  - kind: tool_binding
    name: default
    path: tools/bindings.yaml
    description: Default bindings
agent_bundles:
  - name: gene
    has_schema: true
    group_rules: [fb, wb]
  - name: chat_output
""".strip(),
        encoding="utf-8",
    )

    manifest = load_package_manifest(manifest_path)

    actual_exports = {
        (export.kind, export.name, export.path)
        for export in manifest.exports
    }
    assert actual_exports == {
        (ExportKind.TOOL_BINDING, "default", "tools/bindings.yaml"),
        (ExportKind.AGENT, "gene", "agents/gene"),
        (ExportKind.PROMPT, "gene.system", "agents/gene/prompt.yaml"),
        (ExportKind.SCHEMA, "gene.schema", "agents/gene/schema.py"),
        (ExportKind.GROUP_RULE, "gene.FB", "agents/gene/group_rules/fb.yaml"),
        (ExportKind.GROUP_RULE, "gene.WB", "agents/gene/group_rules/wb.yaml"),
        (ExportKind.AGENT, "chat_output", "agents/chat_output"),
        (ExportKind.PROMPT, "chat_output.system", "agents/chat_output/prompt.yaml"),
    }


def test_load_package_manifest_rejects_package_agent_dir_missing_from_agent_bundles(tmp_path: Path):
    manifest_path = tmp_path / "package.yaml"
    manifest_path.write_text(
        """
package_id: agr.core
display_name: AGR Core Package
version: 1.0.0
package_api_version: 1.0.0
min_runtime_version: 1.0.0
max_runtime_version: 2.0.0
python_package_root: python/src/agr_ai_curation_core
requirements_file: requirements/runtime.txt
agent_bundles:
  - name: gene
""".strip(),
        encoding="utf-8",
    )
    for agent_name in ("gene", "missing_manifest"):
        agent_dir = tmp_path / "agents" / agent_name
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.yaml").write_text(f"agent_id: {agent_name}\n", encoding="utf-8")

    with pytest.raises(PackageManifestError) as exc_info:
        load_package_manifest(manifest_path)

    message = str(exc_info.value)
    assert "agent_bundles is missing package-owned agent directories with agent.yaml" in message
    assert "agents/missing_manifest" in message
    assert "Add each bundle name to agent_bundles to activate it." in message


def test_load_package_manifest_allows_explicit_agent_export_outside_agent_bundles(tmp_path: Path):
    manifest_path = tmp_path / "package.yaml"
    manifest_path.write_text(
        """
package_id: agr.core
display_name: AGR Core Package
version: 1.0.0
package_api_version: 1.0.0
min_runtime_version: 1.0.0
max_runtime_version: 2.0.0
python_package_root: python/src/agr_ai_curation_core
requirements_file: requirements/runtime.txt
exports:
  - kind: agent
    name: explicit_export
    path: agents/explicit_export
    description: Explicit agent export
agent_bundles:
  - name: gene
""".strip(),
        encoding="utf-8",
    )
    for agent_name in ("gene", "explicit_export"):
        agent_dir = tmp_path / "agents" / agent_name
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.yaml").write_text(f"agent_id: {agent_name}\n", encoding="utf-8")

    manifest = load_package_manifest(manifest_path)

    assert {
        (export.kind, export.name, export.path)
        for export in manifest.exports
        if export.kind is ExportKind.AGENT
    } == {
        (ExportKind.AGENT, "explicit_export", "agents/explicit_export"),
        (ExportKind.AGENT, "gene", "agents/gene"),
    }


def test_load_tool_bindings_parses_representative_fixture():
    bindings = load_tool_bindings(FIXTURES_DIR / "valid_bindings.yaml")

    assert bindings.package_id == "agr.base"
    assert bindings.bindings_api_version == "1.0.0"
    assert [tool.tool_id for tool in bindings.tools] == [
        "ask_gene_specialist",
        "file_output",
    ]
    assert bindings.tools[0].binding_kind is ToolBindingKind.STATIC
    assert bindings.tools[0].callable == "agr_base.tools.genes:ask_gene_specialist"
    assert bindings.tools[0].required_context == []
    assert bindings.tools[1].callable_factory == (
        "agr_base.tools.file_output:create_write_output_tool"
    )


def test_load_runtime_overrides_parses_collision_resolution_fixture():
    overrides = load_runtime_overrides(FIXTURES_DIR / "valid_overrides.yaml")

    assert overrides.overrides_api_version == "1.0.0"
    assert overrides.package_precedence == ["agr.base", "org.custom"]
    assert overrides.disabled_packages == ["experimental.package"]
    assert overrides.selections[0].export_kind is ExportKind.AGENT
    assert overrides.selections[0].name == "supervisor"
    assert overrides.selections[0].package_id == "org.custom"


def test_invalid_package_manifest_reports_actionable_field_errors():
    with pytest.raises(PackageManifestError) as exc_info:
        load_package_manifest(FIXTURES_DIR / "invalid_package.yaml")

    message = str(exc_info.value)
    assert "invalid_package.yaml" in message
    assert "package_id" in message
    assert "version" in message
    assert "python_package_root" in message
    assert "requirements_file" in message
    assert "exports.0.name" in message
    assert "exports.0.path" in message


def test_invalid_tool_bindings_report_actionable_field_errors():
    with pytest.raises(ToolBindingsError) as exc_info:
        load_tool_bindings(FIXTURES_DIR / "invalid_bindings.yaml")

    message = str(exc_info.value)
    assert "invalid_bindings.yaml" in message
    assert "package_id" in message
    assert "bindings_api_version" in message
    assert "tool_id" in message
    assert "binding_kind" in message
    assert "callable" in message


def test_invalid_runtime_overrides_report_duplicate_selections():
    with pytest.raises(RuntimeOverridesError) as exc_info:
        load_runtime_overrides(FIXTURES_DIR / "invalid_overrides.yaml")

    message = str(exc_info.value)
    assert "invalid_overrides.yaml" in message
    assert "selections" in message
