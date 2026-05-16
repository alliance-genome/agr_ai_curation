"""Alliance-owned lookup projection and detail-fetch helpers."""

from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping

from agr_ai_curation_runtime.agr_lookup import (
    DETAIL_RETRY_STRATEGY_PER_CURIE,
    LOOKUP_STATUS_NOT_FOUND,
    LOOKUP_STATUS_TRANSIENT,
    LookupProjectionMetadata,
    bulk_item_status_from_lookup_status as _runtime_bulk_item_status_from_lookup_status,
    candidate_from_result as _runtime_candidate_from_result,
    chunk_values,
    create_db_session,
    detail_fetch_failure,
    entity_detail_lookup_attempts as _runtime_entity_detail_lookup_attempts,
    lookup_attempt as _runtime_lookup_attempt,
    lookup_response_payload as _runtime_lookup_response_payload,
    projection_from_entity_match as _runtime_projection_from_entity_match,
    projection_from_result as _runtime_projection_from_result,
)

logger = logging.getLogger(__name__)

ALLIANCE_CURATION_DB_PROVIDER = "alliance_curation_db"
ALLIANCE_CURATION_TOOL_NAME = "agr_curation_query"
ALLIANCE_DETAIL_LOOKUP_STAGES = frozenset(
    {
        "batch_setup_gene_details",
        "batch_fetch_gene_details",
        "fetch_gene_details",
        "batch_setup_allele_details",
        "batch_fetch_allele_details",
        "fetch_allele_details",
    }
)
ALLIANCE_LOOKUP_PROVIDER_DATA_KEYS = (
    "curie",
    "id",
    "internal_id",
    "symbol",
    "name",
    "term_name",
    "vocabulary",
    "vocabulary_label",
    "taxon",
    "gene_type",
    "match_type",
    "matched_variant",
    "ontology_type",
    "namespace",
    "abbreviation",
    "display_name",
    "definition",
    "obsolete",
    "synonyms",
)

_ONTOLOGY_METHODS = frozenset(
    {
        "get_ontology_term",
        "get_ontology_terms",
        "get_ontology_term_by_curie",
        "search_ontology_terms",
        "search_anatomy_terms",
        "search_life_stage_terms",
        "search_go_terms",
    }
)
_VOCABULARY_METHODS = frozenset(
    {
        "get_vocabulary_term",
        "map_curies_to_names",
        "search_vocabulary_terms",
    }
)
_ENTITY_METHODS = frozenset(
    {
        "map_entity_names_to_curies",
        "map_entity_curies_to_info",
    }
)


def alliance_projection_type(method: str) -> str:
    if "gene" in method:
        return "gene_reference"
    if "allele" in method:
        return "allele_reference"
    if method in _ONTOLOGY_METHODS:
        return "ontology_term_reference"
    if method in _VOCABULARY_METHODS:
        return "vocabulary_term_reference"
    if method in _ENTITY_METHODS:
        return "entity_reference"
    if "species" in method:
        return "species_reference"
    if "data_provider" in method:
        return "data_provider_reference"
    return "curation_db_reference"


def alliance_object_type(method: str) -> str | None:
    if "gene" in method:
        return "Gene"
    if "allele" in method:
        return "Allele"
    if method in _ONTOLOGY_METHODS:
        return "OntologyTerm"
    if method in _VOCABULARY_METHODS:
        return "VocabularyTerm"
    if method in _ENTITY_METHODS:
        return "Entity"
    if "species" in method:
        return "Species"
    if "data_provider" in method:
        return "DataProvider"
    return None


def alliance_projection_metadata(method: str) -> LookupProjectionMetadata:
    return LookupProjectionMetadata(
        provider=ALLIANCE_CURATION_DB_PROVIDER,
        tool_name=ALLIANCE_CURATION_TOOL_NAME,
        projection_type=alliance_projection_type(method),
        object_type=alliance_object_type(method),
        provider_data_keys=ALLIANCE_LOOKUP_PROVIDER_DATA_KEYS,
    )


