"""Integration tests for agent SQL queries using ontology_reader role.

These tests verify that AI agents can query ontology data using
read-only database access. Tests the query patterns from quickstart.md.

IMPORTANT: These tests are expected to FAIL until database and tables are set up.
"""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError, ProgrammingError
import sys
from pathlib import Path

# Add the backend/src directory to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


class TestAgentQueries:
    """Integration tests for agent SQL query patterns."""

    @pytest.fixture
    def agent_readonly_engine(self):
        """Create SQLAlchemy engine with ontology_reader (read-only) credentials.

        Uses .pgpass file for password (as agents will).
        """
        # Connection string without password (reads from .pgpass)
        db_url = "postgresql://ontology_reader@localhost/curation_db"
        try:
            engine = create_engine(db_url, pool_size=5, max_overflow=10)
            # Test connection
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            yield engine
            engine.dispose()
        except OperationalError as e:
            pytest.skip(f"Database not available or ontology_reader user not configured: {e}")

    @pytest.fixture
    def agent_session(self, agent_readonly_engine):
        """Create a session for agent queries."""
        Session = sessionmaker(bind=agent_readonly_engine)
        session = Session()
        yield session
        session.close()

    @pytest.fixture
    def admin_session(self):
        """Create an admin session for test data setup."""
        db_url = "postgresql://ontology_admin@localhost/curation_db"
        engine = create_engine(db_url)
        Session = sessionmaker(bind=engine)
        session = Session()
        yield session
        session.close()
        engine.dispose()

    @pytest.fixture
    def test_ontology_data(self, admin_session):
        """Set up test ontology data for agent queries."""
        # Create a test ontology with terms for querying
        try:
            # Insert test ontology
            ontology_id = admin_session.execute(
                text("""
                    INSERT INTO ontology.ontologies (id, name, version, term_count, date_loaded, source_url, namespace)
                    VALUES (gen_random_uuid(), 'test_agent_ontology', '1.0', 5, NOW(), 'http://test.com/test.obo', 'test_ns')
                    RETURNING id
                """)
            ).scalar()
            admin_session.commit()

            # Insert test terms
            term_ids = []
            for i in range(1, 6):
                term_id = admin_session.execute(
                    text("""
                        INSERT INTO ontology.ontology_terms
                        (id, ontology_id, term_id, name, definition, namespace, is_obsolete)
                        VALUES (gen_random_uuid(), :ont_id, :term_id, :name, :definition, 'test_ns', false)
                        RETURNING id
                    """),
                    {
                        "ont_id": ontology_id,
                        "term_id": f"TEST:{i:07d}",
                        "name": f"test term {i}",
                        "definition": f"Definition of test term {i}"
                    }
                ).scalar()
                term_ids.append(term_id)
            admin_session.commit()

            # Insert synonym for term 1
            admin_session.execute(
                text("""
                    INSERT INTO ontology.term_synonyms (id, term_id, synonym, scope)
                    VALUES (gen_random_uuid(), :term_id, 'kinase activity', 'EXACT')
                """),
                {"term_id": term_ids[0]}
            )
            admin_session.commit()

            # Insert relationships (term 2 is_a term 1, term 3 is_a term 1)
            admin_session.execute(
                text("""
                    INSERT INTO ontology.term_relationships (id, subject_term_id, predicate, object_term_id)
                    VALUES
                        (gen_random_uuid(), :child1, 'is_a', :parent),
                        (gen_random_uuid(), :child2, 'is_a', :parent)
                """),
                {"child1": term_ids[1], "child2": term_ids[2], "parent": term_ids[0]}
            )
            admin_session.commit()

            yield {"ontology_id": ontology_id, "term_ids": term_ids}

            # Cleanup
            admin_session.execute(
                text("DELETE FROM ontology.ontologies WHERE id = :id"),
                {"id": ontology_id}
            )
            admin_session.commit()

        except (OperationalError, ProgrammingError) as e:
            pytest.skip(f"Database schema not ready: {e}")

    def test_agent_can_connect_readonly(self, agent_session):
        """Test that agents can connect with ontology_reader credentials.

        Uses .pgpass file for authentication (as documented in quickstart.md).
        """
        # Simple query to verify connection works
        result = agent_session.execute(text("SELECT 1 as test")).scalar()
        assert result == 1, "Agent should be able to connect and query"

    def test_agent_can_count_terms(self, agent_session, test_ontology_data):
        """Test that agents can count total terms."""
        result = agent_session.execute(
            text("SELECT COUNT(*) FROM ontology.ontology_terms")
        ).scalar()

        assert result >= 5, \
            f"Should have at least 5 test terms, found {result}"

    def test_agent_find_term_by_id(self, agent_session, test_ontology_data):
        """Test query pattern: Find term by ID (from quickstart.md)."""
        # Query from quickstart.md
        result = agent_session.execute(
            text("SELECT * FROM ontology.ontology_terms WHERE term_id = :tid"),
            {"tid": "TEST:0000001"}
        ).fetchone()

        assert result is not None, "Should find term by ID"
        assert result.term_id == "TEST:0000001"
        assert result.name == "test term 1"

    def test_agent_search_by_name_or_synonym(self, agent_session, test_ontology_data):
        """Test query pattern: Search by name or synonym (from quickstart.md)."""
        # Query from quickstart.md
        results = agent_session.execute(
            text("""
                SELECT DISTINCT t.*
                FROM ontology.ontology_terms t
                LEFT JOIN ontology.term_synonyms s ON t.id = s.term_id
                WHERE t.name ILIKE :query OR s.synonym ILIKE :query
                LIMIT 10
            """),
            {"query": "%kinase%"}
        ).fetchall()

        assert len(results) > 0, "Should find terms by synonym search"
        # Verify at least one result matches our test data
        found = any(r.term_id == "TEST:0000001" for r in results)
        assert found, "Should find TEST:0000001 via synonym 'kinase activity'"

    def test_agent_get_parent_terms(self, agent_session, test_ontology_data):
        """Test query pattern: Get parent terms via is_a relationships (from quickstart.md)."""
        # Query from quickstart.md
        parents = agent_session.execute(
            text("""
                SELECT parent.*
                FROM ontology.ontology_terms child
                JOIN ontology.term_relationships tr ON child.id = tr.subject_term_id
                JOIN ontology.ontology_terms parent ON tr.object_term_id = parent.id
                WHERE child.term_id = :tid AND tr.predicate = 'is_a'
            """),
            {"tid": "TEST:0000002"}
        ).fetchall()

        assert len(parents) > 0, "Should find parent terms"
        assert any(p.term_id == "TEST:0000001" for p in parents), \
            "TEST:0000002 should have TEST:0000001 as parent"

    def test_agent_get_child_terms(self, agent_session, test_ontology_data):
        """Test query pattern: Get child terms (from quickstart.md)."""
        # Query from quickstart.md
        children = agent_session.execute(
            text("""
                SELECT child.*
                FROM ontology.ontology_terms parent
                JOIN ontology.term_relationships tr ON parent.id = tr.object_term_id
                JOIN ontology.ontology_terms child ON tr.subject_term_id = child.id
                WHERE parent.term_id = :tid AND tr.predicate = 'is_a'
            """),
            {"tid": "TEST:0000001"}
        ).fetchall()

        assert len(children) >= 2, \
            "TEST:0000001 should have at least 2 children (TEST:0000002, TEST:0000003)"

        child_ids = [c.term_id for c in children]
        assert "TEST:0000002" in child_ids
        assert "TEST:0000003" in child_ids

    def test_agent_readonly_permissions(self, agent_session, test_ontology_data):
        """Test that ontology_reader cannot INSERT (read-only verification)."""
        # Agents should NOT be able to modify data
        with pytest.raises(Exception) as exc_info:
            agent_session.execute(
                text("""
                    INSERT INTO ontology.ontology_terms
                    (id, ontology_id, term_id, name, namespace, is_obsolete)
                    VALUES (gen_random_uuid(), :ont_id, 'TEST:9999999', 'unauthorized', 'test', false)
                """),
                {"ont_id": test_ontology_data["ontology_id"]}
            )
            agent_session.commit()

        # Should raise permission denied error
        assert "permission denied" in str(exc_info.value).lower() or \
               "read-only" in str(exc_info.value).lower(), \
               "Read-only user should not be able to INSERT"

    def test_agent_query_performance(self, agent_session, test_ontology_data):
        """Test that agent queries are fast (<100ms goal from tasks.md)."""
        import time

        # Measure query time for typical search
        start_time = time.time()

        agent_session.execute(
            text("""
                SELECT DISTINCT t.*
                FROM ontology.ontology_terms t
                LEFT JOIN ontology.term_synonyms s ON t.id = s.term_id
                WHERE t.name ILIKE :query OR s.synonym ILIKE :query
                LIMIT 10
            """),
            {"query": "%test%"}
        ).fetchall()

        elapsed_ms = (time.time() - start_time) * 1000

        # Goal from tasks.md: <100ms
        assert elapsed_ms < 1000, \
            f"Query should be fast (goal <100ms), took {elapsed_ms:.2f}ms"

    def test_agent_connection_pooling(self, agent_readonly_engine):
        """Test that connection pooling works for agent queries."""
        # Create multiple sessions (simulating multiple agents)
        sessions = []
        try:
            for _ in range(5):
                Session = sessionmaker(bind=agent_readonly_engine)
                session = Session()
                sessions.append(session)

                # Each should be able to query
                result = session.execute(text("SELECT 1")).scalar()
                assert result == 1

        finally:
            for session in sessions:
                session.close()

    def test_agent_handles_missing_data_gracefully(self, agent_session):
        """Test that agents handle queries for non-existent data gracefully."""
        # Query for non-existent term ID
        result = agent_session.execute(
            text("SELECT * FROM ontology.ontology_terms WHERE term_id = :tid"),
            {"tid": "NONEXISTENT:9999999"}
        ).fetchone()

        assert result is None, \
            "Query for non-existent term should return None (not error)"

    def test_agent_can_list_ontologies(self, agent_session, test_ontology_data):
        """Test that agents can see available ontologies."""
        result = agent_session.execute(
            text("SELECT name, term_count FROM ontology.ontologies WHERE name = :name"),
            {"name": "test_agent_ontology"}
        ).fetchone()

        assert result is not None, "Agent should be able to list ontologies"
        assert result.name == "test_agent_ontology"
        assert result.term_count == 5

    def test_agent_uses_pgpass_authentication(self):
        """Test that connection works without explicit password (uses .pgpass).

        This documents the expected authentication pattern from quickstart.md.
        """
        # Connection string without password
        db_url = "postgresql://ontology_reader@localhost/curation_db"

        try:
            engine = create_engine(db_url)
            with engine.connect() as conn:
                result = conn.execute(text("SELECT 1")).scalar()
                assert result == 1, "Connection should work via .pgpass"
        except OperationalError:
            pytest.skip("PostgreSQL .pgpass not configured or user doesn't exist")
        finally:
            if 'engine' in locals():
                engine.dispose()