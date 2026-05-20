"""
AGR Curation Database tool for OpenAI Agents SDK.

Provides structured access to the Alliance Genome Resources Curation Database
using the official agr-curation-api-client package.
"""

import logging
import os
import re
import json
import inspect
from collections import defaultdict
from typing import Optional, List, Dict, Any, Tuple

from pydantic import BaseModel
from agents import function_tool

from agr_ai_curation_runtime.agr_lookup import (
    LOOKUP_STATUS_AMBIGUOUS,
    LOOKUP_STATUS_BLOCKED,
    LOOKUP_STATUS_NOT_FOUND,
    LOOKUP_STATUS_SUCCESS,
    LOOKUP_STATUS_TRANSIENT,
    LOOKUP_STATUS_UNDER_DEVELOPMENT,
    attempt_query as _attempt_query,
    bulk_resolution_summary as _bulk_resolution_summary,
    lookup_explanation as _lookup_explanation,
    lookup_status_from_count as _lookup_status_from_count,
)
from .agr_lookup import (
    bulk_item_status_from_lookup_status as _bulk_item_status_from_lookup_status,
    candidate_from_result as _candidate_from_result,
    create_db_session,
    entity_detail_lookup_attempts as _entity_detail_lookup_attempts,
    fetch_allele_details_bulk as _fetch_allele_details_bulk,
    fetch_gene_details_bulk as _fetch_gene_details_bulk,
    lookup_attempt as _lookup_attempt,
    lookup_response_payload as _lookup_response_payload,
    projection_from_entity_match as _projection_from_entity_match,
    projection_from_result as _projection_from_result,
)
from agr_ai_curation_runtime import get_curation_resolver, is_valid_curie, list_groups
from .search_helpers import (
    enrich_with_match_context,
)

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = int(os.getenv("AGR_DEFAULT_LIMIT", "100"))
HARD_MAX = int(os.getenv("AGR_HARD_MAX", "500"))
_ALLELE_FUZZY_SIMILARITY_THRESHOLD = float(
    os.getenv("AGR_ALLELE_FUZZY_SIMILARITY_THRESHOLD", "0.35")
)


class AgrQueryResult(BaseModel):
    status: str
    data: Any = None
    count: Optional[int] = None
    warnings: Optional[List[str]] = None
    message: Optional[str] = None
    lookup_status: Optional[str] = None
    failure_classification: Optional[str] = None
    explanation: Optional[str] = None
    lookup_attempts: Optional[List[Dict[str, Any]]] = None
    candidate_matches: Optional[List[Dict[str, Any]]] = None
    result_projections: Optional[List[Dict[str, Any]]] = None


# Group-to-taxon mapping — loaded from config/groups.yaml via groups_loader
def _load_group_provider_metadata() -> dict:
    """Build provider metadata from config/groups.yaml."""
    mapping = {}
    for group in list_groups():
        group_id = group.group_id
        mapping[group_id] = {
            "abbreviation": group_id,
            "taxon_id": group.taxon,
            "display_name": getattr(group, "name", None) or getattr(group, "display_name", None),
            "species": getattr(group, "species", None),
        }
    return mapping


def _provider_taxon_mapping(provider_metadata: Dict[str, Dict[str, Any]]) -> dict:
    mapping = {}
    for group_id, metadata in provider_metadata.items():
        taxon = metadata.get("taxon_id")
        if taxon:
            mapping[group_id] = taxon
    return mapping


def _load_provider_mapping_state() -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str], Optional[str]]:
    try:
        provider_metadata = _load_group_provider_metadata()
    except Exception as exc:
        logger.error("Failed to load group-to-taxon mappings: %s", exc)
        return {}, {}, str(exc)
    return provider_metadata, _provider_taxon_mapping(provider_metadata), None


PROVIDER_METADATA, PROVIDER_TO_TAXON, _GROUP_MAPPING_LOAD_ERROR = _load_provider_mapping_state()

# Reverse mapping: taxon to group abbreviation
TAXON_TO_PROVIDER = {v: k for k, v in PROVIDER_TO_TAXON.items()}

# MODs with useful creator/institution info in allele fullnames
MODS_WITH_FULLNAME_ATTRIBUTION = {'MGI', 'RGD'}


def _validate_curie_in_result(result: Dict[str, Any], curie_field: str = "curie") -> Dict[str, Any]:
    """Add CURIE validation metadata to a result dict."""
    curie = result.get(curie_field)
    if curie:
        result["curie_validated"] = is_valid_curie(curie)
        if not result["curie_validated"]:
            logger.warning('Invalid CURIE prefix detected: %s', curie)
    else:
        result["curie_validated"] = False
    return result


def _validate_curie_list(results: List[Dict[str, Any]], curie_field: str = "curie") -> Tuple[List[Dict[str, Any]], int]:
    """Validate CURIEs in a list of results."""
    invalid_count = 0
    for result in results:
        _validate_curie_in_result(result, curie_field)
        if not result.get("curie_validated", False):
            invalid_count += 1
    return results, invalid_count


def _plain_result(result: Any) -> Dict[str, Any]:
    """Return a plain dict for API-client result models or simple test doubles."""
    if result is None:
        raise ValueError("_plain_result received None")
    if isinstance(result, dict):
        return {key: value for key, value in result.items() if value is not None}
    model_dump = getattr(result, "model_dump", None)
    if callable(model_dump):
        return model_dump(exclude_none=True)
    dict_method = getattr(result, "dict", None)
    if callable(dict_method):
        raise TypeError(
            "Cannot serialize result with dict() but no model_dump(); "
            f"unsupported result type {type(result).__name__}"
        )
    if hasattr(result, "__dict__"):
        return {
            key: value
            for key, value in vars(result).items()
            if not key.startswith("_") and value is not None
        }
    raise TypeError(f"Cannot serialize result of type {type(result).__name__}")


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _ontology_term_result(result: Any) -> Dict[str, Any]:
    raw = _plain_result(result)
    term = {
        key: raw.get(key)
        for key in (
            "curie",
            "name",
            "namespace",
            "definition",
            "ontology_type",
            "synonyms",
        )
        if raw.get(key) is not None
    }
    return _validate_curie_in_result(term) if term else term


def _vocabulary_term_result(result: Any) -> Dict[str, Any]:
    raw = _plain_result(result)
    internal_id = _first_present(raw.get("id"), raw.get("internal_id"))
    term = {
        "id": internal_id,
        "internal_id": internal_id,
        "vocabulary": raw.get("vocabulary"),
        "vocabulary_label": raw.get("vocabulary_label"),
        "name": _first_present(raw.get("name"), raw.get("term_name")),
        "term_name": _first_present(raw.get("term_name"), raw.get("name")),
        "abbreviation": raw.get("abbreviation"),
        "definition": raw.get("definition"),
        "obsolete": raw.get("obsolete"),
        "synonyms": raw.get("synonyms") or [],
    }
    return {key: value for key, value in term.items() if value is not None}


def _provider_abbreviation(value: Any) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return normalized.upper()


def _normalize_provider_name(value: Any) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def _provider_metadata_warnings() -> List[str]:
    if not _GROUP_MAPPING_LOAD_ERROR:
        return []
    return [f"provider_metadata_unavailable:{_GROUP_MAPPING_LOAD_ERROR}"]


def _provider_name_values(provider: Dict[str, Any]) -> set[str]:
    values = set()
    for value in (
        provider.get("display_name"),
        provider.get("name"),
        provider.get("abbreviation"),
    ):
        normalized = _normalize_provider_name(value)
        if normalized:
            values.add(normalized)
    return values


def _provider_name_matches(provider: Dict[str, Any], provider_name: str) -> bool:
    normalized = _normalize_provider_name(provider_name)
    return bool(normalized and normalized in _provider_name_values(provider))


def _data_provider_result(result: Any) -> Dict[str, Any]:
    """Return normalized data-provider facts from API rows and group metadata."""
    if isinstance(result, (tuple, list)):
        if len(result) < 2:
            raise ValueError(
                "Data provider tuple rows must contain abbreviation and taxon_id."
            )
        # The API client currently returns lightweight provider rows as
        # (abbreviation, taxon_id, display_name); tests keep this tuple contract
        # visible alongside object-shaped rows.
        raw = {
            "abbreviation": result[0],
            "taxon_id": result[1],
            "display_name": result[2] if len(result) > 2 else None,
        }
    else:
        raw = _plain_result(result)

    abbreviation = _provider_abbreviation(
        _first_present(
            raw.get("abbreviation"),
            raw.get("data_provider"),
            raw.get("provider"),
            raw.get("group_id"),
        )
    )
    metadata = PROVIDER_METADATA.get(abbreviation or "", {})
    display_name = _first_present(
        raw.get("display_name"),
        raw.get("name"),
        raw.get("provider_name"),
        metadata.get("display_name"),
    )
    taxon_id = _first_present(
        raw.get("taxon_id"),
        raw.get("taxon"),
        raw.get("taxon_curie"),
        metadata.get("taxon_id"),
    )
    provider = {
        "abbreviation": abbreviation,
        "taxon_id": taxon_id,
        "display_name": display_name,
        "species": _first_present(raw.get("species"), metadata.get("species")),
    }
    return {key: value for key, value in provider.items() if value is not None}


def _provider_matches_all(
    provider: Dict[str, Any],
    *,
    abbreviation: Optional[str],
    provider_name: Optional[str],
    taxon_id: Optional[str],
) -> bool:
    if abbreviation and provider.get("abbreviation") != abbreviation:
        return False
    if taxon_id and provider.get("taxon_id") != taxon_id:
        return False
    if provider_name:
        if not _provider_name_matches(provider, provider_name):
            return False
    return True


def _data_provider_candidates(
    providers: List[Dict[str, Any]],
    *,
    abbreviation: Optional[str],
    provider_name: Optional[str],
    taxon_id: Optional[str],
    limit: int,
) -> List[Dict[str, Any]]:
    if abbreviation:
        matches = [
            provider for provider in providers if provider.get("abbreviation") == abbreviation
        ]
        if matches:
            return matches[:limit]
    if provider_name:
        matches = [
            provider
            for provider in providers
            if _provider_name_matches(provider, provider_name)
        ]
        if matches:
            return matches[:limit]
    if taxon_id:
        matches = [provider for provider in providers if provider.get("taxon_id") == taxon_id]
        if matches:
            return matches[:limit]
    return providers[:limit]


def _annotate_provider_candidate(
    provider: Dict[str, Any],
    *,
    abbreviation: Optional[str],
    provider_name: Optional[str],
    taxon_id: Optional[str],
) -> Dict[str, Any]:
    candidate = dict(provider)
    mismatches = []
    if abbreviation and provider.get("abbreviation") != abbreviation:
        mismatches.append(
            f"Provider abbreviation {abbreviation!r} does not match candidate abbreviation {provider.get('abbreviation')!r}."
        )
    if taxon_id and provider.get("taxon_id") != taxon_id:
        mismatches.append(
            f"Taxon {taxon_id!r} does not match provider {provider.get('abbreviation')!r} taxon {provider.get('taxon_id')!r}."
        )
    if provider_name:
        if not _provider_name_matches(provider, provider_name):
            mismatches.append(
                f"Provider name {provider_name!r} does not match candidate display name {provider.get('display_name')!r}."
            )
    if mismatches:
        candidate["mismatch_explanation"] = " ".join(mismatches)
    return candidate


