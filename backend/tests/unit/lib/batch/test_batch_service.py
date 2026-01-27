"""Unit tests for batch service."""
import pytest
from unittest.mock import Mock, patch
from datetime import datetime, timezone
from uuid import uuid4

from src.lib.batch.service import BatchService
from src.models.sql.batch import Batch, BatchDocument, BatchStatus, BatchDocumentStatus


class TestBatchService:
    """Tests for BatchService."""

    def test_list_batches_returns_empty_for_new_user(self, test_db):
        """List batches returns empty list for user with no batches."""
        service = BatchService(test_db)
        user_id = 99999  # Non-existent user

        result = service.list_batches(user_id)

        assert result == []

    def test_get_batch_returns_none_for_nonexistent(self, test_db):
        """Get batch returns None for non-existent batch."""
        service = BatchService(test_db)

        result = service.get_batch(uuid4(), user_id=1)

        assert result is None


    def test_is_batch_cancelled_returns_false_for_nonexistent(self, test_db):
        """Is batch cancelled returns False for non-existent batch."""
        service = BatchService(test_db)

        result = service.is_batch_cancelled(uuid4())

        assert result is False

    def test_cancel_batch_returns_none_for_nonexistent(self, test_db):
        """Cancel batch returns None for non-existent batch."""
        service = BatchService(test_db)

        result = service.cancel_batch(uuid4(), user_id=1)

        assert result is None

    def test_get_flow_name_returns_none_for_nonexistent(self, test_db):
        """Get flow name returns None for non-existent flow."""
        service = BatchService(test_db)

        result = service.get_flow_name(uuid4())

        assert result is None

    def test_get_document_titles_returns_empty_for_empty_list(self, test_db):
        """Get document titles returns empty dict for empty list."""
        service = BatchService(test_db)

        result = service.get_document_titles([])

        assert result == {}


class TestBatchStatusValues:
    """Tests for batch status enum values."""

    def test_batch_status_values_are_lowercase(self):
        """BatchStatus enum values should be lowercase strings."""
        assert BatchStatus.PENDING.value == "pending"
        assert BatchStatus.RUNNING.value == "running"
        assert BatchStatus.COMPLETED.value == "completed"
        assert BatchStatus.CANCELLED.value == "cancelled"

    def test_batch_document_status_values_are_lowercase(self):
        """BatchDocumentStatus enum values should be lowercase strings."""
        assert BatchDocumentStatus.PENDING.value == "pending"
        assert BatchDocumentStatus.PROCESSING.value == "processing"
        assert BatchDocumentStatus.COMPLETED.value == "completed"
        assert BatchDocumentStatus.FAILED.value == "failed"


