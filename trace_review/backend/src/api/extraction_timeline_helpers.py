"""Shared TraceReview extraction timeline endpoint helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional

from ..analyzers.extraction_timeline import (
    ExtractionTimelineAnalyzer,
    feedback_trace_sibling_ids,
)
from ..services.feedback_artifacts import fetch_feedback_trace_artifacts


TraceCacheLoader = Callable[[], Awaitable[Dict[str, Any]]]
SiblingTraceLoader = Callable[[], List[str]]
SiblingTraceCacheLoader = Callable[[str], Awaitable[Dict[str, Any]]]
ExceptionFactory = Callable[[BaseException], BaseException]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractionTimelineContext:
    cached_data: Dict[str, Any]
    sibling_trace_ids: List[str]
    sibling_cached_data_by_trace_id: Dict[str, Dict[str, Any]]
    feedback_artifacts: Optional[Dict[str, Any]]


def _feedback_trace_data(
    feedback_artifacts: Optional[Dict[str, Any]],
) -> Mapping[str, Any] | None:
    if not isinstance(feedback_artifacts, dict):
        return None
    trace_data = feedback_artifacts.get("trace_data")
    return trace_data if isinstance(trace_data, Mapping) else None


def _stored_feedback_cache_data(trace_id: str) -> Dict[str, Any]:
    return {
        "raw_trace": {
            "id": trace_id,
            "name": "Stored feedback trace artifact",
        },
        "observations": [],
    }


def _merge_feedback_sibling_trace_ids(
    *,
    trace_id: str,
    sibling_trace_ids: List[str],
    feedback_trace_data: Mapping[str, Any] | None,
    include_sibling_traces: bool,
) -> List[str]:
    if not include_sibling_traces:
        return []

    siblings = list(sibling_trace_ids)
    for sibling_id in feedback_trace_sibling_ids(trace_id, feedback_trace_data):
        if sibling_id not in siblings:
            siblings.append(sibling_id)
    return siblings


async def load_extraction_timeline_context(
    *,
    trace_id: str,
    feedback_id: Optional[str],
    include_sibling_traces: bool,
    load_cached_data: TraceCacheLoader,
    load_sibling_trace_ids: SiblingTraceLoader,
    load_sibling_cached_data: SiblingTraceCacheLoader,
    fallback_exceptions: tuple[type[BaseException], ...],
    unavailable_exception_factory: ExceptionFactory | None = None,
) -> ExtractionTimelineContext:
    feedback_artifacts = fetch_feedback_trace_artifacts(feedback_id)
    feedback_trace_data = _feedback_trace_data(feedback_artifacts)

    try:
        cached_data = await load_cached_data()
        sibling_trace_ids = _merge_feedback_sibling_trace_ids(
            trace_id=trace_id,
            sibling_trace_ids=load_sibling_trace_ids(),
            feedback_trace_data=feedback_trace_data,
            include_sibling_traces=include_sibling_traces,
        )
        sibling_cached_data_by_trace_id: Dict[str, Dict[str, Any]] = {}
        for sibling_trace_id in sibling_trace_ids:
            try:
                sibling_cached_data_by_trace_id[sibling_trace_id] = await load_sibling_cached_data(
                    sibling_trace_id
                )
            except fallback_exceptions as exc:
                logger.debug(
                    "Skipping extraction timeline sibling cached data: trace_id=%s sibling_trace_id=%s error=%s",
                    trace_id,
                    sibling_trace_id,
                    exc,
                )
                continue
    except fallback_exceptions as exc:
        if feedback_trace_data is None:
            if unavailable_exception_factory is not None:
                raise unavailable_exception_factory(exc) from exc
            raise
        cached_data = _stored_feedback_cache_data(trace_id)
        sibling_trace_ids = _merge_feedback_sibling_trace_ids(
            trace_id=trace_id,
            sibling_trace_ids=[],
            feedback_trace_data=feedback_trace_data,
            include_sibling_traces=include_sibling_traces,
        )
        sibling_cached_data_by_trace_id = {}

    return ExtractionTimelineContext(
        cached_data=cached_data,
        sibling_trace_ids=sibling_trace_ids,
        sibling_cached_data_by_trace_id=sibling_cached_data_by_trace_id,
        feedback_artifacts=feedback_artifacts,
    )


def build_extraction_timeline(
    *,
    trace_id: str,
    context: ExtractionTimelineContext,
    include_raw_args: bool,
    include_raw_outputs: bool,
    tool_name: Optional[str],
    event_type: Optional[str],
    candidate_id: Optional[str],
    session_id: Optional[str] = None,
    feedback_id: Optional[str] = None,
) -> Dict[str, Any]:
    feedback_trace_data = _feedback_trace_data(context.feedback_artifacts)
    sibling_observations_by_trace_id = {
        sibling_trace_id: observations
        for sibling_trace_id, sibling_cached_data in context.sibling_cached_data_by_trace_id.items()
        if isinstance((observations := sibling_cached_data.get("observations")), list)
    }
    timeline = ExtractionTimelineAnalyzer.analyze(
        trace_id=trace_id,
        raw_trace=context.cached_data.get("raw_trace"),
        observations=context.cached_data.get("observations", []),
        include_raw_args=include_raw_args,
        include_raw_outputs=include_raw_outputs,
        tool_name=tool_name,
        event_type=event_type,
        candidate_id=candidate_id,
        sibling_trace_ids=context.sibling_trace_ids,
        sibling_observations_by_trace_id=sibling_observations_by_trace_id,
        feedback_trace_data=feedback_trace_data,
    )
    timeline["query"] = {
        "session_id": session_id,
        "feedback_id": feedback_id,
        "feedback_artifact_status": (
            context.feedback_artifacts.get("status")
            if isinstance(context.feedback_artifacts, dict)
            else None
        ),
        "include_raw_args": include_raw_args,
        "include_raw_outputs": include_raw_outputs,
        "tool_name": tool_name,
        "event_type": event_type,
        "candidate_id": candidate_id,
    }
    return timeline


def build_evidence_revisions(
    *,
    trace_id: str,
    context: ExtractionTimelineContext,
    tool_name: Optional[str],
    event_type: Optional[str],
    candidate_id: Optional[str],
    session_id: Optional[str] = None,
    feedback_id: Optional[str] = None,
) -> Dict[str, Any]:
    timeline = build_extraction_timeline(
        trace_id=trace_id,
        context=context,
        include_raw_args=True,
        include_raw_outputs=True,
        tool_name=tool_name,
        event_type=event_type,
        candidate_id=candidate_id,
        session_id=session_id,
        feedback_id=feedback_id,
    )
    return ExtractionTimelineAnalyzer.evidence_revisions(timeline)
