"""Tests for document update (PATCH) endpoint."""
import pytest
from uuid import uuid4

from pydantic import ValidationError


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

        # Response should include document_id, title, and filename.
        response = DocumentUpdateResponse(
            document_id=str(uuid4()),
            title="Updated Title",
            filename="paper.pdf",
        )
        assert response.title == "Updated Title"
        assert response.filename == "paper.pdf"

    def test_update_document_title_max_length(self):
        """Title should enforce max_length=255."""
        from src.schemas.documents import DocumentUpdateRequest

        # 255 characters should pass
        request = DocumentUpdateRequest(title="x" * 255)
        assert request.title is not None
        assert len(request.title) == 255

        # 256 characters should fail
        with pytest.raises(ValidationError):
            DocumentUpdateRequest(title="x" * 256)

    @pytest.mark.parametrize(
        "filename",
        [
            "",
            "   ",
            "paper.txt",
            "../paper.pdf",
            "folder/paper.pdf",
            "folder\\paper.pdf",
            "paper\n.pdf",
            "x" * 252 + ".pdf",
        ],
    )
    def test_update_document_rejects_invalid_filename(self, filename):
        from src.schemas.documents import DocumentUpdateRequest

        with pytest.raises(ValidationError):
            DocumentUpdateRequest(filename=filename)

    def test_update_document_accepts_pdf_filename(self):
        from src.schemas.documents import DocumentUpdateRequest

        request = DocumentUpdateRequest(filename="curator name.PDF")
        assert request.filename == "curator name.PDF"