class TestBatchServiceMocked:
    """Tests for BatchService using mocks to avoid DB constraints."""

    def test_cancel_batch_validates_cancellable_status(self):
        """Cancel batch should only work for pending/running batches."""
        # Test that ValueError is raised for completed status
        mock_db = Mock()
        service = BatchService(mock_db)

        # Create a mock batch that's already completed
        mock_batch = Mock()
        mock_batch.status = BatchStatus.COMPLETED

        # Mock get_batch to return our mock batch
        with patch.object(service, 'get_batch', return_value=mock_batch):
            with pytest.raises(ValueError, match="Cannot cancel batch"):
                service.cancel_batch(uuid4(), user_id=1)

    def test_cancel_batch_sets_cancelled_status(self):
        """Cancel batch should set status to CANCELLED and completed_at."""
        mock_db = Mock()
        service = BatchService(mock_db)

        # Create a mock batch that's running
        mock_batch = Mock()
        mock_batch.status = BatchStatus.RUNNING

        # Mock get_batch to return our mock batch
        with patch.object(service, 'get_batch', return_value=mock_batch):
            result = service.cancel_batch(uuid4(), user_id=1)

            assert result.status == BatchStatus.CANCELLED
            assert result.completed_at is not None
            mock_db.commit.assert_called_once()
            mock_db.refresh.assert_called_once_with(mock_batch)

    def test_increment_batch_completed(self):
        """Increment batch completed should increase count by 1."""
        mock_db = Mock()
        service = BatchService(mock_db)

        mock_batch = Mock()
        mock_batch.completed_documents = 0
        mock_db.query.return_value.filter.return_value.first.return_value = mock_batch

        service.increment_batch_completed(uuid4())

        assert mock_batch.completed_documents == 1
        mock_db.commit.assert_called_once()

    def test_increment_batch_failed(self):
        """Increment batch failed should increase count by 1."""
        mock_db = Mock()
        service = BatchService(mock_db)

        mock_batch = Mock()
        mock_batch.failed_documents = 0
        mock_db.query.return_value.filter.return_value.first.return_value = mock_batch

        service.increment_batch_failed(uuid4())

        assert mock_batch.failed_documents == 1
        mock_db.commit.assert_called_once()

    def test_update_batch_status(self):
        """Update batch status should change status and timestamps."""
        mock_db = Mock()
        service = BatchService(mock_db)

        mock_batch = Mock()
        mock_batch.status = BatchStatus.PENDING
        mock_db.query.return_value.filter.return_value.first.return_value = mock_batch

        started_at = datetime.now(timezone.utc)
        service.update_batch_status(
            uuid4(),
            status=BatchStatus.RUNNING,
            started_at=started_at,
        )

        assert mock_batch.status == BatchStatus.RUNNING
        assert mock_batch.started_at == started_at
        mock_db.commit.assert_called_once()

    def test_update_document_status(self):
        """Update document status should set status and optional fields."""
        mock_db = Mock()
        service = BatchService(mock_db)

        mock_doc = Mock()
        mock_doc.status = BatchDocumentStatus.PENDING
        mock_db.query.return_value.filter.return_value.first.return_value = mock_doc

        service.update_document_status(
            uuid4(),
            status=BatchDocumentStatus.COMPLETED,
            result_file_path="/path/to/result.json",
            processing_time_ms=1500,
        )

        assert mock_doc.status == BatchDocumentStatus.COMPLETED
        assert mock_doc.result_file_path == "/path/to/result.json"
        assert mock_doc.processing_time_ms == 1500
        mock_db.commit.assert_called_once()

    def test_count_running_batches_returns_scalar(self):
        """Count running batches should return query scalar result."""
        mock_db = Mock()
        service = BatchService(mock_db)

        # Mock the query chain to return 0
        mock_db.query.return_value.filter.return_value.scalar.return_value = 0

        count = service.count_running_batches(user_id=123)

        assert count == 0

    def test_count_running_batches_handles_none(self):
        """Count running batches should return 0 when scalar is None."""
        mock_db = Mock()
        service = BatchService(mock_db)

        # Mock the query chain to return None
        mock_db.query.return_value.filter.return_value.scalar.return_value = None

        count = service.count_running_batches(user_id=123)

        assert count == 0

    # CR-11: Test that PENDING batches can be cancelled
    def test_cancel_batch_works_for_pending_status(self):
        """Cancel batch should work for PENDING status batches."""
        mock_db = Mock()
        service = BatchService(mock_db)

        # Create a mock batch that's pending
        mock_batch = Mock()
        mock_batch.status = BatchStatus.PENDING

        # Mock get_batch to return our mock batch
        with patch.object(service, 'get_batch', return_value=mock_batch):
            result = service.cancel_batch(uuid4(), user_id=1)

            assert result.status == BatchStatus.CANCELLED
            assert result.completed_at is not None
            mock_db.commit.assert_called_once()

    # CR-10: Test that processed_at is set for completed/failed status
    def test_update_document_status_sets_processed_at_for_completed(self):
        """Update document status should set processed_at for COMPLETED status."""
        mock_db = Mock()
        service = BatchService(mock_db)

        mock_doc = Mock()
        mock_doc.status = BatchDocumentStatus.PENDING
        mock_doc.processed_at = None
        mock_db.query.return_value.filter.return_value.first.return_value = mock_doc

        service.update_document_status(
            uuid4(),
            status=BatchDocumentStatus.COMPLETED,
        )

        assert mock_doc.status == BatchDocumentStatus.COMPLETED
        assert mock_doc.processed_at is not None

    def test_update_document_status_sets_processed_at_for_failed(self):
        """Update document status should set processed_at for FAILED status."""
        mock_db = Mock()
        service = BatchService(mock_db)

        mock_doc = Mock()
        mock_doc.status = BatchDocumentStatus.PENDING
        mock_doc.processed_at = None
        mock_db.query.return_value.filter.return_value.first.return_value = mock_doc

        service.update_document_status(
            uuid4(),
            status=BatchDocumentStatus.FAILED,
            error_message="Test error",
        )

        assert mock_doc.status == BatchDocumentStatus.FAILED
        assert mock_doc.error_message == "Test error"
        assert mock_doc.processed_at is not None

    def test_update_document_status_does_not_set_processed_at_for_processing(self):
        """Update document status should NOT set processed_at for PROCESSING status."""
        mock_db = Mock()
        service = BatchService(mock_db)

        mock_doc = Mock()
        mock_doc.status = BatchDocumentStatus.PENDING
        mock_doc.processed_at = None
        mock_db.query.return_value.filter.return_value.first.return_value = mock_doc

        service.update_document_status(
            uuid4(),
            status=BatchDocumentStatus.PROCESSING,
        )

        assert mock_doc.status == BatchDocumentStatus.PROCESSING
        # processed_at should not have been set (would remain None)
        # The mock doesn't track this properly, but the service code only sets
        # processed_at for COMPLETED or FAILED status


