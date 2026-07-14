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
        session_id: str,
        curator_id: str,
        file_type: str,
        descriptor: str,
        file_path: str,
        branch_identity: str | None,
    ) -> Any | None:
        for row in self.rows:
            if str(row.file_path) == str(file_path):
                metadata = row.file_metadata or {}
                if branch_identity is None or (
                    isinstance(metadata, dict)
                    and metadata.get("flow_output_branch_identity") == branch_identity
                ):
                    return row

        for row in self.rows:
            metadata = row.file_metadata or {}
            if not isinstance(metadata, dict):
                continue
            if (
                row.session_id == session_id
                and row.curator_id == curator_id
                and row.file_type == file_type
                and metadata.get("structured_projection") is True
                and str(metadata.get("descriptor") or "") == descriptor
                and (
                    branch_identity is None
                    or metadata.get("flow_output_branch_identity") == branch_identity
                )
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
            "csv_formatter",
        )
        second = await save_projected_file_output(
            "csv",
            projection("DeltaGene"),
            "gene_results",
            "csv_formatter",
        )
        third = await save_projected_file_output(
            "csv",
            projection("Wingless"),
            "other_gene_results",
            "csv_formatter",
        )
        fourth = await save_projected_file_output(
            "tsv",
            projection("DeltaGene", output_format="tsv"),
            "gene_results",
            "tsv_formatter",
        )

        assert first["file_id"] == second["file_id"]
        assert first["filename"] == second["filename"]
        assert first["filename"] == f"gene_results_{trace_id}.csv"
        assert third["file_id"] != first["file_id"]
        assert third["filename"] == f"other_gene_results_{trace_id}.csv"
        assert fourth["file_id"] != first["file_id"]
        assert fourth["filename"] == f"gene_results_{trace_id}.tsv"

        rows = sorted(store.rows, key=lambda row: row.filename)
        assert len(rows) == 3
        saved = next(row for row in rows if row.filename == first["filename"])
        saved_tsv = next(row for row in rows if row.filename == fourth["filename"])
        assert saved.agent_name == "csv_formatter"
        assert saved.curator_id == curator_id
        assert saved.session_id == session_id
        assert saved.file_metadata["structured_projection"] is True
        assert saved.file_metadata["descriptor"] == "gene_results"
        assert saved.file_size == second["size_bytes"]
        assert saved.file_hash == second["hash_sha256"]
        assert Path(saved.file_path).read_text(encoding="utf-8").replace("\r\n", "\n") == (
            "symbol\nDeltaGene\n"
        )
        assert saved_tsv.agent_name == "tsv_formatter"
        assert saved_tsv.file_type == "tsv"
        assert Path(saved_tsv.file_path).read_text(encoding="utf-8").replace("\r\n", "\n") == (
            "symbol\nDeltaGene\n"
        )

    @pytest.mark.asyncio
    async def test_flow_formatter_branches_with_same_descriptor_do_not_overwrite(
        self,
        tmp_path,
        monkeypatch,
    ):
        from src.lib.file_outputs import FileOutputStorageService
        from src.lib.flows.output_projection import (
            FlowOutputColumnSpec,
            FlowOutputProjectionResult,
        )
        from src.lib.openai_agents.tools import file_output_tools
        from src.lib.openai_agents.tools.file_output_tools import save_projected_file_output

        trace_id = uuid4().hex
        set_current_trace_id(trace_id)
        set_current_session_id("session-branch-files")
        set_current_user_id("curator-branch-files")
        storage = FileOutputStorageService(base_path=tmp_path)
        monkeypatch.setattr(file_output_tools, "FileOutputStorageService", lambda: storage)
        store = _install_projected_file_output_store(monkeypatch, file_output_tools)

        def projection(symbol: str) -> FlowOutputProjectionResult:
            return FlowOutputProjectionResult(
                format="csv",
                row_source="object",
                columns=[FlowOutputColumnSpec(key="symbol", field_ref="object.payload.symbol")],
                rows=[{"symbol": symbol}],
                total_count=1,
            )

        context = _context_module()
        context.set_current_flow_output_attachment(
            {
                "flow_id": "flow-1",
                "flow_run_id": "run-1",
                "formatter_node_id": "formatter_branch_shared_prefix_alleles",
                "source_node_id": "allele_extract",
                "source_node_ids": ["allele_extract", "gene_extract"],
                "formatter_label": "Allele CSV",
                "source_label": "Allele Extraction",
                "source_labels": ["Allele Extraction", "Gene Extraction"],
                "source_extraction_result_ids": ["result-allele"],
                "source_keys": ["flow-step:1:allele_extractor"],
                "source_envelope_ids": ["envelope-allele"],
                "document_id": "doc-1",
            }
        )
        allele_file = await save_projected_file_output(
            "csv", projection("wg[1]"), "results", "csv_formatter"
        )
        context.set_current_flow_output_attachment(
            {
                "flow_id": "flow-1",
                "flow_run_id": "run-1",
                "formatter_node_id": "formatter_branch_shared_prefix_genes",
                "source_node_id": "gene_extract",
                "document_id": "doc-1",
            }
        )
        gene_file = await save_projected_file_output(
            "csv", projection("wg"), "results", "csv_formatter"
        )

        assert allele_file["file_id"] != gene_file["file_id"]
        assert allele_file["filename"] != gene_file["filename"]
        # Flow branches retain their node/hash discriminator, but the readable
        # descriptor must lead so files from the same paper sort together.
        assert allele_file["filename"].startswith("results_")
        assert allele_file["filename"].endswith(f"_{trace_id}.csv")
        assert allele_file["formatter_label"] == "Allele CSV"
        assert allele_file["source_label"] == "Allele Extraction"
        assert allele_file["source_node_ids"] == ["allele_extract", "gene_extract"]
        assert allele_file["source_labels"] == ["Allele Extraction", "Gene Extraction"]
        assert allele_file["source_extraction_result_ids"] == ["result-allele"]
        assert allele_file["source_envelope_ids"] == ["envelope-allele"]
        assert gene_file["filename"].startswith("results_")
        assert gene_file["filename"].endswith(f"_{trace_id}.csv")
        assert len(store.rows) == 2
        assert {row.file_metadata["source_node_id"] for row in store.rows} == {
            "allele_extract",
            "gene_extract",
        }
        assert all(row.file_metadata["projection_summary"]["row_count"] == 1 for row in store.rows)
        allele_row = next(
            row for row in store.rows if row.file_metadata["source_node_id"] == "allele_extract"
        )
        assert allele_row.file_metadata["source_keys"] == ["flow-step:1:allele_extractor"]
        assert allele_row.file_metadata["source_envelope_ids"] == ["envelope-allele"]
        assert allele_row.file_metadata["source_node_ids"] == [
            "allele_extract",
            "gene_extract",
        ]

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
            agent_name="csv_formatter",
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
            "csv_formatter",
        )

        assert result["file_id"] == stale_id
        assert result["filename"] == f"gene_results_{trace_id}.csv"
        assert len(store.rows) == 1
        saved = store.rows[0]
        assert str(saved.id) == stale_id
        assert Path(saved.file_path).parent == tmp_path / "outputs" / "structured" / trace_id
        assert saved.file_hash == result["hash_sha256"]
        assert Path(saved.file_path).read_text(encoding="utf-8").replace("\r\n", "\n") == (
            "symbol\nDeltaGene\n"
        )

    @pytest.mark.asyncio
    async def test_projected_save_reuses_session_descriptor_across_traces(
        self, tmp_path, monkeypatch
    ):
        """A repeat export in the same chat session should update one file row."""
        from src.lib.file_outputs import FileOutputStorageService
        from src.lib.flows.output_projection import (
            FlowOutputColumnSpec,
            FlowOutputProjectionResult,
        )
        from src.lib.openai_agents.tools import file_output_tools
        from src.lib.openai_agents.tools.file_output_tools import save_projected_file_output

        first_trace_id = uuid4().hex
        second_trace_id = uuid4().hex
        session_id = f"session-{uuid4().hex[:12]}"
        curator_id = "curator-projected-save"
        set_current_trace_id(first_trace_id)
        set_current_session_id(session_id)
        set_current_user_id(curator_id)

        storage = FileOutputStorageService(base_path=tmp_path)
        monkeypatch.setattr(
            file_output_tools,
            "FileOutputStorageService",
            lambda: storage,
        )
        store = _install_projected_file_output_store(monkeypatch, file_output_tools)

        def projection(symbol: str) -> FlowOutputProjectionResult:
            return FlowOutputProjectionResult(
                format="csv",
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
            "csv_formatter",
        )
        first_path = Path(store.rows[0].file_path)
        assert first_path.exists()
        set_current_trace_id(second_trace_id)
        second = await save_projected_file_output(
            "csv",
            projection("DeltaGene"),
            "gene_results",
            "csv_formatter",
        )

        assert first["file_id"] == second["file_id"]
        assert second["trace_id"] == second_trace_id
        assert second["filename"] == f"gene_results_{second_trace_id}.csv"
        assert len(store.rows) == 1
        saved = store.rows[0]
        assert saved.trace_id == second_trace_id
        assert saved.session_id == session_id
        assert saved.curator_id == curator_id
        assert saved.file_metadata["descriptor"] == "gene_results"
        assert not first_path.exists()
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
                "csv_formatter",
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
            "json_formatter",
        )
        assert result["filename"] == f"grouped_gene_results_{trace_id}.json"
        assert result["format"] == "json"

        assert len(store.rows) == 1
        saved = store.rows[0]
        assert saved.agent_name == "json_formatter"
        assert json.loads(Path(saved.file_path).read_text(encoding="utf-8")) == {
            "grouped": [{"rows": [{"symbol": "Notch"}]}]
        }
