"""Integration tests for ontology import from URL flow.

These tests verify the complete import workflow from URL submission through
background processing to database verification. Mocks HTTP requests.

IMPORTANT: These tests are expected to FAIL until the endpoints are implemented (TDD).
"""

import pytest
from fastapi.testclient import TestClient
import sys
from pathlib import Path
import time
from unittest.mock import patch, Mock
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Add the backend/src directory to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


class TestImportUrlFlow:
    """Integration tests for complete ontology import from URL flow."""

    @pytest.fixture
    def client(self):
        """Create a test client for the FastAPI app."""
        try:
            from main import app
            return TestClient(app)
        except ImportError:
            pytest.skip("FastAPI app not available - endpoints not implemented yet")

    @pytest.fixture
    def db_session(self):
        """Create a database session for verification."""
        db_url = "postgresql://ontology_admin@localhost/curation_db"
        engine = create_engine(db_url)
        Session = sessionmaker(bind=engine)
        session = Session()
        yield session
        session.close()

    @pytest.fixture
    def mock_obo_content(self):
        """Mock OBO file content for URL import."""
        return b"""format-version: 1.2
data-version: test_url/2025-09-29
ontology: test_url
default-namespace: test_url_ontology

[Term]
id: URL:0000001
name: test term from URL
namespace: test_url_ontology
def: "A test term loaded from URL" []

[Term]
id: URL:0000002
name: another URL term
namespace: test_url_ontology
def: "Another test term" []
is_a: URL:0000001 ! test term from URL

[Term]
id: URL:0000003
name: third URL term
namespace: test_url_ontology
def: "Third test term" []
is_a: URL:0000001 ! test term from URL
synonym: "URL term 3" EXACT []
"""

    def test_complete_import_url_workflow(self, client, db_session, mock_obo_content):
        """Test complete workflow: import-url → background processing → verify loaded.

        Steps:
        1. Submit URL for import
        2. Mock HTTP request to download OBO file
        3. Poll status endpoint until loaded
        4. Verify ontology and terms in database
        """
        # Mock HTTP request to download OBO file
        with patch('requests.get') as mock_get:
            mock_response = Mock()
            mock_response.content = mock_obo_content
            mock_response.status_code = 200
            mock_response.headers = {'content-type': 'application/obo'}
            mock_get.return_value = mock_response

            # Step 1: Submit import-url request
            payload = {
                "url": "http://example.com/test.obo",
                "name": "test_url_ontology"
            }

            import_response = client.post("/api/ontologies/import-url", json=payload)

            # Verify import was accepted
            assert import_response.status_code == 202, \
                "Import should return 202 Accepted"

            import_data = import_response.json()
            assert "id" in import_data, "Import response should include ontology ID"
            assert "status_url" in import_data, "Import response should include status_url"

            ontology_id = import_data["id"]
            status_url = import_data["status_url"]

        # Step 2: Poll status endpoint until loaded or failed
        max_polls = 30  # 30 seconds max
        poll_interval = 1  # 1 second between polls
        final_status = None

        for _ in range(max_polls):
            status_response = client.get(status_url)
            assert status_response.status_code == 200, \
                "Status endpoint should return 200"

            status_data = status_response.json()
            final_status = status_data["status"]

            if final_status in ["loaded", "failed"]:
                break

            time.sleep(poll_interval)

        # Verify loading completed successfully
        assert final_status == "loaded", \
            f"Ontology should be loaded, got status: {final_status}"

        # Step 3: Verify ontology exists in database
        result = db_session.execute(
            text("SELECT * FROM ontology.ontologies WHERE id = :id"),
            {"id": ontology_id}
        ).fetchone()

        assert result is not None, \
            "Ontology should exist in database"

        assert result.name == "test_url_ontology", \
            "Ontology name should match"

        assert result.source_url == "http://example.com/test.obo", \
            "Source URL should be stored"

        # Step 4: Verify terms were stored
        terms_result = db_session.execute(
            text("SELECT COUNT(*) FROM ontology.ontology_terms WHERE ontology_id = :id"),
            {"id": ontology_id}
        ).scalar()

        assert terms_result == 3, \
            f"Should have 3 terms in database, found {terms_result}"

        # Cleanup
        db_session.execute(
            text("DELETE FROM ontology.ontologies WHERE id = :id"),
            {"id": ontology_id}
        )
        db_session.commit()

    def test_import_url_handles_unreachable_url(self, client):
        """Test that unreachable URLs are handled gracefully."""
        # Mock HTTP request failure
        with patch('requests.get') as mock_get:
            mock_get.side_effect = Exception("Connection failed")

            payload = {
                "url": "http://nonexistent-domain-12345.com/test.obo",
                "name": "test_unreachable"
            }

            response = client.post("/api/ontologies/import-url", json=payload)

            # Should either reject immediately (400) or accept and fail during processing (202)
            assert response.status_code in [400, 202], \
                "Should handle unreachable URL"

            if response.status_code == 202:
                # If accepted, status should eventually be 'failed'
                ontology_id = response.json()["id"]

                # Poll for failure
                max_polls = 30
                for _ in range(max_polls):
                    status_response = client.get(f"/api/ontologies/{ontology_id}/status")
                    status_data = status_response.json()

                    if status_data["status"] == "failed":
                        assert "error_message" in status_data
                        break

                    time.sleep(1)

    def test_import_url_handles_invalid_obo_format(self, client, db_session):
        """Test that invalid OBO format is handled gracefully."""
        # Mock HTTP request returning invalid OBO content
        with patch('requests.get') as mock_get:
            mock_response = Mock()
            mock_response.content = b"This is not a valid OBO file"
            mock_response.status_code = 200
            mock_response.headers = {'content-type': 'text/plain'}
            mock_get.return_value = mock_response

            payload = {
                "url": "http://example.com/invalid.obo",
                "name": "test_invalid_format"
            }

            response = client.post("/api/ontologies/import-url", json=payload)

            # Should either reject or fail during processing
            if response.status_code == 202:
                ontology_id = response.json()["id"]

                # Poll for failure
                max_polls = 30
                for _ in range(max_polls):
                    status_response = client.get(f"/api/ontologies/{ontology_id}/status")
                    status_data = status_response.json()

                    if status_data["status"] in ["failed", "loaded"]:
                        if status_data["status"] == "failed":
                            assert "error_message" in status_data
                        break

                    time.sleep(1)

                # Cleanup if created
                db_session.execute(
                    text("DELETE FROM ontology.ontologies WHERE id = :id"),
                    {"id": ontology_id}
                )
                db_session.commit()

    def test_import_url_stores_source_url(self, client, db_session, mock_obo_content):
        """Test that source_url is stored for future refresh."""
        with patch('requests.get') as mock_get:
            mock_response = Mock()
            mock_response.content = mock_obo_content
            mock_response.status_code = 200
            mock_get.return_value = mock_response

            payload = {
                "url": "http://example.com/test_source.obo",
                "name": "test_source_url"
            }

            response = client.post("/api/ontologies/import-url", json=payload)
            assert response.status_code == 202

            ontology_id = response.json()["id"]

            # Wait for loading
            max_polls = 30
            for _ in range(max_polls):
                status_response = client.get(f"/api/ontologies/{ontology_id}/status")
                if status_response.json()["status"] == "loaded":
                    break
                time.sleep(1)

            # Verify source_url is stored
            result = db_session.execute(
                text("SELECT source_url FROM ontology.ontologies WHERE id = :id"),
                {"id": ontology_id}
            ).fetchone()

            assert result is not None
            assert result.source_url == "http://example.com/test_source.obo", \
                "Source URL should be stored for refresh"

            # Cleanup
            db_session.execute(
                text("DELETE FROM ontology.ontologies WHERE id = :id"),
                {"id": ontology_id}
            )
            db_session.commit()

    def test_import_url_background_task_execution(self, client, mock_obo_content):
        """Test that import is processed in background (returns immediately)."""
        with patch('requests.get') as mock_get:
            mock_response = Mock()
            mock_response.content = mock_obo_content
            mock_response.status_code = 200
            mock_get.return_value = mock_response

            payload = {
                "url": "http://example.com/test_bg.obo",
                "name": "test_background"
            }

            # Import should return immediately (within 1 second)
            import time as time_module
            start_time = time_module.time()
            response = client.post("/api/ontologies/import-url", json=payload)
            elapsed_time = time_module.time() - start_time

            assert response.status_code == 202
            assert elapsed_time < 2.0, \
                "Import should return immediately (background processing)"

            # Initial status should be 'pending' or 'loading', not 'loaded'
            ontology_id = response.json()["id"]
            initial_status = client.get(f"/api/ontologies/{ontology_id}/status")
            assert initial_status.json()["status"] in ["pending", "loading"], \
                "Initial status should indicate processing has started"

    def test_import_url_handles_large_file(self, client):
        """Test that large OBO files can be imported from URL.

        This test documents expected behavior - actual large file testing
        would be done with real ontologies in manual testing.
        """
        # Mock large file response
        with patch('requests.get') as mock_get:
            # Simulate large OBO content (not actually 1GB, just metadata)
            mock_response = Mock()
            mock_response.content = b"format-version: 1.2\n" * 10000  # Simulated large content
            mock_response.status_code = 200
            mock_response.headers = {'content-length': '1073741824'}  # 1GB metadata
            mock_get.return_value = mock_response

            payload = {
                "url": "http://example.com/large.obo",
                "name": "test_large_file"
            }

            response = client.post("/api/ontologies/import-url", json=payload)

            # Should accept large files for background processing
            assert response.status_code in [202, 413], \
                "Should accept large files (202) or reject if too large (413)"