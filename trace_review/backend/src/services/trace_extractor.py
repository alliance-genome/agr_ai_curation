"""
Langfuse Trace Extraction Service
Fetches and processes trace data from Langfuse API
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, cast
from langfuse import Langfuse
from ..analyzers.domain_envelopes import DomainEnvelopeTraceAnalyzer
from ..config import (
    get_langfuse_observation_page_limit,
    get_langfuse_request_timeout_seconds,
    get_langfuse_search_observation_limit,
    get_langfuse_search_request_limit,
    get_trace_source_runtime_config,
)
from .langfuse_run_reconstruction import usage_cost_summary

logger = logging.getLogger(__name__)
OBSERVATION_FIELDS = (
    "core,basic,time,io,metadata,model,usage,metrics,trace_context"
)
SESSION_OBSERVATION_FIELDS = "core,basic,time,trace_context"
TRACE_LIST_OBSERVATION_FIELDS = (
    "core,basic,time,metadata,usage,metrics,trace_context"
)
SESSION_TRACE_LIST_LIMIT = 100


class TraceNotFoundError(RuntimeError):
    """Raised when Langfuse has no observations for an exact trace ID."""


class TraceExtractor:
    """Service for extracting trace data from Langfuse"""

    def __init__(self, source: str = "remote"):
        """
        Initialize with API credentials based on source

        Args:
            source: "remote" (default) or "local"
        """
        source_config = get_trace_source_runtime_config(source)
        self.source = source
        self.host = source_config["host"] or ""
        self.public_key = source_config["public_key"] or ""
        self.secret_key = source_config["secret_key"] or ""

        if not self.host:
            raise ValueError(f"Langfuse host must be set for {source} source")

        if not self.public_key or not self.secret_key:
            if source == "local":
                raise ValueError("LANGFUSE_LOCAL_PUBLIC_KEY and LANGFUSE_LOCAL_SECRET_KEY must be set for local source")
            raise ValueError("LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must be set for remote source")

        # Log configuration for troubleshooting
        logger.debug("TraceExtractor initialized: source=%s, host=%s, pk=%s...", source, self.host, self.public_key[:20] if self.public_key else "None")

        # Initialize Langfuse SDK client
        self.client = Langfuse(
            public_key=self.public_key,
            secret_key=self.secret_key,
            host=self.host
        )

    @staticmethod
    def _normalize_item(item: Any) -> Dict:
        """Convert Langfuse SDK models into plain dictionaries."""
        if hasattr(item, "model_dump"):
            return item.model_dump()
        if hasattr(item, "dict"):
            return item.dict()
        return item

    @staticmethod
    def _first_present(mapping: Mapping[str, Any], *keys: str) -> Any:
        for key in keys:
            if mapping.get(key) is not None:
                return mapping[key]
        return None

    @staticmethod
    def _normalized_datetime(value: Any) -> Optional[datetime]:
        """Normalize SDK/string timestamps for exact, timezone-safe comparisons."""
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _is_root_observation(cls, observation: Mapping[str, Any]) -> bool:
        return bool(
            observation.get("isRootObservation") is True
            or observation.get("is_root_observation") is True
            or not cls._first_present(
                observation,
                "parentObservationId",
                "parent_observation_id",
            )
        )

    @classmethod
    def _trace_latency(cls, observations: List[Dict[str, Any]]) -> float:
        starts: List[datetime] = []
        ends: List[datetime] = []
        for observation in observations:
            start = cls._normalized_datetime(
                cls._first_present(observation, "startTime", "start_time")
            )
            if start is None:
                continue
            starts.append(start)
            end = cls._normalized_datetime(
                cls._first_present(observation, "endTime", "end_time")
            )
            if end is None:
                latency = observation.get("latency")
                if isinstance(latency, (int, float)) and not isinstance(latency, bool):
                    end = start + timedelta(seconds=max(0.0, float(latency)))
            if end is not None:
                ends.append(end)
        if not starts:
            return 0.0
        if ends:
            return max(0.0, (max(ends) - min(starts)).total_seconds())
        if len(starts) > 1:
            return max(0.0, (max(starts) - min(starts)).total_seconds())
        return 0.0

    @classmethod
    def _normalize_v2_observation(cls, item: Any) -> Dict[str, Any]:
        """Preserve v2 fields and add the compatibility aliases used by analyzers."""
        observation = cls._normalize_item(item)
        usage_details = observation.get("usage_details") or observation.get("usageDetails") or {}
        cost_details = observation.get("cost_details") or observation.get("costDetails") or {}
        input_tokens = cls._first_present(
            observation,
            "inputUsage",
            "input_usage",
        )
        output_tokens = cls._first_present(
            observation,
            "outputUsage",
            "output_usage",
        )
        total_tokens = cls._first_present(
            observation,
            "totalUsage",
            "total_usage",
        )
        if input_tokens is None:
            input_tokens = usage_details.get("input") or usage_details.get("input_tokens") or 0
        if output_tokens is None:
            output_tokens = usage_details.get("output") or usage_details.get("output_tokens") or 0
        if total_tokens is None:
            total_tokens = usage_details.get("total") or usage_details.get("total_tokens")
        if total_tokens is None:
            total_tokens = (input_tokens or 0) + (output_tokens or 0)

        calculated_total_cost = cls._first_present(
            observation,
            "calculatedTotalCost",
            "calculated_total_cost",
            "totalCost",
            "total_cost",
        )
        if calculated_total_cost is None:
            calculated_total_cost = cls._first_present(cost_details, "total", "total_cost")

        model_name = cls._first_present(
            observation,
            "providedModelName",
            "provided_model_name",
            "model",
            "modelName",
            "model_name",
            "internal_model_id",
            "model_id",
        )

        observation.update({
            "traceId": cls._first_present(observation, "traceId", "trace_id"),
            "parentObservationId": cls._first_present(
                observation,
                "parentObservationId",
                "parent_observation_id",
            ),
            "startTime": cls._first_present(observation, "startTime", "start_time"),
            "endTime": cls._first_present(observation, "endTime", "end_time"),
            "completionStartTime": cls._first_present(
                observation,
                "completionStartTime",
                "completion_start_time",
            ),
            "timeToFirstToken": cls._first_present(
                observation,
                "timeToFirstToken",
                "time_to_first_token",
            ),
            "statusMessage": cls._first_present(
                observation,
                "statusMessage",
                "status_message",
            ),
            "modelParameters": cls._first_present(
                observation,
                "modelParameters",
                "model_parameters",
            ) or {},
            "providedModelName": model_name,
            "model": model_name,
            "usageDetails": dict(usage_details),
            "costDetails": dict(cost_details),
            "usage": {
                "input": input_tokens or 0,
                "output": output_tokens or 0,
                "total": total_tokens or 0,
            },
            "promptTokens": input_tokens or 0,
            "completionTokens": output_tokens or 0,
            "totalTokens": total_tokens or 0,
            "calculatedTotalCost": calculated_total_cost,
        })
        return observation

    @classmethod
    def get_trace_details(
        cls,
        trace_id: str,
        observations: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Build trace context from the v2 root observation without a legacy read."""
        if not observations:
            raise TraceNotFoundError(
                f"Langfuse returned no v2 observations for trace {trace_id}"
            )

        ordered = sorted(
            observations,
            key=lambda item: (
                str(item.get("startTime") or item.get("start_time") or ""),
                str(item.get("id") or ""),
            ),
        )
        roots = [item for item in ordered if cls._is_root_observation(item)]
        root = roots[0] if roots else ordered[0]
        context = next(
            (
                item for item in ordered
                if item.get("trace_name")
                or item.get("session_id")
                or item.get("user_id")
            ),
            root,
        )

        project_id = cls._first_present(root, "projectId", "project_id")
        total_cost = sum(
            usage_cost_summary(observation)["total_cost"]
            for observation in observations
        )

        return {
            "id": trace_id,
            "rootObservationId": root.get("id"),
            "name": context.get("trace_name") or root.get("trace_name") or root.get("name"),
            "timestamp": root.get("startTime") or root.get("start_time"),
            "sessionId": context.get("session_id") or root.get("session_id"),
            "userId": context.get("user_id") or root.get("user_id"),
            "metadata": root.get("metadata") or {},
            "tags": context.get("tags") or root.get("tags") or [],
            "environment": context.get("environment") or root.get("environment"),
            "input": root.get("input"),
            "output": root.get("output"),
            "latency": cls._trace_latency(observations),
            "totalCost": total_cost,
            "calculatedTotalCost": total_cost,
            "htmlPath": (
                f"/project/{project_id}/traces/{trace_id}"
                if project_id
                else None
            ),
            "observations": [
                observation_id
                for observation in observations
                if (observation_id := observation.get("id"))
            ],
        }

    def list_traces(
        self,
        *,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        name: Optional[str] = None,
        document_id: Optional[str] = None,
        run_id: Optional[str] = None,
        extraction_id: Optional[str] = None,
        offset: int = 0,
        limit: int = SESSION_TRACE_LIST_LIMIT,
        from_timestamp: Optional[datetime] = None,
        to_timestamp: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """List traces through the Langfuse v4 observations API.

        Langfuse v4 has no trace-list endpoint. The supported replacement is a
        cursor-paginated observation query grouped by trace ID on the client.
        """
        filters: List[Dict[str, Any]] = []
        if session_id:
            filters.append({
                "type": "string",
                "column": "sessionId",
                "operator": "=",
                "value": session_id,
            })
        if name:
            filters.append({
                "type": "string",
                "column": "traceName",
                "operator": "=",
                "value": name,
            })
        if user_id:
            filters.append({
                "type": "string",
                "column": "userId",
                "operator": "=",
                "value": user_id,
            })
        normalized_from = self._normalized_datetime(from_timestamp)
        normalized_to = self._normalized_datetime(to_timestamp)
        if normalized_from:
            filters.append({
                "type": "datetime",
                "column": "startTime",
                "operator": ">=",
                "value": normalized_from.isoformat(),
            })
        if normalized_to:
            filters.append({
                "type": "datetime",
                "column": "startTime",
                "operator": "<",
                "value": normalized_to.isoformat(),
            })
        metadata_filters = {
            "document_id": document_id,
            "run_id": run_id,
            "extraction_id": extraction_id,
        }
        for key, value in metadata_filters.items():
            if value:
                filters.append({
                    "type": "stringObject",
                    "column": "metadata",
                    "key": key,
                    "operator": "=",
                    "value": value,
                })

        observations_by_trace: Dict[str, List[Dict[str, Any]]] = {}
        trace_order: List[str] = []
        cursor: Optional[str] = None
        seen_cursors = set()
        seen_observation_ids = set()
        page_count = 0
        scanned_observation_count = 0
        rejected_observation_count = 0
        request_count = 0
        filter_json = json.dumps(filters) if filters else None

        safe_offset = max(0, offset)
        requested_end = safe_offset + max(1, limit)
        search_observation_limit = get_langfuse_search_observation_limit()
        search_request_limit = get_langfuse_search_request_limit()
        source_page_limit = min(get_langfuse_observation_page_limit(), search_observation_limit)
        request_options = {
            "timeout_in_seconds": get_langfuse_request_timeout_seconds(),
        }
        source_exhausted = False
        scan_truncated = False
        while True:
            if request_count >= search_request_limit:
                scan_truncated = True
                break
            remaining_scan = search_observation_limit - scanned_observation_count
            if remaining_scan <= 0:
                scan_truncated = True
                break

            response = self.client.api.observations.get_many(
                fields=TRACE_LIST_OBSERVATION_FIELDS,
                limit=min(source_page_limit, remaining_scan),
                cursor=cursor,
                filter=filter_json,
                request_options=request_options,
            )
            request_count += 1
            response_data = getattr(response, "data", None) or []
            scanned_observation_count += len(response_data)
            page_count += 1
            for item in response_data:
                observation = self._normalize_v2_observation(item)
                if not self._observation_matches_trace_search(
                    observation,
                    session_id=session_id,
                    user_id=user_id,
                    name=name,
                    metadata_filters=metadata_filters,
                    from_timestamp=from_timestamp,
                    to_timestamp=to_timestamp,
                ):
                    rejected_observation_count += 1
                    continue
                observation_id = observation.get("id")
                if observation_id and observation_id in seen_observation_ids:
                    continue
                if observation_id:
                    seen_observation_ids.add(observation_id)
                trace_id = observation.get("traceId") or observation.get("trace_id")
                if not trace_id:
                    continue
                normalized_trace_id = str(trace_id)
                if normalized_trace_id not in observations_by_trace:
                    observations_by_trace[normalized_trace_id] = []
                    trace_order.append(normalized_trace_id)
                observations_by_trace[normalized_trace_id].append(observation)

            response_meta = getattr(response, "meta", None)
            next_cursor = (
                getattr(response_meta, "cursor", None)
                if response_meta is not None
                else None
            )
            if not next_cursor:
                source_exhausted = True
                break
            if not response_data:
                raise RuntimeError(
                    "Langfuse returned an empty trace-search page with a continuation cursor"
                )
            if next_cursor in seen_cursors:
                raise RuntimeError("Langfuse repeated trace-search observation cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        if (
            scanned_observation_count >= search_observation_limit
            and len(trace_order) < requested_end
            and not source_exhausted
        ):
            scan_truncated = True

        traces: List[Dict[str, Any]] = []
        rejected_trace_count = 0
        hydration_truncated = False
        must_hydrate_all = bool(
            from_timestamp
            or to_timestamp
            or scan_truncated
            or any(metadata_filters.values())
        )
        for trace_id in trace_order:
            observations = observations_by_trace[trace_id]
            if must_hydrate_all or not any(
                self._is_root_observation(observation)
                for observation in observations
            ):
                observations, requests_made, hydration_complete = (
                    self._get_observations_bounded(
                        trace_id,
                        fields=TRACE_LIST_OBSERVATION_FIELDS,
                        max_requests=search_request_limit - request_count,
                    )
                )
                request_count += requests_made
                if not hydration_complete:
                    hydration_truncated = True
                    break
            trace = self.get_trace_details(trace_id, observations)
            if not self._trace_matches_search(
                trace,
                session_id=session_id,
                user_id=user_id,
                name=name,
                metadata_filters=metadata_filters,
                from_timestamp=from_timestamp,
                to_timestamp=to_timestamp,
            ):
                rejected_trace_count += 1
                continue
            traces.append(trace)

        scan_truncated = scan_truncated or hydration_truncated

        traces.sort(
            key=lambda trace: (
                self._normalized_datetime(trace.get("timestamp"))
                or datetime.min.replace(tzinfo=timezone.utc),
                str(trace.get("id") or ""),
            )
        )
        meta = {
            "cursor": cursor,
            "pagesScanned": page_count,
            "observationsScanned": scanned_observation_count,
            "observationsRejected": rejected_observation_count,
            "tracesRejected": rejected_trace_count,
            "scanLimit": search_observation_limit,
            "requestLimit": search_request_limit,
            "requestsMade": request_count,
            "scanTruncated": scan_truncated,
            "hydrationTruncated": hydration_truncated,
        }
        total_items = len(traces) if source_exhausted and not scan_truncated else None

        return {
            "source": self.source,
            "traces": traces[safe_offset:requested_end],
            "meta": meta,
            "total_items": total_items,
            "source_exhausted": source_exhausted,
            "scan_truncated": scan_truncated,
            "local_result_count": len(traces),
            "query": {
                "session_id": session_id,
                "user_id": user_id,
                "name": name,
                "document_id": document_id,
                "run_id": run_id,
                "extraction_id": extraction_id,
                "offset": safe_offset,
                "limit": limit,
                "from_timestamp": from_timestamp.isoformat() if from_timestamp else None,
                "to_timestamp": to_timestamp.isoformat() if to_timestamp else None,
            },
        }

    @staticmethod
    def _observation_matches_trace_search(
        observation: Mapping[str, Any],
        *,
        session_id: Optional[str],
        user_id: Optional[str],
        name: Optional[str],
        metadata_filters: Mapping[str, Optional[str]],
        from_timestamp: Optional[datetime],
        to_timestamp: Optional[datetime],
    ) -> bool:
        """Fail closed if a provider-side exact filter is ignored."""
        if session_id and str(observation.get("session_id") or "") != session_id:
            return False
        if user_id and str(observation.get("user_id") or "") != user_id:
            return False
        if name and str(observation.get("trace_name") or "") != name:
            return False
        timestamp = TraceExtractor._normalized_datetime(
            TraceExtractor._first_present(
                observation,
                "startTime",
                "start_time",
            )
        )
        normalized_from = TraceExtractor._normalized_datetime(from_timestamp)
        normalized_to = TraceExtractor._normalized_datetime(to_timestamp)
        if normalized_from and (timestamp is None or timestamp < normalized_from):
            return False
        if normalized_to and (timestamp is None or timestamp >= normalized_to):
            return False

        metadata = observation.get("metadata")
        if not isinstance(metadata, Mapping):
            metadata = {}
        for key, value in metadata_filters.items():
            if value and str(metadata.get(key) or "") != value:
                return False
        return True

    @classmethod
    def _trace_matches_search(
        cls,
        trace: Mapping[str, Any],
        *,
        session_id: Optional[str],
        user_id: Optional[str],
        name: Optional[str],
        metadata_filters: Mapping[str, Optional[str]],
        from_timestamp: Optional[datetime],
        to_timestamp: Optional[datetime],
    ) -> bool:
        """Verify the reconstructed trace, not a possibly matching child row."""
        if session_id and str(trace.get("sessionId") or "") != session_id:
            return False
        if user_id and str(trace.get("userId") or "") != user_id:
            return False
        if name and str(trace.get("name") or "") != name:
            return False

        timestamp = cls._normalized_datetime(trace.get("timestamp"))
        normalized_from = cls._normalized_datetime(from_timestamp)
        normalized_to = cls._normalized_datetime(to_timestamp)
        if normalized_from and (timestamp is None or timestamp < normalized_from):
            return False
        if normalized_to and (timestamp is None or timestamp >= normalized_to):
            return False

        metadata = trace.get("metadata")
        if not isinstance(metadata, Mapping):
            metadata = {}
        return all(
            not value or str(metadata.get(key) or "") == value
            for key, value in metadata_filters.items()
        )

    def probe_v4_capabilities(self) -> Dict[str, Any]:
        """Exercise exact-trace and session discovery without exposing trace data."""
        request_options = {
            "timeout_in_seconds": get_langfuse_request_timeout_seconds(),
        }
        checks: Dict[str, Dict[str, Any]] = {}
        probes = (
            (
                "explicit_trace",
                {"trace_id": "__trace_review_health_missing_trace__"},
                "traceId",
                "__trace_review_health_missing_trace__",
            ),
            (
                "session_discovery",
                {
                    "filter": json.dumps([{
                        "type": "string",
                        "column": "sessionId",
                        "operator": "=",
                        "value": "__trace_review_health_missing_session__",
                    }]),
                },
                "sessionId",
                "__trace_review_health_missing_session__",
            ),
        )
        for check_name, query, field_name, expected in probes:
            try:
                response = self.client.api.observations.get_many(
                    fields="core,basic",
                    limit=1,
                    request_options=request_options,
                    **query,
                )
                rows = [
                    self._normalize_v2_observation(item)
                    for item in (getattr(response, "data", None) or [])
                ]
                widened = any(
                    str(self._first_present(row, field_name, "session_id") or "")
                    != expected
                    for row in rows
                )
                checks[check_name] = {
                    "status": "error" if widened else "ok",
                    "filter_widened": widened,
                }
            except Exception as exc:
                checks[check_name] = {
                    "status": "error",
                    "error_type": exc.__class__.__name__,
                }

        return {
            "status": (
                "ok"
                if all(check["status"] == "ok" for check in checks.values())
                else "degraded"
            ),
            "query_surface": "observations_v2",
            "checks": checks,
        }

    def list_session_traces(self, session_id: str, limit: int = SESSION_TRACE_LIST_LIMIT) -> Dict[str, Any]:
        """List a session's traces from cursor-paginated v2 observations."""
        observations_by_trace: Dict[str, List[Dict[str, Any]]] = {}
        cursor: Optional[str] = None
        seen_cursors = set()
        page_count = 0
        page_limit = min(get_langfuse_observation_page_limit(), max(1, limit))
        filter_json = json.dumps([{
            "type": "string",
            "column": "sessionId",
            "operator": "=",
            "value": session_id,
        }])
        request_options = {
            "timeout_in_seconds": get_langfuse_request_timeout_seconds(),
        }

        while True:
            try:
                response = self.client.api.observations.get_many(
                    fields=SESSION_OBSERVATION_FIELDS,
                    filter=filter_json,
                    limit=page_limit,
                    cursor=cursor,
                    request_options=request_options,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Unable to list Langfuse traces for session {session_id} "
                    f"from {self.source}: {exc.__class__.__name__}"
                ) from exc

            page_count += 1
            for item in getattr(response, "data", None) or []:
                observation = self._normalize_v2_observation(item)
                observation_session_id = self._first_present(
                    observation,
                    "session_id",
                    "sessionId",
                )
                if str(observation_session_id or "") != session_id:
                    raise RuntimeError(
                        "Langfuse session filter returned an observation from another session"
                    )
                trace_id = observation.get("traceId") or observation.get("trace_id")
                if trace_id:
                    observations_by_trace.setdefault(str(trace_id), []).append(observation)

            response_meta = getattr(response, "meta", None)
            cursor = getattr(response_meta, "cursor", None) if response_meta is not None else None
            if not cursor:
                break
            if cursor in seen_cursors:
                raise RuntimeError(
                    f"Langfuse repeated session observation cursor for session {session_id}"
                )
            seen_cursors.add(cursor)

        traces: List[Dict[str, Any]] = []
        for trace_id, observations in observations_by_trace.items():
            trace = self.get_trace_details(trace_id, observations)
            traces.append(trace)
        traces.sort(key=lambda trace: (str(trace.get("timestamp") or ""), str(trace.get("id") or "")))

        meta = {
            "page": page_count,
            "limit": page_limit,
            "totalItems": len(traces),
            "totalPages": page_count,
        }

        return {
            "session_id": session_id,
            "source": self.source,
            "traces": traces,
            "meta": meta,
        }

    def _get_observations_bounded(
        self,
        trace_id: str,
        *,
        fields: str,
        max_requests: Optional[int],
    ) -> tuple[List[Dict], int, bool]:
        """Get an exact trace, optionally stopping at a provider-request budget."""
        observations: List[Dict] = []
        cursor: Optional[str] = None
        seen_cursors = set()
        seen_observation_ids = set()
        page_limit = get_langfuse_observation_page_limit()
        request_options = {
            "timeout_in_seconds": get_langfuse_request_timeout_seconds(),
        }

        request_count = 0
        while max_requests is None or request_count < max_requests:
            response = self.client.api.observations.get_many(
                trace_id=trace_id,
                fields=fields,
                limit=page_limit,
                cursor=cursor,
                request_options=request_options,
            )
            request_count += 1
            response_data = getattr(response, "data", None)
            if response_data:
                for item in response_data:
                    observation = self._normalize_v2_observation(item)
                    returned_trace_id = observation.get("traceId") or observation.get("trace_id")
                    if str(returned_trace_id or "") != trace_id:
                        raise RuntimeError(
                            "Langfuse returned an observation outside requested trace "
                            f"{trace_id}"
                        )
                    observation_id = observation.get("id")
                    if observation_id and observation_id in seen_observation_ids:
                        continue
                    if observation_id:
                        seen_observation_ids.add(observation_id)
                    observations.append(observation)

            meta = getattr(response, "meta", None)
            cursor = getattr(meta, "cursor", None) if meta is not None else None
            if not cursor:
                return observations, request_count, True
            if cursor in seen_cursors:
                raise RuntimeError(
                    f"Langfuse repeated observation cursor for trace {trace_id}"
                )
            seen_cursors.add(cursor)

        return observations, request_count, False

    def get_observations(self, trace_id: str) -> List[Dict]:
        """Get every observation through the cursor-paginated v2 API."""
        observations, _request_count, complete = self._get_observations_bounded(
            trace_id,
            fields=OBSERVATION_FIELDS,
            max_requests=None,
        )
        if not complete:  # pragma: no cover - an unlimited read completes or raises
            raise RuntimeError(f"Langfuse observation read stopped early for trace {trace_id}")
        return observations

    def get_scores(self, trace_id: str) -> List[Dict]:
        """Get all scores for a trace."""
        try:
            response = self.client.api.scores.get_many(
                trace_id=trace_id,
                request_options={
                    "timeout_in_seconds": get_langfuse_request_timeout_seconds(),
                },
            )
            if hasattr(response, 'data'):
                return [self._normalize_item(score) for score in response.data]
            if hasattr(response, 'items'):
                return [self._normalize_item(score) for score in response.items]
            return []
        except Exception:
            return []

    def extract_complete_trace(self, trace_id: str) -> Dict:
        """
        Extract complete trace data including observations and scores
        Returns structured data for caching
        """
        # Fetch all data
        observations = self.get_observations(trace_id)
        trace = self.get_trace_details(trace_id, observations)
        scores = self.get_scores(trace_id)
        domain_observations = [
            observation
            for observation in observations
            if observation.get("id") != trace.get("rootObservationId")
        ]
        domain_envelope = DomainEnvelopeTraceAnalyzer.analyze(
            trace,
            cast(List[Mapping[str, Any]], domain_observations),
            scores=cast(List[Mapping[str, Any]], scores),
        )

        # Build structured response
        trace_fragment = trace_id[:8] if len(trace_id) >= 8 else trace_id

        # Aggregate tokens and costs from observations
        total_tokens = 0
        total_cost = 0
        for obs in observations:
            # Sum tokens from observation usage
            accounting = usage_cost_summary(obs)
            total_tokens += accounting["total_tokens"]
            total_cost += accounting["total_cost"]

        trace["usage"] = {"total": total_tokens}
        trace["totalCost"] = total_cost
        trace["calculatedTotalCost"] = total_cost

        # Get duration in seconds (trace.latency is already in seconds)
        duration_seconds = float(trace.get("latency") or 0)

        return {
            "raw_trace": trace,
            "observations": observations,
            "scores": scores,
            "trace_id_short": trace_fragment,
            # Basic metadata for quick access
            "metadata": {
                "trace_id": trace_id,
                "trace_name": trace.get("name"),
                "duration_seconds": duration_seconds,
                "total_cost": total_cost,
                "total_tokens": total_tokens,
                "observation_count": len(observations),
                "score_count": len(scores),
                "timestamp": trace.get("timestamp"),
                "domain_envelope": DomainEnvelopeTraceAnalyzer.compact(domain_envelope),
            }
        }
