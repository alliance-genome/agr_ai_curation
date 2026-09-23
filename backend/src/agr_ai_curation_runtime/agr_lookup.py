"""Package-neutral lookup response helpers.

Keep status, attempt, projection-envelope, and bulk-summary mechanics here.
Project-specific provider names, projection types, object types, and detail
lookup semantics must be supplied by the caller.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from src.lib.lookup_status import (
    LOOKUP_STATUS_AMBIGUOUS,
    LOOKUP_STATUS_BLOCKED,
    LOOKUP_STATUS_NOT_FOUND,
    LOOKUP_STATUS_SUCCESS,
    LOOKUP_STATUS_TRANSIENT,
    LOOKUP_STATUS_UNDER_DEVELOPMENT,
)

DETAIL_RETRY_STRATEGY_PER_CURIE = "per_curie"
_BULK_ITEM_STATUS_BY_LOOKUP_STATUS = {
    LOOKUP_STATUS_SUCCESS: "resolved",
    LOOKUP_STATUS_NOT_FOUND: "no_matches",
    LOOKUP_STATUS_AMBIGUOUS: LOOKUP_STATUS_AMBIGUOUS,
    LOOKUP_STATUS_TRANSIENT: "transient_failure",
    LOOKUP_STATUS_BLOCKED: "blocked",
    LOOKUP_STATUS_UNDER_DEVELOPMENT: LOOKUP_STATUS_UNDER_DEVELOPMENT,
}
DEFAULT_PROJECTION_TYPE = "lookup_result"
DEFAULT_PROVIDER_DATA_KEYS: tuple[str, ...] = ()
# Largest number of matched rows a single bulk lookup will hand back across all
# of its inputs combined. A bulk call can ask about many symbols at once, and
# each symbol's own per-symbol limit still applies; this is the safety ceiling
# on the grand total so a long symbol list cannot return an unbounded payload.
# Env-configurable via BULK_TOTAL_MATCH_CAP (default 500). This module is shared
# by the backend and the isolated package subprocess (which inherits the backend
# env), so it reads os.getenv directly using one env var name honored everywhere.
DEFAULT_BULK_TOTAL_MATCH_CAP = int(os.getenv("BULK_TOTAL_MATCH_CAP", "500"))


@dataclass(frozen=True)
class LookupProjectionMetadata:
    """Caller-supplied metadata for validator-facing lookup projections."""

    provider: str | None = None
    tool_name: str | None = None
    projection_type: str = DEFAULT_PROJECTION_TYPE
    object_type: str | None = None
    provider_data_keys: tuple[str, ...] = DEFAULT_PROVIDER_DATA_KEYS


def clean_mapping(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return a dict without null or empty-list values."""
    return {
        key: value
        for key, value in raw.items()
        if value is not None and value != []
    }


def attempt_query(method: str, **values: Any) -> dict[str, Any]:
    return clean_mapping({"method": method, **values})


def _projection_metadata(
    metadata: LookupProjectionMetadata | Mapping[str, Any] | None,
) -> LookupProjectionMetadata:
    if metadata is None:
        return LookupProjectionMetadata()
    if isinstance(metadata, LookupProjectionMetadata):
        return metadata
    provider_data_keys = metadata.get("provider_data_keys", DEFAULT_PROVIDER_DATA_KEYS)
    return LookupProjectionMetadata(
        provider=metadata.get("provider"),
        tool_name=metadata.get("tool_name"),
        projection_type=metadata.get("projection_type", DEFAULT_PROJECTION_TYPE),
        object_type=metadata.get("object_type"),
        provider_data_keys=tuple(provider_data_keys),
    )


def _source_metadata(tool_name: str | None, method: str) -> dict[str, Any]:
    return clean_mapping(
        {
            "tool_name": tool_name,
            "method": method,
        }
    )


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def projection_from_result(
    method: str,
    result: Mapping[str, Any],
    *,
    projection_metadata: LookupProjectionMetadata | Mapping[str, Any] | None = None,
    projection_status: str = "resolved",
) -> dict[str, Any]:
    metadata = _projection_metadata(projection_metadata)
    # Lookup families return different identifier/label shapes:
    # ontology rows use curie/name, entity rows may use primary_external_id/symbol,
    # and provider rows use abbreviation/display_name.
    resolved_id = _first_present(
        result.get("curie"),
        result.get("primary_external_id"),
        result.get("id"),
        result.get("internal_id"),
        result.get("abbreviation"),
    )
    resolved_label = _first_present(
        result.get("symbol"),
        result.get("name"),
        result.get("display_name"),
        result.get("abbreviation"),
    )
    projection_key_value = _first_present(
        resolved_id, resolved_label, metadata.projection_type
    )
    projection_key = str(projection_key_value)
    provider_data = {
        key: result.get(key)
        for key in metadata.provider_data_keys
        if result.get(key) is not None
    }
    return clean_mapping(
        {
            "provider": metadata.provider,
            "projection_type": metadata.projection_type,
            "projection_key": projection_key,
            "projection_status": projection_status,
            "object_type": metadata.object_type,
            "resolved_id": resolved_id,
            "resolved_label": resolved_label,
            "source": _source_metadata(metadata.tool_name, method),
            "provider_data": provider_data or None,
        }
    )


