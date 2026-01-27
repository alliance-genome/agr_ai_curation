"""
Langfuse Trace Extraction Service
Fetches and processes trace data from Langfuse API
"""
import os
import json
import logging
from typing import Dict, List, Optional
from langfuse import Langfuse
from ..config import (
    get_langfuse_host, get_langfuse_public_key, get_langfuse_secret_key,
    get_langfuse_local_host, get_langfuse_local_public_key, get_langfuse_local_secret_key
)

logger = logging.getLogger(__name__)


class TraceExtractor:
    """Service for extracting trace data from Langfuse"""

    def __init__(self, source: str = "remote"):
        """
        Initialize with API credentials based on source

        Args:
            source: "remote" (default) or "local"
        """
        if source == "local":
            self.host = get_langfuse_local_host()
            self.public_key = get_langfuse_local_public_key()
            self.secret_key = get_langfuse_local_secret_key()

            if not self.public_key or not self.secret_key:
                raise ValueError("LANGFUSE_LOCAL_PUBLIC_KEY and LANGFUSE_LOCAL_SECRET_KEY must be set for local source")
        else:
            # Default to remote
            self.host = get_langfuse_host()
            self.public_key = get_langfuse_public_key()
            self.secret_key = get_langfuse_secret_key()

            if not self.public_key or not self.secret_key:
                raise ValueError("LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must be set for remote source")

        # Log configuration for troubleshooting
        logger.debug(f"TraceExtractor initialized: source={source}, host={self.host}, pk={self.public_key[:20] if self.public_key else 'None'}...")

        # Initialize Langfuse SDK client
        self.client = Langfuse(
            public_key=self.public_key,
            secret_key=self.secret_key,
            host=self.host
        )

    def get_trace_details(self, trace_id: str) -> Dict:
        """Get detailed trace information with all fields"""
        trace = self.client.api.trace.get(trace_id)
        return trace.dict() if hasattr(trace, 'dict') else trace

    def get_observations(self, trace_id: str) -> List[Dict]:
        """Get all observations for a trace"""
        response = self.client.api.observations.get_many(trace_id=trace_id)

        observations = []
        if hasattr(response, 'data'):
            # Fetch each observation individually for complete data
            for obs in response.data:
                obs_id = obs.id if hasattr(obs, 'id') else None

                if obs_id:
                    try:
                        full_obs = self.client.api.observations.get(obs_id)
                        obs_dict = full_obs.dict() if hasattr(full_obs, 'dict') else full_obs
                        observations.append(obs_dict)
                    except Exception:
                        # Fallback to truncated version
                        obs_dict = obs.dict() if hasattr(obs, 'dict') else obs
                        observations.append(obs_dict)
                else:
                    obs_dict = obs.dict() if hasattr(obs, 'dict') else obs
                    observations.append(obs_dict)

        return observations

    def get_scores(self, trace_id: str) -> List[Dict]:
        """Get all scores for a trace"""
        try:
            response = self.client.api.score_v_2.get(trace_id=trace_id)
            if hasattr(response, 'items'):
                return [score.dict() if hasattr(score, 'dict') else score for score in response.items]
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
        observations = self.get_observations(trace_id)
        scores = self.get_scores(trace_id)

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

