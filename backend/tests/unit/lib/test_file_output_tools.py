"""Unit tests for file_output_tools module.

Tests the tool implementation functions and their context variable integration.
"""
import json
import importlib
from typing import Any, Literal
from uuid import uuid4
import pytest
from pathlib import Path


def _context_module():
    """Load context helpers lazily to avoid stale module references in full-suite runs."""
    return importlib.import_module("src.lib.context")


def clear_context():
    _context_module().clear_context()


def set_current_trace_id(value: str):
    _context_module().set_current_trace_id(value)


def set_current_session_id(value: str):
    _context_module().set_current_session_id(value)


def set_current_user_id(value: str):
    _context_module().set_current_user_id(value)


class _ProjectedFileOutputStore:
    """Tiny in-memory session seam for projected file-output unit tests."""

    def __init__(self):
        self.rows: list[Any] = []

    def session_factory(self):
        return _ProjectedFileOutputSession(self)

    def add(self, row: Any) -> None:
        self.ensure_id(row)
        if row not in self.rows:
            self.rows.append(row)

    def ensure_id(self, row: Any) -> None:
        if getattr(row, "id", None) is None:
            row.id = uuid4()

    def find(
        self,
        _db: Any,
        *,
        trace_id: str,
        file_type: str,
        descriptor: str,
        file_path: str,
    ) -> Any | None:
        for row in self.rows:
            if str(row.file_path) == str(file_path):
                return row

        for row in self.rows:
            metadata = row.file_metadata or {}
            if not isinstance(metadata, dict):
                continue
            if (
                row.trace_id == trace_id
                and row.file_type == file_type
                and metadata.get("structured_projection") is True
                and str(metadata.get("descriptor") or "") == descriptor
            ):
                return row
        return None


class _ProjectedFileOutputSession:
    def __init__(self, store: _ProjectedFileOutputStore):
        self._store = store

    def add(self, row: Any) -> None:
        self._store.add(row)

    def commit(self) -> None:
        return None

    def refresh(self, row: Any) -> None:
        self._store.ensure_id(row)

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


def _install_projected_file_output_store(monkeypatch, file_output_tools):
    store = _ProjectedFileOutputStore()
    monkeypatch.setattr(file_output_tools, "SessionLocal", store.session_factory)
    monkeypatch.setattr(file_output_tools, "_find_existing_projected_file_output", store.find)
    return store


class TestGetContextFromContextvars:
    """Tests for _get_context_from_contextvars helper."""

    def setup_method(self):
        clear_context()

    def teardown_method(self):
        clear_context()

    def test_returns_none_when_no_context_set(self):
        """All values should be None when no context is set."""
        from src.lib.openai_agents.tools.file_output_tools import (
            _get_context_from_contextvars,
        )

        trace_id, session_id, curator_id = _get_context_from_contextvars()

        assert trace_id is None
        assert session_id is None
        assert curator_id is None

    def test_returns_values_when_context_set(self):
        """Returns the context values that have been set."""
        from src.lib.openai_agents.tools.file_output_tools import (
            _get_context_from_contextvars,
        )

        set_current_trace_id("trace-abc123")
        set_current_session_id("session-xyz789")
        set_current_user_id("curator@example.com")

        trace_id, session_id, curator_id = _get_context_from_contextvars()

        assert trace_id == "trace-abc123"
        assert session_id == "session-xyz789"
        assert curator_id == "curator@example.com"


class TestBuildDownloadUrl:
    """Tests for _build_download_url helper."""

    def test_builds_correct_url(self):
        from src.lib.openai_agents.tools.file_output_tools import _build_download_url

        file_id = "abc123-def456"
        url = _build_download_url(file_id)

        assert url == "/api/files/abc123-def456/download"


class TestRawWriterRemoval:
    """Guards proving legacy model-authored file writers are physically absent."""

    def test_raw_save_helpers_are_not_exported(self):
        from src.lib.openai_agents.tools import file_output_tools

        forbidden = {
            "_save_csv_impl",
            "_save_tsv_impl",
            "_save_json_impl",
            "create_csv_tool",
            "create_tsv_tool",
            "create_json_tool",
        }
        assert not forbidden & set(dir(file_output_tools))