def _provider_lookup_response(
    *,
    method: str,
    providers: List[Dict[str, Any]],
    abbreviation: Optional[str],
    provider_name: Optional[str],
    taxon_id: Optional[str],
    limit: int,
) -> AgrQueryResult:
    attempted_query = _attempt_query(
        method,
        abbreviation=abbreviation,
        provider_name=provider_name,
        taxon_id=taxon_id,
        limit=limit,
    )
    candidates = [
        _annotate_provider_candidate(
            provider,
            abbreviation=abbreviation,
            provider_name=provider_name,
            taxon_id=taxon_id,
        )
        for provider in _data_provider_candidates(
            providers,
            abbreviation=abbreviation,
            provider_name=provider_name,
            taxon_id=taxon_id,
            limit=limit,
        )
    ]
    matches = [
        provider
        for provider in candidates
        if _provider_matches_all(
            provider,
            abbreviation=abbreviation,
            provider_name=provider_name,
            taxon_id=taxon_id,
        )
    ]
    lookup_status = _lookup_status_from_count(len(matches), exact_lookup=True)
    explanation = _lookup_explanation(
        method=method,
        lookup_status=lookup_status,
        count=len(matches),
        attempted_query=attempted_query,
    )
    if not matches and any(candidate.get("mismatch_explanation") for candidate in candidates):
        explanation = "No data provider matched all requested fields; candidate provider/taxon conflicts were preserved."
    warnings = [
        *(
            ["provider_taxon_mismatch"]
            if any(candidate.get("mismatch_explanation") for candidate in candidates)
            else []
        ),
        *_provider_metadata_warnings(),
    ]
    lookup_attempt = _lookup_attempt(
        method=method,
        attempted_query=attempted_query,
        lookup_status=lookup_status,
        explanation=explanation,
        candidate_count=len(candidates),
        resolved=matches[0] if len(matches) == 1 else None,
    )
    return _ok(
        data={
            "matches": matches,
            "candidates": candidates,
        },
        count=len(matches),
        warnings=warnings or None,
        message=None if matches else "Data provider not found for the supplied lookup fields.",
        lookup_status=lookup_status,
        failure_classification=None if lookup_status == LOOKUP_STATUS_SUCCESS else lookup_status,
        explanation=explanation,
        lookup_attempts=[lookup_attempt],
        candidate_matches=[_candidate_from_result(method, candidate) for candidate in candidates],
        result_projections=[_projection_from_result(method, match) for match in matches],
    )


