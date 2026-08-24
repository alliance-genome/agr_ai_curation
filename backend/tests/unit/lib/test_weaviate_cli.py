"""Unit tests for the Weaviate command-line interface."""

from unittest.mock import AsyncMock

from click.testing import CliRunner

from src.lib.weaviate_client import cli as weaviate_cli


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