class TestSaveProjectedFileOutput:
    """Tests for structured projection-owned file saves."""

    def setup_method(self):
        clear_context()

    def teardown_method(self):
        clear_context()

    @pytest.mark.asyncio
    async def test_upserts_same_run_descriptor_and_format(self, tmp_path, monkeypatch):
        """A repeated structured finalize should update one downloadable file row."""
        from src.lib.file_outputs import FileOutputStorageService
        from src.lib.flows.output_projection import (
            FlowOutputColumnSpec,
            FlowOutputProjectionResult,
        )
        from src.lib.openai_agents.tools import file_output_tools
        from src.lib.openai_agents.tools.file_output_tools import save_projected_file_output

        trace_id = uuid4().hex
        session_id = f"session-{uuid4().hex[:12]}"
        curator_id = "curator-projected-save"
        set_current_trace_id(trace_id)
        set_current_session_id(session_id)
        set_current_user_id(curator_id)

        storage = FileOutputStorageService(base_path=tmp_path)
        monkeypatch.setattr(
            file_output_tools,
            "FileOutputStorageService",
            lambda: storage,
        )
        store = _install_projected_file_output_store(monkeypatch, file_output_tools)

        def projection(
            symbol: str,
            output_format: Literal["csv", "tsv"] = "csv",
        ) -> FlowOutputProjectionResult:
            return FlowOutputProjectionResult(
                format=output_format,
                row_source="object",
                columns=[
                    FlowOutputColumnSpec(
                        key="symbol",
                        header="Symbol",
                        field_ref="object.payload.symbol",
                    )
                ],
                rows=[{"symbol": symbol}],
                total_count=1,
            )

        first = await save_projected_file_output(
            "csv",
            projection("Notch"),
            "gene_results",
            "csv_output_formatter",
        )
        second = await save_projected_file_output(
            "csv",
            projection("DeltaGene"),
            "gene_results",
            "csv_output_formatter",
        )
        third = await save_projected_file_output(
            "csv",
            projection("Wingless"),
            "other_gene_results",
            "csv_output_formatter",
        )
        fourth = await save_projected_file_output(
            "tsv",
            projection("DeltaGene", output_format="tsv"),
            "gene_results",
            "tsv_output_formatter",
        )

        assert first["file_id"] == second["file_id"]
        assert first["filename"] == second["filename"]
        assert first["filename"] == f"{trace_id}_gene_results.csv"
        assert third["file_id"] != first["file_id"]
        assert third["filename"] == f"{trace_id}_other_gene_results.csv"
        assert fourth["file_id"] != first["file_id"]
        assert fourth["filename"] == f"{trace_id}_gene_results.tsv"

        rows = sorted(store.rows, key=lambda row: row.filename)
        assert len(rows) == 3
        saved = next(row for row in rows if row.filename == first["filename"])
        saved_tsv = next(row for row in rows if row.filename == fourth["filename"])
        assert saved.agent_name == "csv_output_formatter"
        assert saved.curator_id == curator_id
        assert saved.session_id == session_id
        assert saved.file_metadata["structured_projection"] is True
        assert saved.file_metadata["descriptor"] == "gene_results"
        assert saved.file_size == second["size_bytes"]
        assert saved.file_hash == second["hash_sha256"]
        assert Path(saved.file_path).read_text(encoding="utf-8").replace("\r\n", "\n") == (
            "symbol\nDeltaGene\n"
        )
        assert saved_tsv.agent_name == "tsv_output_formatter"
        assert saved_tsv.file_type == "tsv"
        assert Path(saved_tsv.file_path).read_text(encoding="utf-8").replace("\r\n", "\n") == (
            "symbol\nDeltaGene\n"
        )

    @pytest.mark.asyncio
    async def test_projected_save_reuses_structured_row_by_trace_descriptor_and_format(
        self, tmp_path, monkeypatch
    ):
        """Run/descriptor/format identity should survive a previous dated file path."""
        from src.lib.file_outputs import FileOutputStorageService
        from src.lib.flows.output_projection import (
            FlowOutputColumnSpec,
            FlowOutputProjectionResult,
        )
        from src.lib.openai_agents.tools import file_output_tools
        from src.lib.openai_agents.tools.file_output_tools import save_projected_file_output
        from src.models.sql.file_output import FileOutput

        trace_id = uuid4().hex
        session_id = f"session-{uuid4().hex[:12]}"
        set_current_trace_id(trace_id)
        set_current_session_id(session_id)
        set_current_user_id("curator-projected-save")

        stale_path = tmp_path / "outputs" / "2026-06-16" / session_id / f"{trace_id}_gene_results.csv"
        stale_path.parent.mkdir(parents=True, exist_ok=True)
        stale_path.write_text("symbol\nOld\n", encoding="utf-8")
        stale_row = FileOutput(
            filename=f"{trace_id}_gene_results.csv",
            file_path=str(stale_path),
            file_type="csv",
            file_size=stale_path.stat().st_size,
            file_hash="old-hash",
            curator_id="curator-projected-save",
            session_id=session_id,
            trace_id=trace_id,
            agent_name="csv_output_formatter",
            file_metadata={
                "structured_projection": True,
                "descriptor": "gene_results",
            },
        )
        stale_row.id = uuid4()
        stale_id = str(stale_row.id)

        storage = FileOutputStorageService(base_path=tmp_path)
        monkeypatch.setattr(
            file_output_tools,
            "FileOutputStorageService",
            lambda: storage,
        )
        store = _install_projected_file_output_store(monkeypatch, file_output_tools)
        store.add(stale_row)
        projection = FlowOutputProjectionResult(
            format="csv",
            row_source="object",
            columns=[
                FlowOutputColumnSpec(
                    key="symbol",
                    header="Symbol",
                    field_ref="object.payload.symbol",
                )
            ],
            rows=[{"symbol": "DeltaGene"}],
            total_count=1,
        )

        result = await save_projected_file_output(
            "csv",
            projection,
            "gene_results",
            "csv_output_formatter",
        )

        assert result["file_id"] == stale_id
        assert result["filename"] == f"{trace_id}_gene_results.csv"
        assert len(store.rows) == 1
        saved = store.rows[0]
        assert str(saved.id) == stale_id
        assert Path(saved.file_path).parent == tmp_path / "outputs" / "structured" / trace_id
        assert saved.file_hash == result["hash_sha256"]
        assert Path(saved.file_path).read_text(encoding="utf-8").replace("\r\n", "\n") == (
            "symbol\nDeltaGene\n"
        )

    @pytest.mark.asyncio
    async def test_projected_save_requires_trace_session_and_curator_context(self):
        """Structured formatter saves should fail loudly without audited context."""
        from src.lib.flows.output_projection import (
            FlowOutputColumnSpec,
            FlowOutputProjectionResult,
        )
        from src.lib.openai_agents.tools.file_output_tools import save_projected_file_output

        projection = FlowOutputProjectionResult(
            format="csv",
            row_source="object",
            columns=[
                FlowOutputColumnSpec(
                    key="symbol",
                    field_ref="object.payload.symbol",
                )
            ],
            rows=[{"symbol": "Notch"}],
            total_count=1,
        )

        with pytest.raises(ValueError, match="requires trace_id context"):
            await save_projected_file_output(
                "csv",
                projection,
                "gene_results",
                "csv_output_formatter",
            )

    @pytest.mark.asyncio
    async def test_projected_json_save_uses_projection_json_data(self, tmp_path, monkeypatch):
        """JSON projections should save json_data rather than CSV-like rows."""
        from src.lib.file_outputs import FileOutputStorageService
        from src.lib.flows.output_projection import (
            FlowOutputColumnSpec,
            FlowOutputProjectionResult,
        )
        from src.lib.openai_agents.tools import file_output_tools
        from src.lib.openai_agents.tools.file_output_tools import save_projected_file_output

        trace_id = uuid4().hex
        session_id = f"session-{uuid4().hex[:12]}"
        set_current_trace_id(trace_id)
        set_current_session_id(session_id)
        set_current_user_id("curator-json-projection")

        storage = FileOutputStorageService(base_path=tmp_path)
        monkeypatch.setattr(
            file_output_tools,
            "FileOutputStorageService",
            lambda: storage,
        )
        store = _install_projected_file_output_store(monkeypatch, file_output_tools)
        projection = FlowOutputProjectionResult(
            format="json",
            row_source="object",
            columns=[
                FlowOutputColumnSpec(
                    key="symbol",
                    field_ref="object.payload.symbol",
                )
            ],
            rows=[{"symbol": "Notch"}],
            total_count=1,
            json_data={"grouped": [{"rows": [{"symbol": "Notch"}]}]},
        )

        result = await save_projected_file_output(
            "json",
            projection,
            "grouped_gene_results",
            "json_output_formatter",
        )
        assert result["filename"] == f"{trace_id}_grouped_gene_results.json"
        assert result["format"] == "json"

        assert len(store.rows) == 1
        saved = store.rows[0]
        assert saved.agent_name == "json_output_formatter"
        assert json.loads(Path(saved.file_path).read_text(encoding="utf-8")) == {
            "grouped": [{"rows": [{"symbol": "Notch"}]}]
        }
