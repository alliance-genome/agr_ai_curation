"""Unit tests for ontology ingestion utilities."""

from __future__ import annotations

from pathlib import Path

from app.database import SessionLocal, engine as db_engine
from app.models import Base, OntologyTerm, OntologyTermRelation, UnifiedChunk
from app.jobs.ingest_ontology import parse_obo_terms, format_chunk_text, ingest_ontology


def test_parse_obo_terms_extracts_core_fields(tmp_path: Path):
    obo_content = """
[Term]
id: DOID:1234
name: Example disease
def: "An illustrative disease." [PMID:123]
synonym: "Sample" EXACT []
is_a: DOID:0001 ! parent
xref: UMLS:C000000

[Term]
id: DOID:5678
name: Second disease
""".strip()
    obo_path = tmp_path / "sample.obo"
    obo_path.write_text(obo_content)

    terms = list(parse_obo_terms(obo_path))

    assert len(terms) == 2
    first = terms[0]
    assert first["id"] == "DOID:1234"
    assert first["name"] == "Example disease"
    assert first["definition"] == "An illustrative disease."
    assert first["synonyms"] == ["Sample"]
    assert first["parents"] == ["DOID:0001"]
    assert first["xrefs"] == ["UMLS:C000000"]

    chunk_text = format_chunk_text(first)
    assert "Example disease" in chunk_text
    assert "Synonyms" in chunk_text


class StubEmbeddingService:
    def embed_unified_chunks(self, **kwargs):
        return {"embedded": 0}


def test_ingest_ontology_creates_normalized_rows(tmp_path: Path):
    Base.metadata.create_all(bind=db_engine)

    obo_path = tmp_path / "doid_sample.obo"
    obo_path.write_text(
        """
[Term]
id: DOID:0001
name: Parent disease
def: "Parent definition."

[Term]
id: DOID:0002
name: Child disease
def: "Child definition."
is_a: DOID:0001 ! parent
""".strip()
    )

    session = SessionLocal()
    try:
        session.query(OntologyTermRelation).delete()
        session.query(OntologyTerm).delete()
        session.query(UnifiedChunk).filter(
            UnifiedChunk.source_type == "ontology_disease"
        ).delete()
        session.commit()
    finally:
        session.close()

    summary = ingest_ontology(
        ontology_type="disease",
        source_id="unit",
        obo_path=obo_path,
        embedding_service=StubEmbeddingService(),
    )

    session = SessionLocal()
    try:
        terms = (
            session.query(OntologyTerm)
            .filter(
                OntologyTerm.ontology_type == "disease",
                OntologyTerm.source_id == "unit",
            )
            .all()
        )
        relations = (
            session.query(OntologyTermRelation)
            .filter(
                OntologyTermRelation.ontology_type == "disease",
                OntologyTermRelation.source_id == "unit",
            )
            .all()
        )
        chunks = (
            session.query(UnifiedChunk)
            .filter(
                UnifiedChunk.source_type == "ontology_disease",
                UnifiedChunk.source_id == "unit",
            )
            .all()
        )
    finally:
        session.close()

    assert summary["inserted"] == 2
    assert len(terms) == 2
    assert any(term.term_id == "DOID:0002" for term in terms)
    assert summary["relations"] == 1
    assert relations and relations[0].parent_term_id == "DOID:0001"
    assert len(chunks) == 2
