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
    candidate_from_result as _candidate_from_result,
    create_db_session as _create_db_session,
    entity_detail_lookup_attempts as _entity_detail_lookup_attempts,
    fetch_allele_details_bulk as _fetch_allele_details_bulk,
    fetch_gene_details_bulk as _fetch_gene_details_bulk,
    lookup_attempt as _lookup_attempt,
    lookup_explanation as _lookup_explanation,
    lookup_response_payload as _lookup_response_payload,
    lookup_status_from_count as _lookup_status_from_count,
    projection_from_entity_match as _projection_from_entity_match,
    projection_from_result as _projection_from_result,
)
from src.lib.database.curation_resolver import get_curation_resolver
from src.lib.identifier_validation import is_valid_curie
from .search_helpers import (
    validate_search_symbol,
    enrich_with_match_context,
    check_force_parameters,
    log_validation_override,
)

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = int(os.getenv("AGR_DEFAULT_LIMIT", "100"))
HARD_MAX = int(os.getenv("AGR_HARD_MAX", "500"))


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
def _load_group_taxon_mappings() -> dict:
    """Build group-to-taxon mapping from config/groups.yaml."""
    from src.lib.config.groups_loader import list_groups
    mapping = {}
    for group in list_groups():
        if group.taxon:
            mapping[group.group_id] = group.taxon
    return mapping


_GROUP_MAPPING_LOAD_ERROR: Optional[str] = None
try:
    PROVIDER_TO_TAXON = _load_group_taxon_mappings()
except Exception as exc:
    _GROUP_MAPPING_LOAD_ERROR = str(exc)
    PROVIDER_TO_TAXON = {}
    logger.error("Failed to load group-to-taxon mappings: %s", exc)

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


def _normalize_allele_symbol_for_db(symbol: str) -> List[str]:
    """
    Normalize allele symbols for database search.

    The AGR database stores allele symbols with HTML superscript tags:
    - Database format: Arx<sup>tm1Gldn</sup>
    - Paper format: Arx<tm1Gldn>

    This function converts paper notation to database format.
    Returns list of search variants to try (original + normalized).
    """
    variants = [symbol]  # Always try original first

    # Convert angle brackets to HTML sup tags: Gene<allele> -> Gene<sup>allele</sup>
    angle_match = re.match(r'^([A-Za-z0-9]+)<([^>]+)>(.*)$', symbol)
    if angle_match:
        gene = angle_match.group(1)
        allele = angle_match.group(2)
        suffix = angle_match.group(3)
        # Add the database format with <sup> tags
        variants.append(f"{gene}<sup>{allele}</sup>{suffix}")
        # Also try without any brackets (concatenated)
        variants.append(f"{gene}{allele}{suffix}")

    return variants


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


