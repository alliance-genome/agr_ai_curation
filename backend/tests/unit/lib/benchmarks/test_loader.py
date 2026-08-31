from pathlib import Path

import pytest

from src.lib.benchmarks.loader import BenchmarkCatalog, BenchmarkCatalogError


def _load(root: Path, **kwargs) -> BenchmarkCatalog:
    return BenchmarkCatalog(
        root,
        agent_ids=kwargs.get("agent_ids", {"gene"}),
        flow_ids=kwargs.get("flow_ids", {"Gene Curation"}),
        route_validator=kwargs.get("route_validator", lambda _model, _provider: None),
    )


def test_loads_profile_cases_and_stable_fixture_digest(benchmark_root):
    first = _load(benchmark_root)
    second = _load(benchmark_root)
    case = first.profiles[0].cases[0]

    assert first.profiles[0].profile.profile_id == "profile-1"
    assert case.fixture_digest.startswith("sha256:")
    assert case.fixture_digest == second.profiles[0].cases[0].fixture_digest
    assert case.expected == {"ok": True}


def test_checked_in_alliance_profiles_and_synthetic_cases_validate():
    root = Path(__file__).resolve().parents[5] / "packages" / "alliance" / "benchmarks"
    catalog = BenchmarkCatalog(
        root,
        agent_ids={"gene_validation", "ontology_term_validation"},
        flow_ids={"Gene Curation"},
        route_validator=lambda _model, _provider: None,
    )

    assert {loaded.profile.profile_id for loaded in catalog.profiles} == {
        "isolated-gene-agent-v1",
        "isolated-ontology-agent-v1",
        "flow-canary-gene-curation-v1",
    }
    assert sum(len(loaded.cases) for loaded in catalog.profiles) == 3


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ("schema_version: 2", "schema_version"),
        ("id: missing", "Unknown agent target"),
        ("fixture: cases/missing.json", "does not exist"),
    ],
)
def test_rejects_invalid_schema_target_and_missing_fixture(
    benchmark_root, replacement, message
):
    profile = benchmark_root / "profiles" / "profile.yaml"
    content = profile.read_text(encoding="utf-8")
    if replacement.startswith("schema"):
        content = content.replace("schema_version: 1", replacement)
    elif replacement.startswith("id"):
        content = content.replace("id: gene", replacement)
    else:
        content = content.replace("fixture: cases/case-1/input.json", replacement)
    profile.write_text(content, encoding="utf-8")

    with pytest.raises(BenchmarkCatalogError, match=message):
        _load(benchmark_root)


def test_rejects_duplicate_profile_and_route(benchmark_root):
    source = benchmark_root / "profiles" / "profile.yaml"
    (source.parent / "duplicate.yaml").write_text(
        source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    with pytest.raises(BenchmarkCatalogError, match="Duplicate benchmark profile ID"):
        _load(benchmark_root)

    (source.parent / "duplicate.yaml").unlink()
    content = source.read_text(encoding="utf-8")
    content = content.replace(
        "cases:\n",
        "  - provider: openai\n    model: gpt-5.6-sol\ncases:\n",
    )
    source.write_text(content, encoding="utf-8")
    with pytest.raises(
        BenchmarkCatalogError, match="routes must not contain duplicates"
    ):
        _load(benchmark_root)


def test_rejects_route_semantics_from_injected_canonical_validator(benchmark_root):
    def reject(_model, _provider):
        raise ValueError("unknown provider")

    with pytest.raises(BenchmarkCatalogError, match="Invalid route"):
        _load(benchmark_root, route_validator=reject)