def projection_from_result(
    method: str,
    result: Mapping[str, Any],
    *,
    projection_status: str = "resolved",
) -> dict[str, Any]:
    return _runtime_projection_from_result(
        method,
        result,
        projection_metadata=alliance_projection_metadata(method),
        projection_status=projection_status,
    )


def candidate_from_result(method: str, result: Mapping[str, Any]) -> dict[str, Any]:
    return _runtime_candidate_from_result(
        method,
        result,
        projection_metadata=alliance_projection_metadata(method),
    )


def projection_from_entity_match(
    method: str,
    result: Mapping[str, Any],
    *,
    taxon_id: str,
    projection_status: str = "resolved",
    matched_variant: str | None = None,
) -> dict[str, Any]:
    return _runtime_projection_from_entity_match(
        method,
        result,
        taxon_id=taxon_id,
        projection_metadata=alliance_projection_metadata(method),
        projection_status=projection_status,
        matched_variant=matched_variant,
    )


def lookup_attempt(
    *,
    method: str,
    attempted_query: Mapping[str, Any],
    lookup_status: str,
    explanation: str,
    candidate_count: int = 0,
    target_projection: Mapping[str, Any] | None = None,
    resolved: Mapping[str, Any] | None = None,
    error: BaseException | None = None,
) -> dict[str, Any]:
    return _runtime_lookup_attempt(
        method=method,
        attempted_query=attempted_query,
        lookup_status=lookup_status,
        explanation=explanation,
        candidate_count=candidate_count,
        projection_metadata=alliance_projection_metadata(method),
        target_projection=target_projection,
        resolved=resolved,
        error=error,
    )


def entity_detail_lookup_attempts(
    *,
    method: str,
    entity_kind: str,
    input_symbol: str,
    curie: str,
    taxon_id: str,
    matched_entity: str | None,
    match_type: str | None,
    detail_failures: Iterable[Mapping[str, Any]],
    data_provider: str | None = None,
) -> list[dict[str, Any]]:
    return _runtime_entity_detail_lookup_attempts(
        method=method,
        entity_kind=entity_kind,
        input_symbol=input_symbol,
        curie=curie,
        taxon_id=taxon_id,
        matched_entity=matched_entity,
        match_type=match_type,
        detail_failures=detail_failures,
        data_provider=data_provider,
        projection_metadata=alliance_projection_metadata(method),
    )


