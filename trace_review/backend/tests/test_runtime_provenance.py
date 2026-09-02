import hashlib
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.main import _health_payload
from src.runtime_provenance import get_runtime_provenance
from src.services.cache_manager import CacheManager


def test_runtime_provenance_reports_build_sdk_and_source_identity(
    monkeypatch,
    tmp_path: Path,
):
    trace_extractor = tmp_path / "trace_extractor.py"
    trace_extractor.write_text("events_only = True\n", encoding="utf-8")
    monkeypatch.setenv("TRACE_REVIEW_BUILD_REF", "v0.9.1")
    monkeypatch.setenv("TRACE_REVIEW_GIT_SHA", "a" * 40)

    assert get_runtime_provenance(trace_extractor) == {
        "build_ref": "v0.9.1",
        "git_sha": "a" * 40,
        "langfuse_sdk_version": version("langfuse"),
        "trace_extractor_sha256": hashlib.sha256(
            trace_extractor.read_bytes()
        ).hexdigest(),
    }


def test_runtime_provenance_marks_unbaked_checkout_identity_as_absent(monkeypatch):
    monkeypatch.delenv("TRACE_REVIEW_BUILD_REF", raising=False)
    monkeypatch.delenv("TRACE_REVIEW_GIT_SHA", raising=False)

    provenance = get_runtime_provenance()

    assert provenance["build_ref"] is None
    assert provenance["git_sha"] is None
    assert provenance["langfuse_sdk_version"] == version("langfuse")
    assert len(provenance["trace_extractor_sha256"] or "") == 64


@patch("src.main.get_runtime_provenance")
def test_health_payload_includes_runtime_provenance(runtime_provenance):
    runtime_provenance.return_value = {
        "build_ref": "v0.9.1",
        "git_sha": "b" * 40,
        "langfuse_sdk_version": "4.7.1",
        "trace_extractor_sha256": "source-sha",
    }
    app = SimpleNamespace(
        state=SimpleNamespace(cache_manager=CacheManager(ttl_hours=1))
    )

    payload, status_code = _health_payload(app)

    assert status_code == 200
    assert payload["runtime"] == runtime_provenance.return_value


@patch("src.main.get_runtime_provenance")
def test_starting_health_payload_includes_runtime_provenance(runtime_provenance):
    runtime_provenance.return_value = {
        "build_ref": "v0.9.1",
        "git_sha": "c" * 40,
        "langfuse_sdk_version": "4.7.1",
        "trace_extractor_sha256": "source-sha",
    }
    app = SimpleNamespace(state=SimpleNamespace())

    payload, status_code = _health_payload(app)

    assert status_code == 503
    assert payload["runtime"] == runtime_provenance.return_value
