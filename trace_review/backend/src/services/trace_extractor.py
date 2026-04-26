"""
Langfuse Trace Extraction Service
Fetches and processes trace data from Langfuse API
"""
import logging
from typing import Any, Dict, List, Optional
from langfuse import Langfuse
import requests
from requests.auth import HTTPBasicAuth
from ..config import get_trace_source_runtime_config

logger = logging.getLogger(__name__)
OBSERVATION_FIELDS = "core,basic,time,io,metadata,model,usage,prompt,metrics"
SESSION_TRACE_LIST_LIMIT = 100
SESSION_TRACE_LIST_TIMEOUT_SECONDS = 30


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
        self.host = source_config["host"]
        self.public_key = source_config["public_key"]
        self.secret_key = source_config["secret_key"]

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
        if hasattr(item, "dict"):
            return item.dict()
        return item

    def _embedded_collection(self, trace: Optional[Dict], key: str) -> Optional[List[Dict]]:
        """Return embedded trace collections when the trace payload already includes them."""
        if not trace or key not in trace:
            return None
        return [self._normalize_item(item) for item in (trace.get(key) or [])]

    def get_trace_details(self, trace_id: str) -> Dict:
        """Get detailed trace information with all fields"""
        trace = self.client.api.trace.get(trace_id)
        return self._normalize_item(trace)

    def list_session_traces(self, session_id: str, limit: int = SESSION_TRACE_LIST_LIMIT) -> Dict[str, Any]:
        """List Langfuse traces for a session without exposing credentials."""
        traces: List[Dict[str, Any]] = []
        page = 1
        meta: Dict[str, Any] = {}
        endpoint = f"{self.host.rstrip('/')}/api/public/traces"
        auth = HTTPBasicAuth(self.public_key, self.secret_key)

        while True:
            params = {
                "sessionId": session_id,
                "limit": limit,
                "page": page,
                "orderBy": "timestamp.asc",
            }

            try:
                response = requests.get(
                    endpoint,
                    params=params,
                    auth=auth,
                    timeout=SESSION_TRACE_LIST_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                payload = response.json()
            except requests.RequestException as exc:
                raise RuntimeError(
                    f"Unable to list Langfuse traces for session {session_id} "
                    f"from {self.source}: {exc.__class__.__name__}"
                ) from exc
            except ValueError as exc:
                raise RuntimeError(
                    f"Langfuse returned invalid JSON while listing session {session_id} "
                    f"from {self.source}"
                ) from exc

            page_traces = payload.get("data") or []
            traces.extend(self._normalize_item(trace) for trace in page_traces)
            meta = payload.get("meta") or {}

            total_pages = meta.get("totalPages")
            if total_pages is None:
                total_pages = page
            if page >= total_pages:
                break
            page += 1

        return {
            "session_id": session_id,
            "source": self.source,
            "traces": traces,
            "meta": meta,
        }

    def get_observations(self, trace_id: str, trace: Optional[Dict] = None) -> List[Dict]:
        """Get all observations for a trace."""
        embedded = self._embedded_collection(trace, "observations")
        if embedded is not None:
            return embedded

        observations: List[Dict] = []
        cursor: Optional[str] = None

        while True:
            response = self.client.api.observations.get_many(
                trace_id=trace_id,
                fields=OBSERVATION_FIELDS,
                limit=1000,
                cursor=cursor,
            )
            response_data = getattr(response, "data", None)
            if response_data:
                observations.extend(self._normalize_item(obs) for obs in response_data)

            meta = getattr(response, "meta", None)
            cursor = getattr(meta, "cursor", None) if meta is not None else None
            if not cursor:
                break

        return observations

    def get_scores(self, trace_id: str, trace: Optional[Dict] = None) -> List[Dict]:
        """Get all scores for a trace."""
        embedded = self._embedded_collection(trace, "scores")
        if embedded is not None:
            return embedded

        try:
            response = self.client.api.scores.get_many(trace_id=trace_id)
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
        trace = self.get_trace_details(trace_id)
        observations = self.get_observations(trace_id, trace=trace)
        scores = self.get_scores(trace_id, trace=trace)

        # Build structured response
        trace_fragment = trace_id[:8] if len(trace_id) >= 8 else trace_id

        # Aggregate tokens and costs from observations
        total_tokens = 0
        total_cost = 0
        for obs in observations:
            # Sum tokens from observation usage
            obs_usage = obs.get("usage") or {}
            if isinstance(obs_usage, dict):
                total_tokens += obs_usage.get("total", 0)

            # Sum costs from observation
            obs_cost = obs.get("calculatedTotalCost") or 0
            total_cost += obs_cost

        # Fallback to trace-level data if observations don't have the data
        if total_cost == 0:
            total_cost = trace.get("calculatedTotalCost") or 0

        if total_tokens == 0:
            usage = trace.get("usage") or {}
            total_tokens = usage.get("total", 0) if isinstance(usage, dict) else 0

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
                "timestamp": trace.get("timestamp")
            }
        }
