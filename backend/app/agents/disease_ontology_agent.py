"""Disease ontology specialist agent backed by unified search and relational metadata."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.database import SessionLocal
from app.models import OntologyTerm, OntologyTermRelation
from app.services.unified_pipeline_service import get_unified_pipeline
from lib.pipelines.unified_pipeline import UnifiedPipelineOutput


class DiseaseLookupResult(BaseModel):
    term_id: str
    name: str | None = None
    definition: str | None = None
    score: float = 1.0


class DiseaseOntologyAgent:
    """Disease ontology specialist that supports vector and relational lookups."""

    def __init__(self) -> None:
        self._pipeline = get_unified_pipeline()
        self._source_type = "ontology_disease"
        self._default_source_id = "all"
        self._session_factory = SessionLocal

    async def lookup_diseases(
        self,
        *,
        question: str,
        context: str,
        detected_entities: List[str] | Dict[str, Any] | None = None,
        source_id: str | None = None,
    ) -> Dict[str, Any]:
        source_id = source_id or self._default_source_id
        status = await self._pipeline.ensure_index_ready(
            source_type=self._source_type,
            source_id=source_id,
        )
        if status.value != "ready":
            return {
                "status": status.value,
                "entries": [],
                "answer": "Ontology index is not ready.",
            }

        query_mode = self._extract_query_mode(context, detected_entities)

        if query_mode in {"term_lookup", "hierarchy_lookup"}:
            term_id = self._extract_term_id(question, detected_entities)
            if term_id:
                relational = self._fetch_term_details(
                    term_id=term_id, source_id=source_id
                )
                if relational:
                    hierarchy = (
                        self._fetch_term_hierarchy(term_id=term_id, source_id=source_id)
                        if query_mode == "hierarchy_lookup"
                        else {}
                    )
                    answer = self._format_answer(
                        question=question,
                        entries=[relational],
                        hierarchy=hierarchy,
                    )
                    return {
                        "status": "ready",
                        "entries": [relational.model_dump()],
                        "hierarchy": hierarchy,
                        "answer": answer,
                        "citations": [],
                    }

        augmented_query = question
        plain_entities = []
        if isinstance(detected_entities, dict):
            plain_entities = detected_entities.get("diseases", []) or []
        elif isinstance(detected_entities, list):
            plain_entities = detected_entities
        if plain_entities:
            augmented_query += "\nDetected entities: " + ", ".join(plain_entities)

        result: UnifiedPipelineOutput = await self._pipeline.search(
            source_type=self._source_type,
            source_id=source_id,
            query=augmented_query,
            context=context,
        )

        entries: List[DiseaseLookupResult] = []
        citations: List[Dict[str, Any]] = []
        for chunk in result.chunks[:5]:
            metadata = chunk.metadata.get("chunk_metadata", {})
            entries.append(
                DiseaseLookupResult(
                    term_id=metadata.get("term_id", "unknown"),
                    name=metadata.get("name"),
                    definition=metadata.get("definition"),
                    score=chunk.score,
                )
            )
            if chunk.citation:
                citations.append(chunk.citation)

        answer = self._format_answer(question=question, entries=entries)
        return {
            "status": "ready",
            "entries": [entry.model_dump() for entry in entries],
            "answer": answer,
            "citations": citations,
        }

    def _extract_query_mode(
        self, context: str, detected_entities: List[str] | Dict[str, Any] | None
    ) -> str:
        if isinstance(detected_entities, dict):
            mode = detected_entities.get("query_mode")
            if isinstance(mode, str):
                return mode
        if "query_mode" in context:
            if "hierarchy_lookup" in context:
                return "hierarchy_lookup"
            if "term_lookup" in context:
                return "term_lookup"
        return "vector_search"

    def _extract_term_id(
        self, question: str, detected_entities: List[str] | Dict[str, Any] | None
    ) -> Optional[str]:
        if isinstance(detected_entities, dict):
            diseases = detected_entities.get("diseases")
            if diseases:
                for value in diseases:
                    if isinstance(value, str) and value.upper().startswith("DOID"):
                        return value.upper()
        if isinstance(detected_entities, list):
            for value in detected_entities:
                if isinstance(value, str) and value.upper().startswith("DOID"):
                    return value.upper()
        for token in question.replace("\n", " ").split():
            token = token.strip().strip(",:.;")
            if token.upper().startswith("DOID"):
                return token.upper()
        return None

    def _fetch_term_details(
        self, *, term_id: str, source_id: str
    ) -> Optional[DiseaseLookupResult]:
        with self._session_factory() as session:
            row = (
                session.query(OntologyTerm)
                .filter(
                    OntologyTerm.ontology_type == "disease",
                    OntologyTerm.source_id == source_id,
                    OntologyTerm.term_id == term_id,
                )
                .first()
            )
            if not row:
                return None
            return DiseaseLookupResult(
                term_id=row.term_id,
                name=row.name,
                definition=row.definition,
                score=1.0,
            )

    def _fetch_term_hierarchy(
        self, *, term_id: str, source_id: str
    ) -> Dict[str, List[str]]:
        hierarchy = {"parents": [], "children": []}
        with self._session_factory() as session:
            parents = (
                session.query(OntologyTermRelation.parent_term_id)
                .filter(
                    OntologyTermRelation.ontology_type == "disease",
                    OntologyTermRelation.source_id == source_id,
                    OntologyTermRelation.child_term_id == term_id,
                )
                .all()
            )
            children = (
                session.query(OntologyTermRelation.child_term_id)
                .filter(
                    OntologyTermRelation.ontology_type == "disease",
                    OntologyTermRelation.source_id == source_id,
                    OntologyTermRelation.parent_term_id == term_id,
                )
                .all()
            )
        hierarchy["parents"] = [row[0] for row in parents]
        hierarchy["children"] = [row[0] for row in children]
        return hierarchy

    def _format_answer(
        self,
        *,
        question: str,
        entries: List[DiseaseLookupResult],
        hierarchy: Optional[Dict[str, List[str]]] = None,
    ) -> str:
        if not entries:
            return (
                "No disease ontology terms matched the question. Consider refining"
                " the query or re-ingesting the ontology data."
            )

        lines = [
            f"Top ontology matches for '{question}':",
        ]
        for entry in entries:
            description = entry.definition or "Definition unavailable"
            name = entry.name or "Unnamed term"
            lines.append(f"- {entry.term_id} ({name}): {description}")
        if hierarchy:
            parents = hierarchy.get("parents") or []
            children = hierarchy.get("children") or []
            if parents:
                lines.append("Parents: " + ", ".join(parents))
            if children:
                lines.append("Children: " + ", ".join(children))
        return "\n".join(lines)


__all__ = ["DiseaseOntologyAgent"]