def _vocabulary_term_query(
    *,
    term: Optional[str],
    term_name: Optional[str],
    abbreviation: Optional[str],
    synonym: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    for query_field, value in (
        ("term", term),
        ("term_name", term_name),
        ("abbreviation", abbreviation),
        ("synonym", synonym),
    ):
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            return normalized, query_field
    return None, None


def _entity_mapping_result(result: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(result)
    if row.get("entity_curie") is not None:
        row["curie"] = row["entity_curie"]
    if row.get("entity") is not None:
        row["symbol"] = row["entity"]
        row["name"] = row["entity"]
    if row.get("curie") is not None:
        _validate_curie_in_result(row)
    return row


def _lookup_response(
    *,
    method: str,
    data: Any = None,
    count: Optional[int] = None,
    warnings: Optional[List[str]] = None,
    message: Optional[str] = None,
    attempted_query: Optional[Dict[str, Any]] = None,
    exact_lookup: bool = False,
    attempts: Optional[List[Dict[str, Any]]] = None,
) -> AgrQueryResult:
    return _ok(
        **_lookup_response_payload(
            method=method,
            data=data,
            count=count,
            warnings=warnings,
            message=message,
            attempted_query=attempted_query,
            exact_lookup=exact_lookup,
            attempts=attempts,
        )
    )


def _normalize_limit(limit: Optional[int]) -> Tuple[int, List[str]]:
    """Normalize limit with defaults and caps."""
    warnings = []

    if limit is None:
        limit = DEFAULT_LIMIT
        warnings.append(f"default_limit_applied:{DEFAULT_LIMIT}")

    try:
        limit_int = int(limit)
    except (TypeError, ValueError):
        warnings.append(f"invalid_limit_defaulted:{DEFAULT_LIMIT}")
        limit_int = DEFAULT_LIMIT

    if limit_int <= 0:
        warnings.append(f"non_positive_limit_defaulted:{DEFAULT_LIMIT}")
        limit_int = DEFAULT_LIMIT

    if limit_int > HARD_MAX:
        warnings.append(f"limit_capped_at:{HARD_MAX}")
        limit_int = HARD_MAX

    return limit_int, warnings


def _extract_fullname_attribution(fullname: Optional[str], taxon_id: str) -> Optional[Dict[str, Any]]:
    """
    Extract probable creator/institution from allele fullname suffix.

    IMPORTANT: This is a HEURISTIC extraction based on naming conventions.
    The extracted value is the text after the last comma in the fullname,
    which TYPICALLY contains creator or institution info for MGI/RGD alleles,
    but this is not guaranteed.

    Args:
        fullname: The allele's full name (e.g., "targeted mutation 1.1, Joshua Scallan")
        taxon_id: The taxon CURIE (e.g., "NCBITaxon:10090" for mouse)

    Returns:
        Dict with value/confidence/source if extraction succeeded, None otherwise.
        - value: The extracted text
        - confidence: "probable" (2+ words, typical pattern) or "uncertain" (atypical)
        - source: "fullname_suffix" (always - explains provenance)

    Returns None when:
        - fullname is None or empty
        - Source group doesn't typically have attribution info (WB, SGD, ZFIN, FB)
        - No comma-separated suffix found
        - Fullname is uninformative (e.g., "wild type")
        - Extracted text is too short (< 4 chars)

    Examples (MGI):
        "targeted mutation 1.1, Joshua Scallan" → {"value": "Joshua Scallan", ...}
        "gene trap 460B7, Centre for Modeling Human Disease" → {"value": "Centre for...", ...}
        "wild type" → None

    Examples (RGD):
        "angiotensin II receptor; mutation 1, Medical College of Wisconsin" → {...}
    """
    if not fullname:
        return None

    # Determine source group from taxon
    mod = TAXON_TO_PROVIDER.get(taxon_id)
    if not mod:
        logger.debug('Unknown taxon %s, skipping fullname attribution extraction', taxon_id)
        return None

    # Only attempt extraction for MODs known to have attribution info
    # Other MODs: WB/SGD have no fullnames, ZFIN has IDs, FB has descriptive names
    if mod not in MODS_WITH_FULLNAME_ATTRIBUTION:
        return None

    # Skip uninformative patterns
    if fullname.lower() == 'wild type':
        return None

    # Pattern: "..., Creator/Institution" at end of string
    # Matches: ", Firstname Lastname" or ", Institution Name With Spaces"
    # Allows: letters, spaces, &, ', - in the extracted portion
    match = re.search(r',\s+([A-Z][A-Za-z\s&\'\-]+)$', fullname)
    if match:
        extracted = match.group(1).strip()
        # Filter out very short matches (likely parsing errors)
        if len(extracted) >= 4:
            # Determine confidence based on pattern
            # Person names typically have 2-3 words, institutions have more
            word_count = len(extracted.split())
            confidence = "probable" if word_count >= 2 else "uncertain"

            return {
                "value": extracted,
                "confidence": confidence,
                "source": "fullname_suffix"
            }

    return None


def _search_alleles_fuzzy_via_db(
    db: Any,
    *,
    search_pattern: str,
    taxon_curie: str,
    include_synonyms: bool,
    limit: int,
) -> List[Dict[str, Any]]:
    """Use database trigram similarity as a generic fallback for allele search."""
    session = None
    try:
        from sqlalchemy import text

        session = create_db_session(db)
        if session is None:
            return []
    except Exception as exc:
        logger.debug("Allele fuzzy fallback setup failed: %s", exc)
        return []

    try:
        sql_query = text(
            """
            WITH candidates AS (
                SELECT
                    be.primaryexternalid AS entity_curie,
                    symbol.displaytext AS matched_text,
                    'fuzzy_symbol' AS match_type,
                    similarity(lower(symbol.displaytext), lower(:search_pattern)) AS score
                FROM biologicalentity be
                JOIN allele a ON be.id = a.id
                LEFT JOIN ontologyterm taxon ON be.taxon_id = taxon.id
                JOIN slotannotation symbol ON a.id = symbol.singleallele_id
                    AND symbol.slotannotationtype = 'AlleleSymbolSlotAnnotation'
                    AND symbol.obsolete = false
                WHERE taxon.curie = :taxon_curie
                  AND symbol.displaytext IS NOT NULL

                UNION ALL

                SELECT
                    be.primaryexternalid AS entity_curie,
                    synonym.displaytext AS matched_text,
                    'fuzzy_synonym' AS match_type,
                    similarity(lower(synonym.displaytext), lower(:search_pattern)) AS score
                FROM biologicalentity be
                JOIN allele a ON be.id = a.id
                LEFT JOIN ontologyterm taxon ON be.taxon_id = taxon.id
                JOIN slotannotation synonym ON a.id = synonym.singleallele_id
                    AND synonym.slotannotationtype = 'AlleleSynonymSlotAnnotation'
                    AND synonym.obsolete = false
                WHERE :include_synonyms = true
                  AND taxon.curie = :taxon_curie
                  AND synonym.displaytext IS NOT NULL
            ),
            ranked AS (
                SELECT
                    entity_curie,
                    matched_text,
                    match_type,
                    score,
                    row_number() OVER (
                        PARTITION BY entity_curie
                        ORDER BY score DESC, length(matched_text) ASC
                    ) AS rank
                FROM candidates
                WHERE score >= :threshold
            )
            SELECT entity_curie, matched_text, match_type, score
            FROM ranked
            WHERE rank = 1
            ORDER BY score DESC, length(matched_text) ASC
            LIMIT :limit
            """
        )
        rows = session.execute(
            sql_query,
            {
                "search_pattern": search_pattern,
                "taxon_curie": taxon_curie,
                "include_synonyms": include_synonyms,
                "threshold": _ALLELE_FUZZY_SIMILARITY_THRESHOLD,
                "limit": limit,
            },
        ).fetchall()
        return [
            {
                "entity_curie": row[0],
                "entity": row[1],
                "match_type": row[2],
                "score": float(row[3]) if row[3] is not None else None,
            }
            for row in rows
        ]
    except Exception as exc:
        logger.debug(
            "Allele fuzzy fallback failed for %r in %s: %s",
            search_pattern,
            taxon_curie,
            exc,
        )
        return []
    finally:
        if session is not None:
            session.close()


def _ok(
    data: Any = None,
    count: Optional[int] = None,
    warnings: Optional[List[str]] = None,
    message: Optional[str] = None,
    lookup_status: Optional[str] = None,
    failure_classification: Optional[str] = None,
    explanation: Optional[str] = None,
    lookup_attempts: Optional[List[Dict[str, Any]]] = None,
    candidate_matches: Optional[List[Dict[str, Any]]] = None,
    result_projections: Optional[List[Dict[str, Any]]] = None,
) -> AgrQueryResult:
    return AgrQueryResult(
        status="ok",
        data=data,
        count=count,
        warnings=warnings or None,
        message=message,
        lookup_status=lookup_status,
        failure_classification=failure_classification,
        explanation=explanation,
        lookup_attempts=lookup_attempts,
        candidate_matches=candidate_matches,
        result_projections=result_projections,
    )


def _err(
    message: str,
    *,
    method: Optional[str] = None,
    attempted_query: Optional[Dict[str, Any]] = None,
    failure_classification: str = LOOKUP_STATUS_BLOCKED,
    error: Optional[BaseException] = None,
) -> AgrQueryResult:
    lookup_attempts = None
    explanation = message
    if method is not None:
        query = attempted_query or _attempt_query(method)
        lookup_attempts = [
            _lookup_attempt(
                method=method,
                attempted_query=query,
                lookup_status=failure_classification,
                explanation=message,
                error=error,
            )
        ]
    return AgrQueryResult(
        status="error",
        message=message,
        lookup_status=failure_classification,
        failure_classification=failure_classification,
        explanation=explanation,
        lookup_attempts=lookup_attempts,
    )


def _ensure_provider_mappings(method: str) -> Optional[AgrQueryResult]:
    """Return an error response when method requires provider mappings but they are unavailable."""
    methods_requiring_provider_map = {
        "get_gene_by_exact_symbol",
        "search_genes",
        "search_genes_bulk",
        "get_allele_by_exact_symbol",
        "search_alleles",
        "search_alleles_bulk",
    }
    if method not in methods_requiring_provider_map:
        return None
    if PROVIDER_TO_TAXON:
        return None

    msg = (
        "Provider mappings are unavailable. Ensure config/groups.yaml is present "
        "and defines groups with taxon IDs."
    )
    if _GROUP_MAPPING_LOAD_ERROR:
        msg += f" Load error: {_GROUP_MAPPING_LOAD_ERROR}"
    return _err(
        msg,
        method=method,
        attempted_query=_attempt_query(method),
        failure_classification=LOOKUP_STATUS_BLOCKED,
    )



@function_tool(strict_mode=False)
def agr_curation_query(
    method: str,
    gene_symbol: Optional[str] = None,
    gene_symbols: Optional[List[str]] = None,
    gene_id: Optional[str] = None,
    allele_symbol: Optional[str] = None,
    allele_symbols: Optional[List[str]] = None,
    allele_id: Optional[str] = None,
    data_provider: Optional[str] = None,
    provider_name: Optional[str] = None,
    taxon_id: Optional[str] = None,
    term: Optional[str] = None,
    terms: Optional[List[str]] = None,
    vocabulary: Optional[str] = None,
    term_name: Optional[str] = None,
    abbreviation: Optional[str] = None,
    synonym: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_names: Optional[List[str]] = None,
    entity_curies: Optional[List[str]] = None,
    category: Optional[str] = None,
    curies: Optional[List[str]] = None,
    ontology_term_type: Optional[str] = None,
    go_aspect: Optional[str] = None,
    exact_match: bool = False,
    include_synonyms: bool = True,
    include_obsolete: bool = False,
    limit: Optional[int] = None,
    force: bool = False,
    force_reason: Optional[str] = None,
    validation_retry_context: Optional[Dict[str, Any]] = None
) -> AgrQueryResult:
    """
    Query the Alliance Genome Resources Curation Database.

    Search methods send supplied symbols to the curation DB lookup layer without
    local symbol-shape rejection or deterministic nomenclature rewriting.

    Args:
        method: The query method (search_genes, search_genes_bulk, search_alleles, search_alleles_bulk, etc.)
        gene_symbol: Gene symbol to search for
        gene_symbols: List of gene symbols for bulk lookup methods
        gene_id: Gene ID/CURIE for direct lookup
        allele_symbol: Allele symbol to search for
        allele_symbols: List of allele symbols for bulk lookup methods
        allele_id: Allele ID/CURIE for direct lookup
        data_provider: Filter by species (MGI, FB, WB, ZFIN, RGD, SGD, HGNC)
        provider_name: Data provider display name for provider lookup
        taxon_id: Alternative to data_provider (NCBITaxon:XXXXX format)
        term: Search term for ontology searches
        terms: CURIE list for bulk ontology term helper paths
        vocabulary: Controlled vocabulary name or label filter
        term_name: Controlled vocabulary term name to search or resolve
        abbreviation: Controlled vocabulary term abbreviation to search or resolve
        synonym: Controlled vocabulary synonym to search or resolve
        entity_type: Entity helper type (gene, allele, agm, construct, targeting reagent)
        entity_names: Entity names/symbols to map to CURIEs
        entity_curies: Entity CURIEs to map to basic info
        category: CURIE-to-name helper category
        curies: CURIE list for generic CURIE-to-name helper paths
        ontology_term_type: Optional curation DB ontologytermtype filter for get_ontology_term
        go_aspect: GO aspect filter (molecular_function, biological_process, cellular_component)
        exact_match: Require exact match for ontology searches
        include_synonyms: Search synonyms in addition to primary symbols (default: True)
        include_obsolete: Include obsolete controlled vocabulary terms
        limit: Maximum results to return
        force: Accepted for backward-compatible callers; search methods no longer
            perform local symbol validation before querying.
        force_reason: Accepted for backward-compatible callers.
        validation_retry_context: Optional supervisor-owned context for bounded
            validator reruns, such as missing declared result projections.

    Returns:
        AgrQueryResult with status='ok' or 'error'
    """
    limit_value: Optional[int] = None

    def _transient_attempt_query() -> Dict[str, Any]:
        if method == "get_gene_by_id":
            return _attempt_query(method, gene_id=gene_id)
        if method == "get_allele_by_id":
            return _attempt_query(method, allele_id=allele_id)
        if method == "get_ontology_term":
            return _attempt_query(
                method,
                term=term,
                ontology_term_type=ontology_term_type,
            )
        if method == "get_ontology_terms":
            return _attempt_query(method, terms=terms or curies)
        if method in {"get_vocabulary_term", "search_vocabulary_terms"}:
            vocabulary_query, query_field = _vocabulary_term_query(
                term=term,
                term_name=term_name,
                abbreviation=abbreviation,
                synonym=synonym,
            )
            return _attempt_query(
                method,
                vocabulary=vocabulary,
                term=vocabulary_query,
                query_field=query_field,
                exact_match=True if method == "get_vocabulary_term" else exact_match,
                include_synonyms=include_synonyms,
                include_obsolete=include_obsolete,
                limit=limit_value,
            )
        if method == "get_data_provider":
            return _attempt_query(
                method,
                abbreviation=_provider_abbreviation(abbreviation),
                provider_name=provider_name,
                taxon_id=taxon_id,
                limit=limit_value,
            )
        if method == "search_ontology_terms":
            return _attempt_query(
                method,
                term=term,
                ontology_term_type=ontology_term_type,
                exact_match=exact_match,
                include_synonyms=include_synonyms,
                limit=limit_value,
            )
        if method == "map_entity_names_to_curies":
            return _attempt_query(
                method,
                entity_type=entity_type,
                entity_names=entity_names,
                taxon_id=taxon_id,
                data_provider=data_provider,
            )
        if method == "map_entity_curies_to_info":
            return _attempt_query(
                method,
                entity_type=entity_type,
                entity_curies=entity_curies or curies,
            )
        if method == "map_curies_to_names":
            return _attempt_query(method, category=category, curies=curies)
        if method in {"search_anatomy_terms", "search_life_stage_terms"}:
            return _attempt_query(
                method,
                term=term,
                data_provider=data_provider,
                exact_match=exact_match,
                include_synonyms=include_synonyms,
                limit=limit_value,
            )
        if method == "search_go_terms":
            return _attempt_query(
                method,
                term=term,
                go_aspect=go_aspect,
                exact_match=exact_match,
                include_synonyms=include_synonyms,
                limit=limit_value,
            )
        if method in {"get_gene_by_exact_symbol", "search_genes"}:
            return _attempt_query(
                method,
                gene_symbol=gene_symbol,
                data_provider=data_provider,
                include_synonyms=include_synonyms if method == "search_genes" else None,
                limit=limit_value if method == "search_genes" else None,
            )
        if method == "search_genes_bulk":
            return _attempt_query(
                method,
                gene_symbols=gene_symbols,
                data_provider=data_provider,
                include_synonyms=include_synonyms,
                limit=limit_value,
                force=force or None,
            )
        if method in {"get_allele_by_exact_symbol", "search_alleles"}:
            return _attempt_query(
                method,
                allele_symbol=allele_symbol,
                data_provider=data_provider,
                include_synonyms=include_synonyms if method == "search_alleles" else None,
                limit=limit_value if method == "search_alleles" else None,
            )
        if method == "search_alleles_bulk":
            return _attempt_query(
                method,
                allele_symbols=allele_symbols,
                data_provider=data_provider,
                include_synonyms=include_synonyms,
                limit=limit_value,
                force=force or None,
            )
        return _attempt_query(method)

    try:
        resolver = get_curation_resolver()
        db = resolver.get_db_client()
        if db is None:
            if resolver.get_connection_url():
                return _err(
                    (
                        'AGR Curation Database client is unavailable in this runtime. '
                        'The database is configured, but the dependency is missing or failed to initialize.'
                    ),
                    method=method,
                    attempted_query=_attempt_query(method),
                    failure_classification=LOOKUP_STATUS_BLOCKED,
                )
            return _err(
                'AGR Curation Database is not configured. This tool is unavailable.',
                method=method,
                attempted_query=_attempt_query(method),
                failure_classification=LOOKUP_STATUS_BLOCKED,
            )
        provider_mapping_error = _ensure_provider_mappings(method)
        if provider_mapping_error:
            return provider_mapping_error
        limit_value, warnings = _normalize_limit(limit)

        # Log query parameters for tracing
        logger.debug(
            '[agr_curation_query] method=%s, allele_symbol=%s, gene_symbol=%s, data_provider=%s, include_synonyms=%s',
            method,
            allele_symbol,
            gene_symbol,
            data_provider,
            include_synonyms,
        )

        # GET GENE BY EXACT SYMBOL (uses SQL IN clause - requires exact match)
        if method == "get_gene_by_exact_symbol":
            if not gene_symbol:
                return _err(
                    "get_gene_by_exact_symbol requires gene_symbol",
                    method=method,
                    attempted_query=_attempt_query(method, gene_symbol=gene_symbol),
                )

            if ':' in gene_symbol:
                prefix, symbol = gene_symbol.split(':', 1)
                if prefix in PROVIDER_TO_TAXON:
                    if not data_provider:
                        data_provider = prefix
                    gene_symbol = symbol

            if data_provider:
                taxon = PROVIDER_TO_TAXON.get(data_provider)
                if not taxon:
                    return _err(
                        f"Unknown data_provider: {data_provider}. Valid: {list(PROVIDER_TO_TAXON.keys())}",
                        method=method,
                        attempted_query=_attempt_query(
                            method,
                            gene_symbol=gene_symbol,
                            data_provider=data_provider,
                        ),
                    )
                taxon_ids = [taxon]
            else:
                taxon_ids = list(PROVIDER_TO_TAXON.values())

            genes_data: List[Dict[str, Any]] = []
            lookup_attempts: List[Dict[str, Any]] = []
            for tid in taxon_ids:
                try:
                    results = db.map_entity_names_to_curies(
                        entity_type='gene',
                        entity_names=[gene_symbol],
                        taxon_curie=tid
                    )
                    target_projection = (
                        _projection_from_entity_match(method, results[0], taxon_id=tid)
                        if len(results) == 1
                        else None
                    )
                    lookup_attempts.append(
                        _lookup_attempt(
                            method=method,
                            attempted_query=_attempt_query(
                                method,
                                gene_symbol=gene_symbol,
                                taxon_id=tid,
                                data_provider=TAXON_TO_PROVIDER.get(tid),
                            ),
                            lookup_status=(
                                LOOKUP_STATUS_SUCCESS
                                if len(results) == 1
                                else LOOKUP_STATUS_AMBIGUOUS
                                if len(results) > 1
                                else LOOKUP_STATUS_NOT_FOUND
                            ),
                            explanation=(
                                f"Tried exact gene symbol {gene_symbol!r} in taxon {tid}; "
                                f"the curation DB returned {len(results)} candidate(s)."
                            ),
                            candidate_count=len(results),
                            target_projection=target_projection,
                        )
                    )
                    for result in results:
                        curie = result.get('entity_curie')
                        if not curie:
                            continue
                        detail_projection = _projection_from_entity_match(
                            method,
                            result,
                            taxon_id=tid,
                        )
                        try:
                            gene = db.get_gene(curie)
                            if gene and not (
                                getattr(gene, "obsolete", False)
                                or getattr(gene, "internal", False)
                            ):
                                genes_data.append({
                                    "curie": gene.primaryExternalId,
                                    "symbol": gene.geneSymbol.displayText if gene.geneSymbol else result['entity'],
                                    "name": gene.geneFullName.displayText if gene.geneFullName else None,
                                    "taxon": tid,
                                    "gene_type": gene.geneType.get("name") if gene.geneType and isinstance(gene.geneType, dict) else str(gene.geneType) if gene.geneType else None,
                                })
                            else:
                                lookup_attempts.append(
                                    _lookup_attempt(
                                        method=method,
                                        attempted_query=_attempt_query(
                                            method,
                                            gene_symbol=gene_symbol,
                                            gene_id=curie,
                                            taxon_id=tid,
                                            data_provider=TAXON_TO_PROVIDER.get(tid),
                                            lookup_stage="fetch_gene_details",
                                        ),
                                        lookup_status=LOOKUP_STATUS_NOT_FOUND,
                                        explanation=(
                                            f"Exact gene symbol {gene_symbol!r} matched {curie!r} "
                                            f"in taxon {tid}, but no resolved gene details were returned."
                                        ),
                                        candidate_count=1,
                                        target_projection=detail_projection,
                                    )
                                )
                        except Exception as e:
                            logger.warning('Failed to fetch gene %s: %s', curie, e)
                            lookup_attempts.append(
                                _lookup_attempt(
                                    method=method,
                                    attempted_query=_attempt_query(
                                        method,
                                        gene_symbol=gene_symbol,
                                        gene_id=curie,
                                        taxon_id=tid,
                                        data_provider=TAXON_TO_PROVIDER.get(tid),
                                        lookup_stage="fetch_gene_details",
                                    ),
                                    lookup_status=LOOKUP_STATUS_TRANSIENT,
                                    explanation=(
                                        f"Exact gene symbol {gene_symbol!r} matched {curie!r} "
                                        f"in taxon {tid}, but fetching resolved gene details failed."
                                    ),
                                    candidate_count=1,
                                    target_projection=detail_projection,
                                    error=e,
                                )
                            )
                except Exception as e:
                    logger.warning('Failed to search taxon %s: %s', tid, e)
                    lookup_attempts.append(
                        _lookup_attempt(
                            method=method,
                            attempted_query=_attempt_query(
                                method,
                                gene_symbol=gene_symbol,
                                taxon_id=tid,
                                data_provider=TAXON_TO_PROVIDER.get(tid),
                            ),
                            lookup_status=LOOKUP_STATUS_TRANSIENT,
                            explanation=(
                                f"Exact gene symbol lookup for {gene_symbol!r} in taxon {tid} "
                                "failed while querying the curation DB."
                            ),
                            error=e,
                        )
                    )

            validated_data = genes_data[:limit_value]
            validated_data, invalid_curie_count = _validate_curie_list(validated_data)
            if invalid_curie_count > 0:
                warnings.append(f"invalid_curie_prefixes:{invalid_curie_count}")

            return _lookup_response(
                method=method,
                data=validated_data,
                count=len(validated_data),
                warnings=warnings,
                attempted_query=_attempt_query(
                    method,
                    gene_symbol=gene_symbol,
                    data_provider=data_provider,
                ),
                exact_lookup=True,
                attempts=lookup_attempts,
            )

        # SEARCH GENES (uses LIKE search - supports partial matches)
        elif method == "search_genes":
            if not gene_symbol:
                return _err(
                    "search_genes requires gene_symbol",
                    method=method,
                    attempted_query=_attempt_query(method, gene_symbol=gene_symbol),
                )

            if data_provider:
                taxon = PROVIDER_TO_TAXON.get(data_provider)
                if not taxon:
                    return _err(
                        f"Unknown data_provider: {data_provider}",
                        method=method,
                        attempted_query=_attempt_query(
                            method,
                            gene_symbol=gene_symbol,
                            data_provider=data_provider,
                        ),
                    )
                taxon_ids = [taxon]
            else:
                taxon_ids = list(PROVIDER_TO_TAXON.values())

            pending_matches: List[Dict[str, Any]] = []
            gene_curies_by_taxon: Dict[str, List[str]] = defaultdict(list)
            genes_data: List[Dict[str, Any]] = []
            lookup_attempts: List[Dict[str, Any]] = []
            for tid in taxon_ids:
                try:
                    results = db.search_entities(
                        entity_type='gene',
                        search_pattern=gene_symbol,
                        taxon_curie=tid,
                        include_synonyms=include_synonyms,
                        limit=limit_value
                    )
                    lookup_attempts.append(
                        _lookup_attempt(
                            method=method,
                            attempted_query=_attempt_query(
                                method,
                                gene_symbol=gene_symbol,
                                taxon_id=tid,
                                data_provider=TAXON_TO_PROVIDER.get(tid),
                                include_synonyms=include_synonyms,
                                limit=limit_value,
                            ),
                            lookup_status=(
                                LOOKUP_STATUS_SUCCESS
                                if results
                                else LOOKUP_STATUS_NOT_FOUND
                            ),
                            explanation=(
                                f"Searched gene symbol {gene_symbol!r} in taxon {tid}; "
                                f"the curation DB returned {len(results)} candidate(s)."
                            ),
                            candidate_count=len(results),
                        )
                    )
                    for result in results:
                        curie = result.get('entity_curie')
                        if not curie:
                            continue
                        pending_matches.append({
                            "curie": curie,
                            "taxon": tid,
                            "matched_entity": result.get('entity', gene_symbol),
                            "match_type": result.get('match_type', 'unknown'),
                        })
                        gene_curies_by_taxon[tid].append(curie)
                except Exception as e:
                    logger.warning('Failed to fuzzy search taxon %s: %s', tid, e)
                    lookup_attempts.append(
                        _lookup_attempt(
                            method=method,
                            attempted_query=_attempt_query(
                                method,
                                gene_symbol=gene_symbol,
                                taxon_id=tid,
                                data_provider=TAXON_TO_PROVIDER.get(tid),
                                include_synonyms=include_synonyms,
                                limit=limit_value,
                            ),
                            lookup_status=LOOKUP_STATUS_TRANSIENT,
                            explanation=(
                                f"Gene search for {gene_symbol!r} in taxon {tid} failed "
                                "while querying the curation DB."
                            ),
                            error=e,
                        )
                    )

            gene_details_by_taxon: Dict[str, Dict[str, Dict[str, Any]]] = {}
            gene_detail_failures_by_taxon: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
            for tid, curies in gene_curies_by_taxon.items():
                details, detail_failures = _fetch_gene_details_bulk(db, curies)
                gene_details_by_taxon[tid] = details
                gene_detail_failures_by_taxon[tid] = detail_failures

            for match in pending_matches:
                detail = gene_details_by_taxon.get(match["taxon"], {}).get(match["curie"])
                detail_failures = gene_detail_failures_by_taxon.get(
                    match["taxon"], {}
                ).get(match["curie"], [])
                if detail_failures:
                    lookup_attempts.extend(
                        _entity_detail_lookup_attempts(
                            method=method,
                            entity_kind="gene",
                            input_symbol=gene_symbol,
                            curie=match["curie"],
                            taxon_id=match["taxon"],
                            matched_entity=match["matched_entity"],
                            match_type=match["match_type"],
                            detail_failures=detail_failures,
                            data_provider=TAXON_TO_PROVIDER.get(match["taxon"]),
                        )
                    )
                if not detail:
                    continue
                matched_entity = match["matched_entity"]
                primary_symbol = detail.get("symbol") or matched_entity
                gene_entry = {
                    "curie": detail.get("curie", match["curie"]),
                    "symbol": primary_symbol,
                    "name": detail.get("name"),
                    "taxon": match["taxon"],
                    "match_type": match["match_type"],
                }
                if detail.get("gene_type"):
                    gene_entry["gene_type"] = detail["gene_type"]
                enrich_with_match_context(gene_entry, matched_entity, primary_symbol, 'gene')
                genes_data.append(gene_entry)

            validated_data = genes_data[:limit_value]
            validated_data, invalid_curie_count = _validate_curie_list(validated_data)
            if invalid_curie_count > 0:
                warnings.append(f"invalid_curie_prefixes:{invalid_curie_count}")

            return _lookup_response(
                method=method,
                data=validated_data,
                count=len(validated_data),
                warnings=warnings,
                attempted_query=_attempt_query(
                    method,
                    gene_symbol=gene_symbol,
                    data_provider=data_provider,
                    include_synonyms=include_synonyms,
                    limit=limit_value,
                ),
                attempts=lookup_attempts,
            )

        # SEARCH GENES BULK (single tool call, multiple symbols)
        elif method == "search_genes_bulk":
            if not isinstance(gene_symbols, list) or not gene_symbols:
                return _err(
                    "search_genes_bulk requires gene_symbols (list of symbols)",
                    method=method,
                    attempted_query=_attempt_query(method, gene_symbols=gene_symbols),
                )

            normalized_symbols: List[str] = []
            seen_inputs: set[str] = set()
            for raw_symbol in gene_symbols:
                symbol = str(raw_symbol).strip()
                if not symbol:
                    continue
                key = symbol.lower()
                if key in seen_inputs:
                    continue
                seen_inputs.add(key)
                normalized_symbols.append(symbol)

            if not normalized_symbols:
                return _err(
                    "search_genes_bulk received no valid symbols",
                    method=method,
                    attempted_query=_attempt_query(method, gene_symbols=gene_symbols),
                )

            if data_provider:
                taxon = PROVIDER_TO_TAXON.get(data_provider)
                if not taxon:
                    return _err(
                        f"Unknown data_provider: {data_provider}",
                        method=method,
                        attempted_query=_attempt_query(
                            method,
                            gene_symbols=normalized_symbols,
                            data_provider=data_provider,
                        ),
                    )
                taxon_ids = [taxon]
            else:
                taxon_ids = list(PROVIDER_TO_TAXON.values())

            pending_matches: Dict[str, List[Dict[str, Any]]] = {}
            gene_curies_by_taxon: Dict[str, List[str]] = defaultdict(list)
            lookup_attempts_by_symbol: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

            for symbol in normalized_symbols:
                symbol_matches: List[Dict[str, Any]] = []
                for tid in taxon_ids:
                    try:
                        results = db.search_entities(
                            entity_type='gene',
                            search_pattern=symbol,
                            taxon_curie=tid,
                            include_synonyms=include_synonyms,
                            limit=limit_value
                        )
                        lookup_attempts_by_symbol[symbol].append(
                            _lookup_attempt(
                                method=method,
                                attempted_query=_attempt_query(
                                    method,
                                    gene_symbol=symbol,
                                    taxon_id=tid,
                                    data_provider=TAXON_TO_PROVIDER.get(tid),
                                    include_synonyms=include_synonyms,
                                    limit=limit_value,
                                ),
                                lookup_status=(
                                    LOOKUP_STATUS_SUCCESS
                                    if results
                                    else LOOKUP_STATUS_NOT_FOUND
                                ),
                                explanation=(
                                    f"Searched gene symbol {symbol!r} in taxon {tid}; "
                                    f"the curation DB returned {len(results)} candidate(s)."
                                ),
                                candidate_count=len(results),
                            )
                        )
                        for result in results:
                            curie = result.get('entity_curie')
                            if not curie:
                                continue
                            symbol_matches.append({
                                "curie": curie,
                                "taxon": tid,
                                "matched_entity": result.get('entity', symbol),
                                "match_type": result.get('match_type', 'unknown'),
                            })
                            gene_curies_by_taxon[tid].append(curie)
                    except Exception as e:
                        logger.warning("Failed to fuzzy search genes in bulk for '%s' taxon %s: %s", symbol, tid, e)
                        lookup_attempts_by_symbol[symbol].append(
                            _lookup_attempt(
                                method=method,
                                attempted_query=_attempt_query(
                                    method,
                                    gene_symbol=symbol,
                                    taxon_id=tid,
                                    data_provider=TAXON_TO_PROVIDER.get(tid),
                                    include_synonyms=include_synonyms,
                                    limit=limit_value,
                                ),
                                lookup_status=LOOKUP_STATUS_TRANSIENT,
                                explanation=(
                                    f"Gene search for {symbol!r} in taxon {tid} failed "
                                    "while querying the curation DB."
                                ),
                                error=e,
                            )
                        )
                pending_matches[symbol] = symbol_matches

            gene_details_by_taxon: Dict[str, Dict[str, Dict[str, Any]]] = {}
            gene_detail_failures_by_taxon: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
            for tid, curies in gene_curies_by_taxon.items():
                details, detail_failures = _fetch_gene_details_bulk(db, curies)
                gene_details_by_taxon[tid] = details
                gene_detail_failures_by_taxon[tid] = detail_failures

            bulk_items: List[Dict[str, Any]] = []

            for symbol in normalized_symbols:
                item_warnings: List[str] = []
                genes_data: List[Dict[str, Any]] = []
                for match in pending_matches.get(symbol, []):
                    detail = gene_details_by_taxon.get(match["taxon"], {}).get(match["curie"])
                    detail_failures = gene_detail_failures_by_taxon.get(
                        match["taxon"], {}
                    ).get(match["curie"], [])
                    if detail_failures:
                        lookup_attempts_by_symbol[symbol].extend(
                            _entity_detail_lookup_attempts(
                                method=method,
                                entity_kind="gene",
                                input_symbol=symbol,
                                curie=match["curie"],
                                taxon_id=match["taxon"],
                                matched_entity=match["matched_entity"],
                                match_type=match["match_type"],
                                detail_failures=detail_failures,
                                data_provider=TAXON_TO_PROVIDER.get(match["taxon"]),
                            )
                        )
                    if not detail:
                        continue
                    primary_symbol = detail.get("symbol") or match["matched_entity"]
                    gene_entry = {
                        "curie": detail.get("curie", match["curie"]),
                        "symbol": primary_symbol,
                        "name": detail.get("name"),
                        "taxon": match["taxon"],
                        "match_type": match["match_type"],
                    }
                    if detail.get("gene_type"):
                        gene_entry["gene_type"] = detail["gene_type"]
                    enrich_with_match_context(
                        gene_entry,
                        match["matched_entity"],
                        primary_symbol,
                        'gene'
                    )
                    genes_data.append(gene_entry)

                validated_data = genes_data[:limit_value]
                validated_data, invalid_curie_count = _validate_curie_list(validated_data)
                if invalid_curie_count > 0:
                    item_warnings.append(f"invalid_curie_prefixes:{invalid_curie_count}")

                item_lookup_status = _lookup_status_from_count(
                    len(validated_data),
                    exact_lookup=False,
                    attempts=lookup_attempts_by_symbol.get(symbol),
                )
                item_status = _bulk_item_status_from_lookup_status(
                    item_lookup_status,
                    count=len(validated_data),
                    attempts=lookup_attempts_by_symbol.get(symbol),
                )
                item_explanation = _lookup_explanation(
                    method=method,
                    lookup_status=item_lookup_status,
                    count=len(validated_data),
                    attempted_query=_attempt_query(method, gene_symbol=symbol),
                )
                item_payload: Dict[str, Any] = {
                    "input": symbol,
                    "status": item_status,
                    "results": validated_data,
                    "count": len(validated_data),
                    "lookup_status": item_lookup_status,
                    "failure_classification": (
                        None
                        if item_status == "resolved"
                        else (
                            "detail_failure"
                            if item_status == "detail_failure"
                            else item_lookup_status
                        )
                    ),
                    "explanation": item_explanation,
                    "lookup_attempts": lookup_attempts_by_symbol.get(symbol) or None,
                    "candidate_matches": [
                        _candidate_from_result(method, row) for row in validated_data
                    ] or None,
                    "result_projections": [
                        _projection_from_result(method, row) for row in validated_data
                    ] or None,
                }
                if item_warnings:
                    item_payload["warnings"] = item_warnings
                bulk_items.append(item_payload)

            summary = _bulk_resolution_summary(bulk_items)
            return _lookup_response(
                method=method,
                data={
                    "items": bulk_items,
                    **summary,
                    "method": "search_genes_bulk",
                },
                count=summary["resolved_count"],
                warnings=warnings,
                attempted_query=_attempt_query(
                    method,
                    gene_symbols=normalized_symbols,
                    data_provider=data_provider,
                    include_synonyms=include_synonyms,
                    limit=limit_value,
                ),
                attempts=[
                    attempt
                    for symbol in normalized_symbols
                    for attempt in lookup_attempts_by_symbol.get(symbol, [])
                ],
            )

        # GET GENE BY ID
        elif method == "get_gene_by_id":
            if not gene_id:
                return _err(
                    "get_gene_by_id requires gene_id",
                    method=method,
                    attempted_query=_attempt_query(method, gene_id=gene_id),
                )

            gene = db.get_gene(gene_id)
            if not gene:
                return _lookup_response(
                    method=method,
                    data=None,
                    count=0,
                    message=f"Gene not found: {gene_id}",
                    attempted_query=_attempt_query(method, gene_id=gene_id),
                    exact_lookup=True,
                )

            gene_dict = {
                "curie": gene.primaryExternalId,
                "symbol": gene.geneSymbol.displayText if gene.geneSymbol else None,
                "name": gene.geneFullName.displayText if gene.geneFullName else None,
                "taxon": gene.taxon,
                "gene_type": gene.geneType.get("name") if gene.geneType and isinstance(gene.geneType, dict) else str(gene.geneType) if gene.geneType else None,
            }

            if hasattr(gene, 'genomeLocations') and gene.genomeLocations:
                loc = gene.genomeLocations[0]
                gene_dict["genomic_location"] = {
                    "chromosome": loc.chromosome,
                    "start": loc.start,
                    "end": loc.end,
                    "strand": loc.strand,
                    "assembly": loc.assembly,
                }

            _validate_curie_in_result(gene_dict)
            return _lookup_response(
                method=method,
                data=gene_dict,
                attempted_query=_attempt_query(method, gene_id=gene_id),
                exact_lookup=True,
            )

        # GET ALLELE BY EXACT SYMBOL (uses SQL IN clause - requires exact match)
        elif method == "get_allele_by_exact_symbol":
            if not allele_symbol:
                return _err(
                    "get_allele_by_exact_symbol requires allele_symbol",
                    method=method,
                    attempted_query=_attempt_query(method, allele_symbol=allele_symbol),
                )

            if data_provider:
                taxon = PROVIDER_TO_TAXON.get(data_provider)
                if not taxon:
                    return _err(
                        f"Unknown data_provider: {data_provider}",
                        method=method,
                        attempted_query=_attempt_query(
                            method,
                            allele_symbol=allele_symbol,
                            data_provider=data_provider,
                        ),
                    )
                taxon_ids = [taxon]
            else:
                taxon_ids = list(PROVIDER_TO_TAXON.values())

            symbol_variants = [allele_symbol]

            alleles_data: List[Dict[str, Any]] = []
            seen_curies = set()  # Avoid duplicates across variants
            lookup_attempts: List[Dict[str, Any]] = []
            for tid in taxon_ids:
                for symbol_variant in symbol_variants:
                    try:
                        results = db.map_entity_names_to_curies(
                            entity_type='allele',
                            entity_names=[symbol_variant],
                            taxon_curie=tid
                        )
                        target_projection = (
                            _projection_from_entity_match(
                                method,
                                results[0],
                                taxon_id=tid,
                                matched_variant=symbol_variant,
                            )
                            if len(results) == 1
                            else None
                        )
                        lookup_attempts.append(
                            _lookup_attempt(
                                method=method,
                                attempted_query=_attempt_query(
                                    method,
                                    allele_symbol=symbol_variant,
                                    original_allele_symbol=allele_symbol,
                                    taxon_id=tid,
                                    data_provider=TAXON_TO_PROVIDER.get(tid),
                                ),
                                lookup_status=(
                                    LOOKUP_STATUS_SUCCESS
                                    if len(results) == 1
                                    else LOOKUP_STATUS_AMBIGUOUS
                                    if len(results) > 1
                                    else LOOKUP_STATUS_NOT_FOUND
                                ),
                                explanation=(
                                    f"Tried exact allele symbol {symbol_variant!r} in taxon {tid}; "
                                    f"the curation DB returned {len(results)} candidate(s)."
                                ),
                                candidate_count=len(results),
                                target_projection=target_projection,
                            )
                        )
                        for result in results:
                            curie = result.get('entity_curie')
                            if not curie:
                                continue
                            detail_projection = _projection_from_entity_match(
                                method,
                                result,
                                taxon_id=tid,
                                matched_variant=symbol_variant,
                            )
                            try:
                                if curie in seen_curies:
                                    continue  # Skip duplicates
                                seen_curies.add(curie)

                                allele = db.get_allele(curie)
                                if allele:
                                    fullname = allele.alleleFullName.displayText if allele.alleleFullName else None
                                    alleles_data.append({
                                        "curie": allele.primaryExternalId,
                                        "symbol": allele.alleleSymbol.displayText if allele.alleleSymbol else result['entity'],
                                        "name": fullname,
                                        "taxon": tid,
                                        "matched_variant": symbol_variant,  # Track which variant matched
                                        "fullname_attribution": _extract_fullname_attribution(fullname, tid),
                                    })
                                else:
                                    lookup_attempts.append(
                                        _lookup_attempt(
                                            method=method,
                                            attempted_query=_attempt_query(
                                                method,
                                                allele_symbol=symbol_variant,
                                                original_allele_symbol=allele_symbol,
                                                allele_id=curie,
                                                taxon_id=tid,
                                                data_provider=TAXON_TO_PROVIDER.get(tid),
                                                lookup_stage="fetch_allele_details",
                                            ),
                                            lookup_status=LOOKUP_STATUS_NOT_FOUND,
                                            explanation=(
                                                f"Exact allele symbol {symbol_variant!r} matched {curie!r} "
                                                f"in taxon {tid}, but no resolved allele details were returned."
                                            ),
                                            candidate_count=1,
                                            target_projection=detail_projection,
                                        )
                                    )
                            except Exception as e:
                                logger.warning('Failed to fetch allele details: %s', e)
                                lookup_attempts.append(
                                    _lookup_attempt(
                                        method=method,
                                        attempted_query=_attempt_query(
                                            method,
                                            allele_symbol=symbol_variant,
                                            original_allele_symbol=allele_symbol,
                                            allele_id=curie,
                                            taxon_id=tid,
                                            data_provider=TAXON_TO_PROVIDER.get(tid),
                                            lookup_stage="fetch_allele_details",
                                        ),
                                        lookup_status=LOOKUP_STATUS_TRANSIENT,
                                        explanation=(
                                            f"Exact allele symbol {symbol_variant!r} matched {curie!r} "
                                            f"in taxon {tid}, but fetching resolved allele details failed."
                                        ),
                                        candidate_count=1,
                                        target_projection=detail_projection,
                                        error=e,
                                    )
                                )
                    except Exception as e:
                        logger.warning("Failed to search alleles in taxon %s with variant '%s': %s", tid, symbol_variant, e)
                        lookup_attempts.append(
                            _lookup_attempt(
                                method=method,
                                attempted_query=_attempt_query(
                                    method,
                                    allele_symbol=symbol_variant,
                                    original_allele_symbol=allele_symbol,
                                    taxon_id=tid,
                                    data_provider=TAXON_TO_PROVIDER.get(tid),
                                ),
                                lookup_status=LOOKUP_STATUS_TRANSIENT,
                                explanation=(
                                    f"Exact allele symbol lookup for {symbol_variant!r} in taxon {tid} "
                                    "failed while querying the curation DB."
                                ),
                                error=e,
                            )
                        )

            validated_data = alleles_data[:limit_value]
            validated_data, invalid_curie_count = _validate_curie_list(validated_data)
            if invalid_curie_count > 0:
                warnings.append(f"invalid_curie_prefixes:{invalid_curie_count}")

            return _lookup_response(
                method=method,
                data=validated_data,
                count=len(validated_data),
                warnings=warnings,
                attempted_query=_attempt_query(
                    method,
                    allele_symbol=allele_symbol,
                    data_provider=data_provider,
                ),
                exact_lookup=True,
                attempts=lookup_attempts,
            )

        # SEARCH ALLELES (uses LIKE search - supports partial matches)
        elif method == "search_alleles":
            if not allele_symbol:
                return _err(
                    "search_alleles requires allele_symbol",
                    method=method,
                    attempted_query=_attempt_query(method, allele_symbol=allele_symbol),
                )

            symbol_variants = [allele_symbol]

            if data_provider:
                taxon = PROVIDER_TO_TAXON.get(data_provider)
                if not taxon:
                    return _err(
                        f"Unknown data_provider: {data_provider}",
                        method=method,
                        attempted_query=_attempt_query(
                            method,
                            allele_symbol=allele_symbol,
                            data_provider=data_provider,
                        ),
                    )
                taxon_ids = [taxon]
            else:
                taxon_ids = list(PROVIDER_TO_TAXON.values())

            pending_matches: List[Dict[str, Any]] = []
            allele_curies_by_taxon: Dict[str, List[str]] = defaultdict(list)
            alleles_data: List[Dict[str, Any]] = []
            seen_curies = set()  # Avoid duplicates
            lookup_attempts: List[Dict[str, Any]] = []
            for tid in taxon_ids:
                for symbol_variant in symbol_variants:
                    try:
                        results = db.search_entities(
                            entity_type='allele',
                            search_pattern=symbol_variant,
                            taxon_curie=tid,
                            include_synonyms=include_synonyms,
                            limit=limit_value
                        )
                        if not results:
                            results = _search_alleles_fuzzy_via_db(
                                db,
                                search_pattern=symbol_variant,
                                taxon_curie=tid,
                                include_synonyms=include_synonyms,
                                limit=limit_value,
                            )
                        lookup_attempts.append(
                            _lookup_attempt(
                                method=method,
                                attempted_query=_attempt_query(
                                    method,
                                    allele_symbol=symbol_variant,
                                    original_allele_symbol=allele_symbol
                                    if symbol_variant != allele_symbol
                                    else None,
                                    taxon_id=tid,
                                    data_provider=TAXON_TO_PROVIDER.get(tid),
                                    include_synonyms=include_synonyms,
                                    limit=limit_value,
                                ),
                                lookup_status=(
                                    LOOKUP_STATUS_SUCCESS
                                    if results
                                    else LOOKUP_STATUS_NOT_FOUND
                                ),
                                explanation=(
                                    f"Searched allele symbol {symbol_variant!r} in taxon {tid}; "
                                    f"the curation DB returned {len(results)} candidate(s)."
                                ),
                                candidate_count=len(results),
                            )
                        )
                        for result in results:
                            curie = result.get('entity_curie')
                            if not curie:
                                continue
                            pending_matches.append({
                                "curie": curie,
                                "taxon": tid,
                                "matched_entity": result.get('entity', symbol_variant),
                                "match_type": result.get('match_type', 'unknown'),
                            })
                            allele_curies_by_taxon[tid].append(curie)
                    except Exception as e:
                        logger.warning('Failed to fuzzy search alleles in taxon %s: %s', tid, e)
                        lookup_attempts.append(
                            _lookup_attempt(
                                method=method,
                                attempted_query=_attempt_query(
                                    method,
                                    allele_symbol=symbol_variant,
                                    original_allele_symbol=allele_symbol
                                    if symbol_variant != allele_symbol
                                    else None,
                                    taxon_id=tid,
                                    data_provider=TAXON_TO_PROVIDER.get(tid),
                                    include_synonyms=include_synonyms,
                                    limit=limit_value,
                                ),
                                lookup_status=LOOKUP_STATUS_TRANSIENT,
                                explanation=(
                                    f"Allele search for {symbol_variant!r} in taxon {tid} failed "
                                    "while querying the curation DB."
                                ),
                                error=e,
                            )
                        )

            allele_details_by_taxon: Dict[str, Dict[str, Dict[str, Any]]] = {}
            allele_detail_failures_by_taxon: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
            for tid, curies in allele_curies_by_taxon.items():
                details, detail_failures = _fetch_allele_details_bulk(db, curies)
                allele_details_by_taxon[tid] = details
                allele_detail_failures_by_taxon[tid] = detail_failures

            for match in pending_matches:
                curie = match["curie"]
                if curie in seen_curies:
                    continue
                seen_curies.add(curie)

                detail = allele_details_by_taxon.get(match["taxon"], {}).get(curie)
                detail_failures = allele_detail_failures_by_taxon.get(
                    match["taxon"], {}
                ).get(curie, [])
                if detail_failures:
                    lookup_attempts.extend(
                        _entity_detail_lookup_attempts(
                            method=method,
                            entity_kind="allele",
                            input_symbol=allele_symbol,
                            curie=curie,
                            taxon_id=match["taxon"],
                            matched_entity=match["matched_entity"],
                            match_type=match["match_type"],
                            detail_failures=detail_failures,
                            data_provider=TAXON_TO_PROVIDER.get(match["taxon"]),
                        )
                    )
                if not detail:
                    continue

                matched_entity = match["matched_entity"]
                primary_symbol = detail.get("symbol") or matched_entity
                fullname = detail.get("name")
                allele_entry = {
                    "curie": detail.get("curie", curie),
                    "symbol": primary_symbol,
                    "name": fullname,
                    "taxon": match["taxon"],
                    "match_type": match["match_type"],
                    "fullname_attribution": _extract_fullname_attribution(fullname, match["taxon"]),
                }
                enrich_with_match_context(allele_entry, matched_entity, primary_symbol, 'allele')
                alleles_data.append(allele_entry)

            validated_data = alleles_data[:limit_value]
            validated_data, invalid_curie_count = _validate_curie_list(validated_data)
            if invalid_curie_count > 0:
                warnings.append(f"invalid_curie_prefixes:{invalid_curie_count}")

            # Log search results for tracing
            logger.debug(
                '[agr_curation_query] search_alleles returning %s results: %s',
                len(validated_data),
                [d.get('curie') for d in validated_data[:5]],
            )

            return _lookup_response(
                method=method,
                data=validated_data,
                count=len(validated_data),
                warnings=warnings,
                attempted_query=_attempt_query(
                    method,
                    allele_symbol=allele_symbol,
                    data_provider=data_provider,
                    include_synonyms=include_synonyms,
                    limit=limit_value,
                ),
                attempts=lookup_attempts,
            )

        # SEARCH ALLELES BULK (single tool call, multiple symbols)
        elif method == "search_alleles_bulk":
            if not isinstance(allele_symbols, list) or not allele_symbols:
                return _err(
                    "search_alleles_bulk requires allele_symbols (list of symbols)",
                    method=method,
                    attempted_query=_attempt_query(method, allele_symbols=allele_symbols),
                )

            normalized_symbols: List[str] = []
            seen_inputs: set[str] = set()
            for raw_symbol in allele_symbols:
                symbol = str(raw_symbol).strip()
                if not symbol:
                    continue
                key = symbol.lower()
                if key in seen_inputs:
                    continue
                seen_inputs.add(key)
                normalized_symbols.append(symbol)

            if not normalized_symbols:
                return _err(
                    "search_alleles_bulk received no valid symbols",
                    method=method,
                    attempted_query=_attempt_query(method, allele_symbols=allele_symbols),
                )

            if data_provider:
                taxon = PROVIDER_TO_TAXON.get(data_provider)
                if not taxon:
                    return _err(
                        f"Unknown data_provider: {data_provider}",
                        method=method,
                        attempted_query=_attempt_query(
                            method,
                            allele_symbols=normalized_symbols,
                            data_provider=data_provider,
                        ),
                    )
                taxon_ids = [taxon]
            else:
                taxon_ids = list(PROVIDER_TO_TAXON.values())

            pending_matches: Dict[str, List[Dict[str, Any]]] = {}
            allele_curies_by_taxon: Dict[str, List[str]] = defaultdict(list)
            lookup_attempts_by_symbol: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

            for symbol in normalized_symbols:
                symbol_matches: List[Dict[str, Any]] = []
                for tid in taxon_ids:
                    try:
                        results = db.search_entities(
                            entity_type='allele',
                            search_pattern=symbol,
                            taxon_curie=tid,
                            include_synonyms=include_synonyms,
                            limit=limit_value
                        )
                        if not results:
                            results = _search_alleles_fuzzy_via_db(
                                db,
                                search_pattern=symbol,
                                taxon_curie=tid,
                                include_synonyms=include_synonyms,
                                limit=limit_value,
                            )
                        lookup_attempts_by_symbol[symbol].append(
                            _lookup_attempt(
                                method=method,
                                attempted_query=_attempt_query(
                                    method,
                                    allele_symbol=symbol,
                                    taxon_id=tid,
                                    data_provider=TAXON_TO_PROVIDER.get(tid),
                                    include_synonyms=include_synonyms,
                                    limit=limit_value,
                                ),
                                lookup_status=(
                                    LOOKUP_STATUS_SUCCESS
                                    if results
                                    else LOOKUP_STATUS_NOT_FOUND
                                ),
                                explanation=(
                                    f"Searched allele symbol {symbol!r} in taxon {tid}; "
                                    f"the curation DB returned {len(results)} candidate(s)."
                                ),
                                candidate_count=len(results),
                            )
                        )
                        for result in results:
                            curie = result.get('entity_curie')
                            if not curie:
                                continue
                            symbol_matches.append({
                                "curie": curie,
                                "taxon": tid,
                                "matched_entity": result.get('entity', symbol),
                                "match_type": result.get('match_type', 'unknown'),
                            })
                            allele_curies_by_taxon[tid].append(curie)
                    except Exception as e:
                        logger.warning("Failed to fuzzy search alleles in bulk for '%s' taxon %s: %s", symbol, tid, e)
                        lookup_attempts_by_symbol[symbol].append(
                            _lookup_attempt(
                                method=method,
                                attempted_query=_attempt_query(
                                    method,
                                    allele_symbol=symbol,
                                    taxon_id=tid,
                                    data_provider=TAXON_TO_PROVIDER.get(tid),
                                    include_synonyms=include_synonyms,
                                    limit=limit_value,
                                ),
                                lookup_status=LOOKUP_STATUS_TRANSIENT,
                                explanation=(
                                    f"Allele search for {symbol!r} in taxon {tid} failed "
                                    "while querying the curation DB."
                                ),
                                error=e,
                            )
                        )
                pending_matches[symbol] = symbol_matches

            allele_details_by_taxon: Dict[str, Dict[str, Dict[str, Any]]] = {}
            allele_detail_failures_by_taxon: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
            for tid, curies in allele_curies_by_taxon.items():
                details, detail_failures = _fetch_allele_details_bulk(db, curies)
                allele_details_by_taxon[tid] = details
                allele_detail_failures_by_taxon[tid] = detail_failures

            bulk_items: List[Dict[str, Any]] = []

            for symbol in normalized_symbols:
                item_warnings: List[str] = []
                alleles_data: List[Dict[str, Any]] = []
                seen_curies = set()
                for match in pending_matches.get(symbol, []):
                    curie = match["curie"]
                    if curie in seen_curies:
                        continue
                    seen_curies.add(curie)

                    detail = allele_details_by_taxon.get(match["taxon"], {}).get(curie)
                    detail_failures = allele_detail_failures_by_taxon.get(
                        match["taxon"], {}
                    ).get(curie, [])
                    if detail_failures:
                        lookup_attempts_by_symbol[symbol].extend(
                            _entity_detail_lookup_attempts(
                                method=method,
                                entity_kind="allele",
                                input_symbol=symbol,
                                curie=curie,
                                taxon_id=match["taxon"],
                                matched_entity=match["matched_entity"],
                                match_type=match["match_type"],
                                detail_failures=detail_failures,
                                data_provider=TAXON_TO_PROVIDER.get(match["taxon"]),
                            )
                        )
                    if not detail:
                        continue

                    primary_symbol = detail.get("symbol") or match["matched_entity"]
                    fullname = detail.get("name")
                    allele_entry = {
                        "curie": detail.get("curie", curie),
                        "symbol": primary_symbol,
                        "name": fullname,
                        "taxon": match["taxon"],
                        "match_type": match["match_type"],
                        "fullname_attribution": _extract_fullname_attribution(fullname, match["taxon"]),
                    }
                    enrich_with_match_context(
                        allele_entry,
                        match["matched_entity"],
                        primary_symbol,
                        'allele'
                    )
                    alleles_data.append(allele_entry)

                validated_data = alleles_data[:limit_value]
                validated_data, invalid_curie_count = _validate_curie_list(validated_data)
                if invalid_curie_count > 0:
                    item_warnings.append(f"invalid_curie_prefixes:{invalid_curie_count}")

                item_lookup_status = _lookup_status_from_count(
                    len(validated_data),
                    exact_lookup=False,
                    attempts=lookup_attempts_by_symbol.get(symbol),
                )
                item_status = _bulk_item_status_from_lookup_status(
                    item_lookup_status,
                    count=len(validated_data),
                    attempts=lookup_attempts_by_symbol.get(symbol),
                )
                item_explanation = _lookup_explanation(
                    method=method,
                    lookup_status=item_lookup_status,
                    count=len(validated_data),
                    attempted_query=_attempt_query(method, allele_symbol=symbol),
                )
                item_payload: Dict[str, Any] = {
                    "input": symbol,
                    "status": item_status,
                    "results": validated_data,
                    "count": len(validated_data),
                    "lookup_status": item_lookup_status,
                    "failure_classification": (
                        None
                        if item_status == "resolved"
                        else (
                            "detail_failure"
                            if item_status == "detail_failure"
                            else item_lookup_status
                        )
                    ),
                    "explanation": item_explanation,
                    "lookup_attempts": lookup_attempts_by_symbol.get(symbol) or None,
                    "candidate_matches": [
                        _candidate_from_result(method, row) for row in validated_data
                    ] or None,
                    "result_projections": [
                        _projection_from_result(method, row) for row in validated_data
                    ] or None,
                }
                if item_warnings:
                    item_payload["warnings"] = item_warnings
                bulk_items.append(item_payload)

            summary = _bulk_resolution_summary(bulk_items)
            return _lookup_response(
                method=method,
                data={
                    "items": bulk_items,
                    **summary,
                    "method": "search_alleles_bulk",
                },
                count=summary["resolved_count"],
                warnings=warnings,
                attempted_query=_attempt_query(
                    method,
                    allele_symbols=normalized_symbols,
                    data_provider=data_provider,
                    include_synonyms=include_synonyms,
                    limit=limit_value,
                ),
                attempts=[
                    attempt
                    for symbol in normalized_symbols
                    for attempt in lookup_attempts_by_symbol.get(symbol, [])
                ],
            )

        # GET ALLELE BY ID
        elif method == "get_allele_by_id":
            if not allele_id:
                return _err(
                    "get_allele_by_id requires allele_id",
                    method=method,
                    attempted_query=_attempt_query(method, allele_id=allele_id),
                )

            allele = db.get_allele(allele_id)
            if not allele:
                return _lookup_response(
                    method=method,
                    data=None,
                    count=0,
                    message=f"Allele not found: {allele_id}",
                    attempted_query=_attempt_query(method, allele_id=allele_id),
                    exact_lookup=True,
                )

            fullname = allele.alleleFullName.displayText if allele.alleleFullName else None
            taxon = allele.taxon if hasattr(allele, 'taxon') else None
            allele_dict = {
                "curie": allele.primaryExternalId,
                "symbol": allele.alleleSymbol.displayText if allele.alleleSymbol else None,
                "name": fullname,
                "taxon": taxon,
                "fullname_attribution": _extract_fullname_attribution(fullname, taxon) if taxon else None,
            }
            _validate_curie_in_result(allele_dict)
            return _lookup_response(
                method=method,
                data=allele_dict,
                attempted_query=_attempt_query(method, allele_id=allele_id),
                exact_lookup=True,
            )

        # GET SPECIES
        elif method == "get_species":
            species_list = db.get_species()
            species_data = [{
                "abbreviation": s.abbreviation,
                "display_name": s.display_name,
            } for s in species_list]
            return _lookup_response(
                method=method,
                data=species_data,
                count=len(species_data),
                attempted_query=_attempt_query(method),
            )

        # GET DATA PROVIDERS
        elif method == "get_data_providers":
            provider_method = getattr(db, "get_data_providers", None)
            if not callable(provider_method):
                return _err(
                    "AGR Curation API client does not expose get_data_providers in this runtime.",
                    method=method,
                    attempted_query=_attempt_query(method),
                    failure_classification=LOOKUP_STATUS_UNDER_DEVELOPMENT,
                )
            providers = provider_method()
            providers_data = [_data_provider_result(provider) for provider in providers]
            return _lookup_response(
                method=method,
                data=providers_data,
                count=len(providers_data),
                warnings=_provider_metadata_warnings() or None,
                attempted_query=_attempt_query(method),
            )

        # GET DATA PROVIDER
        elif method == "get_data_provider":
            provider_method = getattr(db, "get_data_providers", None)
            attempted_query = _attempt_query(
                method,
                abbreviation=_provider_abbreviation(abbreviation),
                provider_name=provider_name,
                taxon_id=taxon_id,
                limit=limit_value,
            )
            if not abbreviation and not provider_name and not taxon_id:
                return _err(
                    "get_data_provider requires abbreviation, provider_name, or taxon_id",
                    method=method,
                    attempted_query=attempted_query,
                )
            if not callable(provider_method):
                return _err(
                    "AGR Curation API client does not expose get_data_providers in this runtime.",
                    method=method,
                    attempted_query=attempted_query,
                    failure_classification=LOOKUP_STATUS_UNDER_DEVELOPMENT,
                )
            providers = [_data_provider_result(provider) for provider in provider_method()]
            return _provider_lookup_response(
                method=method,
                providers=providers,
                abbreviation=_provider_abbreviation(abbreviation),
                provider_name=provider_name,
                taxon_id=taxon_id,
                limit=limit_value,
            )

        # GET ONTOLOGY TERM
        elif method == "get_ontology_term":
            if not term:
                return _err(
                    f"{method} requires term",
                    method=method,
                    attempted_query=_attempt_query(
                        method,
                        term=term,
                        ontology_term_type=ontology_term_type,
                    ),
                )

            result = db.get_ontology_term(term)
            result_data = _ontology_term_result(result) if result else None
            if result_data and ontology_term_type and result_data.get("ontology_type") != ontology_term_type:
                result_data = None
            validation_warnings = []
            if result_data and not result_data.get("curie_validated", False):
                validation_warnings.append("invalid_curie_prefixes:1")
            return _lookup_response(
                method=method,
                data=result_data,
                count=1 if result_data else 0,
                warnings=validation_warnings,
                message=None if result_data else f"Ontology term not found: {term}",
                attempted_query=_attempt_query(
                    method,
                    term=term,
                    ontology_term_type=ontology_term_type,
                ),
                exact_lookup=True,
            )

        # GET ONTOLOGY TERMS
        elif method == "get_ontology_terms":
            requested_terms = terms or curies or []
            if not requested_terms:
                return _err(
                    "get_ontology_terms requires terms",
                    method=method,
                    attempted_query=_attempt_query(method, terms=requested_terms),
                )

            result_map = db.get_ontology_terms(requested_terms)
            results_data = [
                _ontology_term_result(result)
                for requested in requested_terms
                if (result := result_map.get(requested)) is not None
            ]
            results_data, invalid_curie_count = _validate_curie_list(results_data)
            validation_warnings = [f"invalid_curie_prefixes:{invalid_curie_count}"] if invalid_curie_count > 0 else []
            return _lookup_response(
                method=method,
                data=results_data,
                count=len(results_data),
                warnings=validation_warnings,
                attempted_query=_attempt_query(method, terms=requested_terms),
            )

        # SEARCH ONTOLOGY TERMS
        elif method == "search_ontology_terms":
            if not term or not ontology_term_type:
                return _err(
                    "search_ontology_terms requires term and ontology_term_type",
                    method=method,
                    attempted_query=_attempt_query(
                        method,
                        term=term,
                        ontology_term_type=ontology_term_type,
                    ),
                )

            results = db.search_ontology_terms(
                term=term,
                ontology_type=ontology_term_type,
                exact_match=exact_match,
                include_synonyms=include_synonyms,
                limit=limit_value,
            )
            results_data = [
                _ontology_term_result(result)
                for result in results
            ]
            results_data, invalid_curie_count = _validate_curie_list(results_data)
            if invalid_curie_count > 0:
                warnings.append(f"invalid_curie_prefixes:{invalid_curie_count}")
            return _lookup_response(
                method=method,
                data=results_data,
                count=len(results_data),
                warnings=warnings,
                attempted_query=_attempt_query(
                    method,
                    term=term,
                    ontology_term_type=ontology_term_type,
                    exact_match=exact_match,
                    include_synonyms=include_synonyms,
                    limit=limit_value,
                ),
            )

        # CONTROLLED VOCABULARY TERM LOOKUP
        elif method == "get_vocabulary_term":
            vocabulary_query, query_field = _vocabulary_term_query(
                term=term,
                term_name=term_name,
                abbreviation=abbreviation,
                synonym=synonym,
            )
            attempted_query = _attempt_query(
                method,
                vocabulary=vocabulary,
                term=vocabulary_query,
                query_field=query_field,
                exact_match=True,
                include_synonyms=include_synonyms,
                include_obsolete=include_obsolete,
                limit=limit_value,
            )
            if not vocabulary or not vocabulary_query:
                return _err(
                    "get_vocabulary_term requires vocabulary and a term, term_name, abbreviation, or synonym query",
                    method=method,
                    attempted_query=attempted_query,
                )
            search_method = getattr(db, "search_vocabulary_terms", None)
            if not callable(search_method):
                return _err(
                    "AGR Curation API client does not expose search_vocabulary_terms in this runtime.",
                    method=method,
                    attempted_query=attempted_query,
                    failure_classification=LOOKUP_STATUS_UNDER_DEVELOPMENT,
                )

            results = search_method(
                term=vocabulary_query,
                vocabulary=vocabulary,
                exact_match=True,
                include_synonyms=include_synonyms,
                include_obsolete=include_obsolete,
                limit=limit_value,
            )
            results_data = [_vocabulary_term_result(result) for result in results]
            obsolete_count = sum(1 for result in results_data if result.get("obsolete"))
            if obsolete_count:
                warnings.append(f"obsolete_vocabulary_terms:{obsolete_count}")
            return _lookup_response(
                method=method,
                data=results_data,
                count=len(results_data),
                warnings=warnings,
                message=(
                    None
                    if results_data
                    else (
                        f"Vocabulary term not found in {vocabulary!r}: "
                        f"{vocabulary_query}"
                    )
                ),
                attempted_query=attempted_query,
                exact_lookup=True,
            )

        # CONTROLLED VOCABULARY TERM SEARCH
        elif method == "search_vocabulary_terms":
            vocabulary_query, query_field = _vocabulary_term_query(
                term=term,
                term_name=term_name,
                abbreviation=abbreviation,
                synonym=synonym,
            )
            attempted_query = _attempt_query(
                method,
                vocabulary=vocabulary,
                term=vocabulary_query,
                query_field=query_field,
                exact_match=exact_match,
                include_synonyms=include_synonyms,
                include_obsolete=include_obsolete,
                limit=limit_value,
            )
            if not vocabulary and not vocabulary_query:
                return _err(
                    "search_vocabulary_terms requires vocabulary and/or a term, term_name, abbreviation, or synonym query",
                    method=method,
                    attempted_query=attempted_query,
                )
            search_method = getattr(db, "search_vocabulary_terms", None)
            if not callable(search_method):
                return _err(
                    "AGR Curation API client does not expose search_vocabulary_terms in this runtime.",
                    method=method,
                    attempted_query=attempted_query,
                    failure_classification=LOOKUP_STATUS_UNDER_DEVELOPMENT,
                )

            results = search_method(
                term=vocabulary_query,
                vocabulary=vocabulary,
                exact_match=exact_match,
                include_synonyms=include_synonyms,
                include_obsolete=include_obsolete,
                limit=limit_value,
            )
            results_data = [_vocabulary_term_result(result) for result in results]
            obsolete_count = sum(1 for result in results_data if result.get("obsolete"))
            if obsolete_count:
                warnings.append(f"obsolete_vocabulary_terms:{obsolete_count}")
            return _lookup_response(
                method=method,
                data=results_data,
                count=len(results_data),
                warnings=warnings,
                attempted_query=attempted_query,
            )

        # MAP ENTITY NAMES TO CURIES
        elif method == "map_entity_names_to_curies":
            if not entity_type or not entity_names:
                return _err(
                    "map_entity_names_to_curies requires entity_type and entity_names",
                    method=method,
                    attempted_query=_attempt_query(
                        method,
                        entity_type=entity_type,
                        entity_names=entity_names,
                        taxon_id=taxon_id,
                        data_provider=data_provider,
                    ),
                )
            resolved_taxon = taxon_id
            if not resolved_taxon and data_provider:
                resolved_taxon = PROVIDER_TO_TAXON.get(data_provider)
                if not resolved_taxon:
                    return _err(
                        f"Unknown data_provider: {data_provider}",
                        method=method,
                        attempted_query=_attempt_query(
                            method,
                            entity_type=entity_type,
                            entity_names=entity_names,
                            data_provider=data_provider,
                        ),
                    )
            if not resolved_taxon:
                return _err(
                    "map_entity_names_to_curies requires taxon_id or data_provider",
                    method=method,
                    attempted_query=_attempt_query(
                        method,
                        entity_type=entity_type,
                        entity_names=entity_names,
                        taxon_id=taxon_id,
                        data_provider=data_provider,
                    ),
                )

            results = db.map_entity_names_to_curies(
                entity_type,
                entity_names,
                resolved_taxon,
            )
            results_data = [_entity_mapping_result(result) for result in results]
            results_data, invalid_curie_count = _validate_curie_list(results_data)
            validation_warnings = [f"invalid_curie_prefixes:{invalid_curie_count}"] if invalid_curie_count > 0 else []
            return _lookup_response(
                method=method,
                data=results_data,
                count=len(results_data),
                warnings=validation_warnings,
                attempted_query=_attempt_query(
                    method,
                    entity_type=entity_type,
                    entity_names=entity_names,
                    taxon_id=resolved_taxon,
                    data_provider=data_provider,
                ),
            )

        # MAP ENTITY CURIES TO INFO
        elif method == "map_entity_curies_to_info":
            requested_curies = entity_curies or curies or []
            if not entity_type or not requested_curies:
                return _err(
                    "map_entity_curies_to_info requires entity_type and entity_curies",
                    method=method,
                    attempted_query=_attempt_query(
                        method,
                        entity_type=entity_type,
                        entity_curies=requested_curies,
                    ),
                )

            results = db.map_entity_curies_to_info(
                entity_type=entity_type,
                entity_curies=requested_curies,
            )
            results_data = [_entity_mapping_result(result) for result in results]
            results_data, invalid_curie_count = _validate_curie_list(results_data)
            validation_warnings = [f"invalid_curie_prefixes:{invalid_curie_count}"] if invalid_curie_count > 0 else []
            return _lookup_response(
                method=method,
                data=results_data,
                count=len(results_data),
                warnings=validation_warnings,
                attempted_query=_attempt_query(
                    method,
                    entity_type=entity_type,
                    entity_curies=requested_curies,
                ),
            )

        # MAP CURIES TO NAMES
        elif method == "map_curies_to_names":
            if not category or not curies:
                return _err(
                    "map_curies_to_names requires category and curies",
                    method=method,
                    attempted_query=_attempt_query(method, category=category, curies=curies),
                )

            result_map = db.map_curies_to_names(category=category, curies=curies)
            results_data = [
                {"curie": curie, "name": name}
                for curie, name in result_map.items()
            ]
            results_data, invalid_curie_count = _validate_curie_list(results_data)
            validation_warnings = [f"invalid_curie_prefixes:{invalid_curie_count}"] if invalid_curie_count > 0 else []
            return _lookup_response(
                method=method,
                data=results_data,
                count=len(results_data),
                warnings=validation_warnings,
                attempted_query=_attempt_query(
                    method,
                    category=category,
                    curies=curies,
                ),
            )

        # ANATOMY TERMS SEARCH
        elif method == "search_anatomy_terms":
            if not term or not data_provider:
                return _err(
                    "search_anatomy_terms requires term and data_provider",
                    method=method,
                    attempted_query=_attempt_query(
                        method,
                        term=term,
                        data_provider=data_provider,
                    ),
                )

            results = db.search_anatomy_terms(
                term=term,
                data_provider=data_provider,
                exact_match=exact_match,
                include_synonyms=include_synonyms,
                limit=limit_value
            )
            results_data = [{"curie": r.curie, "name": r.name, "ontology_type": r.ontology_type} for r in results]
            results_data, invalid_curie_count = _validate_curie_list(results_data)
            validation_warnings = [f"invalid_curie_prefixes:{invalid_curie_count}"] if invalid_curie_count > 0 else []

            return _lookup_response(
                method=method,
                data=results_data,
                count=len(results_data),
                warnings=validation_warnings,
                attempted_query=_attempt_query(
                    method,
                    term=term,
                    data_provider=data_provider,
                    exact_match=exact_match,
                    include_synonyms=include_synonyms,
                    limit=limit_value,
                ),
            )

        # LIFE STAGE TERMS SEARCH
        elif method == "search_life_stage_terms":
            if not term or not data_provider:
                return _err(
                    "search_life_stage_terms requires term and data_provider",
                    method=method,
                    attempted_query=_attempt_query(
                        method,
                        term=term,
                        data_provider=data_provider,
                    ),
                )

            results = db.search_life_stage_terms(
                term=term,
                data_provider=data_provider,
                exact_match=exact_match,
                include_synonyms=include_synonyms,
                limit=limit_value
            )
            results_data = [{"curie": r.curie, "name": r.name, "ontology_type": r.ontology_type} for r in results]
            results_data, invalid_curie_count = _validate_curie_list(results_data)
            validation_warnings = [f"invalid_curie_prefixes:{invalid_curie_count}"] if invalid_curie_count > 0 else []

            return _lookup_response(
                method=method,
                data=results_data,
                count=len(results_data),
                warnings=validation_warnings,
                attempted_query=_attempt_query(
                    method,
                    term=term,
                    data_provider=data_provider,
                    exact_match=exact_match,
                    include_synonyms=include_synonyms,
                    limit=limit_value,
                ),
            )

        # GO TERMS SEARCH
        elif method == "search_go_terms":
            if not term:
                return _err(
                    "search_go_terms requires term",
                    method=method,
                    attempted_query=_attempt_query(method, term=term),
                )

            results = db.search_go_terms(
                term=term,
                go_aspect=go_aspect,
                exact_match=exact_match,
                include_synonyms=include_synonyms,
                limit=limit_value
            )
            results_data = [{"curie": r.curie, "name": r.name, "namespace": r.namespace} for r in results]
            results_data, invalid_curie_count = _validate_curie_list(results_data)
            validation_warnings = [f"invalid_curie_prefixes:{invalid_curie_count}"] if invalid_curie_count > 0 else []

            return _lookup_response(
                method=method,
                data=results_data,
                count=len(results_data),
                warnings=validation_warnings,
                attempted_query=_attempt_query(
                    method,
                    term=term,
                    go_aspect=go_aspect,
                    exact_match=exact_match,
                    include_synonyms=include_synonyms,
                    limit=limit_value,
                ),
            )

        else:
            return _err(
                "Unknown method: {method}. Valid: "
                "search_genes, search_genes_bulk, get_gene_by_exact_symbol, get_gene_by_id, "
                "search_alleles, search_alleles_bulk, get_allele_by_exact_symbol, get_allele_by_id, "
                "get_species, get_data_providers, get_data_provider, "
                "get_ontology_term, get_ontology_terms, search_ontology_terms, "
                "get_vocabulary_term, search_vocabulary_terms, "
                "search_anatomy_terms, "
                "search_life_stage_terms, search_go_terms, "
                "map_entity_names_to_curies, map_entity_curies_to_info, "
                "map_curies_to_names".format(method=method),
                method=method,
                attempted_query=_attempt_query(method),
            )

    except Exception as e:
        logger.error("AGR query error: %s", e, exc_info=True)
        return _err(
            f"Query error: {str(e)}",
            method=method,
            attempted_query=_transient_attempt_query(),
            failure_classification=LOOKUP_STATUS_TRANSIENT,
            error=e,
        )




def _unwrap_function_tool_callable(tool: Any, target_name: str) -> Any:
    """Extract original callable from a FunctionTool wrapper."""
    visited_ids = set()
    found: Optional[Any] = None

    def _walk(candidate: Any, depth: int = 0) -> None:
        nonlocal found
        if candidate is None or found is not None or depth > 6:
            return
        obj_id = id(candidate)
        if obj_id in visited_ids:
            return
        visited_ids.add(obj_id)

        if callable(candidate) and getattr(candidate, "__name__", "") == target_name:
            found = candidate
            return

        if callable(candidate):
            for cell in getattr(candidate, "__closure__", ()) or ():
                try:
                    _walk(cell.cell_contents, depth + 1)
                except Exception:
                    continue

        for attr in (
            "on_invoke_tool",
            "_invoke_tool_impl",
            "_function_tool",
            "func",
            "function",
            "_func",
            "_function",
            "handler",
        ):
            if not hasattr(candidate, attr):
                continue
            try:
                _walk(getattr(candidate, attr), depth + 1)
            except Exception:
                continue

        obj_dict = getattr(candidate, "__dict__", None)
        if isinstance(obj_dict, dict):
            for value in obj_dict.values():
                if callable(value) or hasattr(value, "__dict__"):
                    _walk(value, depth + 1)

    _walk(tool)
    if found is None:
        raise RuntimeError(f"Unable to locate callable for tool '{target_name}'")
    return found


_AGR_QUERY_CALLABLE = _unwrap_function_tool_callable(agr_curation_query, "agr_curation_query")


def _derive_agr_query_optional_arg_keys() -> Tuple[str, ...]:
    """Derive forwardable AGR query args from schema/signature (no hardcoded list)."""
    schema = getattr(agr_curation_query, "params_json_schema", {}) or {}
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    if isinstance(properties, dict) and properties:
        keys = tuple(str(k) for k in properties.keys() if str(k) != "method")
        if keys:
            return keys

    try:
        params = inspect.signature(_AGR_QUERY_CALLABLE).parameters
        keys = tuple(str(k) for k in params.keys() if str(k) != "method")
        if keys:
            return keys
    except Exception:
        pass

    return ()


_AGR_QUERY_OPTIONAL_ARG_KEYS = _derive_agr_query_optional_arg_keys()


@function_tool(strict_mode=False)
def agr_species_context_lookup(
    species: Optional[str] = None,
    data_provider: Optional[str] = None,
    provider_name: Optional[str] = None,
    taxon_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> AgrQueryResult:
    """Resolve only species/provider/taxon context for extraction agents.

    This intentionally exposes no gene-name, gene-ID, synonym, or generic entity
    lookup parameters. Gene identity resolution belongs to validator agents.
    """

    provider_query = provider_name
    if not provider_query and species:
        provider_query = species

    if data_provider or provider_query or taxon_id:
        return _AGR_QUERY_CALLABLE(
            method="get_data_provider",
            abbreviation=data_provider,
            provider_name=provider_query,
            taxon_id=taxon_id,
            limit=limit,
        )

    return _AGR_QUERY_CALLABLE(
        method="get_data_providers",
        limit=limit,
    )


def create_groq_agr_curation_query_tool():
    """Create Groq-compatible wrapper for AGR query tool.

    Groq enforces that every property listed in tool `properties` appears in
    `required`. To preserve optional AGR parameters, this wrapper accepts a
    compact required schema: `method` + `payload_json`.
    """

    @function_tool(
        name_override="agr_curation_query",
        description_override=(
            "Query AGR curation DB. Provide method and payload_json. "
            "payload_json must be a JSON object string containing any optional AGR args "
            "(gene_symbol, allele_symbol, data_provider, term, go_aspect, limit, etc)."
        ),
    )
    def agr_curation_query_groq(method: str, payload_json: str) -> AgrQueryResult:
        payload_raw = (payload_json or "").strip()
        if not payload_raw:
            payload: Dict[str, Any] = {}
        else:
            try:
                parsed = json.loads(payload_raw)
            except json.JSONDecodeError as exc:
                return _err(f"payload_json must be valid JSON object string: {exc}")
            if not isinstance(parsed, dict):
                return _err("payload_json must decode to a JSON object")
            payload = parsed

        forwarded_kwargs: Dict[str, Any] = {
            key: payload.get(key) for key in _AGR_QUERY_OPTIONAL_ARG_KEYS
        }
        return _AGR_QUERY_CALLABLE(method=method, **forwarded_kwargs)

    return agr_curation_query_groq