def lookup_response_payload(
    *,
    method: str,
    data: Any = None,
    count: int | None = None,
    warnings: list[str] | None = None,
    message: str | None = None,
    attempted_query: Mapping[str, Any] | None = None,
    exact_lookup: bool = False,
    attempts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return _runtime_lookup_response_payload(
        method=method,
        data=data,
        count=count,
        warnings=warnings,
        message=message,
        attempted_query=attempted_query,
        exact_lookup=exact_lookup,
        attempts=attempts,
        projection_metadata=alliance_projection_metadata(method),
    )


def bulk_item_status_from_lookup_status(
    lookup_status: str,
    *,
    count: int,
    attempts: list[dict[str, Any]] | None = None,
) -> str:
    return _runtime_bulk_item_status_from_lookup_status(
        lookup_status,
        count=count,
        attempts=attempts,
        detail_lookup_stages=ALLIANCE_DETAIL_LOOKUP_STAGES,
    )


def _append_detail_failure(
    detail_failures: dict[str, list[dict[str, Any]]],
    curie: str,
    failure: dict[str, Any],
) -> None:
    detail_failures.setdefault(curie, []).append(failure)


def _append_batch_detail_failures(
    detail_failures: dict[str, list[dict[str, Any]]],
    curies: Iterable[str],
    *,
    lookup_stage: str,
    error: BaseException,
) -> None:
    for curie in curies:
        _append_detail_failure(
            detail_failures,
            curie,
            detail_fetch_failure(
                LOOKUP_STATUS_TRANSIENT,
                error=error,
                lookup_stage=lookup_stage,
                retry_strategy=DETAIL_RETRY_STRATEGY_PER_CURIE,
            ),
        )


def fetch_gene_details_bulk(
    db: Any,
    gene_curies: list[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Fetch Alliance gene details and classify unresolved per-CURIE lookups."""
    unique_curies = list(dict.fromkeys(curie for curie in gene_curies if curie))
    if not unique_curies:
        return {}, {}

    detail_failures: dict[str, list[dict[str, Any]]] = {}
    try:
        from sqlalchemy import text

        session = create_db_session(db)
    except Exception as exc:
        logger.warning("Batch gene detail setup failed: %s", exc)
        _append_batch_detail_failures(
            detail_failures,
            unique_curies,
            lookup_stage="batch_setup_gene_details",
            error=exc,
        )
        session = None
    if session is not None:
        try:
            details: dict[str, dict[str, Any]] = {}
            try:
                sql_query = text(
                    """
                SELECT
                    be.primaryexternalid,
                    symbol.displaytext as gene_symbol,
                    fullname.displaytext as gene_fullname,
                    taxon.curie as taxon_curie,
                    gt.name as gene_type_name
                FROM biologicalentity be
                JOIN gene g ON be.id = g.id
                LEFT JOIN ontologyterm taxon ON be.taxon_id = taxon.id
                LEFT JOIN ontologyterm gt ON g.genetype_id = gt.id
                LEFT JOIN slotannotation symbol ON g.id = symbol.singlegene_id
                    AND symbol.slotannotationtype = 'GeneSymbolSlotAnnotation'
                    AND symbol.obsolete = false
                LEFT JOIN slotannotation fullname ON g.id = fullname.singlegene_id
                    AND fullname.slotannotationtype = 'GeneFullNameSlotAnnotation'
                    AND fullname.obsolete = false
                WHERE be.primaryexternalid IN :gene_ids
                """
                )
                for chunk in chunk_values(unique_curies):
                    rows = session.execute(
                        sql_query,
                        {"gene_ids": tuple(chunk)},
                    ).fetchall()
                    for row in rows:
                        details[row[0]] = {
                            "curie": row[0],
                            "symbol": row[1],
                            "name": row[2],
                            "taxon": row[3],
                            "gene_type": row[4],
                        }
            finally:
                session.close()

            if details:
                for curie in unique_curies:
                    if curie not in details:
                        _append_detail_failure(
                            detail_failures,
                            curie,
                            detail_fetch_failure(LOOKUP_STATUS_NOT_FOUND),
                        )
                return details, detail_failures
        except Exception as exc:
            logger.warning(
                "Batch gene detail fetch failed; retrying per-CURIE: %s",
                exc,
            )
            _append_batch_detail_failures(
                detail_failures,
                unique_curies,
                lookup_stage="batch_fetch_gene_details",
                error=exc,
            )

    details: dict[str, dict[str, Any]] = {}
    for curie in unique_curies:
        try:
            gene = db.get_gene(curie)
        except Exception as exc:
            logger.warning("Failed to fetch gene details for %s: %s", curie, exc)
            _append_detail_failure(
                detail_failures,
                curie,
                detail_fetch_failure(
                    LOOKUP_STATUS_TRANSIENT,
                    error=exc,
                    lookup_stage="fetch_gene_details",
                ),
            )
            continue
        if not gene:
            _append_detail_failure(
                detail_failures,
                curie,
                detail_fetch_failure(LOOKUP_STATUS_NOT_FOUND),
            )
            continue
        details[curie] = {
            "curie": getattr(gene, "primaryExternalId", curie),
            "symbol": (
                gene.geneSymbol.displayText
                if getattr(gene, "geneSymbol", None)
                else None
            ),
            "name": (
                gene.geneFullName.displayText
                if getattr(gene, "geneFullName", None)
                else None
            ),
            "taxon": getattr(gene, "taxon", None),
            "gene_type": (
                gene.geneType.get("name")
                if getattr(gene, "geneType", None) and isinstance(gene.geneType, dict)
                else str(gene.geneType) if getattr(gene, "geneType", None) else None
            ),
        }
    return details, detail_failures


def fetch_allele_details_bulk(
    db: Any,
    allele_curies: list[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Fetch Alliance allele details and classify unresolved per-CURIE lookups."""
    unique_curies = list(dict.fromkeys(curie for curie in allele_curies if curie))
    if not unique_curies:
        return {}, {}

    detail_failures: dict[str, list[dict[str, Any]]] = {}
    try:
        from sqlalchemy import text

        session = create_db_session(db)
    except Exception as exc:
        logger.warning("Batch allele detail setup failed: %s", exc)
        _append_batch_detail_failures(
            detail_failures,
            unique_curies,
            lookup_stage="batch_setup_allele_details",
            error=exc,
        )
        session = None
    if session is not None:
        try:
            details: dict[str, dict[str, Any]] = {}
            try:
                sql_query = text(
                    """
                SELECT
                    be.primaryexternalid,
                    symbol.displaytext as allele_symbol,
                    fullname.displaytext as allele_fullname,
                    taxon.curie as taxon_curie
                FROM biologicalentity be
                JOIN allele a ON be.id = a.id
                LEFT JOIN ontologyterm taxon ON be.taxon_id = taxon.id
                LEFT JOIN slotannotation symbol ON a.id = symbol.singleallele_id
                    AND symbol.slotannotationtype = 'AlleleSymbolSlotAnnotation'
                    AND symbol.obsolete = false
                LEFT JOIN slotannotation fullname ON a.id = fullname.singleallele_id
                    AND fullname.slotannotationtype = 'AlleleFullNameSlotAnnotation'
                    AND fullname.obsolete = false
                WHERE be.primaryexternalid IN :allele_ids
                """
                )
                for chunk in chunk_values(unique_curies):
                    rows = session.execute(
                        sql_query,
                        {"allele_ids": tuple(chunk)},
                    ).fetchall()
                    for row in rows:
                        details[row[0]] = {
                            "curie": row[0],
                            "symbol": row[1],
                            "name": row[2],
                            "taxon": row[3],
                        }
            finally:
                session.close()

            if details:
                for curie in unique_curies:
                    if curie not in details:
                        _append_detail_failure(
                            detail_failures,
                            curie,
                            detail_fetch_failure(LOOKUP_STATUS_NOT_FOUND),
                        )
                return details, detail_failures
        except Exception as exc:
            logger.warning(
                "Batch allele detail fetch failed; retrying per-CURIE: %s",
                exc,
            )
            _append_batch_detail_failures(
                detail_failures,
                unique_curies,
                lookup_stage="batch_fetch_allele_details",
                error=exc,
            )

    details: dict[str, dict[str, Any]] = {}
    for curie in unique_curies:
        try:
            allele = db.get_allele(curie)
        except Exception as exc:
            logger.warning("Failed to fetch allele details for %s: %s", curie, exc)
            _append_detail_failure(
                detail_failures,
                curie,
                detail_fetch_failure(
                    LOOKUP_STATUS_TRANSIENT,
                    error=exc,
                    lookup_stage="fetch_allele_details",
                ),
            )
            continue
        if not allele:
            _append_detail_failure(
                detail_failures,
                curie,
                detail_fetch_failure(LOOKUP_STATUS_NOT_FOUND),
            )
            continue
        details[curie] = {
            "curie": getattr(allele, "primaryExternalId", curie),
            "symbol": (
                allele.alleleSymbol.displayText
                if getattr(allele, "alleleSymbol", None)
                else None
            ),
            "name": (
                allele.alleleFullName.displayText
                if getattr(allele, "alleleFullName", None)
                else None
            ),
            "taxon": getattr(allele, "taxon", None),
        }
    return details, detail_failures


__all__ = [
    "ALLIANCE_CURATION_DB_PROVIDER",
    "ALLIANCE_CURATION_TOOL_NAME",
    "ALLIANCE_DETAIL_LOOKUP_STAGES",
    "alliance_object_type",
    "alliance_projection_metadata",
    "alliance_projection_type",
    "bulk_item_status_from_lookup_status",
    "candidate_from_result",
    "entity_detail_lookup_attempts",
    "fetch_allele_details_bulk",
    "fetch_gene_details_bulk",
    "lookup_attempt",
    "lookup_response_payload",
    "projection_from_entity_match",
    "projection_from_result",
]
