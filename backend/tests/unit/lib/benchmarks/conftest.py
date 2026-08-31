import json
from pathlib import Path

import pytest

from src.lib.benchmarks.loader import BenchmarkCatalog


@pytest.fixture
def benchmark_root(tmp_path: Path) -> Path:
    (tmp_path / "profiles").mkdir()
    case_dir = tmp_path / "cases" / "case-1"
    case_dir.mkdir(parents=True)
    (case_dir / "input.json").write_text(
        json.dumps({"messages": [{"role": "user", "content": "synthetic"}]}),
        encoding="utf-8",
    )
    (case_dir / "gold.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (tmp_path / "profiles" / "profile.yaml").write_text(
        """schema_version: 1
profile_id: profile-1
target:
  kind: agent
  id: gene
routes:
  - provider: openai
    model: gpt-5.6-sol
cases:
  - case_id: case-1
    fixture: cases/case-1/input.json
    expected: cases/case-1/gold.json
scorers:
  - id: exact-json
""",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def benchmark_catalog(benchmark_root: Path) -> BenchmarkCatalog:
    return BenchmarkCatalog(
        benchmark_root,
        agent_ids={"gene"},
        flow_ids={"Gene Curation"},
        route_validator=lambda _model, _provider: None,
    )
