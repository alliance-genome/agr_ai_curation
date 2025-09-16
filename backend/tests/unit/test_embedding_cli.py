"""Tests for the embedding CLI commands using mocked dependencies."""

from __future__ import annotations

import json
from uuid import uuid4
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_service():
    from lib.embedding_service import EmbeddingModelConfig

    service = MagicMock()
    service.list_models.return_value = [
        EmbeddingModelConfig(
            name="text-embedding-3-small",
            dimensions=1536,
            default_version="1.0",
            max_batch_size=128,
            default_batch_size=64,
        )
    ]
    service.embed_pdf.return_value = {
        "embedded": 2,
        "skipped": 0,
        "model": "text-embedding-3-small",
        "version": "v1",
    }
    return service


def test_list_models_outputs_config(mock_service, capsys):
    with patch("lib.cli.embedding_cli._load_service", return_value=mock_service):
        from lib.cli.embedding_cli import main

        main(["list-models"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload[0]["name"] == "text-embedding-3-small"


def test_embed_command_calls_service(mock_service):
    pdf_id = uuid4()
    with patch("lib.cli.embedding_cli._load_service", return_value=mock_service):
        from lib.cli.embedding_cli import main

        main(
            [
                "embed",
                str(pdf_id),
                "--model",
                "text-embedding-3-small",
                "--version",
                "v2",
            ]
        )

    mock_service.embed_pdf.assert_called_once()
    call_kwargs = mock_service.embed_pdf.call_args.kwargs
    assert call_kwargs["pdf_id"] == pdf_id
    assert call_kwargs["model_name"] == "text-embedding-3-small"
    assert call_kwargs["version"] == "v2"


def test_status_command_outputs_summary(mock_service, capsys):
    pdf_id = uuid4()

    fake_doc = MagicMock()
    fake_doc.embeddings_generated = True
    fake_doc.embedding_models = [{"model": "text-embedding-3-small", "version": "v1"}]

    fake_embeddings = [MagicMock(), MagicMock(), MagicMock()]

    with patch("lib.cli.embedding_cli._load_service", return_value=mock_service), patch(
        "lib.cli.embedding_cli.SessionLocal"
    ) as session_local:
        session = MagicMock()
        session.__enter__.return_value = session
        session.get.return_value = fake_doc
        session.query.return_value.filter_by.return_value.all.return_value = (
            fake_embeddings
        )
        session_local.return_value = session

        from lib.cli.embedding_cli import main

        main(["status", str(pdf_id)])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["pdf_id"] == str(pdf_id)
    assert payload["count"] == len(fake_embeddings)
    assert payload["embeddings_generated"] is True