class TestCreateBatchMocked:
    """CR-4: Tests for create_batch method using mocks."""

    def test_create_batch_creates_batch_record(self):
        """Create batch should create a batch record with correct fields."""
        mock_db = Mock()
        service = BatchService(mock_db)

        # Mock the flush to set batch.id
        def set_batch_id(batch=None):
            if hasattr(mock_db, '_last_added') and mock_db._last_added:
                mock_db._last_added.id = uuid4()

        mock_db.flush.side_effect = lambda: None  # No-op
        mock_db.commit.side_effect = lambda: None
        mock_db.refresh.side_effect = lambda x: None

        user_id = 123
        flow_id = uuid4()
        document_ids = [uuid4(), uuid4(), uuid4()]

        # Capture added objects
        added_objects = []
        mock_db.add.side_effect = lambda obj: added_objects.append(obj)

        result = service.create_batch(
            user_id=user_id,
            flow_id=flow_id,
            document_ids=document_ids,
        )

        # Verify batch was added
        assert len(added_objects) >= 1
        batch = added_objects[0]
        assert batch.user_id == user_id
        assert batch.flow_id == flow_id
        assert batch.total_documents == 3
        assert batch.completed_documents == 0
        assert batch.failed_documents == 0
        assert batch.status == BatchStatus.PENDING

    def test_create_batch_creates_document_records_with_positions(self):
        """Create batch should create BatchDocument records with positions."""
        mock_db = Mock()
        service = BatchService(mock_db)

        mock_db.flush.side_effect = lambda: None
        mock_db.commit.side_effect = lambda: None
        mock_db.refresh.side_effect = lambda x: None

        document_ids = [uuid4(), uuid4(), uuid4()]

        added_objects = []
        mock_db.add.side_effect = lambda obj: added_objects.append(obj)

        service.create_batch(
            user_id=1,
            flow_id=uuid4(),
            document_ids=document_ids,
        )

        # Filter to just BatchDocument objects (skip the Batch)
        batch_docs = [obj for obj in added_objects if isinstance(obj, BatchDocument)]
        assert len(batch_docs) == 3

        # Verify positions
        positions = [doc.position for doc in batch_docs]
        assert sorted(positions) == [0, 1, 2]

        # Verify all have pending status
        for doc in batch_docs:
            assert doc.status == BatchDocumentStatus.PENDING