def _validation_warning(
    message: str,
    *,
    method: Optional[str] = None,
    attempted_query: Optional[Dict[str, Any]] = None,
) -> AgrQueryResult:
    """Return a validation warning response (not an error, but search not executed)."""
    lookup_attempts = None
    if method is not None:
        query = attempted_query or _attempt_query(method)
        lookup_attempts = [
            _lookup_attempt(
                method=method,
                attempted_query=query,
                lookup_status=LOOKUP_STATUS_BLOCKED,
                explanation=message,
            )
        ]
    return AgrQueryResult(
        status="validation_warning",
        message=message,
        lookup_status=LOOKUP_STATUS_BLOCKED,
        failure_classification=LOOKUP_STATUS_BLOCKED,
        explanation=message,
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
    taxon_id: Optional[str] = None,
    term: Optional[str] = None,
    ontology_term_type: Optional[str] = None,
    go_aspect: Optional[str] = None,
    exact_match: bool = False,
    include_synonyms: bool = True,
    limit: Optional[int] = None,
    force: bool = False,
    force_reason: Optional[str] = None,
    validation_retry_context: Optional[Dict[str, Any]] = None
) -> AgrQueryResult:
    """
    Query the Alliance Genome Resources Curation Database.

    IMPORTANT - Symbol Validation:
    Gene and allele symbol searches (search_genes, search_alleles) are validated
    before execution. If the symbol contains patterns that suggest genotype notation
    (whitespace, fl/fl, +/+, -/-), the tool returns a validation_warning instead of
    searching. This prevents searches that will definitely fail.

    To handle validation_warning:
    1. Extract the base symbol (remove genotype notation)
    2. Retry with the cleaned symbol
    3. Only use force=True if you're certain the exact string should be searched

    Args:
        method: The query method (search_genes, search_genes_bulk, search_alleles, search_alleles_bulk, etc.)
        gene_symbol: Gene symbol to search for
        gene_symbols: List of gene symbols for bulk lookup methods
        gene_id: Gene ID/CURIE for direct lookup
        allele_symbol: Allele symbol to search for
        allele_symbols: List of allele symbols for bulk lookup methods
        allele_id: Allele ID/CURIE for direct lookup
        data_provider: Filter by species (MGI, FB, WB, ZFIN, RGD, SGD, HGNC)
        taxon_id: Alternative to data_provider (NCBITaxon:XXXXX format)
        term: Search term for ontology searches
        ontology_term_type: Optional curation DB ontologytermtype filter for get_ontology_term_by_curie
        go_aspect: GO aspect filter (molecular_function, biological_process, cellular_component)
        exact_match: Require exact match for ontology searches
        include_synonyms: Search synonyms in addition to primary symbols (default: True)
        limit: Maximum results to return
        force: Skip symbol validation (default: False). Requires force_reason.
        force_reason: Explanation for why validation is being skipped (required if force=True)
        validation_retry_context: Optional supervisor-owned context for bounded
            validator reruns, such as missing declared result projections.

    Returns:
        AgrQueryResult with status='ok', 'error', or 'validation_warning'
    """
    limit_value: Optional[int] = None

    def _transient_attempt_query() -> Dict[str, Any]:
        if method == "get_gene_by_id":
            return _attempt_query(method, gene_id=gene_id)
        if method == "get_allele_by_id":
            return _attempt_query(method, allele_id=allele_id)
        if method == "get_ontology_term_by_curie":
            return _attempt_query(
                method,
                term=term,
                ontology_term_type=ontology_term_type,
            )
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
                            if gene:
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

            # Validate symbol before searching (unless force=True)
            if not force:
                validation = validate_search_symbol(gene_symbol, 'gene')
                if not validation.is_valid:
                    return _validation_warning(
                        validation.warning_message,
                        method=method,
                        attempted_query=_attempt_query(
                            method,
                            gene_symbol=gene_symbol,
                            data_provider=data_provider,
                            include_synonyms=include_synonyms,
                        ),
                    )
            else:
                # Check force_reason is provided
                force_valid, force_error = check_force_parameters(force, force_reason)
                if not force_valid:
                    return _err(
                        force_error,
                        method=method,
                        attempted_query=_attempt_query(
                            method,
                            gene_symbol=gene_symbol,
                            force=force,
                        ),
                    )
                # Log the override for tracing
                log_validation_override(gene_symbol, 'gene', force_reason)

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

            if force:
                force_valid, force_error = check_force_parameters(force, force_reason)
                if not force_valid:
                    return _err(
                        force_error,
                        method=method,
                        attempted_query=_attempt_query(
                            method,
                            gene_symbols=normalized_symbols,
                            force=force,
                        ),
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
            validation_messages: Dict[str, str] = {}
            gene_curies_by_taxon: Dict[str, List[str]] = defaultdict(list)
            lookup_attempts_by_symbol: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

            for symbol in normalized_symbols:
                if not force:
                    validation = validate_search_symbol(symbol, 'gene')
                    if not validation.is_valid:
                        validation_messages[symbol] = validation.warning_message
                        lookup_attempts_by_symbol[symbol].append(
                            _lookup_attempt(
                                method=method,
                                attempted_query=_attempt_query(method, gene_symbol=symbol),
                                lookup_status=LOOKUP_STATUS_BLOCKED,
                                explanation=validation.warning_message,
                            )
                        )
                        continue
                else:
                    log_validation_override(symbol, 'gene', force_reason)

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
            total_matches = 0

            for symbol in normalized_symbols:
                if symbol in validation_messages:
                    bulk_items.append({
                        "input": symbol,
                        "status": "validation_warning",
                        "message": validation_messages[symbol],
                        "results": [],
                        "count": 0,
                        "lookup_status": LOOKUP_STATUS_BLOCKED,
                        "failure_classification": LOOKUP_STATUS_BLOCKED,
                        "explanation": validation_messages[symbol],
                        "lookup_attempts": lookup_attempts_by_symbol.get(symbol) or None,
                    })
                    continue

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

                total_matches += len(validated_data)
                item_lookup_status = _lookup_status_from_count(
                    len(validated_data),
                    exact_lookup=False,
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
                    "status": "ok",
                    "results": validated_data,
                    "count": len(validated_data),
                    "lookup_status": item_lookup_status,
                    "failure_classification": (
                        None
                        if item_lookup_status == LOOKUP_STATUS_SUCCESS
                        else item_lookup_status
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

            return _lookup_response(
                method=method,
                data={
                    "items": bulk_items,
                    "requested_count": len(normalized_symbols),
                    "total_matches": total_matches,
                    "method": "search_genes_bulk",
                },
                count=len(bulk_items),
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

            # Normalize allele symbol: convert Gene<allele> to Gene<sup>allele</sup>
            symbol_variants = _normalize_allele_symbol_for_db(allele_symbol)
            if len(symbol_variants) > 1:
                logger.info("Normalized allele symbol '%s' to variants: %s", allele_symbol, symbol_variants)

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

            # Validate symbol before searching (unless force=True)
            if not force:
                validation = validate_search_symbol(allele_symbol, 'allele')
                if not validation.is_valid:
                    return _validation_warning(
                        validation.warning_message,
                        method=method,
                        attempted_query=_attempt_query(
                            method,
                            allele_symbol=allele_symbol,
                            data_provider=data_provider,
                            include_synonyms=include_synonyms,
                        ),
                    )
            else:
                # Check force_reason is provided
                force_valid, force_error = check_force_parameters(force, force_reason)
                if not force_valid:
                    return _err(
                        force_error,
                        method=method,
                        attempted_query=_attempt_query(
                            method,
                            allele_symbol=allele_symbol,
                            force=force,
                        ),
                    )
                # Log the override for tracing
                log_validation_override(allele_symbol, 'allele', force_reason)

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
                try:
                    results = db.search_entities(
                        entity_type='allele',
                        search_pattern=allele_symbol,
                        taxon_curie=tid,
                        include_synonyms=include_synonyms,
                        limit=limit_value
                    )
                    lookup_attempts.append(
                        _lookup_attempt(
                            method=method,
                            attempted_query=_attempt_query(
                                method,
                                allele_symbol=allele_symbol,
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
                                f"Searched allele symbol {allele_symbol!r} in taxon {tid}; "
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
                            "matched_entity": result.get('entity', allele_symbol),
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
                                allele_symbol=allele_symbol,
                                taxon_id=tid,
                                data_provider=TAXON_TO_PROVIDER.get(tid),
                                include_synonyms=include_synonyms,
                                limit=limit_value,
                            ),
                            lookup_status=LOOKUP_STATUS_TRANSIENT,
                            explanation=(
                                f"Allele search for {allele_symbol!r} in taxon {tid} failed "
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

            if force:
                force_valid, force_error = check_force_parameters(force, force_reason)
                if not force_valid:
                    return _err(
                        force_error,
                        method=method,
                        attempted_query=_attempt_query(
                            method,
                            allele_symbols=normalized_symbols,
                            force=force,
                        ),
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
            validation_messages: Dict[str, str] = {}
            allele_curies_by_taxon: Dict[str, List[str]] = defaultdict(list)
            lookup_attempts_by_symbol: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

            for symbol in normalized_symbols:
                if not force:
                    validation = validate_search_symbol(symbol, 'allele')
                    if not validation.is_valid:
                        validation_messages[symbol] = validation.warning_message
                        lookup_attempts_by_symbol[symbol].append(
                            _lookup_attempt(
                                method=method,
                                attempted_query=_attempt_query(method, allele_symbol=symbol),
                                lookup_status=LOOKUP_STATUS_BLOCKED,
                                explanation=validation.warning_message,
                            )
                        )
                        continue
                else:
                    log_validation_override(symbol, 'allele', force_reason)

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
            total_matches = 0

            for symbol in normalized_symbols:
                if symbol in validation_messages:
                    bulk_items.append({
                        "input": symbol,
                        "status": "validation_warning",
                        "message": validation_messages[symbol],
                        "results": [],
                        "count": 0,
                        "lookup_status": LOOKUP_STATUS_BLOCKED,
                        "failure_classification": LOOKUP_STATUS_BLOCKED,
                        "explanation": validation_messages[symbol],
                        "lookup_attempts": lookup_attempts_by_symbol.get(symbol) or None,
                    })
                    continue

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

                total_matches += len(validated_data)
                item_lookup_status = _lookup_status_from_count(
                    len(validated_data),
                    exact_lookup=False,
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
                    "status": "ok",
                    "results": validated_data,
                    "count": len(validated_data),
                    "lookup_status": item_lookup_status,
                    "failure_classification": (
                        None
                        if item_lookup_status == LOOKUP_STATUS_SUCCESS
                        else item_lookup_status
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

            return _lookup_response(
                method=method,
                data={
                    "items": bulk_items,
                    "requested_count": len(normalized_symbols),
                    "total_matches": total_matches,
                    "method": "search_alleles_bulk",
                },
                count=len(bulk_items),
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
            providers = db.get_data_providers()
            providers_data = [{"abbreviation": abbr, "taxon_id": taxon} for abbr, taxon in providers]
            return _lookup_response(
                method=method,
                data=providers_data,
                count=len(providers_data),
                attempted_query=_attempt_query(method),
            )

        # GET ONTOLOGY TERM BY CURIE
        elif method == "get_ontology_term_by_curie":
            if not term:
                return _err(
                    "get_ontology_term_by_curie requires term",
                    method=method,
                    attempted_query=_attempt_query(
                        method,
                        term=term,
                        ontology_term_type=ontology_term_type,
                    ),
                )
            session = _create_db_session(db)
            if session is None:
                return _err(
                    "get_ontology_term_by_curie requires a curation DB client with SQL session support.",
                    method=method,
                    attempted_query=_attempt_query(
                        method,
                        term=term,
                        ontology_term_type=ontology_term_type,
                    ),
                    failure_classification=LOOKUP_STATUS_UNDER_DEVELOPMENT,
                )

            from sqlalchemy import text

            try:
                rows = session.execute(
                    text(
                        """
                        SELECT curie, name, ontologytermtype
                        FROM ontologyterm
                        WHERE curie = :curie
                          AND (:ontologytermtype IS NULL OR ontologytermtype = :ontologytermtype)
                        ORDER BY curie
                        """
                    ),
                    {
                        "curie": term,
                        "ontologytermtype": ontology_term_type,
                    },
                ).fetchall()
            finally:
                session.close()

            results_data = [
                {
                    "curie": row[0],
                    "name": row[1],
                    "ontology_type": row[2],
                }
                for row in rows
            ]
            results_data, invalid_curie_count = _validate_curie_list(results_data)
            validation_warnings = (
                [f"invalid_curie_prefixes:{invalid_curie_count}"]
                if invalid_curie_count > 0
                else []
            )
            return _lookup_response(
                method=method,
                data=results_data,
                count=len(results_data),
                warnings=validation_warnings,
                attempted_query=_attempt_query(
                    method,
                    term=term,
                    ontology_term_type=ontology_term_type,
                ),
                exact_lookup=True,
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
                "get_species, get_data_providers, "
                "get_ontology_term_by_curie, search_anatomy_terms, "
                "search_life_stage_terms, search_go_terms".format(method=method),
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
