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
import hashlib
from collections import defaultdict
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple, Literal, Annotated, Sequence

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, field_validator, model_validator
from agents import function_tool
import yaml

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
from agr_ai_curation_runtime.extraction_builder import (
    CANDIDATE_STATUS_VALID,
    ExtractionBuilderError,
    ExtractionBuilderValidationError,
    get_active_extraction_builder_workspace,
)
from agr_ai_curation_runtime.evidence_workspace import get_active_evidence_records_snapshot
from agr_ai_curation_runtime.extraction_trace_events import write_extraction_trace_event
from agr_ai_curation_runtime.resolver_call_ledger import (
    ResolverCallLedgerEntry,
    get_active_resolver_call_ledger,
)
from agr_ai_curation_alliance.domain_packs.gene_expression import (
    GENE_EXPRESSION_MATERIALIZER_ID,
    materialize_gene_expression_builder_state,
)
from .search_helpers import (
    enrich_with_match_context,
)
from agr_ai_curation_alliance.domain_packs.paths import get_alliance_domain_packs_dir

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


GENE_EXPRESSION_DOMAIN_PACK_ID = "agr.alliance.gene_expression"
GENE_EXPRESSION_OBJECT_TYPE = "GeneExpressionAnnotation"

GeneExpressionControlledFieldPath = Literal[
    "relation.name",
    "expression_experiment.expression_assay_used",
    "when_expressed_stage_name",
    "expression_pattern.when_expressed.developmental_stage_start",
    "expression_pattern.when_expressed.stage_uberon_slim_terms",
    "expression_pattern.where_expressed",
    "expression_pattern.where_expressed.anatomical_structure",
    "expression_pattern.where_expressed.anatomical_structure_uberon_terms",
    "expression_pattern.where_expressed.cellular_component",
    "expression_pattern.where_expressed.cellular_component_qualifiers",
]

GeneExpressionPatchFieldPath = Literal[
    "pending_ref_id",
    "evidence_record_ids",
    "where_expressed_statement",
    "subject.source_phrase",
    "subject.gene_symbol",
    "subject.primary_external_id",
    "reference.source_phrase",
    "reference.reference_id",
    "reference.curie",
    "reference.pmid",
    "reference.doi",
    "reference.title",
    "data_provider.abbreviation",
    "relation.name",
    "expression_experiment.expression_assay_used",
    "when_expressed_stage_name",
    "expression_pattern.when_expressed.developmental_stage_start",
    "expression_pattern.when_expressed.stage_uberon_slim_terms",
    "expression_pattern.where_expressed",
    "expression_pattern.where_expressed.anatomical_structure",
    "expression_pattern.where_expressed.anatomical_structure_uberon_terms",
    "expression_pattern.where_expressed.cellular_component",
    "expression_pattern.where_expressed.cellular_component_qualifiers",
]

_CONTROLLED_GENE_EXPRESSION_FIELD_PATHS = set(GeneExpressionControlledFieldPath.__args__)
_REFERENCE_PLACEHOLDER_VALUES = {
    "",
    "pmid",
    "pmid:",
    "pmid:12345678",
    "pmid12345678",
    "doi",
    "doi:",
    "wb:...",
    "...",
    "unknown",
    "tbd",
}


class _StrictToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GeneExpressionSubjectInput(_StrictToolModel):
    source_phrase: StrictStr
    gene_symbol: StrictStr
    primary_external_id: Optional[StrictStr]


class GeneExpressionReferenceInput(_StrictToolModel):
    source_phrase: StrictStr
    reference_id: StrictStr


class GeneExpressionControlledFieldInput(_StrictToolModel):
    field_path: GeneExpressionControlledFieldPath
    resolver_call_id: StrictStr
    selected_value: StrictStr


class GeneExpressionPatchUpdateInput(_StrictToolModel):
    field_path: GeneExpressionPatchFieldPath
    string_value: Optional[StrictStr]
    resolver_call_id: Optional[StrictStr]
    evidence_record_ids: Optional[List[StrictStr]] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _validate_update_shape(self) -> "GeneExpressionPatchUpdateInput":
        if self.field_path in _CONTROLLED_GENE_EXPRESSION_FIELD_PATHS:
            if not _clean_string(self.resolver_call_id):
                raise ValueError("controlled field patches require resolver_call_id")
            return self
        if self.field_path == "evidence_record_ids":
            if not self.evidence_record_ids:
                raise ValueError("evidence_record_ids patch requires evidence_record_ids")
            return self
        if not _clean_string(self.string_value):
            raise ValueError(f"{self.field_path} patch requires string_value")
        return self


