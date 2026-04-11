"""Package-aware loader coverage for shipped and fixture packages."""

from copy import deepcopy
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

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
    manifest_payload = deepcopy(payload)
    PackageManifest.model_validate(manifest_payload)
    (package_dir / "package.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )
    (package_dir / "requirements").mkdir(exist_ok=True)
    (package_dir / "requirements" / "runtime.txt").write_text("", encoding="utf-8")


def test_load_agent_definitions_defaults_to_runtime_packages(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    agents = agent_loader.load_agent_definitions(force_reload=True)

    assert "gene_validation" in agents
    assert agents["gene_validation"].folder_name == "gene"
    assert agents["gene_validation"].output_schema == "GeneResultEnvelope"


def test_runtime_packages_gene_extractor_explicitly_declares_record_evidence(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    agents = agent_loader.load_agent_definitions(force_reload=True)
    gene_extractor = agents["gene_extractor"]

    assert gene_extractor.tools == [
        "search_document",
        "read_section",
        "read_subsection",
        "record_evidence",
        "agr_curation_query",
    ]


def test_runtime_packages_gene_extractor_prompt_teaches_verified_evidence_flow(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    source = next(
        item
        for item in agent_sources.resolve_agent_config_sources(REPO_PACKAGES_DIR)
        if item.folder_name == "gene_extractor"
    )

    prompt_payload = yaml.safe_load(source.prompt_yaml.read_text(encoding="utf-8"))
    prompt_content = str(prompt_payload["content"])

    assert "<few_shot_examples>" in prompt_content
    assert prompt_content.count("record_evidence(") >= 3
    assert '"status": "verified"' in prompt_content
    assert "`verified_quote`" in prompt_content
    assert "`chunk_id`" in prompt_content
    assert "Do not call `record_evidence` for every gene mentioned anywhere in the paper." in prompt_content
    assert "Do not place free-text evidence summaries inside these fields." in prompt_content


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
        call["source_file"] == "packages/agr.alliance/agents/gene/prompt.yaml"
        and call["prompt_type"] == "system"
        for call in captured_calls
    )
    assert any(
        call["source_file"] == "packages/agr.alliance/agents/gene/group_rules/fb.yaml"
        and call["prompt_type"] == "group_rules"
        and call["group_id"] == "FB"
        for call in captured_calls
    )
    assert any(
        call["source_file"] == "config/agents/supervisor/prompt.yaml"
        and call["prompt_type"] == "system"
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


def test_resolve_agent_sources_warns_when_package_agent_dir_is_missing_from_agent_bundles(
    tmp_path, caplog
):
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
    for agent_name in ("gene", "missing_manifest"):
        agent_dir = package_dir / "agents" / agent_name
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.yaml").write_text(
            f"agent_id: {agent_name}\nname: {agent_name.title()}\n",
            encoding="utf-8",
        )
        (agent_dir / "prompt.yaml").write_text(
            "content: Demo prompt\n",
            encoding="utf-8",
        )

    with caplog.at_level(logging.WARNING):
        sources = agent_sources.resolve_agent_config_sources(packages_dir)

    assert [source.folder_name for source in sources] == ["gene"]
    assert sources[0].package_id == "demo.core"
    assert "Ignoring undeclared agent bundle directories for package 'demo.core'" in caplog.text
    assert "agent_bundles is missing package-owned agent directories with agent.yaml" in caplog.text
    assert "agents/missing_manifest" in caplog.text

    caplog.clear()

    with caplog.at_level(logging.WARNING):
        repeated_sources = agent_sources.resolve_agent_config_sources(packages_dir)

    assert [source.folder_name for source in repeated_sources] == ["gene"]
    assert "Ignoring undeclared agent bundle directories for package 'demo.core'" in caplog.text
    assert "agent_bundles is missing package-owned agent directories with agent.yaml" in caplog.text
    assert "agents/missing_manifest" in caplog.text


def test_resolve_agent_sources_rejects_package_prompt_exports_without_agent_bundle(tmp_path):
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
            "exports": [
                {
                    "kind": "prompt",
                    "name": "gene.system",
                    "path": "agents/gene/prompt.yaml",
                    "description": "Base prompt without an owning agent export",
                }
            ],
        },
    )
    prompt_file = package_dir / "agents" / "gene" / "prompt.yaml"
    prompt_file.parent.mkdir(parents=True)
    prompt_file.write_text(
        "agent_id: gene_validation\ncontent: Demo prompt\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="Package 'demo.core' exports prompt/schema/group rules for unknown agent bundle\\(s\\): gene",
    ):
        agent_sources.resolve_agent_config_sources(packages_dir)


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


def test_get_default_agent_search_path_prefers_env_override(monkeypatch):
    monkeypatch.setenv("AGENTS_CONFIG_PATH", "/tmp/custom-agents")

    assert agent_sources.get_default_agent_search_path() == Path("/tmp/custom-agents")


def test_get_default_agent_search_paths_layers_repo_config_over_runtime_packages(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    packages_dir = repo_root / "packages"
    config_agents_dir = repo_root / "config" / "agents"
    packages_dir.mkdir(parents=True)
    config_agents_dir.mkdir(parents=True)

    monkeypatch.delenv("AGENTS_CONFIG_PATH", raising=False)
    monkeypatch.setattr(agent_sources, "get_runtime_packages_dir", lambda: packages_dir)
    monkeypatch.setattr(agent_sources, "get_runtime_config_dir", lambda: tmp_path / "runtime-config")
    monkeypatch.setattr(agent_sources, "_find_project_root", lambda: repo_root)

    assert agent_sources.get_default_agent_search_paths() == (
        packages_dir.resolve(strict=False),
        config_agents_dir.resolve(strict=False),
    )


def test_get_default_agent_search_paths_layers_runtime_config_over_packages_without_repo_root(
    monkeypatch, tmp_path
):
    packages_dir = tmp_path / "runtime-packages"
    runtime_config_dir = tmp_path / "runtime-config"
    runtime_agents_dir = runtime_config_dir / "agents"
    packages_dir.mkdir(parents=True)
    runtime_agents_dir.mkdir(parents=True)

    monkeypatch.delenv("AGENTS_CONFIG_PATH", raising=False)
    monkeypatch.setattr(agent_sources, "get_runtime_packages_dir", lambda: packages_dir)
    monkeypatch.setattr(agent_sources, "get_runtime_config_dir", lambda: runtime_config_dir)
    monkeypatch.setattr(agent_sources, "_find_project_root", lambda: None)

    assert agent_sources.get_default_agent_search_paths() == (
        packages_dir.resolve(strict=False),
        runtime_agents_dir.resolve(strict=False),
    )


def test_fallback_packages_dir_ignores_env_override(monkeypatch, tmp_path):
    runtime_packages_dir = tmp_path / "runtime-packages"
    fallback_project_root = tmp_path / "repo"
    expected_packages_dir = fallback_project_root / "packages"

    monkeypatch.setenv("AGENTS_CONFIG_PATH", "/tmp/custom-agents")
    monkeypatch.setattr(agent_sources, "get_runtime_packages_dir", lambda: runtime_packages_dir)
    monkeypatch.setattr(agent_sources, "_find_project_root", lambda: fallback_project_root)

    assert agent_sources._get_fallback_packages_dir() == expected_packages_dir


def test_resolve_search_path_marks_env_override_as_non_default(monkeypatch):
    monkeypatch.setenv("AGENTS_CONFIG_PATH", "/tmp/custom-agents")

    resolved_path, used_default_search_path = agent_sources._resolve_search_path(None)

    assert resolved_path == Path("/tmp/custom-agents")
    assert used_default_search_path is False


def test_discover_agent_schemas_logs_resolved_default_path(monkeypatch, caplog, tmp_path):
    packages_dir = tmp_path / "runtime-packages"

    monkeypatch.setattr(
        schema_discovery,
        "get_default_agent_search_path",
        lambda: packages_dir,
    )
    monkeypatch.setattr(
        schema_discovery,
        "resolve_agent_config_sources",
        lambda _agents_path=None: (),
    )

    with caplog.at_level(logging.INFO):
        schema_discovery.discover_agent_schemas(force_reload=True)

    assert f"Discovering agent schemas from: {packages_dir}" in caplog.text


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


def test_resolve_agent_config_sources_allows_config_override_layer(tmp_path):
    packages_dir = tmp_path / "packages"
    package_dir = packages_dir / "demo_core"
    overrides_dir = tmp_path / "config-agents"
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
    (package_dir / "agents" / "gene" / "agent.yaml").write_text(
        "agent_id: gene_validation\nname: Package Gene\n",
        encoding="utf-8",
    )
    (package_dir / "agents" / "gene" / "prompt.yaml").write_text(
        "content: Package prompt\n",
        encoding="utf-8",
    )

    (overrides_dir / "gene").mkdir(parents=True)
    (overrides_dir / "gene" / "agent.yaml").write_text(
        "agent_id: gene_validation\nname: Override Gene\n",
        encoding="utf-8",
    )
    (overrides_dir / "gene" / "prompt.yaml").write_text(
        "content: Override prompt\n",
        encoding="utf-8",
    )
    (overrides_dir / "custom_local").mkdir(parents=True)
    (overrides_dir / "custom_local" / "agent.yaml").write_text(
        "agent_id: custom_local\nname: Custom Local\n",
        encoding="utf-8",
    )

    sources = agent_sources.resolve_agent_config_sources((packages_dir, overrides_dir))

    assert {source.folder_name for source in sources} == {"custom_local", "gene"}
    gene_source = next(source for source in sources if source.folder_name == "gene")
    assert gene_source.package_id == "demo.core"
    assert gene_source.agent_dir == (overrides_dir / "gene")
    assert gene_source.agent_yaml == (overrides_dir / "gene" / "agent.yaml")
    assert gene_source.prompt_yaml == (overrides_dir / "gene" / "prompt.yaml")


def test_resolve_agent_config_sources_merges_partial_override_without_dropping_package_bundle(tmp_path):
    packages_dir = tmp_path / "packages"
    package_dir = packages_dir / "demo_core"
    overrides_dir = tmp_path / "config-agents"
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
    package_agent_dir = package_dir / "agents" / "gene"
    package_agent_dir.mkdir(parents=True)
    (package_agent_dir / "agent.yaml").write_text(
        "agent_id: gene_validation\nname: Package Gene\n",
        encoding="utf-8",
    )
    (package_agent_dir / "prompt.yaml").write_text(
        "content: Package prompt\n",
        encoding="utf-8",
    )
    (package_agent_dir / "group_rules").mkdir()
    (package_agent_dir / "group_rules" / "fb.yaml").write_text(
        "content: Package FB rules\n",
        encoding="utf-8",
    )
    (package_agent_dir / "group_rules" / "wb.yaml").write_text(
        "content: Package WB rules\n",
        encoding="utf-8",
    )

    override_agent_dir = overrides_dir / "gene"
    (override_agent_dir / "group_rules").mkdir(parents=True)
    (override_agent_dir / "group_rules" / "wb.yaml").write_text(
        "content: Override WB rules\n",
        encoding="utf-8",
    )

    sources = agent_sources.resolve_agent_config_sources((packages_dir, overrides_dir))

    assert {source.folder_name for source in sources} == {"gene"}
    gene_source = sources[0]
    assert gene_source.package_id == "demo.core"
    assert gene_source.agent_dir == override_agent_dir
    assert gene_source.agent_yaml == (package_agent_dir / "agent.yaml")
    assert gene_source.prompt_yaml == (package_agent_dir / "prompt.yaml")
    assert gene_source.group_rule_files == (
        override_agent_dir / "group_rules" / "wb.yaml",
    )
    assert gene_source.source_file_display(package_agent_dir / "prompt.yaml") == (
        "packages/demo.core/agents/gene/prompt.yaml"
    )
    assert gene_source.source_file_display(
        override_agent_dir / "group_rules" / "wb.yaml"
    ).endswith("config-agents/gene/group_rules/wb.yaml")

    loaded_agents = agent_loader.load_agent_definitions(
        (packages_dir, overrides_dir),
        force_reload=True,
    )

    assert "gene_validation" in loaded_agents
    assert loaded_agents["gene_validation"].name == "Package Gene"


def test_merge_group_rule_files_prefers_later_paths():
    fb_package = Path("/tmp/packages/demo_core/agents/gene/group_rules/fb.yaml")
    wb_package = Path("/tmp/packages/demo_core/agents/gene/group_rules/wb.yaml")
    wb_override = Path("/tmp/config-agents/gene/group_rules/wb.yaml")

    merged = agent_sources._merge_group_rule_files(
        (fb_package, wb_package),
        (wb_override,),
    )

    assert merged == (fb_package, wb_override)


def test_resolve_agent_config_sources_rejects_duplicate_bundles_within_packages_root(tmp_path):
    packages_dir = tmp_path / "packages"

    for package_name, package_id in (
        ("demo_alpha", "demo.alpha"),
        ("demo_beta", "demo.beta"),
    ):
        package_dir = packages_dir / package_name
        _write_package_manifest(
            package_dir,
            {
                "package_id": package_id,
                "display_name": package_name,
                "version": "1.0.0",
                "package_api_version": "1.0.0",
                "min_runtime_version": "1.0.0",
                "max_runtime_version": "2.0.0",
                "python_package_root": f"python/src/{package_name}",
                "requirements_file": "requirements/runtime.txt",
                "agent_bundles": [{"name": "gene"}],
            },
        )
        (package_dir / "agents" / "gene").mkdir(parents=True)
        (package_dir / "agents" / "gene" / "agent.yaml").write_text(
            "agent_id: gene_validation\nname: Gene\n",
            encoding="utf-8",
        )
        (package_dir / "agents" / "gene" / "prompt.yaml").write_text(
            "content: Gene prompt\n",
            encoding="utf-8",
        )

    with pytest.raises(ValueError, match=r"Duplicate agent bundle 'gene' discovered in "):
        agent_sources.resolve_agent_config_sources(packages_dir)


def test_load_prompts_defaults_to_runtime_config_override_without_repo_root(
    monkeypatch, tmp_path
):
    packages_dir = tmp_path / "runtime-packages"
    runtime_config_dir = tmp_path / "runtime-config"
    runtime_agents_dir = runtime_config_dir / "agents"
    package_dir = packages_dir / "core"

    _write_package_manifest(
        package_dir,
        {
            "package_id": "agr.core",
            "display_name": "AGR Core",
            "version": "1.0.0",
            "package_api_version": "1.0.0",
            "min_runtime_version": "1.0.0",
            "max_runtime_version": "2.0.0",
            "python_package_root": "python/src/agr_core",
            "requirements_file": "requirements/runtime.txt",
            "agent_bundles": [{"name": "supervisor"}],
        },
    )
    (package_dir / "agents" / "supervisor").mkdir(parents=True)
    (package_dir / "agents" / "supervisor" / "agent.yaml").write_text(
        "agent_id: supervisor\nname: Supervisor\n",
        encoding="utf-8",
    )
    (package_dir / "agents" / "supervisor" / "prompt.yaml").write_text(
        "agent_id: supervisor\ncontent: Core prompt\n",
        encoding="utf-8",
    )

    (runtime_agents_dir / "supervisor").mkdir(parents=True)
    (runtime_agents_dir / "supervisor" / "agent.yaml").write_text(
        "agent_id: supervisor\nname: Supervisor Override\n",
        encoding="utf-8",
    )
    override_prompt = runtime_agents_dir / "supervisor" / "prompt.yaml"
    override_prompt.write_text(
        "agent_id: supervisor\ncontent: Override prompt\n",
        encoding="utf-8",
    )

    db = MagicMock()
    captured_calls = []

    monkeypatch.delenv("AGENTS_CONFIG_PATH", raising=False)
    monkeypatch.setattr(agent_sources, "get_runtime_packages_dir", lambda: packages_dir)
    monkeypatch.setattr(agent_sources, "get_runtime_config_dir", lambda: runtime_config_dir)
    monkeypatch.setattr(agent_sources, "_find_project_root", lambda: None)
    monkeypatch.setattr(prompt_loader, "_acquire_advisory_lock", lambda _db: (True, True))
    monkeypatch.setattr(prompt_loader, "_release_advisory_lock", lambda _db: None)

    def _capture_upsert(**kwargs):
        captured_calls.append(kwargs)
        return (True, 1)

    monkeypatch.setattr(prompt_loader, "_upsert_prompt", _capture_upsert)

    result = prompt_loader.load_prompts(db=db, force_reload=True)

    assert result["base_prompts"] == 1
    assert any(
        call["source_file"] == str(override_prompt.resolve(strict=False))
        and call["prompt_type"] == "system"
        and call["agent_name"] == "supervisor"
        and call["content"] == "Override prompt"
        for call in captured_calls
    )


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
