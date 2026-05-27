"""Package-owned Alliance literature reference lookup tool tests."""

from __future__ import annotations

import importlib.metadata as importlib_metadata
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from . import find_repo_root
from src.lib.packages.tool_registry import load_tool_registry

REPO_ROOT = find_repo_root(Path(__file__))
ALLIANCE_PACKAGE_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
sys.path.insert(0, str(ALLIANCE_PACKAGE_SRC))

import agr_ai_curation_alliance.tools.literature_references as literature_references  # noqa: E402
from agr_curation_api.exceptions import AGRAPIError  # noqa: E402


def _tool_fn():
    return _unwrap_function_tool_callable(
        literature_references.agr_literature_reference_lookup,
        "agr_literature_reference_lookup",
    )


def _unwrap_function_tool_callable(tool: Any, target_name: str) -> Any:
    visited_ids: set[int] = set()
    found = None

    def _walk(candidate: Any, depth: int = 0) -> None:
        nonlocal found
        if candidate is None or found is not None or depth > 6:
            return
        obj_id = id(candidate)
        if obj_id in visited_ids:
            return
        visited_ids.add(obj_id)

        if callable(candidate) and getattr(candidate, "__name__", "") == target_name:
            found = candidate
            return

        if callable(candidate):
            for cell in getattr(candidate, "__closure__", ()) or ():
                try:
                    _walk(cell.cell_contents, depth + 1)
                except Exception:
                    continue

        for attr in (
            "on_invoke_tool",
            "_invoke_tool_impl",
            "_function_tool",
            "func",
            "function",
            "_func",
            "_function",
            "handler",
        ):
            if hasattr(candidate, attr):
                _walk(getattr(candidate, attr), depth + 1)

        obj_dict = getattr(candidate, "__dict__", None)
        if isinstance(obj_dict, dict):
            for value in obj_dict.values():
                if callable(value) or hasattr(value, "__dict__"):
                    _walk(value, depth + 1)

    _walk(tool)
    if found is None:
        raise RuntimeError(f"Unable to locate callable for tool {target_name!r}")
    return found


def _reference(
    *,
    curie: str = "AGRKB:101000000924191",
    title: str = "Suppressed Helicobocter pylori study",
    citation: str = "Hahm KB et al., 1997",
    cross_references: list[str] | None = None,
    source: str | None = "literature_es",
):
    return SimpleNamespace(
        reference_id=None,
        curie=curie,
        title=title,
        short_citation=citation,
        cross_references=cross_references or ["PMID:27528223"],
        source=source,
        obsolete=False,
    )


def test_default_factory_uses_api_client_db_mode(monkeypatch):
    calls = []
    fake_module = ModuleType("agr_curation_api")

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    fake_module.AGRCurationAPIClient = FakeClient
    monkeypatch.setitem(sys.modules, "agr_curation_api", fake_module)

    assert isinstance(literature_references._default_client_factory(), FakeClient)
    assert calls == [{"data_source": "db"}]


def test_numpy2_elasticsearch_compat_restores_removed_aliases(monkeypatch):
    import numpy as np

    monkeypatch.delattr(np, "float_", raising=False)
    monkeypatch.delattr(np, "complex_", raising=False)

    literature_references._ensure_elasticsearch_numpy2_compat()

    assert np.float_ is np.float64
    assert np.complex_ is np.complex128


