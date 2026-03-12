"""Package-aware loader coverage for shipped and fixture packages."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.lib.config import agent_loader, agent_sources, prompt_loader, schema_discovery
from src.lib.packages.models import PackageManifest

from ..packages import find_repo_root

REPO_ROOT = find_repo_root(Path(__file__))
REPO_PACKAGES_DIR = REPO_ROOT / "packages"


@pytest.fixture(autouse=True)
def _reset_loader_caches():
    agent_loader.reset_cache()
    prompt_loader.reset_cache()
    schema_discovery.reset_cache()
    yield
    agent_loader.reset_cache()
    prompt_loader.reset_cache()
    schema_discovery.reset_cache()


def _write_package_manifest(package_dir: Path, payload: dict) -> None:
    package_dir.mkdir(parents=True, exist_ok=True)
    manifest_payload = {
        key: (
            [dict(item) for item in value]
            if key == "agent_bundles"
            else value
        )
        for key, value in payload.items()
    }
    PackageManifest.model_validate(manifest_payload)

    lines = [
        f"package_id: {payload['package_id']}",
        f"display_name: {payload['display_name']}",
        f"version: {payload['version']}",
        f"package_api_version: {payload['package_api_version']}",
        f"min_runtime_version: {payload['min_runtime_version']}",
        f"max_runtime_version: {payload['max_runtime_version']}",
        f"python_package_root: {payload['python_package_root']}",
        f"requirements_file: {payload['requirements_file']}",
        "agent_bundles:",
    ]
    for bundle in payload["agent_bundles"]:
        lines.append(f"  - name: {bundle['name']}")
        if bundle.get("has_schema"):
            lines.append("    has_schema: true")
        group_rules = bundle.get("group_rules", [])
        if group_rules:
            rendered = ", ".join(group_rules)
            lines.append(f"    group_rules: [{rendered}]")

    (package_dir / "package.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (package_dir / "requirements").mkdir(exist_ok=True)
    (package_dir / "requirements" / "runtime.txt").write_text("", encoding="utf-8")


def test_load_agent_definitions_defaults_to_runtime_packages(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    agents = agent_loader.load_agent_definitions(force_reload=True)

    assert "gene_validation" in agents
    assert agents["gene_validation"].folder_name == "gene"
    assert agents["gene_validation"].output_schema == "GeneResultEnvelope"


def test_load_prompts_defaults_to_runtime_packages_and_tracks_package_paths(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    db = MagicMock()
    captured_calls = []

    monkeypatch.setattr(prompt_loader, "_acquire_advisory_lock", lambda _db: (True, True))
    monkeypatch.setattr(prompt_loader, "_release_advisory_lock", lambda _db: None)

    def _capture_upsert(**kwargs):
        captured_calls.append(kwargs)
        return (True, 1)

    monkeypatch.setattr(prompt_loader, "_upsert_prompt", _capture_upsert)

    result = prompt_loader.load_prompts(db=db, force_reload=True)

    assert result["base_prompts"] >= 1
    assert result["group_rules"] >= 1
    assert any(
        call["source_file"] == "packages/agr.core/agents/gene/prompt.yaml"
        and call["prompt_type"] == "system"
        for call in captured_calls
    )
    assert any(
        call["source_file"] == "packages/agr.core/agents/gene/group_rules/fb.yaml"
        and call["prompt_type"] == "group_rules"
        and call["group_id"] == "FB"
        for call in captured_calls
    )


def test_discover_agent_schemas_defaults_to_runtime_packages(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    schemas = schema_discovery.discover_agent_schemas(force_reload=True)

    assert "GeneValidationEnvelope" in schemas
    assert schema_discovery.get_schema_for_agent("gene").__name__ == "GeneValidationEnvelope"


def test_load_agent_definitions_raises_clear_error_for_missing_package_agent_yaml(tmp_path):
    packages_dir = tmp_path / "packages"
    package_dir = packages_dir / "demo_core"
    _write_package_manifest(
        package_dir,
        {
            "package_id": "demo.core",
            "display_name": "Demo Core",
            "version": "1.0.0",
            "package_api_version": "1.0.0",
            "min_runtime_version": "1.0.0",
            "max_runtime_version": "2.0.0",
            "python_package_root": "python/src/demo_core",
            "requirements_file": "requirements/runtime.txt",
            "agent_bundles": [{"name": "gene"}],
        },
    )
    (package_dir / "agents" / "gene").mkdir(parents=True)

    with pytest.raises(FileNotFoundError, match="Package 'demo.core' agent bundle 'gene' is missing agent.yaml"):
        agent_loader.load_agent_definitions(packages_dir, force_reload=True)


def test_find_project_root_prefers_repo_root_over_backend_pytest_ini(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    module_path = repo_root / "backend" / "src" / "lib" / "config" / "agent_sources.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text("", encoding="utf-8")
    (repo_root / "backend" / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (repo_root / "packages").mkdir()
    (repo_root / "config" / "agents").mkdir(parents=True)

    monkeypatch.setattr(agent_sources, "__file__", str(module_path))

    assert agent_sources._find_project_root() == repo_root


def test_default_runtime_packages_dir_must_contain_package_manifests(tmp_path, monkeypatch):
    packages_dir = tmp_path / "runtime-packages"
    packages_dir.mkdir()
    db = MagicMock()

    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(packages_dir))
    monkeypatch.setattr(prompt_loader, "_acquire_advisory_lock", lambda _db: (True, True))
    monkeypatch.setattr(prompt_loader, "_release_advisory_lock", lambda _db: None)

    match = "No runtime packages with package manifests were found"

    with pytest.raises(FileNotFoundError, match=match):
        agent_loader.load_agent_definitions(force_reload=True)

    with pytest.raises(FileNotFoundError, match=match):
        schema_discovery.discover_agent_schemas(force_reload=True)

    with pytest.raises(FileNotFoundError, match=match):
        prompt_loader.load_prompts(db=db, force_reload=True)


def test_env_override_allows_legacy_agent_directory_loading(tmp_path, monkeypatch):
    agents_dir = tmp_path / "legacy-agents"
    gene_dir = agents_dir / "gene"
    group_rules_dir = gene_dir / "group_rules"
    group_rules_dir.mkdir(parents=True)

    (gene_dir / "agent.yaml").write_text(
        "\n".join(
            [
                "agent_id: gene_validation",
                "name: Gene Validation Agent",
                "output_schema: GeneValidationEnvelope",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (gene_dir / "prompt.yaml").write_text(
        "agent_id: gene_validation\ncontent: Legacy gene prompt\n",
        encoding="utf-8",
    )
    (group_rules_dir / "fb.yaml").write_text(
        "group_id: FB\ncontent: Legacy FlyBase rules\n",
        encoding="utf-8",
    )
    (gene_dir / "schema.py").write_text(
        "\n".join(
            [
                "from pydantic import BaseModel",
                "",
                "class GeneValidationEnvelope(BaseModel):",
                "    gene_id: str",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    db = MagicMock()
    captured_calls = []

    monkeypatch.setenv("AGENTS_CONFIG_PATH", str(agents_dir))
    monkeypatch.delenv("AGR_RUNTIME_PACKAGES_DIR", raising=False)
    monkeypatch.setattr(prompt_loader, "_acquire_advisory_lock", lambda _db: (True, True))
    monkeypatch.setattr(prompt_loader, "_release_advisory_lock", lambda _db: None)

    def _capture_upsert(**kwargs):
        captured_calls.append(kwargs)
        return (True, 1)

    monkeypatch.setattr(prompt_loader, "_upsert_prompt", _capture_upsert)

    agents = agent_loader.load_agent_definitions(force_reload=True)
    prompt_result = prompt_loader.load_prompts(db=db, force_reload=True)
    schemas = schema_discovery.discover_agent_schemas(force_reload=True)

    assert "gene_validation" in agents
    assert prompt_result == {"base_prompts": 1, "group_rules": 1}
    assert "GeneValidationEnvelope" in schemas
    assert any(call["source_file"].endswith("legacy-agents/gene/prompt.yaml") for call in captured_calls)
