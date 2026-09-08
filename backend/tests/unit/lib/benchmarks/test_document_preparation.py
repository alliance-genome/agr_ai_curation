import hashlib
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.lib.benchmarks import document_preparation as preparation
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.models.sql.user import User


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["digest", "owner", "disabled", "content"])
async def test_invalid_input_never_writes_artifacts_or_starts_model_storage_work(monkeypatch, invalid):
    content = b'{"user_id":"content-cannot-authorize"}' if invalid == "content" else b"Frozen text"
    context = BenchmarkCuratorContext(
        subject="synthetic-curator", auth_provider="oidc", db_user_id=42, active_groups=(),
    )
    factory = MagicMock()
    factory.return_value.__enter__.return_value.get.return_value = User(
        id=42, auth_sub="different" if invalid == "owner" else context.subject,
        is_active=invalid != "disabled",
    )
    monkeypatch.setattr(preparation, "SessionLocal", factory)
    artifacts = MagicMock()
    create = AsyncMock()
    index = AsyncMock()
    monkeypatch.setattr(preparation, "_write_artifacts", artifacts)
    monkeypatch.setattr(preparation, "create_document", create)
    monkeypatch.setattr(preparation, "index_owned_document_elements", index)
    with pytest.raises((PermissionError, ValueError)):
        await preparation.prepare_frozen_document(
            document_id=uuid4(), content=content,
            content_type="application/json" if invalid == "content" else "text/plain",
            snapshot_digest="bad" if invalid == "digest" else f"sha256:{hashlib.sha256(content).hexdigest()}",
            curator=context, weaviate_client=object(),
            stage_checkpoint=AsyncMock(),
        )
    artifacts.assert_not_called()
    create.assert_not_called()
    index.assert_not_called()