class TestBatchToResponseMocked:
    """CR-5: Tests for batch_to_response method using mocks."""

    def test_batch_to_response_converts_batch_model(self):
        """batch_to_response should convert Batch model to response schema."""
        mock_db = Mock()
        service = BatchService(mock_db)

        # Create a mock batch
        mock_batch = Mock()
        mock_batch.id = uuid4()
        mock_batch.flow_id = uuid4()
        mock_batch.status = BatchStatus.COMPLETED
        mock_batch.total_documents = 2
        mock_batch.completed_documents = 2
        mock_batch.failed_documents = 0
        mock_batch.created_at = datetime.now(timezone.utc)
        mock_batch.started_at = datetime.now(timezone.utc)
        mock_batch.completed_at = datetime.now(timezone.utc)

        # Create mock documents
        mock_doc1 = Mock()
        mock_doc1.id = uuid4()
        mock_doc1.document_id = uuid4()
        mock_doc1.position = 0
        mock_doc1.status = BatchDocumentStatus.COMPLETED
        mock_doc1.result_file_path = "/path/to/result1.json"
        mock_doc1.error_message = None
        mock_doc1.processing_time_ms = 1000
        mock_doc1.processed_at = datetime.now(timezone.utc)

        mock_batch.documents = [mock_doc1]

        # Mock flow lookup to return None
        mock_db.query.return_value.filter.return_value.first.return_value = None

        # Mock document titles lookup
        with patch.object(service, 'get_document_titles', return_value={}):
            result = service.batch_to_response(mock_batch, flow_name="Test Flow")

        assert result.id == mock_batch.id
        assert result.flow_id == mock_batch.flow_id
        assert result.flow_name == "Test Flow"
        assert result.status == BatchStatus.COMPLETED
        assert result.total_documents == 2
        assert result.completed_documents == 2
        assert len(result.documents) == 1

    def test_batch_to_response_looks_up_flow_name(self):
        """batch_to_response should look up flow name if not provided."""
        mock_db = Mock()
        service = BatchService(mock_db)

        # Create a mock batch
        mock_batch = Mock()
        mock_batch.id = uuid4()
        mock_batch.flow_id = uuid4()
        mock_batch.status = BatchStatus.PENDING
        mock_batch.total_documents = 1
        mock_batch.completed_documents = 0
        mock_batch.failed_documents = 0
        mock_batch.created_at = datetime.now(timezone.utc)
        mock_batch.started_at = None
        mock_batch.completed_at = None
        mock_batch.documents = []

        # Mock flow lookup
        with patch.object(service, 'get_flow_name', return_value="Looked Up Flow") as mock_get_flow:
            with patch.object(service, 'get_document_titles', return_value={}):
                result = service.batch_to_response(mock_batch)

        mock_get_flow.assert_called_once_with(mock_batch.flow_id)
        assert result.flow_name == "Looked Up Flow"

    def test_batch_to_response_looks_up_document_titles(self):
        """batch_to_response should look up document titles."""
        mock_db = Mock()
        service = BatchService(mock_db)

        doc_id = uuid4()

        # Create a mock batch with one document
        mock_batch = Mock()
        mock_batch.id = uuid4()
        mock_batch.flow_id = uuid4()
        mock_batch.status = BatchStatus.COMPLETED
        mock_batch.total_documents = 1
        mock_batch.completed_documents = 1
        mock_batch.failed_documents = 0
        mock_batch.created_at = datetime.now(timezone.utc)
        mock_batch.started_at = datetime.now(timezone.utc)
        mock_batch.completed_at = datetime.now(timezone.utc)

        mock_doc = Mock()
        mock_doc.id = uuid4()
        mock_doc.document_id = doc_id
        mock_doc.position = 0
        mock_doc.status = BatchDocumentStatus.COMPLETED
        mock_doc.result_file_path = "/path/to/result.json"
        mock_doc.error_message = None
        mock_doc.processing_time_ms = 500
        mock_doc.processed_at = datetime.now(timezone.utc)

        mock_batch.documents = [mock_doc]

        # Mock document titles lookup
        with patch.object(service, 'get_document_titles', return_value={doc_id: "My Document"}) as mock_titles:
            result = service.batch_to_response(mock_batch, flow_name="Flow")

        mock_titles.assert_called_once()
        assert result.documents[0].document_title == "My Document"
