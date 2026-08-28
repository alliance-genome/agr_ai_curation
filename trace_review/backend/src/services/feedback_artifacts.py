"""Fetch stored feedback trace artifacts from the main AI Curation backend."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Mapping

import requests

logger = logging.getLogger(__name__)

BACKEND_URL_ENV = "AI_CURATION_BACKEND_URL"
SERVICE_TOKEN_ENV = "TRACE_REVIEW_INTERNAL_API_TOKEN"
REQUEST_TIMEOUT_SECONDS = 10


def feedback_artifacts_contain_trace(
    artifacts: Mapping[str, Any] | None,
    trace_id: str,
) -> bool:
    """Return whether an owner-authorized feedback response contains a trace ID."""
    if not isinstance(artifacts, Mapping):
        return False
    normalized_trace_id = str(trace_id or "").strip()
    if not normalized_trace_id:
        return False

    trace_ids = artifacts.get("trace_ids")
    if isinstance(trace_ids, list) and normalized_trace_id in {
        str(item or "").strip() for item in trace_ids
    }:
        return True

    trace_data = artifacts.get("trace_data")
    traces = trace_data.get("traces") if isinstance(trace_data, Mapping) else None
    if not isinstance(traces, list):
        return False
    return any(
        isinstance(trace, Mapping)
        and str(trace.get("trace_id") or trace.get("id") or "").strip()
        == normalized_trace_id
        for trace in traces
    )


def _backend_url() -> str | None:
    configured = os.getenv(BACKEND_URL_ENV, "").strip()
    if not configured:
        return None
    return configured.rstrip("/")


def fetch_feedback_trace_artifacts(
    feedback_id: str | None,
    *,
    caller_sub: str | None = None,
    caller_email: str | None = None,
) -> Dict[str, Any] | None:
    """Return stored feedback trace artifacts when TraceReview is configured to fetch them."""

    if not feedback_id:
        return None

    backend_url = _backend_url()
    token = os.getenv(SERVICE_TOKEN_ENV, "").strip()
    if not backend_url or not token:
        return {
            "feedback_id": feedback_id,
            "status": "not_configured",
            "trace_data": None,
        }

    endpoint = f"{backend_url}/api/feedback/{feedback_id}/trace-artifacts"
    try:
        headers = {"Authorization": f"Bearer {token}"}
        if caller_sub:
            headers["X-AGR-Trusted-Caller-Sub"] = caller_sub
        if caller_email:
            headers["X-AGR-Trusted-Caller-Email"] = caller_email
        response = requests.get(
            endpoint,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning(
            "Failed to fetch feedback trace artifacts for %s: %s",
            feedback_id,
            exc.__class__.__name__,
        )
        return {
            "feedback_id": feedback_id,
            "status": "unavailable",
            "trace_data": None,
            "error": exc.__class__.__name__,
        }

    if response.status_code == 404:
        return {
            "feedback_id": feedback_id,
            "status": "not_found",
            "trace_data": None,
        }
    if response.status_code >= 400:
        logger.warning(
            "Feedback trace artifact fetch failed for %s with HTTP %s",
            feedback_id,
            response.status_code,
        )
        return {
            "feedback_id": feedback_id,
            "status": "unavailable",
            "trace_data": None,
            "http_status": response.status_code,
        }

    try:
        payload = response.json()
    except ValueError:
        return {
            "feedback_id": feedback_id,
            "status": "unavailable",
            "trace_data": None,
            "error": "invalid_json",
        }

    if not isinstance(payload, dict):
        return {
            "feedback_id": feedback_id,
            "status": "unavailable",
            "trace_data": None,
            "error": "invalid_payload",
        }

    payload["status"] = "available" if payload.get("trace_data") else "missing"
    return payload
