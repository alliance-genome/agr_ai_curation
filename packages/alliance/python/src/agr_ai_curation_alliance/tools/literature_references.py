"""Alliance literature reference lookup tools backed by the public API client."""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, List, Optional

from agents import function_tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SOURCE = "literature_es"
TOOL_PROVIDER = "agr_literature_reference_lookup"
# Default and hard-cap page sizes for literature reference lookups.
# Env-configurable via LITERATURE_REFERENCE_DEFAULT_LIMIT (default 20) and
# LITERATURE_REFERENCE_HARD_MAX (default 100). This module runs in the isolated
# package subprocess (inherits the backend env), so it reads os.getenv directly.
DEFAULT_LIMIT = int(os.getenv("LITERATURE_REFERENCE_DEFAULT_LIMIT", "20"))
HARD_MAX = int(os.getenv("LITERATURE_REFERENCE_HARD_MAX", "100"))

_EXPECTED_UPSTREAM_ERROR_TYPES: tuple[type[BaseException], ...] = (
    ConnectionError,
    ValueError,
    RuntimeError,
    ImportError,
    ModuleNotFoundError,
)
try:
    from agr_curation_api.exceptions import AGRAPIError
except (ImportError, ModuleNotFoundError):
    pass
else:
    _EXPECTED_UPSTREAM_ERROR_TYPES = _EXPECTED_UPSTREAM_ERROR_TYPES + (AGRAPIError,)


class LiteratureReferenceLookupResult(BaseModel):
    """Structured result returned by the literature reference lookup tool."""

    status: str
    source: str = SOURCE
    method: str
    query: Optional[str] = None
    exact_match: bool = False
    count: int = 0
    lookup_status: str
    message: str
    lookup_attempts: List[Dict[str, Any]] = Field(default_factory=list)
    resolved_reference: Optional[Dict[str, Any]] = None
    candidate_references: List[Dict[str, Any]] = Field(default_factory=list)
    ambiguity: Optional[Dict[str, Any]] = None
    no_match: Optional[Dict[str, Any]] = None
    failure_classification: Optional[str] = None


def _default_client_factory() -> Any:
    """Create the official API client in DB mode so it owns ES configuration."""
    from agr_curation_api import AGRCurationAPIClient

    return AGRCurationAPIClient(data_source="db")


_client_factory: Callable[[], Any] = _default_client_factory


def _ensure_elasticsearch_numpy2_compat() -> None:
    """Keep the upstream ES client importable under the repo's NumPy 2.x pin."""
    try:
        import numpy as np
    except (ImportError, ModuleNotFoundError):
        return

    if not hasattr(np, "float_"):
        np.float_ = np.float64  # type: ignore[attr-defined]
    if not hasattr(np, "complex_"):
        np.complex_ = np.complex128  # type: ignore[attr-defined]