@pytest.mark.parametrize(
    ("identifier", "curie", "cross_references"),
    [
        ("PMID:27528223", "AGRKB:101000000924191", ["PMID:27528223"]),
        ("DOI:10.1093/genetics/iyad001", "AGRKB:101000000924192", ["DOI:10.1093/genetics/iyad001"]),
        ("AGRKB:101000000924191", "AGRKB:101000000924191", ["PMID:27528223"]),
    ],
)
def test_exact_identifier_lookup_returns_resolved_reference(
    monkeypatch,
    identifier,
    curie,
    cross_references,
):
    calls = []

    class FakeClient:
        def get_literature_reference(self, value):
            calls.append(value)
            return _reference(curie=curie, cross_references=cross_references)

    monkeypatch.setattr(
        literature_references,
        "_client_factory",
        lambda: FakeClient(),
    )

    result = _tool_fn()(
        method="get_literature_reference",
        identifier=identifier,
    )

    assert result.status == "ok"
    assert result.lookup_status == "success"
    assert result.source == "literature_es"
    assert result.count == 1
    assert result.resolved_reference["curie"] == curie
    assert result.resolved_reference["source"] == "literature_es"
    assert result.resolved_reference["matched_identifier"] == identifier
    assert result.lookup_attempts[0]["source"] == "literature_es"
    assert result.lookup_attempts[0]["method"] == "get_literature_reference"
    assert calls == [identifier]


def test_fuzzy_title_search_returns_candidate_with_match_context(monkeypatch):
    calls = []

    class FakeClient:
        def search_literature_references(self, **kwargs):
            calls.append(kwargs)
            return [
                _reference(
                    title="Suppressed Helicobocter pylori-induced gastritis",
                    citation="Hahm KB et al.",
                )
            ]

    monkeypatch.setattr(
        literature_references,
        "_client_factory",
        lambda: FakeClient(),
    )

    result = _tool_fn()(
        method="search_literature_references",
        query="Hahm KB Suppressed Helicobocter pylori",
        exact_match=False,
        limit=20,
    )

    assert result.status == "ok"
    assert result.lookup_status == "success"
    assert result.count == 1
    assert result.resolved_reference["title"] == "Suppressed Helicobocter pylori-induced gastritis"
    assert result.resolved_reference["matched_citation"] == "Hahm KB et al."
    assert calls == [
        {
            "query": "Hahm KB Suppressed Helicobocter pylori",
            "exact_match": False,
            "limit": 20,
        }
    ]


def test_ambiguous_search_returns_candidate_references_and_ambiguity(monkeypatch):
    class FakeClient:
        def search_literature_references(self, **_kwargs):
            return [
                _reference(curie="AGRKB:1", title="Reference title one"),
                _reference(curie="AGRKB:2", title="Reference title two"),
            ]

    monkeypatch.setattr(
        literature_references,
        "_client_factory",
        lambda: FakeClient(),
    )

    result = _tool_fn()(
        method="search_literature_references",
        query="Reference title",
    )

    assert result.status == "ok"
    assert result.lookup_status == "ambiguous"
    assert result.resolved_reference is None
    assert [candidate["curie"] for candidate in result.candidate_references] == [
        "AGRKB:1",
        "AGRKB:2",
    ]
    assert result.ambiguity == {
        "query": "Reference title",
        "candidate_count": 2,
        "source": "literature_es",
        "explanation": result.message,
    }


def test_no_match_returns_curator_safe_no_match_details(monkeypatch):
    class FakeClient:
        def get_literature_reference(self, _identifier):
            return None

    monkeypatch.setattr(
        literature_references,
        "_client_factory",
        lambda: FakeClient(),
    )

    result = _tool_fn()(
        method="get_literature_reference",
        identifier="MGI:6254583",
    )

    assert result.status == "ok"
    assert result.lookup_status == "not_found"
    assert result.count == 0
    assert result.candidate_references == []
    assert result.no_match["query"] == "MGI:6254583"
    assert "No literature reference matched" in result.message


def test_missing_upstream_source_is_preserved_as_none(monkeypatch):
    class FakeClient:
        def get_literature_reference(self, _identifier):
            return _reference(source=None)

    monkeypatch.setattr(
        literature_references,
        "_client_factory",
        lambda: FakeClient(),
    )

    result = _tool_fn()(
        method="get_literature_reference",
        identifier="PMID:27528223",
    )

    assert result.status == "ok"
    assert result.resolved_reference["source"] is None