def candidate_from_result(
    method: str,
    result: Mapping[str, Any],
    *,
    projection_metadata: LookupProjectionMetadata | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    # A candidate is a lightweight pointer to a match: just enough for the
    # reader to recognize it and look up the full details elsewhere. The full
    # projection lives once under result_projections, so it is intentionally
    # not re-embedded here (that previously duplicated every match's data).
    metadata = _projection_metadata(projection_metadata)
    projection = projection_from_result(
        method,
        result,
        projection_metadata=metadata,
    )
    return clean_mapping(
        {
            "provider": metadata.provider,
            "object_type": projection.get("object_type"),
            "candidate_id": projection.get("resolved_id"),
            "candidate_label": projection.get("resolved_label"),
            "match_type": result.get("match_type") or result.get("matched_variant"),
        }
    )


def projection_from_entity_match(
    method: str,
    result: Mapping[str, Any],
    *,
    taxon_id: str,
    projection_metadata: LookupProjectionMetadata | Mapping[str, Any] | None = None,
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
        projection_metadata=projection_metadata,
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
    projection_metadata: LookupProjectionMetadata | Mapping[str, Any] | None = None,
    target_projection: Mapping[str, Any] | None = None,
    resolved: Mapping[str, Any] | None = None,
    error: BaseException | None = None,
) -> dict[str, Any]:
    metadata = _projection_metadata(projection_metadata)
    resolved_projection = (
        projection_from_result(
            method,
            resolved,
            projection_metadata=metadata,
        )
        if resolved is not None
        else target_projection
    )
    payload = clean_mapping(
        {
            "source": _source_metadata(metadata.tool_name, method),
            "provider": metadata.provider,
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
    projection_metadata: LookupProjectionMetadata | Mapping[str, Any] | None = None,
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
        projection_metadata=projection_metadata,
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
        projection_metadata=projection_metadata,
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
    projection_metadata: LookupProjectionMetadata | Mapping[str, Any] | None = None,
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
            projection_metadata=projection_metadata,
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
    if attempts:
        attempt_statuses = [
            attempt.get("lookup_status")
            for attempt in attempts
            if attempt.get("lookup_status")
        ]
        if any(status == LOOKUP_STATUS_TRANSIENT for status in attempt_statuses):
            return LOOKUP_STATUS_TRANSIENT
        if attempt_statuses and all(
            status == LOOKUP_STATUS_BLOCKED for status in attempt_statuses
        ):
            return LOOKUP_STATUS_BLOCKED
    return LOOKUP_STATUS_NOT_FOUND


def _attempts_include_detail_lookup(
    attempts: list[dict[str, Any]] | None,
    *,
    detail_lookup_stages: Iterable[str] = (),
) -> bool:
    if not attempts:
        return False
    detail_stage_set = set(detail_lookup_stages)
    if not detail_stage_set:
        return False
    for attempt in attempts:
        attempted_query = attempt.get("attempted_query")
        if not isinstance(attempted_query, Mapping):
            continue
        lookup_stage = attempted_query.get("lookup_stage")
        if lookup_stage in detail_stage_set:
            return True
    return False


def bulk_item_status_from_lookup_status(
    lookup_status: str,
    *,
    count: int,
    attempts: list[dict[str, Any]] | None = None,
    detail_lookup_stages: Iterable[str] = (),
) -> str:
    """Map lookup metadata to explicit per-input bulk resolution status."""
    if count > 0:
        return "resolved"
    if _attempts_include_detail_lookup(
        attempts,
        detail_lookup_stages=detail_lookup_stages,
    ):
        return "detail_failure"
    try:
        return _BULK_ITEM_STATUS_BY_LOOKUP_STATUS[lookup_status]
    except KeyError as exc:
        raise ValueError(f"Unexpected bulk lookup status: {lookup_status!r}") from exc


def bulk_resolution_summary(items: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize bulk lookup resolution without confusing inputs with matches."""
    status_counts: dict[str, int] = {}
    resolved_count = 0
    resolved_input_count = 0

    for item in items:
        status = str(item["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
        item_count = item["count"]
        resolved_count += item_count
        if status == "resolved":
            resolved_input_count += 1

    requested_count = len(items)
    unresolved_input_count = requested_count - resolved_input_count
    blocked_input_count = (
        status_counts.get("blocked", 0)
        + status_counts.get("validation_warning", 0)
    )

    if requested_count == 0:
        resolution_status = "no_matches"
    elif resolved_input_count == requested_count:
        resolution_status = "resolved"
    elif resolved_input_count > 0:
        resolution_status = "partial"
    elif status_counts.get("detail_failure", 0) > 0:
        resolution_status = "detail_failure"
    elif status_counts.get("transient_failure", 0) > 0:
        resolution_status = "transient_failure"
    elif blocked_input_count == requested_count:
        resolution_status = "blocked"
    else:
        resolution_status = "no_matches"

    return {
        "requested_count": requested_count,
        "resolved_count": resolved_count,
        "total_matches": resolved_count,
        "resolved_input_count": resolved_input_count,
        "unresolved_input_count": unresolved_input_count,
        "resolution_status": resolution_status,
        "status_counts": status_counts,
    }


_BULK_ITEM_MATCH_LIST_KEYS = (
    "results",
    "candidate_matches",
    "result_projections",
)


def cap_bulk_total_matches(
    items: list[dict[str, Any]],
    *,
    total_match_cap: int = DEFAULT_BULK_TOTAL_MATCH_CAP,
) -> dict[str, Any]:
    """Bound the grand total of matched rows a bulk lookup hands back.

    Each input already respects its own per-input limit, but a long input list
    could still add up to an unbounded total. This trims item match lists in
    input order until the running total reaches ``total_match_cap``, edits each
    affected item in place (its match lists and ``count`` shrink to what was
    kept), and reports how much was returned versus found.

    Returns a small summary with ``total_count`` (matches found before the cap),
    ``returned_count`` (matches kept after the cap), and ``truncated`` (whether
    the cap removed anything).
    """
    total_count = sum(int(item.get("count", 0)) for item in items)
    if total_match_cap < 0:
        raise ValueError(f"total_match_cap must be non-negative: {total_match_cap!r}")

    remaining = total_match_cap
    returned_count = 0
    for item in items:
        item_count = int(item.get("count", 0))
        keep = item_count if item_count <= remaining else remaining
        if keep < item_count:
            for key in _BULK_ITEM_MATCH_LIST_KEYS:
                value = item.get(key)
                if isinstance(value, list):
                    item[key] = value[:keep]
            item["count"] = keep
            item["match_total_before_cap"] = item_count
        returned_count += keep
        remaining -= keep

    return {
        "total_count": total_count,
        "returned_count": returned_count,
        "truncated": returned_count < total_count,
        "total_match_cap": total_match_cap,
    }


def lookup_explanation(
    *,
    method: str,
    lookup_status: str,
    count: int,
    attempted_query: Mapping[str, Any],
) -> str:
    target = attempted_query.get("method") or method
    for key, value in attempted_query.items():
        if key == "method" or value is None or value == []:
            continue
        target = value
        break
    if lookup_status == LOOKUP_STATUS_SUCCESS:
        return f"{method} resolved {target!r} to {count} lookup result(s)."
    if lookup_status == LOOKUP_STATUS_AMBIGUOUS:
        return (
            f"{method} found {count} candidate lookup results for {target!r}; "
            "curator or repair logic must choose one."
        )
    if lookup_status == LOOKUP_STATUS_TRANSIENT:
        return (
            f"{method} could not complete for {target!r} because one or more "
            "lookup calls failed transiently."
        )
    if lookup_status == LOOKUP_STATUS_BLOCKED:
        return (
            f"{method} was not executed for {target!r} because a configured validator "
            "or runtime prerequisite is blocked."
        )
    if lookup_status == LOOKUP_STATUS_UNDER_DEVELOPMENT:
        return f"{method} is declared for {target!r} but is still under development."
    return f"{method} tried the lookup provider for {target!r} and found no matching result."


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
    projection_metadata: LookupProjectionMetadata | Mapping[str, Any] | None = None,
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
    metadata = _projection_metadata(projection_metadata)
    projections = [
        projection_from_result(method, row, projection_metadata=metadata) for row in rows
    ]
    candidates = [
        candidate_from_result(method, row, projection_metadata=metadata) for row in rows
    ]
    lookup_attempts = attempts or [
        lookup_attempt(
            method=method,
            attempted_query=query,
            lookup_status=lookup_status,
            explanation=explanation,
            candidate_count=count_value,
            projection_metadata=metadata,
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


_DERIVED_ROW_VIEW_KEYS = ("candidate_matches", "result_projections")
_MATCHED_ROW_ATTEMPT_KEYS = ("target_projection", "resolved_id", "resolved_label")


def _lean_lookup_level(level: Mapping[str, Any]) -> dict[str, Any]:
    """Drop one response level's restatements of its own rows and text.

    ``candidate_matches`` and ``result_projections`` are derived from the
    level's returned rows, so they name exactly the row identities an
    attempt's matched-row projection repeats. An attempt about a record that
    is not among the returned rows (for example a failed detail fetch) keeps
    its projection, because that record appears nowhere else.
    """
    returned_ids = {
        projection.get("resolved_id")
        for projection in level.get("result_projections") or []
        if isinstance(projection, Mapping)
    } | {
        candidate.get("candidate_id")
        for candidate in level.get("candidate_matches") or []
        if isinstance(candidate, Mapping)
    }
    returned_ids.discard(None)
    lean = {key: value for key, value in level.items() if key not in _DERIVED_ROW_VIEW_KEYS}
    level_texts = [text for text in (level.get("message"), level.get("explanation")) if text is not None]
    if lean.get("explanation") is not None and lean.get("explanation") == lean.get("message"):
        del lean["explanation"]
    attempts = lean.get("lookup_attempts")
    if isinstance(attempts, list):
        lean_attempts = []
        for attempt in attempts:
            if not isinstance(attempt, Mapping):
                lean_attempts.append(attempt)
                continue
            attempt = dict(attempt)
            projection = attempt.get("target_projection")
            # lookup_attempt derives resolved_id from target_projection, so the
            # projection alone names the matched record.
            matched_id = projection.get("resolved_id") if isinstance(projection, Mapping) else None
            if matched_id is not None and matched_id in returned_ids:
                for key in _MATCHED_ROW_ATTEMPT_KEYS:
                    attempt.pop(key, None)
            if attempt.get("explanation") in level_texts:
                del attempt["explanation"]
            if "coverage" in attempt and attempt["coverage"] == level.get("coverage"):
                del attempt["coverage"]
            lean_attempts.append(attempt)
        lean["lookup_attempts"] = lean_attempts
    return lean


def lookup_model_view(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the model-facing reading of a lookup response: each match once.

    ``lookup_response_payload`` responses restate every returned row as a
    candidate match, a result projection and, for single matches, an
    attempt's target projection, and repeat the message as the explanation.
    The model needs each row once, so those restatements are dropped here,
    at the top level and in each bulk input group (``data.items[]``). Rows,
    counts, statuses, coverage, queries and errors are kept, and no row list
    is reindexed, so JSON pointers into the complete response still resolve
    in this view. Top-level attempts that repeat a bulk group's attempt are
    dropped because the group already carries them. The caller keeps the
    complete response; this view is never a substitute for it.
    """
    lean = _lean_lookup_level(payload)
    data = payload.get("data")
    if isinstance(data, Mapping) and isinstance(data.get("items"), list):
        group_attempts = [
            attempt
            for item in data["items"]
            if isinstance(item, Mapping)
            for attempt in item.get("lookup_attempts") or []
        ]
        lean["data"] = {
            **data,
            "items": [
                _lean_lookup_level(item) if isinstance(item, Mapping) else item
                for item in data["items"]
            ],
        }
        top_attempts = payload.get("lookup_attempts")
        if isinstance(top_attempts, list) and group_attempts:
            kept = [
                lean_attempt
                for attempt, lean_attempt in zip(top_attempts, lean["lookup_attempts"])
                if attempt not in group_attempts
            ]
            if kept:
                lean["lookup_attempts"] = kept
            else:
                del lean["lookup_attempts"]
    return lean


def chunk_values(values: list[str], chunk_size: int = 200) -> list[list[str]]:
    """Return fixed-size chunks to keep SQL IN clauses bounded."""
    return [values[i:i + chunk_size] for i in range(0, len(values), chunk_size)]


def create_db_session(db: Any) -> Any | None:
    create_session = getattr(db, "create_session", None)
    if not callable(create_session):
        return None
    return create_session()


__all__ = [
    "DETAIL_RETRY_STRATEGY_PER_CURIE",
    "DEFAULT_PROJECTION_TYPE",
    "DEFAULT_PROVIDER_DATA_KEYS",
    "DEFAULT_BULK_TOTAL_MATCH_CAP",
    "LookupProjectionMetadata",
    "LOOKUP_STATUS_SUCCESS",
    "LOOKUP_STATUS_NOT_FOUND",
    "LOOKUP_STATUS_AMBIGUOUS",
    "LOOKUP_STATUS_TRANSIENT",
    "LOOKUP_STATUS_BLOCKED",
    "LOOKUP_STATUS_UNDER_DEVELOPMENT",
    "clean_mapping",
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
    "bulk_item_status_from_lookup_status",
    "bulk_resolution_summary",
    "cap_bulk_total_matches",
    "lookup_explanation",
    "lookup_response_payload",
    "lookup_model_view",
    "chunk_values",
    "create_db_session",
]
