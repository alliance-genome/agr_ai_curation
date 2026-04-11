"""Unit tests for the public package-runtime file-output wrappers."""

from types import SimpleNamespace

import pytest

runtime_file_outputs = pytest.importorskip("agr_ai_curation_runtime.file_outputs")


def test_get_current_file_output_context_reads_backend_context(monkeypatch):
    monkeypatch.setattr(
        runtime_file_outputs,
        "_load_context_module",
        lambda: SimpleNamespace(
            get_current_trace_id=lambda: "trace-123",
            get_current_session_id=lambda: "session-456",
            get_current_user_id=lambda: "user-789",
        ),
    )

    assert runtime_file_outputs.get_current_file_output_context() == (
        runtime_file_outputs.FileOutputRequestContext(
            trace_id="trace-123",
            session_id="session-456",
            curator_id="user-789",
        )
    )


def test_persist_file_output_registers_saved_file(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class _FakeStorageService:
        def save_output(self, **kwargs):
            captured["storage_kwargs"] = kwargs
            return (
                tmp_path / "outputs" / "trace-123_gene_results.csv",
                "hash-abc",
                27,
                ["Line 2, cell 1: warning"],
            )

    class _FakeFileOutput:
        def __init__(self, **kwargs):
            self.id = "file-123"
            self.__dict__.update(kwargs)

    class _FakeSession:
        def add(self, file_output):
            captured["file_output"] = file_output

        def commit(self):
            captured["committed"] = True

        def refresh(self, file_output):
            captured["refreshed"] = file_output

        def rollback(self):
            captured["rolled_back"] = True

        def close(self):
            captured["closed"] = True

    fake_session = _FakeSession()

    monkeypatch.setattr(
        runtime_file_outputs,
        "_load_storage_service_class",
        lambda: _FakeStorageService,
    )
    monkeypatch.setattr(
        runtime_file_outputs,
        "_load_session_factory",
        lambda: lambda: fake_session,
    )
    monkeypatch.setattr(
        runtime_file_outputs,
        "_load_file_output_model",
        lambda: _FakeFileOutput,
    )

    result = runtime_file_outputs.persist_file_output(
        content="gene_id,symbol\nFBgn0001,Notch\n",
        file_type="csv",
        descriptor="gene_results",
        agent_name="csv_formatter",
        context=runtime_file_outputs.FileOutputRequestContext(
            trace_id="trace-123",
            session_id="session-456",
            curator_id="user-789",
        ),
    )

    assert captured["storage_kwargs"] == {
        "trace_id": "trace-123",
        "session_id": "session-456",
        "content": "gene_id,symbol\nFBgn0001,Notch\n",
        "file_type": "csv",
        "descriptor": "gene_results",
    }

    file_output = captured["file_output"]
    assert file_output.filename == "trace-123_gene_results.csv"
    assert file_output.file_path == str(tmp_path / "outputs" / "trace-123_gene_results.csv")
    assert file_output.file_type == "csv"
    assert file_output.file_size == 27
    assert file_output.file_hash == "hash-abc"
    assert file_output.curator_id == "user-789"
    assert file_output.session_id == "session-456"
    assert file_output.trace_id == "trace-123"
    assert file_output.agent_name == "csv_formatter"
    assert captured["committed"] is True
    assert captured["refreshed"] is file_output
    assert captured["closed"] is True
    assert "rolled_back" not in captured

    assert result == runtime_file_outputs.PersistedFileOutput(
        file_id="file-123",
        filename="trace-123_gene_results.csv",
        file_type="csv",
        size_bytes=27,
        hash_sha256="hash-abc",
        download_url="/api/files/file-123/download",
        warnings=("Line 2, cell 1: warning",),
    )


def test_build_effective_context_generates_independent_fallback_ids(monkeypatch):
    fallback_values = iter(
        [
            SimpleNamespace(hex="a" * 32),
            SimpleNamespace(hex="b" * 32),
        ]
    )
    monkeypatch.setattr(
        runtime_file_outputs.uuid,
        "uuid4",
        lambda: next(fallback_values),
    )

    assert runtime_file_outputs._build_effective_context(
        runtime_file_outputs.FileOutputRequestContext(
            trace_id=None,
            session_id=None,
            curator_id=None,
        )
    ) == ("a" * 32, "b" * 8, "unknown")
