"""Tests for document update (PATCH) endpoint."""
import pytest
from unittest.mock import patch, MagicMock
from uuid import uuid4

from pydantic import ValidationError
from fastapi import HTTPException


class TestDocumentUpdateEndpoint:
    """Tests for PATCH /weaviate/documents/{document_id} endpoint."""

    def test_update_document_title_schema_exists(self):
        """Verify the DocumentUpdateRequest schema exists."""
        from src.schemas.documents import DocumentUpdateRequest

        # Schema should allow title update
        request = DocumentUpdateRequest(title="New Title")
        assert request.title == "New Title"

    def test_update_document_title_optional(self):
        """Title should be optional in update request."""
        from src.schemas.documents import DocumentUpdateRequest

        # Should be able to create without title (for future fields)
        request = DocumentUpdateRequest()
        assert request.title is None

    def test_update_document_response_schema_exists(self):
        """Verify the DocumentUpdateResponse schema exists."""
        from src.schemas.documents import DocumentUpdateResponse

        # Response should include document_id, title, and updated_at
        response = DocumentUpdateResponse(
            document_id=str(uuid4()),
            title="Updated Title",
        )
        assert response.title == "Updated Title"

    def test_update_document_title_max_length(self):
        """Title should enforce max_length=255."""
        from src.schemas.documents import DocumentUpdateRequest

        # 255 characters should pass
        request = DocumentUpdateRequest(title="x" * 255)
        assert len(request.title) == 255

        # 256 characters should fail
        with pytest.raises(ValidationError):
            DocumentUpdateRequest(title="x" * 256)
