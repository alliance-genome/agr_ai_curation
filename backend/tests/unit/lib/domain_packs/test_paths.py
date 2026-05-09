"""Unit tests for provider-agnostic domain-pack path helpers."""

from pathlib import Path

import pytest

from src.lib.domain_packs import paths


@pytest.fixture(autouse=True)
def _clear_domain_pack_path_env(monkeypatch):
    for variable in ("AGR_RUNTIME_ROOT", "AGR_DOMAIN_PACKS_DIR"):
        monkeypatch.delenv(variable, raising=False)


def test_default_domain_packs_dir_uses_runtime_root():
    assert paths.get_domain_packs_dir() == Path("/runtime/domain_packs")


def test_domain_packs_dir_honors_effective_runtime_root(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime-host"
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(runtime_root))

    assert paths.get_domain_packs_dir() == runtime_root / "domain_packs"


def test_relative_domain_packs_dir_resolves_under_effective_runtime_root(
    monkeypatch,
    tmp_path,
):
    runtime_root = tmp_path / "runtime-host"
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("AGR_DOMAIN_PACKS_DIR", "published/domain-packs")

    assert paths.get_domain_packs_dir() == runtime_root / "published/domain-packs"


def test_absolute_domain_packs_dir_override_is_honored(monkeypatch, tmp_path):
    domain_packs_dir = tmp_path / "domain-packs"
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(tmp_path / "runtime-host"))
    monkeypatch.setenv("AGR_DOMAIN_PACKS_DIR", str(domain_packs_dir))

    assert paths.get_domain_packs_dir() == domain_packs_dir


def test_relative_domain_packs_dir_rejects_parent_directory_traversal(monkeypatch):
    monkeypatch.setenv("AGR_DOMAIN_PACKS_DIR", "../escape")

    with pytest.raises(ValueError, match="must not traverse parent directories"):
        paths.get_domain_packs_dir()