class GeneExpressionStageInput(_StrictToolModel):
    pending_ref_id: StrictStr
    evidence_record_ids: List[StrictStr] = Field(min_length=1, max_length=20)
    where_expressed_statement: StrictStr
    subject: GeneExpressionSubjectInput
    reference: GeneExpressionReferenceInput
    controlled_fields: List[GeneExpressionControlledFieldInput] = Field(min_length=1, max_length=20)

    @field_validator("pending_ref_id", "where_expressed_statement")
    @classmethod
    def _non_empty_string(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must be non-empty")
        return cleaned


class GeneExpressionPatchInput(_StrictToolModel):
    candidate_id: StrictStr
    pending_ref_id: StrictStr
    updates: List[GeneExpressionPatchUpdateInput] = Field(min_length=1, max_length=25)


class GeneExpressionDiscardInput(_StrictToolModel):
    candidate_id: StrictStr
    reason: Optional[StrictStr]


class GeneExpressionListInput(_StrictToolModel):
    include_discarded: bool


class GeneExpressionFinalizeInput(_StrictToolModel):
    candidate_ids: List[StrictStr] = Field(min_length=1, max_length=50)


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
    taxon_curie: Optional[str],
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
                    taxon.curie AS taxon_curie,
                    symbol.displaytext AS matched_text,
                    'fuzzy_symbol' AS match_type,
                    similarity(lower(symbol.displaytext), lower(:search_pattern)) AS score
                FROM biologicalentity be
                JOIN allele a ON be.id = a.id
                LEFT JOIN ontologyterm taxon ON be.taxon_id = taxon.id
                JOIN slotannotation symbol ON a.id = symbol.singleallele_id
                    AND symbol.slotannotationtype = 'AlleleSymbolSlotAnnotation'
                    AND symbol.obsolete = false
                WHERE (:taxon_curie IS NULL OR taxon.curie = :taxon_curie)
                  AND symbol.displaytext IS NOT NULL

                UNION ALL

                SELECT
                    be.primaryexternalid AS entity_curie,
                    taxon.curie AS taxon_curie,
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
                  AND (:taxon_curie IS NULL OR taxon.curie = :taxon_curie)
                  AND synonym.displaytext IS NOT NULL
            ),
            ranked AS (
                SELECT
                    entity_curie,
                    taxon_curie,
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
            SELECT entity_curie, taxon_curie, matched_text, match_type, score
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
                "taxon_curie": row[1],
                "entity": row[2],
                "match_type": row[3],
                "score": float(row[4]) if row[4] is not None else None,
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
                        if data_provider and not results:
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
                            result_taxon = result.get("taxon_curie") or tid
                            if not result_taxon:
                                continue
                            pending_matches.append({
                                "curie": curie,
                                "taxon": result_taxon,
                                "matched_entity": result.get('entity', symbol_variant),
                                "match_type": result.get('match_type', 'unknown'),
                            })
                            allele_curies_by_taxon[result_taxon].append(curie)
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
            if not data_provider and not pending_matches:
                for symbol_variant in symbol_variants:
                    try:
                        results = _search_alleles_fuzzy_via_db(
                            db,
                            search_pattern=symbol_variant,
                            taxon_curie=None,
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
                                    include_synonyms=include_synonyms,
                                    limit=limit_value,
                                ),
                                lookup_status=(
                                    LOOKUP_STATUS_SUCCESS
                                    if results
                                    else LOOKUP_STATUS_NOT_FOUND
                                ),
                                explanation=(
                                    f"Searched allele symbol {symbol_variant!r} across all taxa; "
                                    f"the curation DB returned {len(results)} fuzzy candidate(s)."
                                ),
                                candidate_count=len(results),
                            )
                        )
                        for result in results:
                            curie = result.get('entity_curie')
                            result_taxon = result.get("taxon_curie")
                            if not curie or not result_taxon:
                                continue
                            pending_matches.append({
                                "curie": curie,
                                "taxon": result_taxon,
                                "matched_entity": result.get('entity', symbol_variant),
                                "match_type": result.get('match_type', 'unknown'),
                            })
                            allele_curies_by_taxon[result_taxon].append(curie)
                    except Exception as e:
                        logger.warning(
                            "Failed to fuzzy search alleles across all taxa: %s",
                            e,
                        )
                        lookup_attempts.append(
                            _lookup_attempt(
                                method=method,
                                attempted_query=_attempt_query(
                                    method,
                                    allele_symbol=symbol_variant,
                                    include_synonyms=include_synonyms,
                                    limit=limit_value,
                                ),
                                lookup_status=LOOKUP_STATUS_TRANSIENT,
                                explanation=(
                                    f"Allele search for {symbol_variant!r} across all taxa failed "
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
                        if data_provider and not results:
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
                            result_taxon = result.get("taxon_curie") or tid
                            if not result_taxon:
                                continue
                            symbol_matches.append({
                                "curie": curie,
                                "taxon": result_taxon,
                                "matched_entity": result.get('entity', symbol),
                                "match_type": result.get('match_type', 'unknown'),
                            })
                            allele_curies_by_taxon[result_taxon].append(curie)
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
                if not data_provider and not symbol_matches:
                    try:
                        results = _search_alleles_fuzzy_via_db(
                            db,
                            search_pattern=symbol,
                            taxon_curie=None,
                            include_synonyms=include_synonyms,
                            limit=limit_value,
                        )
                        lookup_attempts_by_symbol[symbol].append(
                            _lookup_attempt(
                                method=method,
                                attempted_query=_attempt_query(
                                    method,
                                    allele_symbol=symbol,
                                    include_synonyms=include_synonyms,
                                    limit=limit_value,
                                ),
                                lookup_status=(
                                    LOOKUP_STATUS_SUCCESS
                                    if results
                                    else LOOKUP_STATUS_NOT_FOUND
                                ),
                                explanation=(
                                    f"Searched allele symbol {symbol!r} across all taxa; "
                                    f"the curation DB returned {len(results)} fuzzy candidate(s)."
                                ),
                                candidate_count=len(results),
                            )
                        )
                        for result in results:
                            curie = result.get('entity_curie')
                            result_taxon = result.get("taxon_curie")
                            if not curie or not result_taxon:
                                continue
                            symbol_matches.append({
                                "curie": curie,
                                "taxon": result_taxon,
                                "matched_entity": result.get('entity', symbol),
                                "match_type": result.get('match_type', 'unknown'),
                            })
                            allele_curies_by_taxon[result_taxon].append(curie)
                    except Exception as e:
                        logger.warning(
                            "Failed to fuzzy search alleles in bulk for '%s' across all taxa: %s",
                            symbol,
                            e,
                        )
                        lookup_attempts_by_symbol[symbol].append(
                            _lookup_attempt(
                                method=method,
                                attempted_query=_attempt_query(
                                    method,
                                    allele_symbol=symbol,
                                    include_synonyms=include_synonyms,
                                    limit=limit_value,
                                ),
                                lookup_status=LOOKUP_STATUS_TRANSIENT,
                                explanation=(
                                    f"Allele search for {symbol!r} across all taxa failed "
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


def _load_domain_pack_data(domain_pack_id: str) -> Dict[str, Any] | None:
    for metadata_path in get_alliance_domain_packs_dir().glob("*/domain_pack.yaml"):
        loaded = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and loaded.get("pack_id") == domain_pack_id:
            return loaded
    return None


def _field_term_helper_policy(
    *,
    domain_pack_id: str,
    object_type: str,
    field_path: str,
) -> Dict[str, Any] | None:
    pack = _load_domain_pack_data(domain_pack_id)
    if not pack:
        return None
    normalized_field_path = field_path
    object_prefix = f"{object_type}."
    if normalized_field_path.startswith(object_prefix):
        normalized_field_path = normalized_field_path[len(object_prefix) :]
    normalized_field_path = normalized_field_path.removeprefix("payload.")

    for object_definition in pack.get("object_definitions", []) or []:
        if not isinstance(object_definition, dict):
            continue
        if object_definition.get("object_type") != object_type:
            continue
        for field in object_definition.get("fields", []) or []:
            if not isinstance(field, dict):
                continue
            if field.get("field_path") != normalized_field_path:
                continue
            metadata = field.get("metadata")
            if not isinstance(metadata, dict):
                return None
            policy = metadata.get("term_helper")
            return policy if isinstance(policy, dict) else None
    return None


def _helper_attempt(
    *,
    domain_pack_id: str,
    object_type: str,
    field_path: str,
    query: Optional[str],
    source_phrase: Optional[str],
    data_provider: Optional[str],
    taxon: Optional[str],
    limit: Optional[int],
    exact_match: bool,
) -> Dict[str, Any]:
    return _attempt_query(
        "get_domain_field_term_options",
        domain_pack_id=domain_pack_id,
        object_type=object_type,
        field_path=field_path,
        query=query,
        source_phrase=source_phrase,
        data_provider=data_provider,
        taxon=taxon,
        limit=limit,
        exact_match=exact_match,
    )


def _term_name(value: Mapping[str, Any]) -> Optional[str]:
    name = _first_present(value.get("name"), value.get("term_name"), value.get("label"))
    return str(name) if name is not None else None


def _controlled_vocabulary_helper_result(
    *,
    term: Mapping[str, Any],
    field_path: str,
    vocabulary: str,
    queried_at: str,
) -> Dict[str, Any]:
    name = _term_name(term)
    internal_id = _first_present(term.get("internal_id"), term.get("id"))
    source = {
        "provider": "alliance_curation_db",
        "tool": "agr_curation_query",
        "method": "search_vocabulary_terms",
    }
    return {
        "field_path": field_path,
        "value": name,
        "term_name": name,
        "vocabulary": vocabulary,
        "internal_id": internal_id,
        "source": source,
        "term_source": {
            "kind": "controlled_vocabulary",
            "vocabulary": vocabulary,
        },
        "helper_result": {
            "value": name,
            "name": name,
            "term_name": name,
            "vocabulary": vocabulary,
            "internal_id": internal_id,
            "abbreviation": term.get("abbreviation"),
            "synonyms": term.get("synonyms") or [],
            "obsolete": bool(term.get("obsolete", False)),
            "authority": "live_validated_option",
            "source": source,
        },
        "lookup": {
            "method": "search_vocabulary_terms",
            "queried_at": queried_at,
        },
    }


def _ontology_helper_result(
    *,
    term: Mapping[str, Any],
    field_path: str,
    slot_hint: str,
    term_source: Mapping[str, Any],
    lookup_method: str,
    source_phrase: str,
    queried_at: str,
    source: Optional[Mapping[str, Any]] = None,
    authority: str = "hint_only",
) -> Dict[str, Any]:
    label = _term_name(term)
    normalized_label = (label or "").casefold()
    normalized_source = source_phrase.strip().casefold()
    match_type = "exact_label" if normalized_label == normalized_source else "candidate"
    return {
        "source_phrase": source_phrase,
        "field_path": field_path,
        "slot_hint": slot_hint,
        "value": term.get("curie") or label,
        "term_name": label,
        "curie": term.get("curie"),
        "ontology_type": term.get("ontology_type"),
        "term_source": dict(term_source),
        "candidate": {
            "curie": term.get("curie"),
            "label": label,
            "name": label,
            "namespace": _first_present(term.get("namespace"), term.get("ontology_type")),
            "ontology_type": term.get("ontology_type"),
            "obsolete": bool(term.get("obsolete", False)),
            "authority": authority,
        },
        "lookup": {
            "method": lookup_method,
            "matched_value": label,
            "match_type": match_type,
            "queried_at": queried_at,
        },
        "source": dict(source)
        if source is not None
        else {
            "provider": "alliance_curation_db",
            "tool": "agr_curation_query",
            "method": lookup_method,
        },
    }


def _helper_match_status(helper_results: list[Dict[str, Any]]) -> str:
    if not helper_results:
        return "unresolved"
    if len(helper_results) == 1:
        return "resolved"
    return "ambiguous"


def _helper_lookup_status(helper_results: list[Dict[str, Any]]) -> str:
    if not helper_results:
        return LOOKUP_STATUS_NOT_FOUND
    if len(helper_results) == 1:
        return LOOKUP_STATUS_SUCCESS
    return LOOKUP_STATUS_AMBIGUOUS


def _helper_option(result: Mapping[str, Any]) -> Dict[str, Any]:
    source = result.get("source")
    option = {
        "field_path": result.get("field_path"),
        "value": result.get("value"),
        "term_name": result.get("term_name"),
        "curie": result.get("curie"),
        "internal_id": result.get("internal_id"),
        "vocabulary": result.get("vocabulary"),
        "ontology_type": result.get("ontology_type"),
        "slot_hint": result.get("slot_hint"),
        "source": source,
    }
    return {key: value for key, value in option.items() if value is not None}


def _resolver_metadata(policy: Mapping[str, Any]) -> Dict[str, Any]:
    """Return domain-pack resolver config with conservative defaults."""

    configured = policy.get("resolver")
    resolver = dict(configured) if isinstance(configured, Mapping) else {}
    resolver.setdefault("primary_tool", "resolve_domain_field_term")
    resolver.setdefault("search_tool", "search_domain_field_terms")
    resolver.setdefault("inspect_tool", "inspect_ontology_term")
    resolver.setdefault("accepted_provenance_tools", ["resolve_domain_field_term"])
    resolver.setdefault("unresolved_metadata_path", "metadata.normalization_notes")
    resolver.setdefault(
        "search_channels",
        [
            "exact_label",
            "exact_synonym",
            "configured_mapping",
            "current_api_label_or_synonym_search",
        ],
    )
    return resolver


def _resolver_policy_response(
    *,
    domain_pack_id: str,
    object_type: str,
    field_path: str,
    attempted_query: Dict[str, Any],
) -> Tuple[Optional[Mapping[str, Any]], Optional[Mapping[str, Any]], Optional[AgrQueryResult]]:
    policy = _field_term_helper_policy(
        domain_pack_id=domain_pack_id,
        object_type=object_type,
        field_path=field_path,
    )
    if policy is None:
        return (
            None,
            None,
            _err(
                "No domain-pack term helper policy is declared for this field.",
                method=attempted_query.get("method") or "domain_field_resolver",
                attempted_query=attempted_query,
                failure_classification=LOOKUP_STATUS_NOT_FOUND,
            ),
        )
    term_source = policy.get("term_source")
    if not isinstance(term_source, Mapping):
        return (
            policy,
            None,
            _err(
                "Domain-pack term helper policy is missing term_source metadata.",
                method=attempted_query.get("method") or "domain_field_resolver",
                attempted_query=attempted_query,
                failure_classification=LOOKUP_STATUS_BLOCKED,
            ),
        )
    return policy, term_source, None


def _resolver_candidate_from_helper_result(
    result: Mapping[str, Any],
    *,
    source_tool: str,
    source_phrase: Optional[str],
    index: int,
) -> Dict[str, Any]:
    candidate = result.get("candidate")
    helper_result = result.get("helper_result")
    source = result.get("source")
    lookup = result.get("lookup") if isinstance(result.get("lookup"), Mapping) else {}
    if not isinstance(candidate, Mapping):
        candidate = {}
    if not isinstance(helper_result, Mapping):
        helper_result = {}
    label = _first_present(
        result.get("term_name"),
        candidate.get("name"),
        candidate.get("label"),
        helper_result.get("term_name"),
        helper_result.get("name"),
    )
    value = _first_present(result.get("value"), candidate.get("curie"), label)
    curie = _first_present(result.get("curie"), candidate.get("curie"))
    vocabulary = _first_present(result.get("vocabulary"), helper_result.get("vocabulary"))
    matched_string = _first_present(
        lookup.get("matched_value"),
        label,
        source_phrase,
    )
    matched_field = "label" if label and matched_string == label else "candidate"
    match_mode = str(lookup.get("match_type") or "candidate")
    score = 1.0 if match_mode in {"exact_label", "exact_synonym"} else max(0.0, 0.85 - (index * 0.05))
    source_provider = source.get("provider") if isinstance(source, Mapping) else None
    normalized = {
        "candidate_id": f"candidate-{index + 1}",
        "field_path": result.get("field_path"),
        "slot_hint": result.get("slot_hint"),
        "value": value,
        "curie": curie,
        "name": label,
        "term_name": label,
        "vocabulary": vocabulary,
        "internal_id": _first_present(result.get("internal_id"), helper_result.get("internal_id")),
        "ontology_type": _first_present(result.get("ontology_type"), candidate.get("ontology_type")),
        "namespace": candidate.get("namespace"),
        "definition": candidate.get("definition"),
        "term_source": dict(result.get("term_source"))
        if isinstance(result.get("term_source"), Mapping)
        else None,
        "obsolete": bool(_first_present(candidate.get("obsolete"), helper_result.get("obsolete"), False)),
        "matched_string": matched_string,
        "matched_field": matched_field,
        "match_mode": match_mode,
        "score": round(score, 3),
        "score_breakdown": {
            "authority": candidate.get("authority") or helper_result.get("authority"),
            "rank": index + 1,
            "backend": source_provider or "alliance_curation_db_current_search",
        },
        "path_hints": [
            hint
            for hint in (
                result.get("slot_hint"),
                result.get("field_path"),
            )
            if hint
        ],
        "source": dict(source) if isinstance(source, Mapping) else None,
        "source_tool": source_tool,
    }
    return {key: value for key, value in normalized.items() if value is not None}


def _candidate_policy_blocker(
    *,
    term_source: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> Optional[str]:
    """Return a blocker code when a candidate violates field policy."""

    ontology_family = str(term_source.get("ontology_family") or "").strip().casefold()
    candidate_curie = str(candidate.get("curie") or candidate.get("value") or "").strip()
    namespace = str(candidate.get("namespace") or "").strip()
    namespace_key = namespace.casefold()
    ontology_type = str(candidate.get("ontology_type") or "").strip()
    expected_type = str(term_source.get("ontology_term_type") or "").strip()

    if expected_type and ontology_type != expected_type:
        return "candidate_ontology_type_mismatch"

    if ontology_family == "go":
        if candidate_curie and not candidate_curie.upper().startswith("GO:"):
            return "candidate_ontology_family_mismatch"
        go_aspect = str(term_source.get("go_aspect") or "").strip()
        if go_aspect:
            if not namespace:
                return "candidate_go_aspect_unavailable"
            if namespace_key != go_aspect.casefold():
                return "candidate_go_aspect_mismatch"
    elif ontology_family == "uberon":
        if candidate_curie and not candidate_curie.upper().startswith("UBERON:"):
            return "candidate_ontology_family_mismatch"
    elif ontology_family in {"anatomy", "life_stage", "assay"}:
        if candidate_curie.upper().startswith("GO:"):
            return "candidate_ontology_family_mismatch"

    slim_membership = term_source.get("slim_membership")
    if isinstance(slim_membership, Mapping):
        allowed_curies = slim_membership.get("allowed_term_curies")
        if isinstance(allowed_curies, list):
            allowed = {str(curie) for curie in allowed_curies if curie is not None}
            if allowed and candidate_curie not in allowed:
                return "candidate_not_in_allowed_slim_terms"
    return None


def _direct_resolution_context(
    *,
    domain_pack_id: str,
    object_type: str,
    field_path: str,
    policy: Mapping[str, Any],
    term_source: Mapping[str, Any],
    candidate: Mapping[str, Any],
    attempted_query: Mapping[str, Any],
) -> Tuple[str, Mapping[str, Any], Mapping[str, Any], Optional[AgrQueryResult]]:
    """Resolve broad routing fields to their concrete selector slot."""

    if term_source.get("kind") != "anatomical_site":
        return field_path, policy, term_source, None

    slot_hint = candidate.get("slot_hint")
    if not isinstance(slot_hint, str) or not slot_hint.strip():
        return (
            field_path,
            policy,
            term_source,
            _err(
                "Anatomical-site resolver candidates must include a concrete slot_hint.",
                method="resolve_domain_field_term",
                attempted_query=dict(attempted_query),
                failure_classification=LOOKUP_STATUS_BLOCKED,
            ),
        )

    direct_policy = _field_term_helper_policy(
        domain_pack_id=domain_pack_id,
        object_type=object_type,
        field_path=slot_hint,
    )
    direct_term_source = (
        direct_policy.get("term_source")
        if isinstance(direct_policy, Mapping)
        else None
    )
    if not isinstance(direct_policy, Mapping) or not isinstance(direct_term_source, Mapping):
        return (
            field_path,
            policy,
            term_source,
            _err(
                f"Anatomical-site slot_hint {slot_hint!r} is not backed by a domain-pack resolver policy.",
                method="resolve_domain_field_term",
                attempted_query=dict(attempted_query),
                failure_classification=LOOKUP_STATUS_BLOCKED,
            ),
        )

    return slot_hint, direct_policy, direct_term_source, None


def _resolver_candidates_from_helper_payload(
    payload: Mapping[str, Any],
    *,
    source_tool: str,
    source_phrase: Optional[str],
) -> List[Dict[str, Any]]:
    helper_results = payload.get("helper_results")
    if not isinstance(helper_results, list):
        return []
    return [
        _resolver_candidate_from_helper_result(
            result,
            source_tool=source_tool,
            source_phrase=source_phrase,
            index=index,
        )
        for index, result in enumerate(helper_results)
        if isinstance(result, Mapping)
    ]


def _resolver_instruction(
    *,
    resolution_status: str,
    field_path: str,
    source_phrase: Optional[str],
    resolver: Mapping[str, Any],
    candidate: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    if resolution_status == "resolved":
        return [
            f"Set only the controlled selector for {field_path} from the selected candidate.",
            "Pass this resolve_domain_field_term tool call ID as resolver_call_id to the builder tool; do not author metadata.provenance.helper_selections.",
        ]
    if resolution_status == "ambiguous":
        return [
            f"Do not set {field_path} yet.",
            "Use the suggested next_tool_call, resolve with an explicit candidate_curie or candidate_value, or search with a narrower evidence phrase.",
        ]
    if resolution_status == "blocked":
        return [
            f"Do not set {field_path}.",
            "Preserve the paper phrase in unresolved metadata and let validation report the blocker.",
        ]
    if candidate and candidate.get("obsolete"):
        return [
            f"Do not use obsolete candidate for {field_path}.",
            "Search for a replacement or preserve the unresolved paper phrase.",
        ]
    phrase = source_phrase or "the paper phrase"
    return [
        f"Do not set {field_path} from memory.",
        f"Preserve {phrase!r} in unresolved metadata and let validation report the unresolved selector.",
    ]


def _resolver_debug_payload(
    *,
    stage: str,
    status: str,
    field_path: str,
    term_source: Mapping[str, Any],
    policy: Mapping[str, Any],
    lookup_attempts: Optional[List[Dict[str, Any]]] = None,
    candidate_count: Optional[int] = None,
    selected_candidate: Optional[Mapping[str, Any]] = None,
    policy_blocker: Optional[str] = None,
    context_counts: Optional[Mapping[str, int]] = None,
    requested_field_path: Optional[str] = None,
) -> Dict[str, Any]:
    lookup = policy.get("lookup") if isinstance(policy.get("lookup"), Mapping) else {}
    resolver = _resolver_metadata(policy)
    slim_membership = term_source.get("slim_membership")
    allowed_term_count = None
    if isinstance(slim_membership, Mapping):
        allowed_terms = slim_membership.get("allowed_term_curies")
        if isinstance(allowed_terms, list):
            allowed_term_count = len(allowed_terms)

    selected = selected_candidate or {}
    debug = {
        "resolver_stage": stage,
        "status": status,
        "field_path": field_path,
        "requested_field_path": requested_field_path,
        "term_source_kind": term_source.get("kind"),
        "ontology_family": term_source.get("ontology_family"),
        "ontology_term_type": term_source.get("ontology_term_type"),
        "go_aspect": term_source.get("go_aspect"),
        "vocabulary": term_source.get("vocabulary"),
        "authority": policy.get("authority"),
        "lookup_method": lookup.get("method"),
        "provider_required": lookup.get("provider_required"),
        "search_channels": resolver.get("search_channels"),
        "accepted_provenance_tools": resolver.get("accepted_provenance_tools"),
        "candidate_count": candidate_count,
        "selected_candidate_id": selected.get("candidate_id"),
        "selected_curie": selected.get("curie"),
        "selected_value": selected.get("value"),
        "selected_name": selected.get("name") or selected.get("term_name"),
        "slot_hint": selected.get("slot_hint"),
        "policy_blocker": policy_blocker,
        "slim_allowed_term_count": allowed_term_count,
        "lookup_attempt_count": len(lookup_attempts or []),
        "lookup_methods": [
            str(attempt.get("method"))
            for attempt in (lookup_attempts or [])
            if isinstance(attempt, Mapping) and attempt.get("method")
        ],
        "context_counts": dict(context_counts) if context_counts else None,
    }
    return {key: value for key, value in debug.items() if value is not None}


def _resolver_diagnostic_summary(
    *,
    stage: str,
    status: str,
    field_path: str,
    source_phrase: Optional[str],
    candidate_count: Optional[int] = None,
    selected_candidate: Optional[Mapping[str, Any]] = None,
    policy_blocker: Optional[str] = None,
    requested_field_path: Optional[str] = None,
) -> str:
    label = source_phrase or "(no source phrase)"
    routed = (
        f"; requested {requested_field_path}"
        if requested_field_path and requested_field_path != field_path
        else ""
    )
    count = f"; candidates={candidate_count}" if candidate_count is not None else ""
    selected = ""
    if selected_candidate:
        selected_value = (
            selected_candidate.get("curie")
            or selected_candidate.get("value")
            or selected_candidate.get("name")
            or selected_candidate.get("term_name")
        )
        if selected_value:
            selected = f"; selected={selected_value}"
    blocker = f"; blocker={policy_blocker}" if policy_blocker else ""
    return (
        f"{stage} {status} for {field_path}{routed}: "
        f"source_phrase={label!r}{count}{selected}{blocker}"
    )


def _payload_field_instructions(
    *,
    field_path: str,
    candidate: Mapping[str, Any],
    term_source: Mapping[str, Any],
) -> Dict[str, Any]:
    if term_source.get("kind") == "controlled_vocabulary":
        return {
            "set": [
                {
                    "field_path": field_path,
                    "value": candidate.get("term_name") or candidate.get("name") or candidate.get("value"),
                }
            ]
        }
    curie = candidate.get("curie") or candidate.get("value")
    name = candidate.get("name") or candidate.get("term_name")
    if field_path.endswith("_terms") or field_path.endswith("_qualifiers"):
        return {
            "append": [
                {
                    "field_path": field_path,
                    "value": {
                        "curie": curie,
                        "name": name,
                    },
                }
            ]
        }
    if field_path.endswith("_name"):
        return {
            "set": [
                {
                    "field_path": field_path,
                    "value": name or curie,
                }
            ]
        }
    return {
        "set": [
            {"field_path": f"{field_path}.curie", "value": curie},
            {"field_path": f"{field_path}.name", "value": name},
        ]
    }


def _resolver_helper_selection(
    *,
    field_path: str,
    source_phrase: Optional[str],
    candidate: Mapping[str, Any],
    term_source: Mapping[str, Any],
    policy: Mapping[str, Any],
    evidence_context: Mapping[str, Any],
    resolved_at: str,
) -> Dict[str, Any]:
    selected_value = (
        candidate.get("term_name")
        if term_source.get("kind") == "controlled_vocabulary"
        else candidate.get("curie") or candidate.get("value")
    )
    selected_name = candidate.get("name") or candidate.get("term_name")
    selection = {
        "field_path": field_path,
        "source_tool": "resolve_domain_field_term",
        "source_phrase": source_phrase,
        "selected_value": selected_value,
        "selected_name": selected_name,
        "selected_curie": candidate.get("curie"),
        "selected_internal_id": candidate.get("internal_id"),
        "vocabulary": candidate.get("vocabulary"),
        "ontology_type": candidate.get("ontology_type"),
        "slot_hint": candidate.get("slot_hint"),
        "lookup_status": LOOKUP_STATUS_SUCCESS,
        "authority": policy.get("authority") or "selector_evidence",
        "term_source": dict(term_source),
        "source": candidate.get("source"),
        "resolved_at": resolved_at,
        "evidence_context": dict(evidence_context) if evidence_context else None,
    }
    return {key: value for key, value in selection.items() if value is not None}


def _ontology_lookup_result(
    *,
    lookup_method: str,
    normalized_phrase: str,
    term_source: Mapping[str, Any],
    data_provider: Optional[str],
    exact_match: bool,
    limit_value: int,
) -> AgrQueryResult:
    # term_source is the canonical field-scoped selector metadata. lookup declares
    # which package-owned method may be called, but must not override term filters.
    if lookup_method == "search_anatomy_terms":
        return _AGR_QUERY_CALLABLE(
            method="search_anatomy_terms",
            term=normalized_phrase,
            data_provider=data_provider,
            exact_match=exact_match,
            include_synonyms=True,
            limit=limit_value,
        )
    if lookup_method == "search_life_stage_terms":
        return _AGR_QUERY_CALLABLE(
            method="search_life_stage_terms",
            term=normalized_phrase,
            data_provider=data_provider,
            exact_match=exact_match,
            include_synonyms=True,
            limit=limit_value,
        )
    if lookup_method == "search_go_terms":
        go_aspect = term_source.get("go_aspect")
        if not isinstance(go_aspect, str) or not go_aspect.strip():
            return _err(
                "Ontology helper term_source metadata requires go_aspect for search_go_terms.",
                method="get_domain_field_term_options",
                failure_classification=LOOKUP_STATUS_BLOCKED,
            )
        return _AGR_QUERY_CALLABLE(
            method="search_go_terms",
            term=normalized_phrase,
            go_aspect=go_aspect,
            exact_match=exact_match,
            include_synonyms=True,
            limit=limit_value,
        )
    if lookup_method == "search_ontology_terms":
        ontology_term_type = term_source.get("ontology_term_type")
        if not isinstance(ontology_term_type, str) or not ontology_term_type.strip():
            return _err(
                "Ontology helper term_source metadata requires ontology_term_type for search_ontology_terms.",
                method="get_domain_field_term_options",
                failure_classification=LOOKUP_STATUS_BLOCKED,
            )
        return _AGR_QUERY_CALLABLE(
            method="search_ontology_terms",
            term=normalized_phrase,
            ontology_term_type=ontology_term_type,
            exact_match=exact_match,
            include_synonyms=True,
            limit=limit_value,
        )
    return _err(
        f"Unsupported ontology helper lookup method: {lookup_method}",
        method="get_domain_field_term_options",
        failure_classification=LOOKUP_STATUS_UNDER_DEVELOPMENT,
    )


def _configured_ontology_mapping_results(
    *,
    policy: Mapping[str, Any],
    normalized_phrase: str,
    field_path: str,
    term_source: Mapping[str, Any],
    lookup_method: str,
    queried_at: str,
) -> list[Dict[str, Any]]:
    mappings = policy.get("configured_mappings")
    if not isinstance(mappings, list):
        return []

    phrase_key = normalized_phrase.strip().casefold()
    helper_results: list[Dict[str, Any]] = []
    for index, mapping in enumerate(mappings):
        if not isinstance(mapping, Mapping):
            continue
        labels = mapping.get("labels")
        if isinstance(labels, str):
            label_values = [labels]
        elif isinstance(labels, list):
            label_values = [label for label in labels if isinstance(label, str)]
        else:
            label_values = []
        if phrase_key not in {label.strip().casefold() for label in label_values}:
            continue

        candidate = mapping.get("candidate")
        if not isinstance(candidate, Mapping):
            continue
        source = {
            "provider": "domain_pack_config",
            "tool": "get_domain_field_term_options",
            "method": "configured_label_mapping",
            "mapping_index": index,
        }
        mapping_id = mapping.get("mapping_id")
        if isinstance(mapping_id, str) and mapping_id.strip():
            source["mapping_id"] = mapping_id
        helper_results.append(
            _ontology_helper_result(
                term=candidate,
                field_path=field_path,
                slot_hint=field_path,
                term_source=term_source,
                lookup_method=lookup_method,
                source_phrase=normalized_phrase,
                queried_at=queried_at,
                source=source,
                authority="configured_mapping",
            )
        )
    return helper_results


@function_tool(strict_mode=False)
def get_domain_field_term_options(
    domain_pack_id: str,
    object_type: str,
    field_path: str,
    query: Optional[str] = None,
    source_phrase: Optional[str] = None,
    evidence_context: Optional[Dict[str, Any]] = None,
    data_provider: Optional[str] = None,
    taxon: Optional[str] = None,
    limit: Optional[int] = None,
    exact_match: bool = False,
) -> AgrQueryResult:
    """Return extractor-facing term options declared by domain-pack field metadata.

    Helper results are selector guidance for extraction. Validator and
    materializer bindings remain the authority for final accepted IDs.
    """

    evidence_context = evidence_context or {}
    phrase = source_phrase or query
    if phrase is None:
        phrase_value = (
            evidence_context.get("source_phrase")
            or evidence_context.get("query")
        )
        phrase = str(phrase_value) if phrase_value is not None else None
    normalized_phrase = phrase.strip() if isinstance(phrase, str) else None
    normalized_query = query.strip() if isinstance(query, str) and query.strip() else None
    limit_value = limit or 25
    queried_at = datetime.now(timezone.utc).isoformat()
    attempted_query = _helper_attempt(
        domain_pack_id=domain_pack_id,
        object_type=object_type,
        field_path=field_path,
        query=normalized_query,
        source_phrase=normalized_phrase,
        data_provider=data_provider,
        taxon=taxon,
        limit=limit_value,
        exact_match=exact_match,
    )

    policy = _field_term_helper_policy(
        domain_pack_id=domain_pack_id,
        object_type=object_type,
        field_path=field_path,
    )
    if policy is None:
        return _err(
            "No domain-pack term helper policy is declared for this field.",
            method="get_domain_field_term_options",
            attempted_query=attempted_query,
            failure_classification=LOOKUP_STATUS_NOT_FOUND,
        )

    term_source = policy.get("term_source")
    if not isinstance(term_source, dict):
        return _err(
            "Domain-pack term helper policy is missing term_source metadata.",
            method="get_domain_field_term_options",
            attempted_query=attempted_query,
            failure_classification=LOOKUP_STATUS_BLOCKED,
        )

    helper_results: list[Dict[str, Any]] = []
    lookup_attempts: list[Dict[str, Any]] = []
    warnings: list[str] = []

    if term_source.get("kind") == "controlled_vocabulary":
        vocabulary = term_source.get("vocabulary")
        if not isinstance(vocabulary, str) or not vocabulary.strip():
            return _err(
                "Controlled-vocabulary helper policy requires a vocabulary.",
                method="get_domain_field_term_options",
                attempted_query=attempted_query,
                failure_classification=LOOKUP_STATUS_BLOCKED,
            )
        result = _AGR_QUERY_CALLABLE(
            method="search_vocabulary_terms",
            vocabulary=vocabulary,
            term=normalized_phrase,
            exact_match=exact_match,
            include_obsolete=False,
            limit=limit_value,
        )
        lookup_attempts.extend(result.lookup_attempts or [])
        if result.status != "ok":
            return result
        for term in result.data or []:
            if isinstance(term, Mapping):
                helper_results.append(
                    _controlled_vocabulary_helper_result(
                        term=term,
                        field_path=field_path,
                        vocabulary=vocabulary,
                        queried_at=queried_at,
                    )
                )
        data = {
            "domain_pack_id": domain_pack_id,
            "object_type": object_type,
            "field_path": field_path,
            "source_phrase": normalized_phrase,
            "term_source": dict(term_source),
            "helper_results": helper_results,
            "options": [_helper_option(item) for item in helper_results],
            "match_status": _helper_match_status(helper_results),
            "authority": "helper_guidance",
        }
        return AgrQueryResult(
            status="ok",
            data=data,
            count=len(helper_results),
            warnings=result.warnings,
            message=result.message,
            lookup_status=_helper_lookup_status(helper_results),
            failure_classification=None if helper_results else LOOKUP_STATUS_NOT_FOUND,
            lookup_attempts=lookup_attempts,
        )

    if term_source.get("kind") == "ontology":
        if not normalized_phrase:
            return _err(
                f"{field_path} helper requires query or source_phrase.",
                method="get_domain_field_term_options",
                attempted_query=attempted_query,
            )
        lookup = policy.get("lookup")
        if not isinstance(lookup, Mapping):
            return _err(
                "Ontology helper policy requires lookup metadata.",
                method="get_domain_field_term_options",
                attempted_query=attempted_query,
                failure_classification=LOOKUP_STATUS_BLOCKED,
            )
        lookup_method = lookup.get("method")
        if not isinstance(lookup_method, str):
            return _err(
                "Ontology helper policy requires a package lookup method.",
                method="get_domain_field_term_options",
                attempted_query=attempted_query,
                failure_classification=LOOKUP_STATUS_BLOCKED,
            )
        configured_results = _configured_ontology_mapping_results(
            policy=policy,
            normalized_phrase=normalized_phrase,
            field_path=field_path,
            term_source=term_source,
            lookup_method=lookup_method,
            queried_at=queried_at,
        )
        if configured_results:
            helper_results.extend(configured_results)
            result = None
        elif lookup.get("provider_required") and not data_provider:
            warnings.append(f"{lookup_method}_skipped:data_provider_required")
            result = None
        else:
            result = _ontology_lookup_result(
                lookup_method=lookup_method,
                normalized_phrase=normalized_phrase,
                term_source=term_source,
                data_provider=data_provider,
                exact_match=exact_match,
                limit_value=limit_value,
            )
        if result is not None:
            lookup_attempts.extend(result.lookup_attempts or [])
            if result.status != "ok":
                return result
            warnings.extend(result.warnings or [])
            for term in result.data or []:
                if isinstance(term, Mapping):
                    helper_results.append(
                        _ontology_helper_result(
                            term=term,
                            field_path=field_path,
                            slot_hint=field_path,
                            term_source=term_source,
                            lookup_method=lookup_method,
                            source_phrase=normalized_phrase,
                            queried_at=queried_at,
                        )
                    )
        data = {
            "domain_pack_id": domain_pack_id,
            "object_type": object_type,
            "field_path": field_path,
            "source_phrase": normalized_phrase,
            "term_source": dict(term_source),
            "helper_results": helper_results,
            "options": [_helper_option(item) for item in helper_results],
            "match_status": _helper_match_status(helper_results),
            "authority": "helper_guidance",
        }
        return AgrQueryResult(
            status="ok",
            data=data,
            count=len(helper_results),
            warnings=warnings or None,
            lookup_status=_helper_lookup_status(helper_results),
            failure_classification=None if helper_results else LOOKUP_STATUS_NOT_FOUND,
            lookup_attempts=lookup_attempts or [attempted_query],
        )

    if term_source.get("kind") == "anatomical_site":
        if not normalized_phrase:
            return _err(
                "expression_pattern.where_expressed helper requires source_phrase.",
                method="get_domain_field_term_options",
                attempted_query=attempted_query,
            )
        routing = policy.get("site_routing")
        candidates = (
            routing.get("candidates")
            if isinstance(routing, Mapping)
            else None
        )
        if not isinstance(candidates, list):
            return _err(
                "Anatomical-site helper policy requires site_routing candidates.",
                method="get_domain_field_term_options",
                attempted_query=attempted_query,
                failure_classification=LOOKUP_STATUS_BLOCKED,
            )
        for candidate_policy in candidates:
            if not isinstance(candidate_policy, Mapping):
                continue
            slot_hint = candidate_policy.get("slot_hint")
            lookup = candidate_policy.get("lookup")
            candidate_source = candidate_policy.get("term_source")
            if not (
                isinstance(slot_hint, str)
                and isinstance(lookup, Mapping)
                and isinstance(candidate_source, Mapping)
            ):
                continue
            lookup_method = lookup.get("method")
            if lookup_method == "search_anatomy_terms":
                if not data_provider:
                    warnings.append("anatomy_lookup_skipped:data_provider_required")
                    continue
                result = _AGR_QUERY_CALLABLE(
                    method="search_anatomy_terms",
                    term=normalized_phrase,
                    data_provider=data_provider,
                    exact_match=exact_match,
                    include_synonyms=True,
                    limit=limit_value,
                )
            elif lookup_method == "search_go_terms":
                result = _AGR_QUERY_CALLABLE(
                    method="search_go_terms",
                    term=normalized_phrase,
                    go_aspect=candidate_source.get("go_aspect"),
                    exact_match=exact_match,
                    include_synonyms=True,
                    limit=limit_value,
                )
            else:
                warnings.append(f"unsupported_lookup_method:{lookup_method}")
                continue
            lookup_attempts.extend(result.lookup_attempts or [])
            if result.status != "ok":
                warnings.extend(result.warnings or [])
                if result.message:
                    warnings.append(f"{lookup_method}:{result.message}")
                continue
            warnings.extend(result.warnings or [])
            for term in result.data or []:
                if isinstance(term, Mapping):
                    helper_results.append(
                        _ontology_helper_result(
                            term=term,
                            field_path=field_path,
                            slot_hint=slot_hint,
                            term_source=candidate_source,
                            lookup_method=str(lookup_method),
                            source_phrase=normalized_phrase,
                            queried_at=queried_at,
                        )
                    )
        data = {
            "domain_pack_id": domain_pack_id,
            "object_type": object_type,
            "field_path": field_path,
            "source_phrase": normalized_phrase,
            "term_source": dict(term_source),
            "helper_results": helper_results,
            "options": [_helper_option(item) for item in helper_results],
            "match_status": _helper_match_status(helper_results),
            "required_any": (
                routing.get("required_any") if isinstance(routing, Mapping) else None
            ),
            "authority": "helper_guidance",
        }
        return AgrQueryResult(
            status="ok",
            data=data,
            count=len(helper_results),
            warnings=warnings or None,
            lookup_status=_helper_lookup_status(helper_results),
            failure_classification=None if helper_results else LOOKUP_STATUS_NOT_FOUND,
            lookup_attempts=lookup_attempts or [attempted_query],
        )

    return _err(
        f"Unsupported term helper kind: {term_source.get('kind')}",
        method="get_domain_field_term_options",
        attempted_query=attempted_query,
        failure_classification=LOOKUP_STATUS_UNDER_DEVELOPMENT,
    )


@function_tool(strict_mode=False)
def search_domain_field_terms(
    domain_pack_id: str,
    object_type: str,
    field_path: str,
    query: str,
    evidence_context: Optional[Dict[str, Any]] = None,
    data_provider: Optional[str] = None,
    taxon: Optional[str] = None,
    branch_root_curie: Optional[str] = None,
    limit: Optional[int] = None,
    exact_match: bool = False,
) -> AgrQueryResult:
    """Search domain-pack declared ontology/CV candidates for one field.

    This is broad candidate discovery only. It never accepts a controlled field
    value; call resolve_domain_field_term before writing final selectors.
    """

    evidence_context = evidence_context or {}
    normalized_query = query.strip() if isinstance(query, str) and query.strip() else None
    limit_value = limit or 10
    attempted_query = _attempt_query(
        "search_domain_field_terms",
        domain_pack_id=domain_pack_id,
        object_type=object_type,
        field_path=field_path,
        query=normalized_query,
        data_provider=data_provider,
        taxon=taxon,
        branch_root_curie=branch_root_curie,
        limit=limit_value,
        exact_match=exact_match,
    )
    if not normalized_query:
        return _err(
            "search_domain_field_terms requires a non-empty query.",
            method="search_domain_field_terms",
            attempted_query=attempted_query,
        )

    policy, term_source, policy_error = _resolver_policy_response(
        domain_pack_id=domain_pack_id,
        object_type=object_type,
        field_path=field_path,
        attempted_query=attempted_query,
    )
    if policy_error is not None:
        return policy_error
    assert policy is not None and term_source is not None
    resolver = _resolver_metadata(policy)

    helper_callable = _unwrap_function_tool_callable(
        get_domain_field_term_options,
        "get_domain_field_term_options",
    )
    result = helper_callable(
        domain_pack_id=domain_pack_id,
        object_type=object_type,
        field_path=field_path,
        query=normalized_query,
        source_phrase=normalized_query,
        evidence_context=evidence_context,
        data_provider=data_provider,
        taxon=taxon,
        limit=limit_value,
        exact_match=exact_match,
    )
    if result.status != "ok":
        return result

    payload = result.data if isinstance(result.data, Mapping) else {}
    candidates = _resolver_candidates_from_helper_payload(
        payload,
        source_tool="search_domain_field_terms",
        source_phrase=normalized_query,
    )
    lookup_status = _helper_lookup_status(candidates)
    warnings = list(result.warnings or [])
    warnings.append("limited_search_backend:current_api_exact_prefix_contains")
    if branch_root_curie:
        warnings.append("branch_root_curie_preserved_for_future_filtering")
    if "vector_recall" in resolver.get("search_channels", []):
        warnings.append("vector_recall_not_authoritative")

    next_tool_call: Dict[str, Any]
    if len(candidates) == 1:
        next_field_path = candidates[0].get("slot_hint") or field_path
        next_tool_call = {
            "tool": "resolve_domain_field_term",
            "arguments": {
                "domain_pack_id": domain_pack_id,
                "object_type": object_type,
                "field_path": next_field_path,
                "source_phrase": normalized_query,
                "candidate_curie": candidates[0].get("curie"),
                "candidate_value": candidates[0].get("value"),
                "data_provider": data_provider,
                "taxon": taxon,
            },
        }
    elif candidates and term_source.get("kind") != "controlled_vocabulary":
        next_tool_call = {
            "tool": "inspect_ontology_term",
            "arguments": {
                "domain_pack_id": domain_pack_id,
                "object_type": object_type,
                "field_path": candidates[0].get("slot_hint") or field_path,
                "curie": candidates[0].get("curie"),
                "data_provider": data_provider,
                "include_parents": True,
                "include_children": True,
                "include_siblings": True,
                "max_depth": 1,
                "limit": 10,
            },
        }
    elif candidates:
        next_tool_call = {
            "tool": "search_domain_field_terms",
            "arguments": {
                "domain_pack_id": domain_pack_id,
                "object_type": object_type,
                "field_path": field_path,
                "query": normalized_query,
                "data_provider": data_provider,
                "taxon": taxon,
                "limit": limit_value,
            },
            "note": "Controlled-vocabulary ambiguity cannot be inspected as ontology; narrow the query or resolve with a chosen candidate_value.",
        }
    else:
        next_tool_call = {
            "tool": "search_domain_field_terms",
            "arguments": {
                "domain_pack_id": domain_pack_id,
                "object_type": object_type,
                "field_path": field_path,
                "query": normalized_query,
                "data_provider": data_provider,
                "taxon": taxon,
                "limit": limit_value,
            },
        }

    lookup_attempts = result.lookup_attempts or [attempted_query]
    resolution_status = (
        LOOKUP_STATUS_SUCCESS
        if len(candidates) == 1
        else LOOKUP_STATUS_AMBIGUOUS
        if candidates
        else LOOKUP_STATUS_NOT_FOUND
    )
    data = {
        "domain_pack_id": domain_pack_id,
        "object_type": object_type,
        "field_path": field_path,
        "query": normalized_query,
        "evidence_context": evidence_context,
        "term_source": dict(term_source),
        "resolver": resolver,
        "candidates": candidates,
        "lookup_attempts": lookup_attempts,
        "next_tool_call": next_tool_call,
        "instructions": _resolver_instruction(
            resolution_status="ambiguous" if len(candidates) > 1 else "unresolved",
            field_path=field_path,
            source_phrase=normalized_query,
            resolver=resolver,
        )
        if len(candidates) != 1
        else [
            "Candidate discovery found one candidate; call resolve_domain_field_term before setting the payload field.",
        ],
        "diagnostic_summary": _resolver_diagnostic_summary(
            stage="search",
            status=resolution_status,
            field_path=field_path,
            source_phrase=normalized_query,
            candidate_count=len(candidates),
            selected_candidate=candidates[0] if len(candidates) == 1 else None,
        ),
        "debug": _resolver_debug_payload(
            stage="search",
            status=resolution_status,
            field_path=field_path,
            term_source=term_source,
            policy=policy,
            lookup_attempts=lookup_attempts,
            candidate_count=len(candidates),
            selected_candidate=candidates[0] if len(candidates) == 1 else None,
        ),
        "authority": "candidate_discovery_only",
    }
    return AgrQueryResult(
        status="ok",
        data=data,
        count=len(candidates),
        warnings=warnings,
        lookup_status=lookup_status,
        failure_classification=None if candidates else LOOKUP_STATUS_NOT_FOUND,
        lookup_attempts=lookup_attempts,
        candidate_matches=[
            _candidate_from_result("search_domain_field_terms", candidate)
            for candidate in candidates
        ],
    )


def _ontology_tree_rows(
    *,
    db: Any,
    curie: str,
    curie_prefix: str,
    relation: str,
    limit: int,
) -> List[Dict[str, Any]]:
    create_session = getattr(db, "create_session", None)
    if callable(create_session):
        session_rows = _ontology_tree_rows_from_session(
            create_session=create_session,
            curie=curie,
            curie_prefix=curie_prefix,
            relation=relation,
            limit=limit,
        )
        if session_rows is not None:
            return session_rows

    pairs_method = getattr(db, "get_ontology_pairs", None)
    if not callable(pairs_method):
        return []
    rows: List[Dict[str, Any]] = []
    for pair in pairs_method(curie_prefix):
        if not isinstance(pair, Mapping):
            continue
        if relation == "parents" and pair.get("child_curie") == curie:
            rows.append(
                {
                    "relation": "parent",
                    "curie": pair.get("parent_curie"),
                    "name": pair.get("parent_name"),
                    "namespace": pair.get("parent_type"),
                    "obsolete": pair.get("parent_is_obsolete"),
                }
            )
        elif relation == "children" and pair.get("parent_curie") == curie:
            rows.append(
                {
                    "relation": "child",
                    "curie": pair.get("child_curie"),
                    "name": pair.get("child_name"),
                    "namespace": pair.get("child_type"),
                    "obsolete": pair.get("child_is_obsolete"),
                }
            )
        if len(rows) >= limit:
            break
    return rows


def _ontology_tree_rows_from_session(
    *,
    create_session: Any,
    curie: str,
    curie_prefix: str,
    relation: str,
    limit: int,
) -> Optional[List[Dict[str, Any]]]:
    """Return bounded ontology context without scanning an entire ontology."""

    try:
        from sqlalchemy import text
    except ImportError:
        return None

    if relation == "parents":
        where_clause = "otc.curie = :curie"
        context_prefix_clause = "otp.curie LIKE :curieprefix"
        select_columns = """
            'parent' AS relation,
            otp.curie AS contextCurie,
            otp.name AS contextName,
            otp.namespace AS contextType,
            otp.obsolete AS contextIsObsolete
        """
    elif relation == "children":
        where_clause = "otp.curie = :curie"
        context_prefix_clause = "otc.curie LIKE :curieprefix"
        select_columns = """
            'child' AS relation,
            otc.curie AS contextCurie,
            otc.name AS contextName,
            otc.namespace AS contextType,
            otc.obsolete AS contextIsObsolete
        """
    else:
        return []

    session = create_session()
    try:
        sql_query = text(
            f"""
            SELECT DISTINCT
                {select_columns}
            FROM
                ontologyterm otc
                JOIN ontologytermclosure otpc ON otc.id = otpc.closuresubject_id
                JOIN ontologyterm otp ON otpc.closureobject_id = otp.id
            WHERE
                {where_clause}
                AND {context_prefix_clause}
                AND otpc.distance = 1
                AND otpc.closuretypes in ('["part_of"]', '["is_a"]')
            LIMIT :limit
            """
        )
        rows = session.execute(
            sql_query,
            {
                "curie": curie,
                "curieprefix": f"{curie_prefix}%",
                "limit": limit,
            },
        ).fetchall()
        return [
            {
                "relation": row[0],
                "curie": row[1],
                "name": row[2],
                "namespace": row[3],
                "obsolete": row[4],
            }
            for row in rows
        ]
    except Exception as exc:
        logger.warning(
            "Targeted ontology tree lookup failed for %s relation=%s: %s",
            curie,
            relation,
            exc,
        )
        return None
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


@function_tool(strict_mode=False)
def inspect_ontology_term(
    domain_pack_id: str,
    object_type: str,
    field_path: str,
    curie: str,
    data_provider: Optional[str] = None,
    include_parents: bool = True,
    include_children: bool = True,
    include_siblings: bool = False,
    max_depth: int = 1,
    limit: Optional[int] = None,
) -> AgrQueryResult:
    """Inspect one authoritative ontology term and bounded graph context."""

    normalized_curie = curie.strip() if isinstance(curie, str) and curie.strip() else None
    limit_value = limit or 25
    attempted_query = _attempt_query(
        "inspect_ontology_term",
        domain_pack_id=domain_pack_id,
        object_type=object_type,
        field_path=field_path,
        curie=normalized_curie,
        data_provider=data_provider,
        include_parents=include_parents,
        include_children=include_children,
        include_siblings=include_siblings,
        max_depth=max_depth,
        limit=limit_value,
    )
    if not normalized_curie:
        return _err(
            "inspect_ontology_term requires a CURIE.",
            method="inspect_ontology_term",
            attempted_query=attempted_query,
        )

    policy, term_source, policy_error = _resolver_policy_response(
        domain_pack_id=domain_pack_id,
        object_type=object_type,
        field_path=field_path,
        attempted_query=attempted_query,
    )
    if policy_error is not None:
        return policy_error
    assert policy is not None and term_source is not None
    if term_source.get("kind") not in {"ontology", "anatomical_site"}:
        return _err(
            "inspect_ontology_term can only inspect ontology-backed fields.",
            method="inspect_ontology_term",
            attempted_query=attempted_query,
            failure_classification=LOOKUP_STATUS_BLOCKED,
        )

    lookup_result = _AGR_QUERY_CALLABLE(
        method="get_ontology_term",
        term=normalized_curie,
        ontology_term_type=term_source.get("ontology_term_type"),
    )
    if lookup_result.status != "ok":
        return lookup_result
    lookup_attempts = lookup_result.lookup_attempts or [attempted_query]
    term = lookup_result.data if isinstance(lookup_result.data, Mapping) else None
    if not term:
        return AgrQueryResult(
            status="unresolved",
            data={
                "domain_pack_id": domain_pack_id,
                "object_type": object_type,
                "field_path": field_path,
                "curie": normalized_curie,
                "term_source": dict(term_source),
                "instructions": _resolver_instruction(
                    resolution_status="unresolved",
                    field_path=field_path,
                    source_phrase=normalized_curie,
                    resolver=_resolver_metadata(policy),
                ),
                "diagnostic_summary": _resolver_diagnostic_summary(
                    stage="inspect",
                    status=LOOKUP_STATUS_NOT_FOUND,
                    field_path=field_path,
                    source_phrase=normalized_curie,
                    candidate_count=0,
                ),
                "debug": _resolver_debug_payload(
                    stage="inspect",
                    status=LOOKUP_STATUS_NOT_FOUND,
                    field_path=field_path,
                    term_source=term_source,
                    policy=policy,
                    lookup_attempts=lookup_attempts,
                    candidate_count=0,
                ),
            },
            count=0,
            message=f"Ontology term not found: {normalized_curie}",
            lookup_status=LOOKUP_STATUS_NOT_FOUND,
            failure_classification=LOOKUP_STATUS_NOT_FOUND,
            lookup_attempts=lookup_attempts,
        )

    warnings = list(lookup_result.warnings or [])
    parents: List[Dict[str, Any]] = []
    children: List[Dict[str, Any]] = []
    siblings: List[Dict[str, Any]] = []
    db = get_curation_resolver().get_db_client()
    curie_prefix = normalized_curie.split(":", 1)[0]
    if db is None:
        warnings.append("ontology_tree_context_unavailable:curation_db_unavailable")
    else:
        if include_parents:
            parents = _ontology_tree_rows(
                db=db,
                curie=normalized_curie,
                curie_prefix=curie_prefix,
                relation="parents",
                limit=limit_value,
            )
        if include_children:
            children = _ontology_tree_rows(
                db=db,
                curie=normalized_curie,
                curie_prefix=curie_prefix,
                relation="children",
                limit=limit_value,
            )
        if include_siblings and parents:
            sibling_seen: set[str] = set()
            for parent in parents[:max(1, max_depth)]:
                parent_curie = parent.get("curie")
                if not isinstance(parent_curie, str):
                    continue
                for row in _ontology_tree_rows(
                    db=db,
                    curie=parent_curie,
                    curie_prefix=curie_prefix,
                    relation="children",
                    limit=limit_value,
                ):
                    row_curie = row.get("curie")
                    if row_curie == normalized_curie or not isinstance(row_curie, str):
                        continue
                    if row_curie in sibling_seen:
                        continue
                    sibling_seen.add(row_curie)
                    row["relation"] = "sibling"
                    siblings.append(row)
                    if len(siblings) >= limit_value:
                        break
        if not parents and not children and not siblings:
            warnings.append("ontology_tree_context_unavailable:api_client_has_no_bounded_neighbors")

    expected_type = term_source.get("ontology_term_type")
    policy_blocker = _candidate_policy_blocker(
        term_source=term_source,
        candidate=term,
    )
    policy_checks = {
        "ontology_type_matches": (
            True
            if not expected_type
            else term.get("ontology_type") == expected_type
        ),
        "ontology_family_matches": policy_blocker
        not in {
            "candidate_ontology_family_mismatch",
            "candidate_ontology_type_mismatch",
        },
        "go_aspect_matches": policy_blocker
        not in {
            "candidate_go_aspect_mismatch",
            "candidate_go_aspect_unavailable",
        },
        "allowed_by_slim_membership": policy_blocker != "candidate_not_in_allowed_slim_terms",
        "go_aspect": term_source.get("go_aspect"),
        "ontology_family": term_source.get("ontology_family"),
    }
    resolver = _resolver_metadata(policy)
    should_use = (
        policy_checks["ontology_type_matches"]
        and policy_checks["ontology_family_matches"]
        and policy_checks["go_aspect_matches"]
        and policy_checks["allowed_by_slim_membership"]
        and not term.get("obsolete")
    )
    inspect_status = LOOKUP_STATUS_SUCCESS if should_use else LOOKUP_STATUS_BLOCKED
    policy_blocker = None if should_use else policy_blocker
    context_counts = {
        "parents": len(parents),
        "children": len(children),
        "siblings": len(siblings),
    }
    data = {
        "domain_pack_id": domain_pack_id,
        "object_type": object_type,
        "field_path": field_path,
        "curie": normalized_curie,
        "term": term,
        "term_source": dict(term_source),
        "policy_checks": policy_checks,
        "context": {
            "parents": parents,
            "children": children,
            "siblings": siblings,
            "max_depth": max_depth,
            "limit": limit_value,
        },
        "instructions": (
            [
                "This term passes field policy checks; call resolve_domain_field_term before setting the payload field.",
            ]
            if should_use
            else _resolver_instruction(
                resolution_status="unresolved",
                field_path=field_path,
                source_phrase=normalized_curie,
                resolver=resolver,
                candidate=term,
            )
        ),
        "next_tool_call": {
            "tool": "resolve_domain_field_term",
            "arguments": {
                "domain_pack_id": domain_pack_id,
                "object_type": object_type,
                "field_path": field_path,
                "source_phrase": term.get("name") or normalized_curie,
                "candidate_curie": normalized_curie,
                "data_provider": data_provider,
            },
        }
        if should_use
        else None,
        "diagnostic_summary": _resolver_diagnostic_summary(
            stage="inspect",
            status=inspect_status,
            field_path=field_path,
            source_phrase=term.get("name") or normalized_curie,
            candidate_count=1,
            selected_candidate=term,
            policy_blocker=policy_blocker,
        ),
        "debug": _resolver_debug_payload(
            stage="inspect",
            status=inspect_status,
            field_path=field_path,
            term_source=term_source,
            policy=policy,
            lookup_attempts=lookup_attempts,
            candidate_count=1,
            selected_candidate=term,
            policy_blocker=policy_blocker,
            context_counts=context_counts,
        ),
        "authority": "inspection_only",
    }
    return AgrQueryResult(
        status="ok",
        data=data,
        count=1,
        warnings=warnings or None,
        lookup_status=inspect_status,
        failure_classification=None if should_use else LOOKUP_STATUS_BLOCKED,
        lookup_attempts=lookup_attempts,
    )


@function_tool(strict_mode=False)
def resolve_domain_field_term(
    domain_pack_id: str,
    object_type: str,
    field_path: str,
    source_phrase: str,
    evidence_context: Optional[Dict[str, Any]] = None,
    candidate_curie: Optional[str] = None,
    candidate_value: Optional[str] = None,
    data_provider: Optional[str] = None,
    taxon: Optional[str] = None,
    limit: Optional[int] = None,
) -> AgrQueryResult:
    """Resolve one final controlled selector for a domain-pack field."""

    evidence_context = evidence_context or {}
    normalized_phrase = (
        source_phrase.strip()
        if isinstance(source_phrase, str) and source_phrase.strip()
        else None
    )
    normalized_candidate_curie = (
        candidate_curie.strip()
        if isinstance(candidate_curie, str) and candidate_curie.strip()
        else None
    )
    normalized_candidate_value = (
        candidate_value.strip()
        if isinstance(candidate_value, str) and candidate_value.strip()
        else None
    )
    limit_value = limit or 10
    attempted_query = _attempt_query(
        "resolve_domain_field_term",
        domain_pack_id=domain_pack_id,
        object_type=object_type,
        field_path=field_path,
        source_phrase=normalized_phrase,
        candidate_curie=normalized_candidate_curie,
        candidate_value=normalized_candidate_value,
        data_provider=data_provider,
        taxon=taxon,
        limit=limit_value,
    )
    if not normalized_phrase:
        return _err(
            "resolve_domain_field_term requires source_phrase.",
            method="resolve_domain_field_term",
            attempted_query=attempted_query,
        )

    policy, term_source, policy_error = _resolver_policy_response(
        domain_pack_id=domain_pack_id,
        object_type=object_type,
        field_path=field_path,
        attempted_query=attempted_query,
    )
    if policy_error is not None:
        return policy_error
    assert policy is not None and term_source is not None
    resolver = _resolver_metadata(policy)

    search_callable = _unwrap_function_tool_callable(
        search_domain_field_terms,
        "search_domain_field_terms",
    )
    search_result = search_callable(
        domain_pack_id=domain_pack_id,
        object_type=object_type,
        field_path=field_path,
        query=normalized_phrase,
        evidence_context=evidence_context,
        data_provider=data_provider,
        taxon=taxon,
        limit=limit_value,
    )
    if search_result.status != "ok":
        lookup_attempts = search_result.lookup_attempts or [attempted_query]
        return AgrQueryResult(
            status="blocked",
            data={
                "domain_pack_id": domain_pack_id,
                "object_type": object_type,
                "field_path": field_path,
                "source_phrase": normalized_phrase,
                "term_source": dict(term_source),
                "instructions": _resolver_instruction(
                    resolution_status="blocked",
                    field_path=field_path,
                    source_phrase=normalized_phrase,
                    resolver=resolver,
                ),
                "unresolved_metadata_path": resolver.get("unresolved_metadata_path"),
                "diagnostic_summary": _resolver_diagnostic_summary(
                    stage="resolve",
                    status=LOOKUP_STATUS_BLOCKED,
                    field_path=field_path,
                    source_phrase=normalized_phrase,
                    candidate_count=0,
                ),
                "debug": _resolver_debug_payload(
                    stage="resolve",
                    status=LOOKUP_STATUS_BLOCKED,
                    field_path=field_path,
                    term_source=term_source,
                    policy=policy,
                    lookup_attempts=lookup_attempts,
                    candidate_count=0,
                ),
            },
            message=search_result.message,
            warnings=search_result.warnings,
            lookup_status=search_result.lookup_status,
            failure_classification=LOOKUP_STATUS_BLOCKED,
            lookup_attempts=lookup_attempts,
        )

    search_payload = search_result.data if isinstance(search_result.data, Mapping) else {}
    candidates = [
        candidate
        for candidate in search_payload.get("candidates", [])
        if isinstance(candidate, Mapping)
    ]
    lookup_attempts = search_result.lookup_attempts or [attempted_query]

    selected_candidates: List[Mapping[str, Any]] = []
    if normalized_candidate_curie:
        selected_candidates = [
            candidate
            for candidate in candidates
            if candidate.get("curie") == normalized_candidate_curie
        ]
    elif normalized_candidate_value:
        selected_candidates = [
            candidate
            for candidate in candidates
            if normalized_candidate_value
            in {
                str(value)
                for value in (
                    candidate.get("value"),
                    candidate.get("name"),
                    candidate.get("term_name"),
                    candidate.get("internal_id"),
                )
                if value is not None
            }
        ]
    else:
        selected_candidates = list(candidates)

    if len(selected_candidates) != 1:
        resolution_status = "ambiguous" if selected_candidates or candidates else "unresolved"
        selected_or_all = selected_candidates or candidates
        lookup_status = (
            LOOKUP_STATUS_AMBIGUOUS
            if resolution_status == "ambiguous"
            else LOOKUP_STATUS_NOT_FOUND
        )
        return AgrQueryResult(
            status=resolution_status,
            data={
                "domain_pack_id": domain_pack_id,
                "object_type": object_type,
                "field_path": field_path,
                "source_phrase": normalized_phrase,
                "term_source": dict(term_source),
                "candidates": selected_candidates or candidates,
                "instructions": _resolver_instruction(
                    resolution_status=resolution_status,
                    field_path=field_path,
                    source_phrase=normalized_phrase,
                    resolver=resolver,
                ),
                "next_tool_call": search_payload.get("next_tool_call"),
                "unresolved_metadata_path": resolver.get("unresolved_metadata_path"),
                "diagnostic_summary": _resolver_diagnostic_summary(
                    stage="resolve",
                    status=lookup_status,
                    field_path=field_path,
                    source_phrase=normalized_phrase,
                    candidate_count=len(selected_or_all),
                ),
                "debug": _resolver_debug_payload(
                    stage="resolve",
                    status=lookup_status,
                    field_path=field_path,
                    term_source=term_source,
                    policy=policy,
                    lookup_attempts=lookup_attempts,
                    candidate_count=len(selected_or_all),
                ),
            },
            count=len(selected_or_all),
            warnings=search_result.warnings,
            lookup_status=lookup_status,
            failure_classification=resolution_status,
            lookup_attempts=lookup_attempts,
            candidate_matches=[
                _candidate_from_result("resolve_domain_field_term", candidate)
                for candidate in selected_or_all
            ],
        )

    selected = selected_candidates[0]
    if selected.get("obsolete"):
        return AgrQueryResult(
            status="blocked",
            data={
                "domain_pack_id": domain_pack_id,
                "object_type": object_type,
                "field_path": field_path,
                "source_phrase": normalized_phrase,
                "selected_candidate": dict(selected),
                "instructions": _resolver_instruction(
                    resolution_status="blocked",
                    field_path=field_path,
                    source_phrase=normalized_phrase,
                    resolver=resolver,
                    candidate=selected,
                ),
                "diagnostic_summary": _resolver_diagnostic_summary(
                    stage="resolve",
                    status=LOOKUP_STATUS_BLOCKED,
                    field_path=field_path,
                    source_phrase=normalized_phrase,
                    candidate_count=1,
                    selected_candidate=selected,
                    policy_blocker="candidate_is_obsolete",
                ),
                "debug": _resolver_debug_payload(
                    stage="resolve",
                    status=LOOKUP_STATUS_BLOCKED,
                    field_path=field_path,
                    term_source=term_source,
                    policy=policy,
                    lookup_attempts=lookup_attempts,
                    candidate_count=1,
                    selected_candidate=selected,
                    policy_blocker="candidate_is_obsolete",
                ),
            },
            count=1,
            warnings=search_result.warnings,
            lookup_status=LOOKUP_STATUS_BLOCKED,
            failure_classification=LOOKUP_STATUS_BLOCKED,
            lookup_attempts=lookup_attempts,
        )

    (
        effective_field_path,
        effective_policy,
        effective_term_source,
        direct_policy_error,
    ) = _direct_resolution_context(
        domain_pack_id=domain_pack_id,
        object_type=object_type,
        field_path=field_path,
        policy=policy,
        term_source=term_source,
        candidate=selected,
        attempted_query=attempted_query,
    )
    if direct_policy_error is not None:
        return direct_policy_error

    policy_blocker = _candidate_policy_blocker(
        term_source=effective_term_source,
        candidate=selected,
    )
    if policy_blocker is not None:
        return AgrQueryResult(
            status="blocked",
            data={
                "domain_pack_id": domain_pack_id,
                "object_type": object_type,
                "field_path": effective_field_path,
                "requested_field_path": field_path,
                "source_phrase": normalized_phrase,
                "selected_candidate": dict(selected),
                "term_source": dict(effective_term_source),
                "policy_blocker": policy_blocker,
                "instructions": _resolver_instruction(
                    resolution_status="blocked",
                    field_path=effective_field_path,
                    source_phrase=normalized_phrase,
                    resolver=resolver,
                    candidate=selected,
                ),
                "diagnostic_summary": _resolver_diagnostic_summary(
                    stage="resolve",
                    status=LOOKUP_STATUS_BLOCKED,
                    field_path=effective_field_path,
                    requested_field_path=field_path,
                    source_phrase=normalized_phrase,
                    candidate_count=1,
                    selected_candidate=selected,
                    policy_blocker=policy_blocker,
                ),
                "debug": _resolver_debug_payload(
                    stage="resolve",
                    status=LOOKUP_STATUS_BLOCKED,
                    field_path=effective_field_path,
                    requested_field_path=field_path,
                    term_source=effective_term_source,
                    policy=effective_policy,
                    lookup_attempts=lookup_attempts,
                    candidate_count=1,
                    selected_candidate=selected,
                    policy_blocker=policy_blocker,
                ),
            },
            count=1,
            warnings=search_result.warnings,
            lookup_status=LOOKUP_STATUS_BLOCKED,
            failure_classification=LOOKUP_STATUS_BLOCKED,
            lookup_attempts=lookup_attempts,
        )

    resolved_at = datetime.now(timezone.utc).isoformat()
    helper_selection = _resolver_helper_selection(
        field_path=effective_field_path,
        source_phrase=normalized_phrase,
        candidate=selected,
        term_source=effective_term_source,
        policy=effective_policy,
        evidence_context=evidence_context,
        resolved_at=resolved_at,
    )
    data = {
        "domain_pack_id": domain_pack_id,
        "object_type": object_type,
        "field_path": effective_field_path,
        "requested_field_path": field_path,
        "source_phrase": normalized_phrase,
        "selected_candidate": dict(selected),
        "payload_field_instructions": _payload_field_instructions(
            field_path=effective_field_path,
            candidate=selected,
            term_source=effective_term_source,
        ),
        "helper_selection": helper_selection,
        "instructions": _resolver_instruction(
            resolution_status="resolved",
            field_path=effective_field_path,
            source_phrase=normalized_phrase,
            resolver=resolver,
        ),
        "diagnostic_summary": _resolver_diagnostic_summary(
            stage="resolve",
            status=LOOKUP_STATUS_SUCCESS,
            field_path=effective_field_path,
            requested_field_path=field_path,
            source_phrase=normalized_phrase,
            candidate_count=1,
            selected_candidate=selected,
        ),
        "debug": _resolver_debug_payload(
            stage="resolve",
            status=LOOKUP_STATUS_SUCCESS,
            field_path=effective_field_path,
            requested_field_path=field_path,
            term_source=effective_term_source,
            policy=effective_policy,
            lookup_attempts=lookup_attempts,
            candidate_count=1,
            selected_candidate=selected,
        ),
        "authority": "selector_evidence",
    }
    return AgrQueryResult(
        status="resolved",
        data=data,
        count=1,
        warnings=search_result.warnings,
        lookup_status=LOOKUP_STATUS_SUCCESS,
        lookup_attempts=lookup_attempts,
        candidate_matches=[
            _candidate_from_result("resolve_domain_field_term", dict(selected))
        ],
        result_projections=[
            {
                "projection_type": "domain_field_selector",
                "field_path": effective_field_path,
                "resolved_value": helper_selection.get("selected_value"),
                "resolved_name": helper_selection.get("selected_name"),
                "source_tool": "resolve_domain_field_term",
            }
        ],
    )


def _clean_string(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _gene_expression_candidate_id(workspace: Any, pending_ref_id: str) -> str:
    for candidate in workspace.candidates.values():
        if pending_ref_id in candidate.pending_ref_ids:
            return candidate.candidate_id
    return f"gex-candidate-{len(workspace.candidates) + 1}"


def _reference_id_validation_issue(reference_id: str) -> Optional[Dict[str, Any]]:
    normalized = reference_id.strip()
    if normalized.casefold() in _REFERENCE_PLACEHOLDER_VALUES or "..." in normalized:
        return {
            "field_path": "reference.reference_id",
            "reason": "placeholder_reference",
            "message": "reference.reference_id must be an actual PMID, DOI, or Alliance reference identifier.",
        }
    if ":" not in normalized and not normalized.upper().startswith("PMID"):
        return {
            "field_path": "reference.reference_id",
            "reason": "invalid_reference_id",
            "message": "reference.reference_id must include a concrete identifier prefix.",
        }
    return None


def _gene_expression_validation_result(
    *,
    message: str,
    issues: Sequence[Mapping[str, Any]],
    method: str,
    attempted_query: Optional[Dict[str, Any]] = None,
) -> AgrQueryResult:
    issue_list = [dict(issue) for issue in issues]
    _emit_gene_expression_builder_event(
        "gene_expression_builder.validation_failed",
        action=method,
        input_summary=attempted_query,
        output_summary={"message": message, "validation_issues": issue_list},
        validation={"status": "failed", "issues": issue_list},
    )
    return AgrQueryResult(
        status="error",
        data={"validation_issues": issue_list},
        count=len(issue_list),
        message=message,
        lookup_status=LOOKUP_STATUS_BLOCKED,
        failure_classification="validation_failed",
        explanation=message,
        lookup_attempts=[
            _lookup_attempt(
                method=method,
                attempted_query=attempted_query or _attempt_query(method),
                lookup_status=LOOKUP_STATUS_BLOCKED,
                explanation=message,
            )
        ],
    )


def _emit_gene_expression_builder_event(
    event_type: str,
    *,
    action: str,
    input_summary: Any = None,
    output_summary: Any = None,
    validation: Optional[Mapping[str, Any]] = None,
    tool_call_id: Optional[str] = None,
) -> None:
    workspace = None
    try:
        workspace = get_active_extraction_builder_workspace()
    except RuntimeError:
        pass
    write_extraction_trace_event(
        event_type=event_type,
        trace_id=getattr(workspace, "run_id", None),
        tool_call_id=tool_call_id,
        domain_pack_id=GENE_EXPRESSION_DOMAIN_PACK_ID,
        input_summary=input_summary,
        output_summary=output_summary,
        validation=validation,
        metadata={
            "action": action,
            "builder_run_id": getattr(workspace, "run_id", None),
            "object_type": GENE_EXPRESSION_OBJECT_TYPE,
        },
    )


def _model_validation_issues(exc: ValidationError) -> List[Dict[str, Any]]:
    return [
        {
            "field_path": ".".join(str(part) for part in error.get("loc", ())),
            "reason": str(error.get("type") or "invalid"),
            "message": str(error.get("msg") or "Invalid value"),
        }
        for error in exc.errors()
    ]


def _resolver_entry_for_controlled_field(
    *,
    resolver_call_id: str,
    field_path: str,
    selected_value: Optional[str] = None,
) -> Tuple[Optional[ResolverCallLedgerEntry], Optional[Dict[str, Any]]]:
    if not _clean_string(resolver_call_id):
        issue = {
            "field_path": field_path,
            "reason": "missing_resolver_call_id",
            "message": "Controlled fields require resolver_call_id from resolve_domain_field_term.",
        }
        _emit_gene_expression_builder_event(
            "gene_expression_builder.missing_provenance_rejected",
            action="resolver_lookup",
            input_summary={"field_path": field_path, "resolver_call_id": resolver_call_id},
            output_summary=issue,
            validation={"status": "failed", "issue": issue},
        )
        return None, issue
    try:
        entry = get_active_resolver_call_ledger().get(resolver_call_id)
    except (RuntimeError, KeyError) as exc:
        issue = {
            "field_path": field_path,
            "reason": "unknown_resolver_call_id",
            "message": str(exc),
            "resolver_call_id": resolver_call_id,
        }
        _emit_gene_expression_builder_event(
            "gene_expression_builder.missing_provenance_rejected",
            action="resolver_lookup",
            input_summary={"field_path": field_path, "resolver_call_id": resolver_call_id},
            output_summary=issue,
            validation={"status": "failed", "issue": issue},
            tool_call_id=resolver_call_id,
        )
        return None, issue

    if entry.domain_pack_id != GENE_EXPRESSION_DOMAIN_PACK_ID or entry.object_type != GENE_EXPRESSION_OBJECT_TYPE:
        issue = {
            "field_path": field_path,
            "reason": "resolver_scope_mismatch",
            "message": "resolver_call_id was not resolved for Alliance gene-expression annotations.",
            "resolver_call_id": resolver_call_id,
        }
        return None, issue
    if entry.field_path != field_path:
        issue = {
            "field_path": field_path,
            "reason": "resolver_field_path_mismatch",
            "message": f"resolver_call_id resolved {entry.field_path}, not {field_path}.",
            "resolver_call_id": resolver_call_id,
        }
        return None, issue
    if selected_value is not None and _clean_string(selected_value) != entry.selected_value:
        issue = {
            "field_path": field_path,
            "reason": "resolver_selected_value_mismatch",
            "message": "selected_value must match the validated resolver output.",
            "resolver_call_id": resolver_call_id,
        }
        return None, issue
    return entry, None


def _apply_resolver_selection(
    payload: Dict[str, Any],
    *,
    entry: ResolverCallLedgerEntry,
) -> None:
    instructions = entry.payload_field_instructions
    for operation in instructions.get("set", []) if isinstance(instructions.get("set"), list) else []:
        if isinstance(operation, Mapping):
            _set_dotted_payload_value(
                payload,
                str(operation.get("field_path") or ""),
                operation.get("value"),
            )
    for operation in instructions.get("append", []) if isinstance(instructions.get("append"), list) else []:
        if isinstance(operation, Mapping):
            _append_dotted_payload_value(
                payload,
                str(operation.get("field_path") or ""),
                operation.get("value"),
            )


def _set_dotted_payload_value(payload: Dict[str, Any], field_path: str, value: Any) -> None:
    if not field_path:
        return
    current = payload
    parts = field_path.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
        if not isinstance(current, dict):
            return
    current[parts[-1]] = value


def _append_dotted_payload_value(payload: Dict[str, Any], field_path: str, value: Any) -> None:
    if not field_path:
        return
    current = payload
    parts = field_path.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
        if not isinstance(current, dict):
            return
    current.setdefault(parts[-1], [])
    if isinstance(current[parts[-1]], list):
        current[parts[-1]].append(value)


def _stage_payload_from_gene_expression_input(
    stage_input: GeneExpressionStageInput,
    resolver_entries: List[ResolverCallLedgerEntry],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "domain_pack_id": GENE_EXPRESSION_DOMAIN_PACK_ID,
        "object_type": GENE_EXPRESSION_OBJECT_TYPE,
        "pending_ref_id": stage_input.pending_ref_id,
        "where_expressed_statement": stage_input.where_expressed_statement,
        "expression_annotation_subject": {
            "source_phrase": stage_input.subject.source_phrase,
            "gene_symbol": stage_input.subject.gene_symbol,
            "primary_external_id": stage_input.subject.primary_external_id,
        },
        "single_reference": {
            "source_phrase": stage_input.reference.source_phrase,
            "reference_id": stage_input.reference.reference_id,
        },
        "metadata": {
            "provenance": {
                "helper_selections": [
                    entry.provenance_selection() for entry in resolver_entries
                ]
            }
        },
    }
    for entry in resolver_entries:
        _apply_resolver_selection(payload, entry=entry)
    return payload


def _builder_summary(workspace: Any, *, include_discarded: bool = False) -> Dict[str, Any]:
    snapshot = workspace.snapshot(redact_payload=True)
    candidates = snapshot["candidates"]
    if not include_discarded:
        candidates = [
            candidate
            for candidate in candidates
            if candidate.get("status") != "discarded"
        ]
    return {
        "builder_run_id": snapshot.get("run_id"),
        "state": snapshot.get("state"),
        "candidate_count": len(candidates),
        "candidate_ids": [candidate.get("candidate_id") for candidate in candidates],
        "pending_ref_ids": snapshot["pending_ref_ids"],
        "evidence_record_ids": snapshot["evidence_record_ids"],
        "resolver_selection_refs": snapshot["resolver_selection_refs"],
        "candidates": candidates,
        "finalization": snapshot.get("finalization"),
    }


@function_tool(strict_mode=True)
def stage_gene_expression_observation(
    pending_ref_id: str,
    evidence_record_ids: Annotated[List[str], Field(min_length=1, max_length=20)],
    where_expressed_statement: str,
    subject: GeneExpressionSubjectInput,
    reference: GeneExpressionReferenceInput,
    controlled_fields: Annotated[List[GeneExpressionControlledFieldInput], Field(min_length=1, max_length=20)],
) -> AgrQueryResult:
    """Stage one gene-expression observation candidate through the builder workspace."""

    attempted_query = _attempt_query(
        "stage_gene_expression_observation",
        pending_ref_id=pending_ref_id,
        evidence_record_ids=evidence_record_ids,
        where_expressed_statement=where_expressed_statement,
        subject=subject.model_dump(mode="json") if hasattr(subject, "model_dump") else subject,
        reference=reference.model_dump(mode="json") if hasattr(reference, "model_dump") else reference,
        controlled_fields=[
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            for item in (controlled_fields or [])
        ],
    )
    _emit_gene_expression_builder_event(
        "gene_expression_builder.stage_requested",
        action="stage",
        input_summary=attempted_query,
    )
    try:
        stage_input = GeneExpressionStageInput(
            pending_ref_id=pending_ref_id,
            evidence_record_ids=evidence_record_ids,
            where_expressed_statement=where_expressed_statement,
            subject=subject,
            reference=reference,
            controlled_fields=controlled_fields,
        )
    except ValidationError as exc:
        return _gene_expression_validation_result(
            message="stage_gene_expression_observation failed input validation.",
            issues=_model_validation_issues(exc),
            method="stage_gene_expression_observation",
            attempted_query=attempted_query,
        )

    issues: List[Dict[str, Any]] = []
    reference_issue = _reference_id_validation_issue(stage_input.reference.reference_id)
    if reference_issue:
        issues.append(reference_issue)

    resolver_entries: List[ResolverCallLedgerEntry] = []
    for controlled_field in stage_input.controlled_fields:
        entry, issue = _resolver_entry_for_controlled_field(
            resolver_call_id=controlled_field.resolver_call_id,
            field_path=controlled_field.field_path,
            selected_value=controlled_field.selected_value,
        )
        if issue:
            issues.append(issue)
        elif entry is not None:
            resolver_entries.append(entry)
    if issues:
        return _gene_expression_validation_result(
            message="stage_gene_expression_observation rejected invalid builder input.",
            issues=issues,
            method="stage_gene_expression_observation",
            attempted_query=attempted_query,
        )

    workspace = get_active_extraction_builder_workspace()
    candidate_id = _gene_expression_candidate_id(workspace, stage_input.pending_ref_id)
    payload = _stage_payload_from_gene_expression_input(stage_input, resolver_entries)
    candidate = workspace.upsert_candidate(
        candidate_id=candidate_id,
        staged_fields=payload,
        pending_ref_ids=[stage_input.pending_ref_id],
        evidence_record_ids=stage_input.evidence_record_ids,
        resolver_selection_refs=[entry.tool_call_id for entry in resolver_entries],
        status=CANDIDATE_STATUS_VALID,
    )
    summary = {
        "candidate_id": candidate.candidate_id,
        "status": candidate.status,
        "pending_ref_ids": candidate.pending_ref_ids,
        "evidence_record_ids": candidate.evidence_record_ids,
        "resolver_selection_refs": candidate.resolver_selection_refs,
        "builder": _builder_summary(workspace),
    }
    _emit_gene_expression_builder_event(
        "gene_expression_builder.stage_completed",
        action="stage",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=1, lookup_status=LOOKUP_STATUS_SUCCESS)


@function_tool(strict_mode=True)
def patch_gene_expression_observation(
    candidate_id: str,
    pending_ref_id: str,
    updates: Annotated[List[GeneExpressionPatchUpdateInput], Field(min_length=1, max_length=25)],
) -> AgrQueryResult:
    """Patch enumerated fields on one staged gene-expression observation."""

    attempted_query = _attempt_query(
        "patch_gene_expression_observation",
        candidate_id=candidate_id,
        pending_ref_id=pending_ref_id,
        updates=[
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            for item in (updates or [])
        ],
    )
    _emit_gene_expression_builder_event(
        "gene_expression_builder.patch_requested",
        action="patch",
        input_summary=attempted_query,
    )
    try:
        patch_input = GeneExpressionPatchInput(
            candidate_id=candidate_id,
            pending_ref_id=pending_ref_id,
            updates=updates,
        )
    except ValidationError as exc:
        return _gene_expression_validation_result(
            message="patch_gene_expression_observation failed input validation.",
            issues=_model_validation_issues(exc),
            method="patch_gene_expression_observation",
            attempted_query=attempted_query,
        )

    workspace = get_active_extraction_builder_workspace()
    try:
        candidate = workspace.get_candidate(patch_input.candidate_id)
    except KeyError as exc:
        return _gene_expression_validation_result(
            message=str(exc),
            issues=[
                {
                    "field_path": "candidate_id",
                    "reason": "unknown_candidate_id",
                    "message": str(exc),
                }
            ],
            method="patch_gene_expression_observation",
            attempted_query=attempted_query,
        )
    if patch_input.pending_ref_id not in candidate.pending_ref_ids:
        return _gene_expression_validation_result(
            message="patch_gene_expression_observation pending_ref_id does not match the staged candidate.",
            issues=[
                {
                    "field_path": "pending_ref_id",
                    "reason": "pending_ref_id_mismatch",
                    "message": "pending_ref_id must match the staged candidate.",
                }
            ],
            method="patch_gene_expression_observation",
            attempted_query=attempted_query,
        )

    issues: List[Dict[str, Any]] = []
    payload = deepcopy(candidate.staged_fields)
    resolver_refs = list(candidate.resolver_selection_refs)
    evidence_ids = list(candidate.evidence_record_ids)
    helper_selections = (
        payload.setdefault("metadata", {})
        .setdefault("provenance", {})
        .setdefault("helper_selections", [])
    )
    for update in patch_input.updates:
        if update.field_path in _CONTROLLED_GENE_EXPRESSION_FIELD_PATHS:
            assert update.resolver_call_id is not None
            entry, issue = _resolver_entry_for_controlled_field(
                resolver_call_id=update.resolver_call_id,
                field_path=update.field_path,
            )
            if issue:
                issues.append(issue)
                continue
            assert entry is not None
            _apply_resolver_selection(payload, entry=entry)
            helper_selections.append(entry.provenance_selection())
            if entry.tool_call_id not in resolver_refs:
                resolver_refs.append(entry.tool_call_id)
            continue
        if update.field_path == "evidence_record_ids":
            evidence_ids = list(update.evidence_record_ids or [])
            continue
        if update.field_path == "reference.reference_id" and update.string_value:
            reference_issue = _reference_id_validation_issue(update.string_value)
            if reference_issue:
                issues.append(reference_issue)
                continue
        _set_gene_expression_patch_value(
            payload,
            update.field_path,
            update.string_value,
        )

    if issues:
        return _gene_expression_validation_result(
            message="patch_gene_expression_observation rejected invalid builder input.",
            issues=issues,
            method="patch_gene_expression_observation",
            attempted_query=attempted_query,
        )
    workspace.upsert_candidate(
        candidate_id=patch_input.candidate_id,
        staged_fields=payload,
        pending_ref_ids=candidate.pending_ref_ids,
        evidence_record_ids=evidence_ids,
        resolver_selection_refs=resolver_refs,
        status=CANDIDATE_STATUS_VALID,
    )
    summary = {
        "candidate_id": patch_input.candidate_id,
        "patched_field_count": len(patch_input.updates),
        "builder": _builder_summary(workspace),
    }
    _emit_gene_expression_builder_event(
        "gene_expression_builder.patch_completed",
        action="patch",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=1, lookup_status=LOOKUP_STATUS_SUCCESS)


def _set_gene_expression_patch_value(
    payload: Dict[str, Any],
    field_path: str,
    value: Optional[str],
) -> None:
    mapping = {
        "subject.source_phrase": "expression_annotation_subject.source_phrase",
        "subject.gene_symbol": "expression_annotation_subject.gene_symbol",
        "subject.primary_external_id": "expression_annotation_subject.primary_external_id",
        "reference.source_phrase": "single_reference.source_phrase",
        "reference.reference_id": "single_reference.reference_id",
        "reference.curie": "single_reference.curie",
        "reference.pmid": "single_reference.pmid",
        "reference.doi": "single_reference.doi",
        "reference.title": "single_reference.title",
    }
    target_path = mapping.get(field_path, field_path)
    _set_dotted_payload_value(payload, target_path, value)


@function_tool(strict_mode=True)
def discard_gene_expression_observation(
    candidate_id: str,
    reason: Optional[str],
) -> AgrQueryResult:
    """Discard one staged gene-expression observation candidate."""

    attempted_query = _attempt_query(
        "discard_gene_expression_observation",
        candidate_id=candidate_id,
        reason=reason,
    )
    _emit_gene_expression_builder_event(
        "gene_expression_builder.discard_requested",
        action="discard",
        input_summary=attempted_query,
    )
    try:
        discard_input = GeneExpressionDiscardInput(candidate_id=candidate_id, reason=reason)
    except ValidationError as exc:
        return _gene_expression_validation_result(
            message="discard_gene_expression_observation failed input validation.",
            issues=_model_validation_issues(exc),
            method="discard_gene_expression_observation",
            attempted_query=attempted_query,
        )
    workspace = get_active_extraction_builder_workspace()
    try:
        workspace.discard_candidate(discard_input.candidate_id, reason=discard_input.reason)
    except (KeyError, ExtractionBuilderError) as exc:
        return _gene_expression_validation_result(
            message=str(exc),
            issues=[
                {
                    "field_path": "candidate_id",
                    "reason": "discard_failed",
                    "message": str(exc),
                }
            ],
            method="discard_gene_expression_observation",
            attempted_query=attempted_query,
        )
    summary = _builder_summary(workspace, include_discarded=True)
    _emit_gene_expression_builder_event(
        "gene_expression_builder.discard_completed",
        action="discard",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=summary["candidate_count"], lookup_status=LOOKUP_STATUS_SUCCESS)


@function_tool(strict_mode=True)
def list_staged_gene_expression_observations(
    include_discarded: bool,
) -> AgrQueryResult:
    """List compact summaries for staged gene-expression observations."""

    attempted_query = _attempt_query(
        "list_staged_gene_expression_observations",
        include_discarded=include_discarded,
    )
    _emit_gene_expression_builder_event(
        "gene_expression_builder.list_requested",
        action="list",
        input_summary=attempted_query,
    )
    try:
        list_input = GeneExpressionListInput(include_discarded=include_discarded)
    except ValidationError as exc:
        return _gene_expression_validation_result(
            message="list_staged_gene_expression_observations failed input validation.",
            issues=_model_validation_issues(exc),
            method="list_staged_gene_expression_observations",
            attempted_query=attempted_query,
        )
    workspace = get_active_extraction_builder_workspace()
    summary = _builder_summary(workspace, include_discarded=list_input.include_discarded)
    _emit_gene_expression_builder_event(
        "gene_expression_builder.list_completed",
        action="list",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=summary["candidate_count"], lookup_status=LOOKUP_STATUS_SUCCESS)


@function_tool(strict_mode=True)
def finalize_gene_expression_extraction(
    candidate_ids: Annotated[List[str], Field(min_length=1, max_length=50)],
) -> AgrQueryResult:
    """Finalize staged gene-expression candidates through the builder handoff contract."""

    attempted_query = _attempt_query(
        "finalize_gene_expression_extraction",
        candidate_ids=candidate_ids,
    )
    _emit_gene_expression_builder_event(
        "gene_expression_builder.finalize_requested",
        action="finalize",
        input_summary=attempted_query,
    )
    try:
        finalize_input = GeneExpressionFinalizeInput(candidate_ids=candidate_ids)
    except ValidationError as exc:
        return _gene_expression_validation_result(
            message="finalize_gene_expression_extraction failed input validation.",
            issues=_model_validation_issues(exc),
            method="finalize_gene_expression_extraction",
            attempted_query=attempted_query,
        )

    workspace = get_active_extraction_builder_workspace()
    if getattr(workspace, "finalization", None) is not None:
        finalization = workspace.finalization
        summary = {
            "builder_finalization": finalization.summary(),
            "builder": _builder_summary(workspace, include_discarded=True),
        }
        _emit_gene_expression_builder_event(
            "gene_expression_builder.finalize_completed",
            action="finalize",
            input_summary=attempted_query,
            output_summary=summary,
        )
        return _ok(
            data=summary,
            count=finalization.finalized_candidate_count,
            lookup_status=LOOKUP_STATUS_SUCCESS,
        )

    issues: List[Dict[str, Any]] = []
    for candidate_id in finalize_input.candidate_ids:
        try:
            candidate = workspace.get_candidate(candidate_id)
        except KeyError as exc:
            issues.append(
                {
                    "field_path": "candidate_ids",
                    "reason": "unknown_candidate_id",
                    "message": str(exc),
                    "candidate_id": candidate_id,
                }
            )
            continue
        if not candidate.evidence_record_ids:
            issues.append(
                {
                    "field_path": "evidence_record_ids",
                    "reason": "missing_evidence_record_ids",
                    "message": "Finalized gene-expression candidates require evidence_record_ids.",
                    "candidate_id": candidate_id,
                }
            )
        if not candidate.resolver_selection_refs:
            issues.append(
                {
                    "field_path": "controlled_fields",
                    "reason": "missing_resolver_call_id",
                    "message": "Finalized gene-expression candidates require validated resolver selections.",
                    "candidate_id": candidate_id,
                }
            )
    if issues:
        workspace.record_validation_failure(errors=issues, candidate_ids=finalize_input.candidate_ids)
        return _gene_expression_validation_result(
            message="finalize_gene_expression_extraction failed builder validation.",
            issues=issues,
            method="finalize_gene_expression_extraction",
            attempted_query=attempted_query,
        )

    _emit_gene_expression_builder_event(
        "gene_expression_materializer.started",
        action="materialize",
        input_summary={
            "candidate_ids": finalize_input.candidate_ids,
            "materializer_id": GENE_EXPRESSION_MATERIALIZER_ID,
        },
    )
    try:
        evidence_records = get_active_evidence_records_snapshot()
    except RuntimeError:
        evidence_records = []
    try:
        resolver_ledger = get_active_resolver_call_ledger()
    except RuntimeError:
        resolver_ledger = None

    materialization = materialize_gene_expression_builder_state(
        workspace=workspace,
        candidate_ids=finalize_input.candidate_ids,
        evidence_records=evidence_records,
        resolver_entry_lookup=resolver_ledger.get if resolver_ledger is not None else None,
    )
    placeholder_issues = [
        issue
        for issue in materialization.issues
        if issue.get("reason") == "placeholder_reference"
    ]
    for issue in placeholder_issues:
        _emit_gene_expression_builder_event(
            "gene_expression_materializer.placeholder_reference_rejected",
            action="materialize",
            input_summary={"candidate_ids": finalize_input.candidate_ids},
            output_summary=issue,
            validation={"status": "failed", "issue": issue},
        )
    if not materialization.ok or materialization.payload is None:
        issue_list = [dict(issue) for issue in materialization.issues]
        workspace.record_validation_failure(
            errors=issue_list,
            candidate_ids=finalize_input.candidate_ids,
        )
        _emit_gene_expression_builder_event(
            "gene_expression_materializer.validation_failed",
            action="materialize",
            input_summary={"candidate_ids": finalize_input.candidate_ids},
            output_summary=materialization.summary(),
            validation={"status": "failed", "issues": issue_list},
        )
        return _gene_expression_validation_result(
            message="finalize_gene_expression_extraction failed materialization validation.",
            issues=issue_list,
            method="finalize_gene_expression_extraction",
            attempted_query=attempted_query,
        )

    materialized_candidate_id = finalize_input.candidate_ids[0]
    if len(finalize_input.candidate_ids) > 1:
        digest = hashlib.sha256(
            "|".join(finalize_input.candidate_ids).encode("utf-8")
        ).hexdigest()[:12]
        materialized_candidate_id = f"gene-expression-envelope-{digest}"
    workspace.upsert_candidate(
        candidate_id=materialized_candidate_id,
        staged_fields=materialization.payload,
        pending_ref_ids=[
            pending_ref
            for candidate_id in finalize_input.candidate_ids
            for pending_ref in workspace.get_candidate(candidate_id).pending_ref_ids
        ],
        evidence_record_ids=materialization.evidence_record_ids,
        resolver_selection_refs=[
            resolver_ref
            for candidate_id in finalize_input.candidate_ids
            for resolver_ref in workspace.get_candidate(candidate_id).resolver_selection_refs
        ],
        status=CANDIDATE_STATUS_VALID,
    )
    _emit_gene_expression_builder_event(
        "gene_expression_materializer.evidence_provenance_summary",
        action="materialize",
        input_summary={"candidate_ids": finalize_input.candidate_ids},
        output_summary=materialization.summary(),
    )
    _emit_gene_expression_builder_event(
        "gene_expression_materializer.completed",
        action="materialize",
        input_summary={"candidate_ids": finalize_input.candidate_ids},
        output_summary={
            **materialization.summary(),
            "materialized_candidate_id": materialized_candidate_id,
            "curatable_objects": materialization.payload.get("curatable_objects", []),
            "materialized_envelope": materialization.payload,
        },
    )

    try:
        finalization = workspace.finalize(candidate_ids=[materialized_candidate_id])
    except ExtractionBuilderValidationError as exc:
        return _gene_expression_validation_result(
            message=str(exc),
            issues=list(workspace.validation_errors),
            method="finalize_gene_expression_extraction",
            attempted_query=attempted_query,
        )
    except (KeyError, ValueError, ExtractionBuilderError) as exc:
        return _gene_expression_validation_result(
            message=str(exc),
            issues=[
                {
                    "field_path": "candidate_ids",
                    "reason": "finalization_failed",
                    "message": str(exc),
                }
            ],
            method="finalize_gene_expression_extraction",
            attempted_query=attempted_query,
        )

    summary = {
        "builder_finalization": finalization.summary(),
        "builder": _builder_summary(workspace, include_discarded=True),
    }
    _emit_gene_expression_builder_event(
        "gene_expression_builder.finalize_completed",
        action="finalize",
        input_summary=attempted_query,
        output_summary=summary,
    )
    return _ok(data=summary, count=finalization.finalized_candidate_count, lookup_status=LOOKUP_STATUS_SUCCESS)


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
