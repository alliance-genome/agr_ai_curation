"""Shared AGR curation lookup response helpers.

The backend tool and packaged Alliance tool intentionally expose the same lookup
contract. Keep projection, attempt, status, and detail-fetch metadata here so
the two runtime surfaces cannot drift.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping

from src.lib.lookup_status import (
    LOOKUP_STATUS_AMBIGUOUS,
    LOOKUP_STATUS_BLOCKED,
    LOOKUP_STATUS_NOT_FOUND,
    LOOKUP_STATUS_SUCCESS,
    LOOKUP_STATUS_TRANSIENT,
    LOOKUP_STATUS_UNDER_DEVELOPMENT,
)

logger = logging.getLogger(__name__)

AGR_CURATION_DB_PROVIDER = "alliance_curation_db"
AGR_CURATION_TOOL_NAME = "agr_curation_query"
DETAIL_RETRY_STRATEGY_PER_CURIE = "per_curie"


def clean_mapping(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return a dict without null or empty-list values."""
    return {
        key: value
        for key, value in raw.items()
        if value is not None and value != []
    }


def method_projection_type(method: str) -> str:
    if "gene" in method:
        return "gene_reference"
    if "allele" in method:
        return "allele_reference"
    if "ontology" in method or method in {
        "search_anatomy_terms",
        "search_life_stage_terms",
        "search_go_terms",
    }:
        return "ontology_term_reference"
    if "species" in method:
        return "species_reference"
    if "data_provider" in method:
        return "data_provider_reference"
    return "curation_db_reference"


def method_entity_type(method: str) -> str | None:
    if "gene" in method:
        return "Gene"
    if "allele" in method:
        return "Allele"
    if "ontology" in method or method in {
        "search_anatomy_terms",
        "search_life_stage_terms",
        "search_go_terms",
    }:
        return "OntologyTerm"
    if "species" in method:
        return "Species"
    if "data_provider" in method:
        return "DataProvider"
    return None


def attempt_query(method: str, **values: Any) -> dict[str, Any]:
    return clean_mapping({"method": method, **values})


def projection_from_result(
    method: str,
    result: Mapping[str, Any],
    *,
    projection_status: str = "resolved",
) -> dict[str, Any]:
    # Lookup families return different identifier/label shapes:
    # ontology rows use curie/name, entity rows may use primary_external_id/symbol,
    # and provider rows use abbreviation/display_name.
    resolved_id = (
        result.get("curie")
        or result.get("primary_external_id")
        or result.get("abbreviation")
    )
    resolved_label = (
        result.get("symbol")
        or result.get("name")
        or result.get("display_name")
        or result.get("abbreviation")
    )
    projection_key = str(
        resolved_id or resolved_label or method_projection_type(method)
    )
    provider_data = {
        key: result.get(key)
        for key in (
            "curie",
            "symbol",
            "name",
            "taxon",
            "gene_type",
            "match_type",
            "matched_variant",
            "ontology_type",
            "namespace",
            "abbreviation",
            "display_name",
        )
        if result.get(key) is not None
    }
    return clean_mapping(
        {
            "provider": AGR_CURATION_DB_PROVIDER,
            "projection_type": method_projection_type(method),
            "projection_key": projection_key,
            "projection_status": projection_status,
            "object_type": method_entity_type(method),
            "resolved_id": resolved_id,
            "resolved_label": resolved_label,
            "source": {
                "tool_name": AGR_CURATION_TOOL_NAME,
                "method": method,
            },
            "provider_data": provider_data or None,
        }
    )


def candidate_from_result(method: str, result: Mapping[str, Any]) -> dict[str, Any]:
    projection = projection_from_result(method, result)
    return clean_mapping(
        {
            "provider": AGR_CURATION_DB_PROVIDER,
            "candidate_id": projection.get("resolved_id"),
            "candidate_label": projection.get("resolved_label"),
            "match_type": result.get("match_type") or result.get("matched_variant"),
            "projection": projection,
        }
    )


def projection_from_entity_match(
    method: str,
    result: Mapping[str, Any],
    *,
    taxon_id: str,
    projection_status: str = "resolved",
    matched_variant: str | None = None,
) -> dict[str, Any]:
    return projection_from_result(
        method,
        clean_mapping(
            {
                "curie": result.get("entity_curie"),
                "symbol": result.get("entity"),
                "taxon": taxon_id,
                "match_type": result.get("match_type"),
                "matched_variant": matched_variant,
            }
        ),
        projection_status=projection_status,
    )


