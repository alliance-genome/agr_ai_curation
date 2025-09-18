"""CLI job for ingesting ontology data into the unified RAG store."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import (
    IngestionState,
    IngestionStatus,
    OntologyTerm,
    OntologyTermRelation,
    UnifiedChunk,
)
from app.services.embedding_service_factory import get_embedding_service
from app.services.settings_lookup import get_setting_value


def parse_obo_terms(path: Path) -> Iterable[Dict[str, object]]:
    """Parse a minimal subset of the OBO format for disease ontology files."""

    term: Dict[str, object] = {}
    current_section: str | None = None

    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue

            if line.startswith("["):
                current_section = line
                if line == "[Term]":
                    if term:
                        yield term
                        term = {}
                continue

            if current_section != "[Term]":
                continue

            if line.startswith("id:"):
                term["id"] = line.split("id:", 1)[1].strip()
            elif line.startswith("name:"):
                term["name"] = line.split("name:", 1)[1].strip()
            elif line.startswith("def:"):
                definition = line.split("def:", 1)[1].strip()
                if definition.startswith('"'):
                    definition = definition.split('"')[1]
                term["definition"] = definition
            elif line.startswith("synonym:"):
                synonym_body = line.split("synonym:", 1)[1].strip()
                if synonym_body.startswith('"'):
                    synonym_text = synonym_body.split('"')[1]
                    term.setdefault("synonyms", []).append(synonym_text)
            elif line.startswith("is_a:"):
                parent = line.split("is_a:", 1)[1].strip().split(" ")[0]
                term.setdefault("parents", []).append(parent)
            elif line.startswith("xref:"):
                xref = line.split("xref:", 1)[1].strip()
                term.setdefault("xrefs", []).append(xref)

    if term:
        yield term


def format_chunk_text(term: Dict[str, object]) -> str:
    name = term.get("name", "")
    definition = term.get("definition", "")
    synonyms = term.get("synonyms", [])
    parents = term.get("parents", [])

    lines = [f"Term: {name}"]
    if definition:
        lines.append(f"Definition: {definition}")
    if synonyms:
        lines.append("Synonyms: " + ", ".join(str(item) for item in synonyms))
    if parents:
        lines.append("Parents: " + ", ".join(str(item) for item in parents))
    return "\n".join(lines)


def upsert_status(
    session: Session,
    *,
    source_type: str,
    source_id: str,
    state: IngestionState,
    message: str | None = None,
) -> None:
    record = (
        session.query(IngestionStatus)
        .filter(
            IngestionStatus.source_type == source_type,
            IngestionStatus.source_id == source_id,
        )
        .first()
    )
    if record is None:
        record = IngestionStatus(
            source_type=source_type,
            source_id=source_id,
            status=state,
            message=message,
        )
        session.add(record)
    else:
        record.status = state
        record.message = message


def _compute_file_info(path: Path) -> Dict[str, object]:
    """Return metadata useful for change tracking of the source file."""

    stats = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            if not chunk:
                break
            digest.update(chunk)

    modified_at = datetime.utcfromtimestamp(stats.st_mtime).isoformat() + "Z"
    return {
        "path": str(path),
        "size_bytes": stats.st_size,
        "modified_at": modified_at,
        "sha256": digest.hexdigest(),
    }


def ingest_ontology(
    *,
    ontology_type: str,
    source_id: str,
    obo_path: Path,
    session_factory=SessionLocal,
    embedding_service=None,
    auto_embed: bool = False,
) -> Dict[str, int]:
    settings = get_settings()
    source_type = f"ontology_{ontology_type}"
    embedding_service = embedding_service or get_embedding_service()

    obo_path = Path(obo_path)
    if not obo_path.exists():
        raise FileNotFoundError(f"Ontology file not found: {obo_path}")

    file_info = _compute_file_info(obo_path)

    terms = list(parse_obo_terms(obo_path))
    if not terms:
        raise RuntimeError(f"No terms parsed from {obo_path}")

    term_rows: List[OntologyTerm] = []
    relation_rows: List[OntologyTermRelation] = []

    deleted_relations = deleted_terms = deleted_chunks = 0

    with session_factory() as session:
        deleted_relations = (
            session.query(OntologyTermRelation)
            .filter(
                OntologyTermRelation.ontology_type == ontology_type,
                OntologyTermRelation.source_id == source_id,
            )
            .delete(synchronize_session=False)
        )
        deleted_terms = (
            session.query(OntologyTerm)
            .filter(
                OntologyTerm.ontology_type == ontology_type,
                OntologyTerm.source_id == source_id,
            )
            .delete(synchronize_session=False)
        )
        deleted_chunks = (
            session.query(UnifiedChunk)
            .filter(
                UnifiedChunk.source_type == source_type,
                UnifiedChunk.source_id == source_id,
            )
            .delete(synchronize_session=False)
        )

        deletion_summary = {
            "chunks": deleted_chunks,
            "terms": deleted_terms,
            "relations": deleted_relations,
        }

        upsert_status(
            session,
            source_type=source_type,
            source_id=source_id,
            state=IngestionState.INDEXING,
            message=json.dumps(
                {
                    "stage": "indexing",
                    "file_info": file_info,
                    "deleted": deletion_summary,
                }
            ),
        )
        session.commit()

        chunks: List[UnifiedChunk] = []
        for term in terms:
            term_id = str(term.get("id", ""))
            if not term_id:
                continue
            chunk_text = format_chunk_text(term)
            metadata = {
                "term_id": term_id,
                "name": term.get("name"),
                "definition": term.get("definition"),
                "synonyms": term.get("synonyms", []),
                "parents": term.get("parents", []),
                "xrefs": term.get("xrefs", []),
            }
            term_rows.append(
                OntologyTerm(
                    ontology_type=ontology_type,
                    source_id=source_id,
                    term_id=term_id,
                    name=term.get("name"),
                    definition=term.get("definition"),
                    synonyms=term.get("synonyms", []),
                    xrefs=term.get("xrefs", []),
                    term_metadata={
                        "parents": term.get("parents", []),
                    },
                )
            )
            for parent in term.get("parents", []):
                relation_rows.append(
                    OntologyTermRelation(
                        ontology_type=ontology_type,
                        source_id=source_id,
                        child_term_id=term_id,
                        parent_term_id=str(parent),
                        relation_type="is_a",
                    )
                )
            chunks.append(
                UnifiedChunk(
                    source_type=source_type,
                    source_id=source_id,
                    chunk_id=term_id,
                    chunk_text=chunk_text,
                    chunk_metadata=metadata,
                )
            )

        if term_rows:
            session.bulk_save_objects(term_rows)
        if relation_rows:
            session.bulk_save_objects(relation_rows)
        session.bulk_save_objects(chunks)

        chunk_total = len(chunks)

        insertion_summary = {
            "terms": len(term_rows),
            "relations": len(relation_rows),
            "chunks": len(chunks),
        }

        upsert_status(
            session,
            source_type=source_type,
            source_id=source_id,
            state=IngestionState.INDEXING,
            message=json.dumps(
                {
                    "stage": "embedding_pending",
                    "file_info": file_info,
                    "deleted": deletion_summary,
                    "inserted": insertion_summary,
                }
            ),
        )
        session.commit()

    chunk_total = chunk_total or len(terms)
    ontology_model = get_setting_value(
        "ontology_embedding_model_name",
        settings.ontology_embedding_model_name or settings.embedding_model_name,
        cast=str,
    )
    batch_size = get_setting_value(
        "ontology_embedding_batch_size",
        settings.ontology_embedding_batch_size or settings.embedding_default_batch_size,
        cast=int,
    )
    if batch_size and batch_size <= 0:
        batch_size = None

    embed_summary = {
        "embedded": 0,
        "skipped": chunk_total,
        "model": ontology_model,
        "source_type": source_type,
        "source_id": source_id,
    }

    if auto_embed:
        embedding_service = embedding_service or get_embedding_service()
        embed_summary = embedding_service.embed_unified_chunks(
            source_type=source_type,
            source_id=source_id,
            model_name=ontology_model,
            batch_size=batch_size,
            force=True,
        )

    with session_factory() as session:
        upsert_status(
            session,
            source_type=source_type,
            source_id=source_id,
            state=IngestionState.READY,
            message=json.dumps(
                {
                    "stage": (
                        "ready"
                        if embed_summary.get("embedded")
                        else "awaiting_embeddings"
                    ),
                    "file_info": file_info,
                    "deleted": deletion_summary,
                    "inserted": insertion_summary,
                    "embedding": embed_summary,
                }
            ),
        )
        session.commit()

    return {
        "inserted": len(term_rows),
        "relations": len(relation_rows),
        "deleted_chunks": deleted_chunks,
        "deleted_terms": deleted_terms,
        "deleted_relations": deleted_relations,
        "embedded": int(embed_summary.get("embedded", 0)),
        "file_info": file_info,
        "embedding_summary": embed_summary,
        "insertion_summary": insertion_summary,
        "deletion_summary": deletion_summary,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest ontology data into unified_chunks"
    )
    parser.add_argument("--type", default="disease", help="Ontology type identifier")
    parser.add_argument("--source-id", default="all", help="Source identifier scope")
    parser.add_argument(
        "--obo-path",
        type=Path,
        default=Path("doid.obo.txt"),
        help="Path to the ontology OBO file",
    )
    parser.add_argument(
        "--auto-embed",
        action="store_true",
        help="Automatically generate embeddings after ingestion",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    summary = ingest_ontology(
        ontology_type=args.type,
        source_id=args.source_id,
        obo_path=args.obo_path,
        auto_embed=args.auto_embed,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    main()