@pytest.mark.parametrize(
    ("error", "classification", "message_fragment"),
    [
        (
            ValueError("Literature Elasticsearch is not configured: set ELASTICSEARCH_HOST"),
            "blocked",
            "configuration is missing",
        ),
        (
            AGRAPIError("Literature Elasticsearch is not configured: set ELASTICSEARCH_HOST"),
            "blocked",
            "configuration is missing",
        ),
        (
            RuntimeError("Elasticsearch query failed for literature references: timeout"),
            "transient",
            "could not reach",
        ),
        (
            AGRAPIError("Elasticsearch query failed for literature references: timeout"),
            "transient",
            "could not reach",
        ),
    ],
)
def test_upstream_configuration_or_connection_failure_returns_structured_error(
    monkeypatch,
    error,
    classification,
    message_fragment,
):
    class FakeClient:
        def get_literature_reference(self, _identifier):
            raise error

    monkeypatch.setattr(
        literature_references,
        "_client_factory",
        lambda: FakeClient(),
    )

    result = _tool_fn()(
        method="get_literature_reference",
        identifier="PMID:27528223",
    )

    assert result.status == "error"
    assert result.lookup_status == classification
    assert result.failure_classification == classification
    assert message_fragment in result.message
    assert result.lookup_attempts[0]["error_type"] == type(error).__name__


def test_invalid_explicit_limit_raises_validation_error():
    with pytest.raises(ValueError, match="limit must be greater than or equal to 1"):
        _tool_fn()(
            method="search_literature_references",
            query="Reference title",
            limit=0,
        )


def test_unexpected_tool_bug_is_not_reported_as_upstream_failure(monkeypatch):
    class FakeClient:
        def search_literature_references(self, **_kwargs):
            return [object()]

    monkeypatch.setattr(
        literature_references,
        "_client_factory",
        lambda: FakeClient(),
    )

    with pytest.raises(TypeError, match="Cannot serialize literature reference"):
        _tool_fn()(
            method="search_literature_references",
            query="Reference title",
        )


def test_alliance_binding_exposes_literature_reference_tool_id():
    registry = load_tool_registry(
        REPO_ROOT / "packages",
        runtime_version="1.5.0",
        supported_package_api_version="1.0.0",
    )

    binding = registry.get("agr_literature_reference_lookup")

    assert binding is not None
    assert binding.binding_kind.value == "static"
    assert binding.import_path == (
        "agr_ai_curation_alliance.tools.literature_references:"
        "agr_literature_reference_lookup"
    )
    assert binding.metadata["methods"]["get_literature_reference"]["source"] == "literature_es"
    assert binding.metadata["methods"]["search_literature_references"]["source"] == "literature_es"


def test_runtime_api_client_dependency_exposes_literature_helpers():
    version = importlib_metadata.version("agr-curation-api-client")
    major, minor, patch = (int(part) for part in version.split(".")[:3])

    from agr_curation_api import AGRCurationAPIClient

    assert (major, minor, patch) >= (0, 10, 1)
    assert hasattr(AGRCurationAPIClient, "get_literature_reference")
    assert hasattr(AGRCurationAPIClient, "search_literature_references")


@pytest.mark.skipif(
    not (
        os.environ.get("RUN_LITERATURE_ES_SMOKE") == "1"
        and os.environ.get("ELASTICSEARCH_HOST")
    ),
    reason="Live literature ES smoke requires RUN_LITERATURE_ES_SMOKE=1 and Elasticsearch env.",
)
def test_live_literature_es_smoke_when_environment_is_available(monkeypatch):
    monkeypatch.setattr(
        literature_references,
        "_client_factory",
        literature_references._default_client_factory,
    )

    pmid_result = _tool_fn()(
        method="get_literature_reference",
        identifier="PMID:27528223",
    )
    fuzzy_result = _tool_fn()(
        method="search_literature_references",
        query="Hahm KB Suppressed Helicobocter pylori",
        limit=5,
    )

    assert pmid_result.status == "ok"
    assert pmid_result.lookup_status == "success"
    assert pmid_result.resolved_reference["curie"] == "AGRKB:101000000924191"
    assert fuzzy_result.status == "ok"
    assert fuzzy_result.count >= 1
