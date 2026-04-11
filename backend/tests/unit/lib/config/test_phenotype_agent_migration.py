"""Regression tests for optional system-agent seed migrations."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

import pytest
import yaml

from ..packages import find_repo_root


REPO_ROOT = find_repo_root(Path(__file__))
MIGRATION_SPECS = (
    (
        "phenotype_extractor",
        REPO_ROOT / "backend" / "alembic" / "versions" / "c4d5e6f7a8b9_add_phenotype_system_agent.py",
        "_load_phenotype_spec",
    ),
    (
        "allele_extractor",
        REPO_ROOT / "backend" / "alembic" / "versions" / "d5e6f7a8b9c0_add_allele_extractor_system_agent.py",
        "_load_agent_spec",
    ),
    (
        "disease_extractor",
        REPO_ROOT / "backend" / "alembic" / "versions" / "e6f7a8b9c0d1_add_disease_extractor_system_agent.py",
        "_load_agent_spec",
    ),
    (
        "chemical_extractor",
        REPO_ROOT / "backend" / "alembic" / "versions" / "f7a8b9c0d1e2_add_chemical_extractor_system_agent.py",
        "_load_agent_spec",
    ),
    (
        "gene_extractor",
        REPO_ROOT / "backend" / "alembic" / "versions" / "08b9c0d1e2f3_add_gene_extractor_system_agent.py",
        "_load_agent_spec",
    ),
)


def _load_migration_module(monkeypatch, *, module_name: str, migration_path: Path):
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

    spec = spec_from_file_location(module_name, migration_path)
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


def _make_source(agent_dir: Path, *, folder_name: str):
    return types.SimpleNamespace(
        folder_name=folder_name,
        agent_yaml=agent_dir / "agent.yaml",
        prompt_yaml=agent_dir / "prompt.yaml",
        source_file_display=lambda path: f"packages/alliance/agents/{folder_name}/{path.name}",
    )


def test_load_phenotype_spec_reads_resolved_bundle(tmp_path, monkeypatch):
    agent_dir = tmp_path / "packages" / "alliance" / "agents" / "phenotype_extractor"
    _write_agent_bundle(
        agent_dir,
        name="Phenotype Bundle",
        prompt="phenotype prompt",
    )

    module = _load_migration_module(
        monkeypatch,
        module_name="phenotype_agent_migration_test",
        migration_path=MIGRATION_SPECS[0][1],
    )
    monkeypatch.setattr(
        module,
        "resolve_agent_config_sources",
        lambda: (_make_source(agent_dir, folder_name="phenotype_extractor"),),
    )

    spec, prompt, source_file = module._load_phenotype_spec()

    assert spec["name"] == "Phenotype Bundle"
    assert prompt == "phenotype prompt"
    assert source_file == "packages/alliance/agents/phenotype_extractor/prompt.yaml"


@pytest.mark.parametrize(
    ("module_name", "migration_path", "loader_name"),
    MIGRATION_SPECS,
)
def test_optional_system_agent_spec_loader_returns_none_when_bundle_missing(
    monkeypatch,
    module_name,
    migration_path,
    loader_name,
):
    module = _load_migration_module(
        monkeypatch,
        module_name=f"{module_name}_missing_bundle_test",
        migration_path=migration_path,
    )
    monkeypatch.setattr(module, "resolve_agent_config_sources", lambda: ())

    assert getattr(module, loader_name)() is None


@pytest.mark.parametrize(
    ("module_name", "migration_path", "loader_name"),
    MIGRATION_SPECS,
)
def test_optional_system_agent_upgrade_noops_when_bundle_missing(
    monkeypatch,
    module_name,
    migration_path,
    loader_name,
):
    module = _load_migration_module(
        monkeypatch,
        module_name=f"{module_name}_upgrade_skip_test",
        migration_path=migration_path,
    )
    monkeypatch.setattr(module, "resolve_agent_config_sources", lambda: ())

    bind_calls = []
    module.op = types.SimpleNamespace(
        get_bind=lambda: bind_calls.append("get_bind") or object()
    )

    def _unexpected(*args, **kwargs):
        raise AssertionError(f"{loader_name} missing-bundle skip should not touch the database")

    monkeypatch.setattr(module, "_prompt_overrides_column_name", _unexpected)

    module.upgrade()

    assert bind_calls == []
