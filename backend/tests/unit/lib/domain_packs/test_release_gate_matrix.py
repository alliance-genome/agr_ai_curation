"""Tests for the 0.7.0 domain-envelope release gate matrix."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml


pytestmark = [
    pytest.mark.domain_envelope_release_gate,
    pytest.mark.provider_agnostic_domain_pack,
]

BACKEND_DIR = Path(__file__).resolve().parents[4]
MATRIX_PATH = (
    BACKEND_DIR
    / "tests"
    / "fixtures"
    / "domain_packs"
    / "release_gate_matrix.yaml"
)


def _matrix() -> dict[str, Any]:
    return yaml.safe_load(MATRIX_PATH.read_text(encoding="utf-8"))


def _path_file_entries(path_file: str) -> list[str]:
    path = BACKEND_DIR / path_file
    assert path.is_file(), f"Release-gate path file is missing: {path_file}"
    entries: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        assert (BACKEND_DIR / line).exists(), (
            f"Release-gate path file {path_file} references missing path {line}"
        )
        entries.append(line)
    assert entries, f"Release-gate path file is empty: {path_file}"
    return entries


def test_domain_envelope_release_gate_matrix_covers_required_surfaces():
    matrix = _matrix()
    suites = matrix["suites"]
    required_surfaces = set(matrix["required_surfaces"])

    covered_surfaces = {
        surface
        for suite in suites.values()
        for surface in suite.get("surfaces", [])
    }

    assert required_surfaces <= covered_surfaces
    assert suites["provider_agnostic_unit"]["external_dependencies"] == []
    assert suites["alliance_live_db_contract"]["explicit_opt_in_env"] == (
        "ALLIANCE_LIVE_DB_CONTRACT_TESTS"
    )


@pytest.mark.parametrize(
    "suite_key",
    (
        "provider_agnostic_unit",
        "alliance_domain_pack_contract",
        "alliance_live_db_contract",
    ),
)
def test_release_gate_matrix_path_files_resolve_to_existing_tests(suite_key: str):
    suite = _matrix()["suites"][suite_key]
    entries = _path_file_entries(suite["path_file"])

    if suite_key == "alliance_live_db_contract":
        assert entries == [
            "tests/contract/alliance/domain_packs/test_live_db_lookup_contract.py"
        ]
    else:
        assert "test_live_db_lookup_contract.py" not in "\n".join(entries)
