"""Tests for pdf/chunk CLI interface"""

import json
from types import SimpleNamespace

import pytest

from lib.cli import pdf_cli
from lib.pdf_processor import (
    ExtractionResult,
    HashResult,
    UnstructuredElement,
    ValidationResult,
)
from lib.chunk_manager import Chunk, ChunkResult, ChunkingStrategy


@pytest.fixture
def dummy_extraction_result():
    elements = [
        UnstructuredElement(
            type="Title",
            text="Sample Title",
            metadata={"page_number": 1},
            element_id="e1",
            page_number=1,
        )
    ]

    return ExtractionResult(
        pdf_path="sample.pdf",
        elements=elements,
        page_count=1,
        full_text="Sample Title",
        metadata={"title": "Sample"},
        tables=[],
        figures=[],
        extraction_time_ms=10.0,
        file_size_bytes=1234,
        processing_strategy="fast",
        content_hash="abc",
        content_hash_normalized="def",
        page_hashes=["ghi"],
    )


def run_cli(monkeypatch, capsys, argv, processor_stub):
    calls = SimpleNamespace(extract=None, validate=None, hash=None)

    class StubProcessor:
        def __init__(self, *args, **kwargs):
            pass

        def extract(self, *args, **kwargs):
            calls.extract = (args, kwargs)
            return processor_stub["extract"]

        def validate(self, *args, **kwargs):
            calls.validate = (args, kwargs)
            return processor_stub["validate"]

        def hash(self, *args, **kwargs):
            calls.hash = (args, kwargs)
            return processor_stub["hash"]

    monkeypatch.setattr(pdf_cli, "PDFProcessor", StubProcessor)
    monkeypatch.setattr(pdf_cli.sys, "argv", argv)
    pdf_cli.main()
    captured = capsys.readouterr()
    return captured, calls


def test_extract_command_outputs_json(monkeypatch, capsys, dummy_extraction_result):
    processor_stub = {
        "extract": dummy_extraction_result,
        "validate": None,
        "hash": None,
    }

    captured, calls = run_cli(
        monkeypatch,
        capsys,
        ["prog", "extract", "sample.pdf", "--strategy", "fast"],
        processor_stub,
    )

    output = json.loads(captured.out)
    assert output["pdf_path"] == "sample.pdf"
    assert calls.extract is not None


def test_validate_command(monkeypatch, capsys, dummy_extraction_result):
    validation = ValidationResult(
        is_valid=True,
        page_count=1,
        has_text=True,
        file_size_bytes=123,
        is_encrypted=False,
        is_corrupted=False,
        is_scanned=False,
    )

    processor_stub = {
        "extract": dummy_extraction_result,
        "validate": validation,
        "hash": None,
    }

    captured, calls = run_cli(
        monkeypatch,
        capsys,
        ["prog", "validate", "sample.pdf"],
        processor_stub,
    )

    output = json.loads(captured.out)
    assert output["is_valid"] is True
    assert calls.validate[0][0] == "sample.pdf"


def test_hash_command(monkeypatch, capsys, dummy_extraction_result):
    hash_result = HashResult(
        file_hash="f",
        content_hash="c",
        content_hash_normalized="n",
        page_hashes=["p"],
        page_count=1,
    )

    processor_stub = {
        "extract": dummy_extraction_result,
        "validate": None,
        "hash": hash_result,
    }

    captured, calls = run_cli(
        monkeypatch,
        capsys,
        ["prog", "hash", "sample.pdf", "--per-page"],
        processor_stub,
    )

    output = json.loads(captured.out)
    assert output["file_hash"] == "f"
    assert calls.hash[1]["per_page"] is True


def test_chunk_command_uses_chunk_manager(monkeypatch, capsys, dummy_extraction_result):
    chunk = Chunk(
        chunk_index=0,
        text="Sample chunk",
        token_count=5,
        char_start=0,
        char_end=12,
        page_start=1,
        page_end=1,
    )
    chunk_result = ChunkResult(
        chunks=[chunk],
        total_chunks=1,
        avg_chunk_size=12,
        processing_time_ms=5.0,
        strategy=ChunkingStrategy.BY_PAGE,
        parameters={"max_characters": 500},
    )

    class StubManager:
        def __init__(self):
            self.called = None

        def chunk(self, extraction_result, **kwargs):
            self.called = kwargs
            return chunk_result

    manager = StubManager()
    monkeypatch.setattr(
        pdf_cli,
        "PDFProcessor",
        lambda *args, **kwargs: SimpleNamespace(
            extract=lambda *a, **kw: dummy_extraction_result
        ),
    )
    monkeypatch.setattr(pdf_cli, "ChunkManager", lambda: manager)
    monkeypatch.setattr(
        pdf_cli.sys,
        "argv",
        [
            "prog",
            "chunk",
            "sample.pdf",
            "--strategy",
            "by_page",
            "--max-chars",
            "400",
            "--overlap",
            "100",
        ],
    )

    pdf_cli.main()
    output = json.loads(capsys.readouterr().out)
    assert output["chunk_result"]["total_chunks"] == 1
    assert manager.called["strategy"] == ChunkingStrategy.BY_PAGE
    assert manager.called["max_characters"] == 400
