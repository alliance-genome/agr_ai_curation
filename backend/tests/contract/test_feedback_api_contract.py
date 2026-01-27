"""Contract tests for POST /api/feedback/submit endpoint.

These tests verify the API contract matches the specification in
specs/005-user-feedback-system/contracts/submit_feedback.yaml

CRITICAL: These tests MUST FAIL before implementation!
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
import time


@pytest.fixture
def client(monkeypatch):
    """Create test client with mocked dependencies."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("UNSTRUCTURED_API_URL", "http://test-unstructured")

    import sys
    import os

    sys.path.insert(
        0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    from main import app

    return TestClient(app)


@pytest.fixture
def mock_feedback_db():
    """Mock database session for feedback operations."""
    with patch("src.models.sql.database.get_feedback_db") as mock:
        db = MagicMock()
        mock.return_value.__enter__.return_value = db
        yield db


@pytest.fixture
def valid_feedback_payload():
    """Valid feedback submission payload."""
    return {
        "session_id": "test_session_123",
        "curator_id": "test_curator@example.com",
        "feedback_text": "Test feedback: AI suggested wrong ontology term",
        "trace_ids": ["trace_test_abc123"],
    }


class TestFeedbackSubmitEndpoint:
    """Contract tests for POST /api/feedback/submit endpoint."""

    def test_successful_feedback_submission(
        self, client, mock_feedback_db, valid_feedback_payload
    ):
        """Test successful feedback submission (200 response).

        VERIFY: This test should FAIL initially (endpoint doesn't exist yet).
        """
        # Measure response time
        start_time = time.time()

        response = client.post("/api/feedback/submit", json=valid_feedback_payload)

        elapsed_time_ms = (time.time() - start_time) * 1000

        # Response code
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

        # Response schema
        data = response.json()
        assert "status" in data, "Response missing 'status' field"
        assert "feedback_id" in data, "Response missing 'feedback_id' field"
        assert "message" in data, "Response missing 'message' field"

        # Response values
        assert data["status"] == "success", f"Expected status='success', got {data['status']}"
        assert isinstance(
            data["feedback_id"], str
        ), f"Expected feedback_id to be string, got {type(data['feedback_id'])}"
        assert len(data["feedback_id"]) == 36, "feedback_id should be UUID format (36 chars)"
        assert isinstance(
            data["message"], str
        ), f"Expected message to be string, got {type(data['message'])}"
        assert len(data["message"]) > 0, "Message should not be empty"

        # Performance requirement: < 500ms
        assert (
            elapsed_time_ms < 500
        ), f"Response time {elapsed_time_ms:.2f}ms exceeds 500ms requirement"

    def test_empty_feedback_text_validation(self, client, valid_feedback_payload):
        """Test empty feedback_text validation (400 response).

        VERIFY: This test should FAIL initially (endpoint doesn't exist yet).
        """
        # Empty feedback text
        payload = valid_feedback_payload.copy()
        payload["feedback_text"] = ""

        response = client.post("/api/feedback/submit", json=payload)

        # Should reject with 400
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"

        data = response.json()
        assert "status" in data, "Error response missing 'status' field"
        assert "error" in data, "Error response missing 'error' field"
        assert "details" in data, "Error response missing 'details' field"

        assert data["status"] == "error", f"Expected status='error', got {data['status']}"
        assert isinstance(data["details"], list), "Details should be a list"
        assert len(data["details"]) > 0, "Details should contain validation errors"

        # Check that error mentions feedback_text
        detail_fields = [d.get("field") for d in data["details"]]
        assert (
            "feedback_text" in detail_fields
        ), "Expected validation error for 'feedback_text'"

    def test_whitespace_only_feedback_text_validation(
        self, client, valid_feedback_payload
    ):
        """Test whitespace-only feedback_text validation (400 response).

        VERIFY: This test should FAIL initially (endpoint doesn't exist yet).
        """
        # Whitespace-only feedback text
        payload = valid_feedback_payload.copy()
        payload["feedback_text"] = "   \n\t   "

        response = client.post("/api/feedback/submit", json=payload)

        # Should reject with 400
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"

        data = response.json()
        assert data["status"] == "error"

        # Check that error mentions empty or whitespace
        error_message = data.get("error", "").lower()
        details_str = str(data.get("details", [])).lower()
        assert "empty" in error_message or "empty" in details_str or "whitespace" in details_str, \
            "Expected error message to mention 'empty' or 'whitespace'"

    def test_empty_trace_ids_accepted(self, client, mock_feedback_db, valid_feedback_payload):
        """Test empty trace_ids is accepted (200 response).

        trace_ids is now optional - empty arrays are allowed.
        VERIFY: This test should FAIL initially (endpoint doesn't exist yet).
        """
        # Empty trace_ids list
        payload = valid_feedback_payload.copy()
        payload["trace_ids"] = []

        response = client.post("/api/feedback/submit", json=payload)

        # Should accept empty trace_ids
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

        data = response.json()
        assert data["status"] == "success"
        assert "feedback_id" in data
        assert "message" in data

    def test_omitted_trace_ids_defaults_to_empty(self, client, mock_feedback_db, valid_feedback_payload):
        """Test omitted trace_ids field defaults to empty array (200 response).

        trace_ids is optional - if omitted, should default to empty array.
        VERIFY: This test should FAIL initially (endpoint doesn't exist yet).
        """
        # Remove trace_ids from payload entirely
        payload = valid_feedback_payload.copy()
        del payload["trace_ids"]

        response = client.post("/api/feedback/submit", json=payload)

        # Should accept with default empty trace_ids
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

        data = response.json()
        assert data["status"] == "success"
        assert "feedback_id" in data

    def test_missing_required_fields(self, client):
        """Test missing required fields validation (400 or 422 response).

        VERIFY: This test should FAIL initially (endpoint doesn't exist yet).
        """
        # Missing all fields
        response = client.post("/api/feedback/submit", json={})

        # Should reject with 400 or 422 (FastAPI validation)
        assert response.status_code in [
            400,
            422,
        ], f"Expected 400 or 422, got {response.status_code}"

    def test_response_schema_matches_contract(
        self, client, mock_feedback_db, valid_feedback_payload
    ):
        """Test response schema exactly matches contract specification.

        VERIFY: This test should FAIL initially (endpoint doesn't exist yet).
        """
        response = client.post("/api/feedback/submit", json=valid_feedback_payload)

        assert response.status_code == 200

        data = response.json()

        # Exact schema match
        assert set(data.keys()) == {
            "status",
            "feedback_id",
            "message",
        }, f"Response has extra/missing keys: {data.keys()}"

        # Type checks
        assert isinstance(data["status"], str)
        assert isinstance(data["feedback_id"], str)
        assert isinstance(data["message"], str)

        # Value checks
        assert data["status"] == "success"

    def test_multiple_trace_ids_accepted(
        self, client, mock_feedback_db, valid_feedback_payload
    ):
        """Test that multiple trace IDs are accepted.

        VERIFY: This test should FAIL initially (endpoint doesn't exist yet).
        """
        payload = valid_feedback_payload.copy()
        payload["trace_ids"] = ["trace_1", "trace_2", "trace_3"]

        response = client.post("/api/feedback/submit", json=payload)

        assert response.status_code == 200
        assert response.json()["status"] == "success"
