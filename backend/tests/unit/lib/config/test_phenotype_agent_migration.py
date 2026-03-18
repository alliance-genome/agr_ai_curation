"""Regression tests for the phenotype agent seed migration helper."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

import yaml

from ..packages import find_repo_root


REPO_ROOT = find_repo_root(Path(__file__))
MIGRATION_PATH = (
    REPO_ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "c4d5e6f7a8b9_add_phenotype_system_agent.py"
)


def _load_migration_module(monkeypatch):
    dummy_alembic = types.ModuleType("alembic")
    dummy_alembic.op = object()
    monkeypatch.setitem(sys.modules, "alembic", dummy_alembic)

    dummy_sqlalchemy = types.ModuleType("sqlalchemy")
    dummy_sqlalchemy.func = types.SimpleNamespace(now=lambda: None)
    monkeypatch.setitem(sys.modules, "sqlalchemy", dummy_sqlalchemy)
    monkeypatch.setitem(sys.modules, "sqlalchemy.dialects", types.ModuleType("sqlalchemy.dialects"))

    dummy_postgresql = types.ModuleType("sqlalchemy.dialects.postgresql")
    dummy_postgresql.JSONB = object
    dummy_postgresql.UUID = object
    dummy_postgresql.insert = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "sqlalchemy.dialects.postgresql", dummy_postgresql)

    spec = spec_from_file_location("phenotype_agent_migration_test", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None

    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_agent_bundle(agent_dir: Path, *, name: str, prompt: str) -> None:
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "agent.yaml").write_text(
        yaml.safe_dump(
            {
                "agent_id": "phenotype_extractor",
                "name": name,
                "description": "Phenotype bundle",
                "tools": ["search_document"],
                "output_schema": "PhenotypeResultEnvelope",
                "model_config": {"model": "gpt-4o", "temperature": 0.1},
                "frontend": {"icon": "dna", "show_in_palette": True},
                "supervisor_routing": {"enabled": True, "batchable": False},
                "group_rules_enabled": True,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (agent_dir / "prompt.yaml").write_text(
        yaml.safe_dump(
            {
                "agent_id": "phenotype_extractor",
                "content": prompt,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _clear_runtime_env(monkeypatch) -> None:
    for env_var in ("AGR_RUNTIME_ROOT", "AGR_RUNTIME_PACKAGES_DIR"):
        monkeypatch.delenv(env_var, raising=False)


def test_load_phenotype_spec_prefers_runtime_package_bundle(tmp_path, monkeypatch):
    _clear_runtime_env(monkeypatch)

    runtime_root = tmp_path / "runtime"
    repo_root = tmp_path / "repo"
    _write_agent_bundle(
        runtime_root / "packages" / "core" / "agents" / "phenotype_extractor",
        name="Runtime Bundle",
        prompt="runtime prompt",
    )
    _write_agent_bundle(
        repo_root / "config" / "agents" / "phenotype_extractor",
        name="Repo Config Bundle",
        prompt="repo config prompt",
    )

    module = _load_migration_module(monkeypatch)
    monkeypatch.setattr(module, "_repo_root", lambda: repo_root)
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(runtime_root))

    spec, prompt = module._load_phenotype_spec()

    assert spec["name"] == "Runtime Bundle"
    assert prompt == "runtime prompt"


def test_load_phenotype_spec_falls_back_to_repo_core_bundle(tmp_path, monkeypatch):
    _clear_runtime_env(monkeypatch)

    repo_root = tmp_path / "repo"
    _write_agent_bundle(
        repo_root / "packages" / "core" / "agents" / "phenotype_extractor",
        name="Repo Core Bundle",
        prompt="repo core prompt",
    )

    module = _load_migration_module(monkeypatch)
    monkeypatch.setattr(module, "_repo_root", lambda: repo_root)

    spec, prompt = module._load_phenotype_spec()

    assert spec["name"] == "Repo Core Bundle"
    assert prompt == "repo core prompt"


def test_load_phenotype_spec_falls_back_to_repo_config_bundle(tmp_path, monkeypatch):
    _clear_runtime_env(monkeypatch)

    repo_root = tmp_path / "repo"
    _write_agent_bundle(
        repo_root / "config" / "agents" / "phenotype_extractor",
        name="Repo Config Bundle",
        prompt="repo config prompt",
    )

    module = _load_migration_module(monkeypatch)
    monkeypatch.setattr(module, "_repo_root", lambda: repo_root)

    spec, prompt = module._load_phenotype_spec()

    assert spec["name"] == "Repo Config Bundle"
    assert prompt == "repo config prompt"