def is_projection_result(result: Mapping[str, Any]) -> bool:
    return any(
        result.get(key) is not None
        for key in (
            "curie",
            "primary_external_id",
            "abbreviation",
            "symbol",
            "name",
            "display_name",
        )
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
    resolved_projection = (
        projection_from_result(method, resolved)
        if resolved is not None
        else target_projection
    )
    payload = clean_mapping(
        {
            "source": {
                "tool_name": AGR_CURATION_TOOL_NAME,
                "method": method,
            },
            "provider": AGR_CURATION_DB_PROVIDER,
            "attempted_query": dict(attempted_query),
            "target_projection": dict(resolved_projection)
            if resolved_projection
            else None,
            "lookup_status": lookup_status,
            "candidate_count": candidate_count,
            "resolved_id": resolved_projection.get("resolved_id")
            if resolved_projection
            else None,
            "resolved_label": resolved_projection.get("resolved_label")
            if resolved_projection
            else None,
            "explanation": explanation,
        }
    )
    if error is not None:
        payload["error"] = {
            "type": type(error).__name__,
            "message": str(error),
        }
    return payload


def detail_fetch_failure(
    lookup_status: str,
    *,
    error: BaseException | None = None,
    lookup_stage: str | None = None,
    retry_strategy: str | None = None,
) -> dict[str, Any]:
    failure: dict[str, Any] = clean_mapping(
        {
            "lookup_status": lookup_status,
            "lookup_stage": lookup_stage,
            "retry_strategy": retry_strategy,
        }
    )
    if error is not None:
        failure["error"] = error
    return failure


def entity_detail_lookup_attempt(
    *,
    method: str,
    entity_kind: str,
    input_symbol: str,
    curie: str,
    taxon_id: str,
    matched_entity: str | None,
    match_type: str | None,
    lookup_status: str,
    data_provider: str | None = None,
    lookup_stage: str | None = None,
    retry_strategy: str | None = None,
    error: BaseException | None = None,
) -> dict[str, Any]:
    symbol_field = f"{entity_kind}_symbol"
    id_field = f"{entity_kind}_id"
    stage = lookup_stage or f"fetch_{entity_kind}_details"
    target_projection = projection_from_result(
        method,
        clean_mapping(
            {
                "curie": curie,
                "symbol": matched_entity,
                "taxon": taxon_id,
                "match_type": match_type,
            }
        ),
    )
    explanation = (
        f"{entity_kind.title()} search for {input_symbol!r} matched {curie!r} "
        f"in taxon {taxon_id}, but no resolved {entity_kind} details were returned."
    )
    if lookup_status == LOOKUP_STATUS_TRANSIENT:
        if stage.startswith("batch_setup_"):
            explanation = (
                f"{entity_kind.title()} search for {input_symbol!r} matched {curie!r} "
                f"in taxon {taxon_id}, but setting up the batch {entity_kind} detail "
                "lookup failed before retrying per-CURIE detail fetches."
            )
        elif stage.startswith("batch_fetch_"):
            explanation = (
                f"{entity_kind.title()} search for {input_symbol!r} matched {curie!r} "
                f"in taxon {taxon_id}, but the batch {entity_kind} detail lookup "
                "failed before retrying per-CURIE detail fetches."
            )
        else:
            explanation = (
                f"{entity_kind.title()} search for {input_symbol!r} matched {curie!r} "
                f"in taxon {taxon_id}, but fetching resolved {entity_kind} details failed."
            )
    return lookup_attempt(
        method=method,
        attempted_query=attempt_query(
            method,
            **{
                symbol_field: input_symbol,
                id_field: curie,
                "taxon_id": taxon_id,
                "data_provider": data_provider,
                "lookup_stage": stage,
                "retry_strategy": retry_strategy,
            },
        ),
        lookup_status=lookup_status,
        explanation=explanation,
        candidate_count=1,
        target_projection=target_projection,
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
    return [
        entity_detail_lookup_attempt(
            method=method,
            entity_kind=entity_kind,
            input_symbol=input_symbol,
            curie=curie,
            taxon_id=taxon_id,
            matched_entity=matched_entity,
            match_type=match_type,
            lookup_status=str(failure["lookup_status"]),
            data_provider=data_provider,
            lookup_stage=failure.get("lookup_stage"),
            retry_strategy=failure.get("retry_strategy"),
            error=failure.get("error"),
        )
        for failure in detail_failures
        if failure.get("lookup_status")
    ]


def lookup_status_from_count(
    count: int,
    *,
    exact_lookup: bool,
    attempts: list[dict[str, Any]] | None = None,
) -> str:
    if count > 1 and exact_lookup:
        return LOOKUP_STATUS_AMBIGUOUS
    if count > 0:
        return LOOKUP_STATUS_SUCCESS
    if attempts and any(
        attempt.get("lookup_status") == LOOKUP_STATUS_TRANSIENT
        for attempt in attempts
    ):
        return LOOKUP_STATUS_TRANSIENT
    return LOOKUP_STATUS_NOT_FOUND


def lookup_explanation(
    *,
    method: str,
    lookup_status: str,
    count: int,
    attempted_query: Mapping[str, Any],
) -> str:
    target = attempted_query.get("gene_id") or attempted_query.get("allele_id")
    target = (
        target
        or attempted_query.get("gene_symbol")
        or attempted_query.get("allele_symbol")
    )
    target = (
        target
        or attempted_query.get("term")
        or attempted_query.get("method")
        or method
    )
    if lookup_status == LOOKUP_STATUS_SUCCESS:
        return f"{method} resolved {target!r} to {count} curation DB result(s)."
    if lookup_status == LOOKUP_STATUS_AMBIGUOUS:
        return (
            f"{method} found {count} candidate curation DB results for {target!r}; "
            "curator or repair logic must choose one."
        )
    if lookup_status == LOOKUP_STATUS_TRANSIENT:
        return (
            f"{method} could not complete for {target!r} because one or more "
            "curation DB calls failed transiently."
        )
    if lookup_status == LOOKUP_STATUS_BLOCKED:
        return (
            f"{method} was not executed for {target!r} because a required validator "
            "or runtime prerequisite is blocked."
        )
    if lookup_status == LOOKUP_STATUS_UNDER_DEVELOPMENT:
        return f"{method} is declared for {target!r} but is still under development."
    return f"{method} tried the curation DB for {target!r} and found no matching result."


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
    rows: list[dict[str, Any]]
    if isinstance(data, list):
        rows = [row for row in data if isinstance(row, dict)]
    elif isinstance(data, dict) and is_projection_result(data):
        rows = [data]
    else:
        rows = []

    count_value = count if count is not None else len(rows)
    lookup_status = lookup_status_from_count(
        count_value,
        exact_lookup=exact_lookup,
        attempts=attempts,
    )
    query = dict(attempted_query or attempt_query(method))
    explanation = message or lookup_explanation(
        method=method,
        lookup_status=lookup_status,
        count=count_value,
        attempted_query=query,
    )
    projections = [projection_from_result(method, row) for row in rows]
    candidates = [candidate_from_result(method, row) for row in rows]
    lookup_attempts = attempts or [
        lookup_attempt(
            method=method,
            attempted_query=query,
            lookup_status=lookup_status,
            explanation=explanation,
            candidate_count=count_value,
            target_projection=projections[0] if len(projections) == 1 else None,
        )
    ]
    return {
        "data": data,
        "count": count,
        "warnings": warnings,
        "message": message,
        "lookup_status": lookup_status,
        "failure_classification": (
            None if lookup_status == LOOKUP_STATUS_SUCCESS else lookup_status
        ),
        "explanation": explanation,
        "lookup_attempts": lookup_attempts,
        "candidate_matches": candidates or None,
        "result_projections": projections or None,
    }


def chunk_values(values: list[str], chunk_size: int = 200) -> list[list[str]]:
    """Return fixed-size chunks to keep SQL IN clauses bounded."""
    return [values[i:i + chunk_size] for i in range(0, len(values), chunk_size)]


def create_db_session(db: Any) -> Any | None:
    create_session = getattr(db, "create_session", None)
    if not callable(create_session):
        return None
    return create_session()


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
    """Fetch gene details and classify unresolved per-CURIE detail lookups."""
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
    """Fetch allele details and classify unresolved per-CURIE detail lookups."""
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
    "AGR_CURATION_DB_PROVIDER",
    "AGR_CURATION_TOOL_NAME",
    "DETAIL_RETRY_STRATEGY_PER_CURIE",
    "LOOKUP_STATUS_SUCCESS",
    "LOOKUP_STATUS_NOT_FOUND",
    "LOOKUP_STATUS_AMBIGUOUS",
    "LOOKUP_STATUS_TRANSIENT",
    "LOOKUP_STATUS_BLOCKED",
    "LOOKUP_STATUS_UNDER_DEVELOPMENT",
    "clean_mapping",
    "method_projection_type",
    "method_entity_type",
    "attempt_query",
    "projection_from_result",
    "candidate_from_result",
    "projection_from_entity_match",
    "is_projection_result",
    "lookup_attempt",
    "detail_fetch_failure",
    "entity_detail_lookup_attempt",
    "entity_detail_lookup_attempts",
    "lookup_status_from_count",
    "lookup_explanation",
    "lookup_response_payload",
    "chunk_values",
    "create_db_session",
    "fetch_gene_details_bulk",
    "fetch_allele_details_bulk",
]
