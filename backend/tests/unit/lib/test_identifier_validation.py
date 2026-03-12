"""Tests for identifier prefix validation runtime-state loading."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.lib import identifier_validation


@pytest.fixture(autouse=True)
def _reset_prefix_cache(monkeypatch):
    identifier_validation.load_prefixes.cache_clear()
    for variable in (
        "AGR_RUNTIME_ROOT",
        "AGR_RUNTIME_STATE_DIR",
        "IDENTIFIER_PREFIX_STATE_DIR",
        "IDENTIFIER_PREFIX_FILE_PATH",
    ):
        monkeypatch.delenv(variable, raising=False)
    yield
    identifier_validation.load_prefixes.cache_clear()


def _write_prefix_file(path: Path, prefixes: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"prefixes": prefixes}),
        encoding="utf-8",
    )


def test_load_prefixes_reads_runtime_identifier_prefix_file(monkeypatch, tmp_path: Path):
    runtime_root = tmp_path / "runtime"
    prefix_file = runtime_root / "state" / "identifier_prefixes" / "identifier_prefixes.json"
    _write_prefix_file(prefix_file, ["FB", "WB"])
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(runtime_root))

    assert identifier_validation.get_prefix_file_path() == prefix_file
    assert identifier_validation.load_prefixes() == {"FB", "WB"}


def test_load_prefixes_raises_when_identifier_prefix_file_missing(monkeypatch, tmp_path: Path):
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(runtime_root))

    with pytest.raises(identifier_validation.PrefixLoadError, match="Prefix file not found"):
        identifier_validation.load_prefixes()


def test_load_prefixes_raises_when_prefix_list_empty(monkeypatch, tmp_path: Path):
    prefix_file = tmp_path / "runtime" / "state" / "identifier_prefixes" / "identifier_prefixes.json"
    _write_prefix_file(prefix_file, [])
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(tmp_path / "runtime"))

    with pytest.raises(identifier_validation.PrefixLoadError, match="contains no prefixes"):
        identifier_validation.load_prefixes()


def test_is_valid_curie_uses_loaded_prefixes(monkeypatch, tmp_path: Path):
    prefix_file = tmp_path / "runtime" / "state" / "identifier_prefixes" / "identifier_prefixes.json"
    _write_prefix_file(prefix_file, ["FB"])
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(tmp_path / "runtime"))

    assert identifier_validation.is_valid_curie("FB:12345") is True
    assert identifier_validation.is_valid_curie("WB:12345") is False
    assert identifier_validation.is_valid_curie("missing-colon") is False


def test_load_prefixes_cache_can_be_cleared_when_path_changes(monkeypatch, tmp_path: Path):
    first_file = tmp_path / "runtime" / "state" / "identifier_prefixes" / "first.json"
    second_file = tmp_path / "runtime" / "state" / "identifier_prefixes" / "second.json"
    _write_prefix_file(first_file, ["FB"])
    _write_prefix_file(second_file, ["WB"])
    monkeypatch.setenv("IDENTIFIER_PREFIX_FILE_PATH", str(first_file))

    assert identifier_validation.load_prefixes() == {"FB"}

    monkeypatch.setenv("IDENTIFIER_PREFIX_FILE_PATH", str(second_file))
    assert identifier_validation.load_prefixes() == {"FB"}

    identifier_validation.load_prefixes.cache_clear()
    assert identifier_validation.load_prefixes() == {"WB"}
