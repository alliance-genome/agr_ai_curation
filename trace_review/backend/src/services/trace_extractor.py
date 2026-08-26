"""
Langfuse Trace Extraction Service
Fetches and processes trace data from Langfuse API
"""
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, cast
from langfuse import Langfuse
from ..analyzers.domain_envelopes import DomainEnvelopeTraceAnalyzer
from ..config import (
    get_langfuse_observation_page_limit,
    get_langfuse_request_timeout_seconds,
    get_trace_source_runtime_config,
)
from .langfuse_run_reconstruction import usage_cost_summary

logger = logging.getLogger(__name__)
OBSERVATION_FIELDS = "core,basic,time,io,metadata,model,usage,trace_context"
SESSION_OBSERVATION_FIELDS = "core,basic,time,trace_context"
SESSION_TRACE_LIST_LIMIT = 100


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
            raise RuntimeError(f"Langfuse returned no v2 observations for trace {trace_id}")

        ordered = sorted(
            observations,
            key=lambda item: (
                str(item.get("startTime") or item.get("start_time") or ""),
                str(item.get("id") or ""),
            ),
        )
        roots = [
            item for item in ordered
            if item.get("isRootObservation") is True
            or item.get("is_root_observation") is True
            or not (item.get("parentObservationId") or item.get("parent_observation_id"))
        ]
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
            "latency": root.get("latency") or 0,
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
        limit: int = SESSION_TRACE_LIST_LIMIT,
        from_timestamp: Optional[datetime] = None,
        to_timestamp: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """List Langfuse traces using indexed fields and metadata filters."""
        filters: List[Dict[str, Any]] = []
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

        page = 1
        traces: List[Dict[str, Any]] = []
        meta: Dict[str, Any] = {}
        filter_json = json.dumps(filters) if filters else None

        while len(traces) < limit:
            page_limit = min(100, limit - len(traces))
            response = self.client.api.trace.list(
                page=page,
                limit=page_limit,
                session_id=session_id,
                user_id=user_id,
                name=name,
                from_timestamp=from_timestamp,
                to_timestamp=to_timestamp,
                order_by="timestamp.asc",
                filter=filter_json,
            )
            response_data = getattr(response, "data", None) or []
            traces.extend(self._normalize_item(trace) for trace in response_data)

            response_meta = getattr(response, "meta", None)
            meta = self._normalize_item(response_meta) if response_meta is not None else {}
            total_pages = meta.get("totalPages") or meta.get("total_pages")
            if total_pages is None:
                break
            if page >= total_pages:
                break
            page += 1

        return {
            "source": self.source,
            "traces": traces,
            "meta": meta,
            "query": {
                "session_id": session_id,
                "user_id": user_id,
                "name": name,
                "document_id": document_id,
                "run_id": run_id,
                "extraction_id": extraction_id,
                "limit": limit,
                "from_timestamp": from_timestamp.isoformat() if from_timestamp else None,
                "to_timestamp": to_timestamp.isoformat() if to_timestamp else None,
            },
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

    def get_observations(self, trace_id: str) -> List[Dict]:
        """Get every observation through the cursor-paginated v2 API."""
        observations: List[Dict] = []
        cursor: Optional[str] = None
        seen_cursors = set()
        seen_observation_ids = set()
        page_limit = get_langfuse_observation_page_limit()
        request_options = {
            "timeout_in_seconds": get_langfuse_request_timeout_seconds(),
        }

        while True:
            response = self.client.api.observations.get_many(
                trace_id=trace_id,
                fields=OBSERVATION_FIELDS,
                limit=page_limit,
                cursor=cursor,
                request_options=request_options,
            )
            response_data = getattr(response, "data", None)
            if response_data:
                for item in response_data:
                    observation = self._normalize_v2_observation(item)
                    observation_id = observation.get("id")
                    if observation_id and observation_id in seen_observation_ids:
                        continue
                    if observation_id:
                        seen_observation_ids.add(observation_id)
                    observations.append(observation)

            meta = getattr(response, "meta", None)
            cursor = getattr(meta, "cursor", None) if meta is not None else None
            if not cursor:
                break
            if cursor in seen_cursors:
                raise RuntimeError(
                    f"Langfuse repeated observation cursor for trace {trace_id}"
                )
            seen_cursors.add(cursor)

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
