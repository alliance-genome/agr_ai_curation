"""Tests for config.models_loader."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def reset_model_cache():
    import src.lib.config.models_loader as models_loader_module

    models_loader_module.reset_cache()
    yield
    models_loader_module.reset_cache()


def _write_model_package(
    packages_dir: Path,
    *,
    directory_name: str,
    package_id: str,
    models_text: str,
) -> None:
    package_dir = packages_dir / directory_name
    (package_dir / "config").mkdir(parents=True)
    (package_dir / "requirements").mkdir(parents=True)
    (package_dir / "requirements" / "runtime.txt").write_text("", encoding="utf-8")
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
  - kind: model
    name: default_models
    path: config/models.yaml
    description: Default models
""",
        encoding="utf-8",
    )
    (package_dir / "config" / "models.yaml").write_text(models_text.strip() + "\n", encoding="utf-8")


def test_load_models_reads_yaml(tmp_path: Path):
    import src.lib.config.models_loader as models_loader_module

    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
models:
  - model_id: gpt-4o
    name: GPT-4o
    provider: openai
    default: true
    curator_visible: false
    reasoning_options: [low, medium, high]
    default_reasoning: medium
    reasoning_descriptions:
      medium: Balanced reasoning
    recommended_for:
      - General curation
    avoid_for:
      - Low-latency retrieval only
        """.strip(),
        encoding="utf-8",
    )

    loaded = models_loader_module.load_models(
        models_path=config_path,
        packages_dir=tmp_path / "missing-packages",
        force_reload=True,
    )

    assert "gpt-4o" in loaded
    assert loaded["gpt-4o"].default is True
    assert loaded["gpt-4o"].curator_visible is False
    assert loaded["gpt-4o"].reasoning_options == ["low", "medium", "high"]
    assert loaded["gpt-4o"].default_reasoning == "medium"
    assert loaded["gpt-4o"].reasoning_descriptions["medium"] == "Balanced reasoning"
    assert loaded["gpt-4o"].recommended_for == ["General curation"]
    assert loaded["gpt-4o"].avoid_for == ["Low-latency retrieval only"]
    assert loaded["gpt-4o"].source_label == f"runtime override 'models.yaml' at {config_path}"
    assert models_loader_module.get_default_model().model_id == "gpt-4o"


def test_load_models_uses_sorted_package_order_for_collisions(tmp_path: Path):
    import src.lib.config.models_loader as models_loader_module

    packages_dir = tmp_path / "packages"
    _write_model_package(
        packages_dir,
        directory_name="agr-base",
        package_id="agr.base",
        models_text="""
models:
  - model_id: shared-model
    name: AGR Base Shared
    provider: openai
  - model_id: agr-only
    name: AGR Base Only
    provider: openai
""",
    )
    _write_model_package(
        packages_dir,
        directory_name="org-custom",
        package_id="org.custom",
        models_text="""
models:
  - model_id: shared-model
    name: Org Custom Shared
    provider: groq
  - model_id: org-only
    name: Org Custom Only
    provider: groq
""",
    )

    loaded = models_loader_module.load_models(packages_dir=packages_dir, force_reload=True)

    assert loaded["shared-model"].name == "Org Custom Shared"
    assert loaded["shared-model"].provider == "groq"
    assert loaded["shared-model"].source_label is not None
    assert "package default 'org.custom'" in loaded["shared-model"].source_label
    assert loaded["agr-only"].name == "AGR Base Only"
    assert loaded["org-only"].name == "Org Custom Only"


def test_load_models_runtime_override_wins_over_package_defaults(tmp_path: Path):
    import src.lib.config.models_loader as models_loader_module

    packages_dir = tmp_path / "packages"
    override_path = tmp_path / "models.yaml"
    _write_model_package(
        packages_dir,
        directory_name="agr-base",
        package_id="agr.base",
        models_text="""
models:
  - model_id: shared-model
    name: AGR Base Shared
    provider: openai
    default: true
""",
    )
    override_path.write_text(
        """
models:
  - model_id: shared-model
    name: Runtime Shared
    provider: groq
    default: false
  - model_id: runtime-only
    name: Runtime Only
    provider: openai
""".strip(),
        encoding="utf-8",
    )

    loaded = models_loader_module.load_models(
        models_path=override_path,
        packages_dir=packages_dir,
        force_reload=True,
    )

    assert loaded["shared-model"].name == "Runtime Shared"
    assert loaded["shared-model"].provider == "groq"
    assert loaded["shared-model"].source_label == (
        f"runtime override 'models.yaml' at {override_path}"
    )
    assert loaded["runtime-only"].name == "Runtime Only"


def test_load_models_reports_runtime_override_source_on_invalid_entry(tmp_path: Path):
    import src.lib.config.models_loader as models_loader_module

    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
models:
  - name: Missing ID
    provider: openai
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="runtime override 'models.yaml'") as exc_info:
        models_loader_module.load_models(models_path=config_path, force_reload=True)

    assert str(config_path) in str(exc_info.value)
