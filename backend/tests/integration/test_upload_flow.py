"""Integration tests for ontology upload flow.

These tests verify the complete upload workflow from file upload through
status polling to final database verification. Uses a small test ontology.

IMPORTANT: These tests are expected to FAIL until the endpoints are implemented (TDD).
"""

import pytest
from fastapi.testclient import TestClient
import sys
from pathlib import Path
import io
import time
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Add the backend/src directory to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


class TestUploadFlow:
    """Integration tests for complete ontology upload flow."""

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
        # Use test database or main database depending on environment
        db_url = "postgresql://ontology_admin@localhost/curation_db"
        engine = create_engine(db_url)
        Session = sessionmaker(bind=engine)
        session = Session()
        yield session
        session.close()

    @pytest.fixture
    def mini_obo_file(self):
        """Create a minimal OBO file with 10-50 terms for testing."""
        obo_content = """format-version: 1.2
data-version: test_mini/2025-09-29
ontology: test_mini
default-namespace: test_ontology

[Term]
id: TEST:0000001
name: biological process
namespace: test_ontology
def: "Any process specifically pertinent to the functioning of integrated living units." [TEST:TEST]

[Term]
id: TEST:0000002
name: cellular process
namespace: test_ontology
def: "Any process that is carried out at the cellular level." [TEST:TEST]
is_a: TEST:0000001 ! biological process

[Term]
id: TEST:0000003
name: metabolic process
namespace: test_ontology
def: "The chemical reactions and pathways." [TEST:TEST]
is_a: TEST:0000001 ! biological process
synonym: "metabolism" EXACT []

[Term]
id: TEST:0000004
name: cellular metabolic process
namespace: test_ontology
def: "The chemical reactions and pathways in a cell." [TEST:TEST]
is_a: TEST:0000002 ! cellular process
is_a: TEST:0000003 ! metabolic process

[Term]
id: TEST:0000005
name: protein metabolic process
namespace: test_ontology
def: "The chemical reactions involving proteins." [TEST:TEST]
is_a: TEST:0000003 ! metabolic process

[Term]
id: TEST:0000006
name: cell communication
namespace: test_ontology
def: "Any process that mediates interactions between a cell and its surroundings." [TEST:TEST]
is_a: TEST:0000002 ! cellular process

[Term]
id: TEST:0000007
name: signal transduction
namespace: test_ontology
def: "The cellular process of signal transmission." [TEST:TEST]
is_a: TEST:0000006 ! cell communication

[Term]
id: TEST:0000008
name: response to stimulus
namespace: test_ontology
def: "Any process that results from a stimulus." [TEST:TEST]
is_a: TEST:0000001 ! biological process

[Term]
id: TEST:0000009
name: response to stress
namespace: test_ontology
def: "Any process that results from a stress stimulus." [TEST:TEST]
is_a: TEST:0000008 ! response to stimulus

[Term]
id: TEST:0000010
name: obsolete old term
namespace: test_ontology
def: "This term is obsolete." [TEST:TEST]
is_obsolete: true
replaced_by: TEST:0000001

[Typedef]
id: part_of
name: part of
is_transitive: true
"""
        return io.BytesIO(obo_content.encode('utf-8'))

    def test_complete_upload_workflow(self, client, db_session, mini_obo_file):
        """Test complete workflow: upload → status polling → verify loaded.

        This is the main quickstart scenario from quickstart.md.
        Steps:
        1. Upload OBO file
        2. Poll status endpoint until loaded
        3. Verify ontology exists in database
        4. Verify terms were stored correctly
        """
        # Step 1: Upload ontology file
        files = {'file': ('test_mini.obo', mini_obo_file, 'application/obo')}
        data = {'name': 'test_mini_ontology'}

        upload_response = client.post("/api/ontologies/upload", files=files, data=data)

        # Verify upload was accepted
        assert upload_response.status_code == 202, \
            "Upload should return 202 Accepted"

        upload_data = upload_response.json()
        assert "id" in upload_data, "Upload response should include ontology ID"
        assert "status_url" in upload_data, "Upload response should include status_url"

        ontology_id = upload_data["id"]
        status_url = upload_data["status_url"]

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

        # Verify progress is 100%
        assert status_data["progress"] == 100, \
            "Progress should be 100% when loaded"

        # Step 3: Verify ontology exists in database
        result = db_session.execute(
            text("SELECT * FROM ontology.ontologies WHERE id = :id"),
            {"id": ontology_id}
        ).fetchone()

        assert result is not None, \
            "Ontology should exist in database"

        assert result.name == "test_mini_ontology", \
            "Ontology name should match uploaded name"

        # Step 4: Verify terms were stored
        terms_result = db_session.execute(
            text("SELECT COUNT(*) FROM ontology.ontology_terms WHERE ontology_id = :id"),
            {"id": ontology_id}
        ).scalar()

        assert terms_result == 10, \
            f"Should have 10 terms in database, found {terms_result}"

        # Verify term_count was updated in ontology record
        assert result.term_count == 10, \
            "Ontology term_count should be 10"

        # Cleanup
        db_session.execute(
            text("DELETE FROM ontology.ontologies WHERE id = :id"),
            {"id": ontology_id}
        )
        db_session.commit()

    def test_upload_verifies_term_details(self, client, db_session, mini_obo_file):
        """Test that uploaded terms have correct details (name, definition, relationships)."""
        # Upload ontology
        files = {'file': ('test_mini.obo', mini_obo_file, 'application/obo')}
        data = {'name': 'test_mini_details'}

        upload_response = client.post("/api/ontologies/upload", files=files, data=data)
        assert upload_response.status_code == 202

        ontology_id = upload_response.json()["id"]

        # Wait for loading to complete
        max_polls = 30
        for _ in range(max_polls):
            status_response = client.get(f"/api/ontologies/{ontology_id}/status")
            if status_response.json()["status"] == "loaded":
                break
            time.sleep(1)

        # Verify specific term details
        term_result = db_session.execute(
            text("""
                SELECT term_id, name, definition, namespace, is_obsolete, replaced_by
                FROM ontology.ontology_terms
                WHERE ontology_id = :id AND term_id = 'TEST:0000001'
            """),
            {"id": ontology_id}
        ).fetchone()

        assert term_result is not None, "Term TEST:0000001 should exist"
        assert term_result.name == "biological process"
        assert term_result.namespace == "test_ontology"
        assert term_result.is_obsolete is False

        # Check obsolete term
        obsolete_result = db_session.execute(
            text("""
                SELECT term_id, is_obsolete, replaced_by
                FROM ontology.ontology_terms
                WHERE ontology_id = :id AND term_id = 'TEST:0000010'
            """),
            {"id": ontology_id}
        ).fetchone()

        assert obsolete_result is not None, "Obsolete term should exist"
        assert obsolete_result.is_obsolete is True
        assert obsolete_result.replaced_by == "TEST:0000001"

        # Cleanup
        db_session.execute(
            text("DELETE FROM ontology.ontologies WHERE id = :id"),
            {"id": ontology_id}
        )
        db_session.commit()

    def test_upload_verifies_relationships(self, client, db_session, mini_obo_file):
        """Test that relationships (is_a) are stored correctly."""
        # Upload ontology
        files = {'file': ('test_mini.obo', mini_obo_file, 'application/obo')}
        data = {'name': 'test_mini_relationships'}

        upload_response = client.post("/api/ontologies/upload", files=files, data=data)
        assert upload_response.status_code == 202

        ontology_id = upload_response.json()["id"]

        # Wait for loading
        max_polls = 30
        for _ in range(max_polls):
            status_response = client.get(f"/api/ontologies/{ontology_id}/status")
            if status_response.json()["status"] == "loaded":
                break
            time.sleep(1)

        # Count relationships
        relationships_result = db_session.execute(
            text("""
                SELECT COUNT(*)
                FROM ontology.term_relationships tr
                JOIN ontology.ontology_terms t ON tr.subject_term_id = t.id
                WHERE t.ontology_id = :id AND tr.predicate = 'is_a'
            """),
            {"id": ontology_id}
        ).scalar()

        assert relationships_result > 0, \
            "Should have is_a relationships stored"

        # Verify specific relationship (TEST:0000002 is_a TEST:0000001)
        specific_rel = db_session.execute(
            text("""
                SELECT COUNT(*)
                FROM ontology.term_relationships tr
                JOIN ontology.ontology_terms subj ON tr.subject_term_id = subj.id
                JOIN ontology.ontology_terms obj ON tr.object_term_id = obj.id
                WHERE subj.term_id = 'TEST:0000002'
                  AND obj.term_id = 'TEST:0000001'
                  AND tr.predicate = 'is_a'
            """)
        ).scalar()

        assert specific_rel == 1, \
            "TEST:0000002 should have is_a relationship to TEST:0000001"

        # Cleanup
        db_session.execute(
            text("DELETE FROM ontology.ontologies WHERE id = :id"),
            {"id": ontology_id}
        )
        db_session.commit()

    def test_upload_verifies_synonyms(self, client, db_session, mini_obo_file):
        """Test that synonyms are stored correctly."""
        # Upload ontology
        files = {'file': ('test_mini.obo', mini_obo_file, 'application/obo')}
        data = {'name': 'test_mini_synonyms'}

        upload_response = client.post("/api/ontologies/upload", files=files, data=data)
        assert upload_response.status_code == 202

        ontology_id = upload_response.json()["id"]

        # Wait for loading
        max_polls = 30
        for _ in range(max_polls):
            status_response = client.get(f"/api/ontologies/{ontology_id}/status")
            if status_response.json()["status"] == "loaded":
                break
            time.sleep(1)

        # Verify synonym for TEST:0000003 (metabolic process has synonym "metabolism")
        synonym_result = db_session.execute(
            text("""
                SELECT s.synonym, s.scope
                FROM ontology.term_synonyms s
                JOIN ontology.ontology_terms t ON s.term_id = t.id
                WHERE t.term_id = 'TEST:0000003' AND t.ontology_id = :id
            """),
            {"id": ontology_id}
        ).fetchone()

        assert synonym_result is not None, \
            "TEST:0000003 should have a synonym"
        assert synonym_result.synonym == "metabolism"
        assert synonym_result.scope == "EXACT"

        # Cleanup
        db_session.execute(
            text("DELETE FROM ontology.ontologies WHERE id = :id"),
            {"id": ontology_id}
        )
        db_session.commit()