def _plain_reference(reference: Any) -> Dict[str, Any]:
    if isinstance(reference, dict):
        return dict(reference)
    model_dump = getattr(reference, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    dict_method = getattr(reference, "dict", None)
    if callable(dict_method):
        return dict_method()
    if hasattr(reference, "__dict__"):
        return {
            key: value
            for key, value in vars(reference).items()
            if not key.startswith("_")
        }
    raise TypeError(f"Cannot serialize literature reference of type {type(reference).__name__}")


def _normalized_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalized_match_value(value: Any) -> str:
    return str(value or "").strip().casefold()


def _contains_query_context(value: Any, query: str) -> bool:
    field_value = _normalized_match_value(value)
    if not field_value:
        return False
    query_value = _normalized_match_value(query)
    if query_value and query_value in field_value:
        return True
    query_terms = {
        term
        for term in query_value.replace(":", " ").replace("/", " ").split()
        if len(term) >= 4
    }
    return any(term in field_value for term in query_terms)


def _as_reference_candidate(reference: Any, query: str) -> Dict[str, Any]:
    raw = _plain_reference(reference)
    cross_references = list(raw.get("cross_references") or [])
    title = raw.get("title")
    citation = raw.get("short_citation")
    query_value = _normalized_match_value(query)

    matched_identifier = None
    curie = raw.get("curie")
    if query_value and _normalized_match_value(curie) == query_value:
        matched_identifier = curie
    if matched_identifier is None:
        for cross_reference in cross_references:
            if _normalized_match_value(cross_reference) == query_value:
                matched_identifier = cross_reference
                break

    matched_title = title if _contains_query_context(title, query) else None
    matched_citation = citation if _contains_query_context(citation, query) else None

    return {
        "reference_id": raw.get("reference_id"),
        "curie": curie,
        "title": title,
        "short_citation": citation,
        "cross_references": cross_references,
        "source": raw.get("source"),
        "obsolete": raw.get("obsolete"),
        "matched_identifier": matched_identifier,
        "matched_title": matched_title,
        "matched_citation": matched_citation,
    }


def _normalize_limit(limit: Optional[int]) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    if limit < 1:
        raise ValueError("limit must be greater than or equal to 1")
    return min(limit, HARD_MAX)


def _lookup_attempt(
    *,
    method: str,
    query: Optional[str],
    exact_match: bool,
    limit: int,
    lookup_status: str,
    explanation: str,
    candidate_count: int = 0,
    error: Optional[Exception] = None,
) -> Dict[str, Any]:
    attempt: Dict[str, Any] = {
        "provider": TOOL_PROVIDER,
        "source": SOURCE,
        "method": method,
        "query": {
            "value": query,
            "exact_match": exact_match,
            "limit": limit,
        },
        "lookup_status": lookup_status,
        "result_count": candidate_count,
        "explanation": explanation,
    }
    if error is not None:
        attempt["error_type"] = type(error).__name__
    return attempt


def _success_result(
    *,
    method: str,
    query: str,
    exact_match: bool,
    limit: int,
    candidates: List[Dict[str, Any]],
) -> LiteratureReferenceLookupResult:
    count = len(candidates)
    if count == 0:
        message = (
            f"No literature reference matched {query!r}. Check the PMID, DOI, AGRKB ID, "
            "title, or citation text before using a source reference."
        )
        return LiteratureReferenceLookupResult(
            status="ok",
            method=method,
            query=query,
            exact_match=exact_match,
            count=0,
            lookup_status="not_found",
            message=message,
            lookup_attempts=[
                _lookup_attempt(
                    method=method,
                    query=query,
                    exact_match=exact_match,
                    limit=limit,
                    lookup_status="not_found",
                    explanation=message,
                    candidate_count=0,
                )
            ],
            no_match={
                "query": query,
                "source": SOURCE,
                "explanation": message,
            },
        )

    if count == 1:
        message = "Resolved one literature reference from the Alliance literature search index."
        return LiteratureReferenceLookupResult(
            status="ok",
            method=method,
            query=query,
            exact_match=exact_match,
            count=1,
            lookup_status="success",
            message=message,
            lookup_attempts=[
                _lookup_attempt(
                    method=method,
                    query=query,
                    exact_match=exact_match,
                    limit=limit,
                    lookup_status="success",
                    explanation=message,
                    candidate_count=1,
                )
            ],
            resolved_reference=candidates[0],
            candidate_references=candidates,
        )

    message = (
        f"Found {count} candidate literature references for {query!r}; curator review "
        "is required before selecting one."
    )
    return LiteratureReferenceLookupResult(
        status="ok",
        method=method,
        query=query,
        exact_match=exact_match,
        count=count,
        lookup_status="ambiguous",
        message=message,
        lookup_attempts=[
            _lookup_attempt(
                method=method,
                query=query,
                exact_match=exact_match,
                limit=limit,
                lookup_status="ambiguous",
                explanation=message,
                candidate_count=count,
            )
        ],
        candidate_references=candidates,
        ambiguity={
            "query": query,
            "candidate_count": count,
            "source": SOURCE,
            "explanation": message,
        },
    )


def _failure_classification(error: Exception) -> str:
    if isinstance(error, (ImportError, ModuleNotFoundError)):
        return "blocked"
    message = str(error).casefold()
    if (
        "not configured" in message
        or "elasticsearch_host" in message
        or "configuration" in message
        or "required" in message
    ):
        return "blocked"
    return "transient"


def _failure_message(error: Exception, classification: str) -> str:
    if classification == "blocked":
        return (
            "Literature reference search is unavailable because the Elasticsearch "
            "configuration is missing or incomplete."
        )
    return (
        "Literature reference search could not reach the upstream Elasticsearch-backed "
        "reference index. Try again after the network route or service recovers."
    )


def _error_result(
    *,
    method: str,
    query: Optional[str],
    exact_match: bool,
    limit: int,
    error: Exception,
) -> LiteratureReferenceLookupResult:
    classification = _failure_classification(error)
    message = _failure_message(error, classification)
    logger.warning("Literature reference lookup failed: %s", error)
    return LiteratureReferenceLookupResult(
        status="error",
        method=method,
        query=query,
        exact_match=exact_match,
        count=0,
        lookup_status=classification,
        message=message,
        failure_classification=classification,
        lookup_attempts=[
            _lookup_attempt(
                method=method,
                query=query,
                exact_match=exact_match,
                limit=limit,
                lookup_status=classification,
                explanation=message,
                error=error,
            )
        ],
    )


@function_tool(strict_mode=False)
def agr_literature_reference_lookup(
    method: str,
    identifier: Optional[str] = None,
    query: Optional[str] = None,
    exact_match: bool = False,
    limit: Optional[int] = None,
) -> LiteratureReferenceLookupResult:
    """Resolve Alliance literature references through the official API client.

    Args:
        method: Either get_literature_reference for exact PMID/DOI/AGRKB lookup
            or search_literature_references for fuzzy title/citation search.
        identifier: PMID, DOI, AGRKB reference CURIE, MOD reference ID, or title
            for exact lookup.
        query: Title, citation, AGRKB ID, PMID, DOI, or MOD reference ID for
            literature search.
        exact_match: Require exact search semantics when method is
            search_literature_references.
        limit: Maximum search candidates to return. Defaults to 20 and is capped.
    """
    limit_value = _normalize_limit(limit)
    method_value = _normalized_text(method)
    if method_value not in {"get_literature_reference", "search_literature_references"}:
        message = (
            "Unsupported literature reference method. Use get_literature_reference "
            "or search_literature_references."
        )
        return LiteratureReferenceLookupResult(
            status="error",
            method=method_value or "",
            query=_normalized_text(identifier) or _normalized_text(query),
            exact_match=exact_match,
            count=0,
            lookup_status="blocked",
            message=message,
            failure_classification="blocked",
            lookup_attempts=[
                _lookup_attempt(
                    method=method_value or "",
                    query=_normalized_text(identifier) or _normalized_text(query),
                    exact_match=exact_match,
                    limit=limit_value,
                    lookup_status="blocked",
                    explanation=message,
                )
            ],
        )

    query_value = (
        _normalized_text(identifier)
        if method_value == "get_literature_reference"
        else _normalized_text(query)
    )
    if not query_value:
        message = (
            "get_literature_reference requires identifier; "
            "search_literature_references requires query."
        )
        return LiteratureReferenceLookupResult(
            status="error",
            method=method_value,
            query=query_value,
            exact_match=exact_match,
            count=0,
            lookup_status="blocked",
            message=message,
            failure_classification="blocked",
            lookup_attempts=[
                _lookup_attempt(
                    method=method_value,
                    query=query_value,
                    exact_match=exact_match,
                    limit=limit_value,
                    lookup_status="blocked",
                    explanation=message,
                )
            ],
        )

    effective_exact_match = True if method_value == "get_literature_reference" else exact_match
    try:
        _ensure_elasticsearch_numpy2_compat()
        client = _client_factory()
        if method_value == "get_literature_reference":
            reference = client.get_literature_reference(query_value)
            references = [reference] if reference is not None else []
        else:
            references = client.search_literature_references(
                query=query_value,
                exact_match=exact_match,
                limit=limit_value,
            )
        candidates = [_as_reference_candidate(reference, query_value) for reference in references]
        return _success_result(
            method=method_value,
            query=query_value,
            exact_match=effective_exact_match,
            limit=limit_value,
            candidates=candidates,
        )
    except _EXPECTED_UPSTREAM_ERROR_TYPES as exc:
        return _error_result(
            method=method_value,
            query=query_value,
            exact_match=effective_exact_match,
            limit=limit_value,
            error=exc,
        )
