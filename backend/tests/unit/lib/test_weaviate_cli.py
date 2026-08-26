"""Unit tests for the Weaviate command-line interface."""

import json
from unittest.mock import AsyncMock

from click.testing import CliRunner

from src.lib.weaviate_client import cli as weaviate_cli


def _invoke_list_documents(monkeypatch, response, *args):
    monkeypatch.setattr(weaviate_cli, "connect_to_weaviate", lambda *_: None)
    monkeypatch.setattr(weaviate_cli, "list_documents", lambda *_args, **_kwargs: response)

    return CliRunner().invoke(
        weaviate_cli.cli,
        ["list-documents", "--user-id", "user-1", *args],
    )


def test_list_documents_table_uses_flat_canonical_response(monkeypatch):
    response = {
        "documents": [
            {
                "document_id": "doc-1",
                "filename": "paper.pdf",
                "file_size_bytes": 123,
                "embedding_status": "completed",
                "chunk_count": 4,
                "vector_count": 4,
            }
        ],
        "total": 25,
        "limit": 20,
        "offset": 20,
    }

    result = _invoke_list_documents(monkeypatch, response)

    assert result.exit_code == 0
    assert "Documents (Page 2/2):" in result.output
    assert "ID: doc-1" in result.output
    assert "Filename: paper.pdf" in result.output
    assert "Size: 123 bytes" in result.output
    assert "Status: completed" in result.output
    assert "Chunks: 4" in result.output
    assert "Vectors: 4" in result.output
    assert "Total: 25 documents" in result.output


def test_list_documents_table_handles_empty_canonical_response(monkeypatch):
    response = {
        "documents": [],
        "total": 0,
        "limit": 20,
        "offset": 0,
    }

    result = _invoke_list_documents(monkeypatch, response)

    assert result.exit_code == 0
    assert result.output == "No documents found\n"


def test_list_documents_json_outputs_canonical_response(monkeypatch):
    response = {
        "documents": [
            {
                "document_id": "doc-1",
                "filename": "paper.pdf",
                "file_size_bytes": 123,
                "embedding_status": "completed",
                "chunk_count": 4,
                "vector_count": 4,
            }
        ],
        "total": 1,
        "limit": 20,
        "offset": 0,
    }

    result = _invoke_list_documents(monkeypatch, response, "--output-format", "json")

    assert result.exit_code == 0
    assert json.loads(result.output) == response


def test_get_document_text_uses_lean_document_result(monkeypatch):
    """Text output must not depend on the retired rich detail fields."""
    get_document = AsyncMock(
        return_value={
            "document": {
                "filename": "paper.pdf",
                "fileSize": 2048,
                "processingStatus": "completed",
                "embeddingStatus": "completed",
                "creationDate": "2026-08-24T00:00:00Z",
                "lastAccessedDate": "2026-08-24T01:00:00Z",
            },
            "chunks": [],
            "total_chunks": 3,
            "schema_version": "1.0",
        }
    )
    monkeypatch.setattr(weaviate_cli, "connect_to_weaviate", lambda *_: None)
    monkeypatch.setattr(weaviate_cli, "get_document", get_document)

    result = CliRunner().invoke(
        weaviate_cli.cli,
        ["get-document", "document-1", "--user-id", "user-1"],
    )

    assert result.exit_code == 0
    assert "Document Details:" in result.output
    assert "Filename: paper.pdf" in result.output
    assert "Total Chunks: 3" in result.output
    assert "Embeddings:" not in result.output
    assert "Embedded Chunks:" not in result.output
    get_document.assert_awaited_once_with("user-1", "document-1")
