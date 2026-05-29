"""Fetch stored feedback trace artifacts from the main AI Curation backend."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

import requests

logger = logging.getLogger(__name__)

BACKEND_URL_ENV = "AI_CURATION_BACKEND_URL"
SERVICE_TOKEN_ENV = "TRACE_REVIEW_INTERNAL_API_TOKEN"
REQUEST_TIMEOUT_SECONDS = 10


def _backend_url() -> str | None:
    configured = os.getenv(BACKEND_URL_ENV, "").strip()
    if not configured:
        return None
    return configured.rstrip("/")


def fetch_feedback_trace_artifacts(feedback_id: str | None) -> Dict[str, Any] | None:
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
        response = requests.get(
            endpoint,
            headers={"Authorization": f"Bearer {token}"},
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
