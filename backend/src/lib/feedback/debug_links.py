"""Canonical debug links for feedback and TraceReview handoffs."""

from urllib.parse import quote


def _path_segment(value: str) -> str:
    """Encode one URL path segment without preserving slashes."""

    return quote(str(value), safe="")


def build_feedback_debug_url(feedback_id: str) -> str:
    """Return the canonical AI Curation feedback debug detail path."""

    return f"/api/feedback/{_path_segment(feedback_id)}/debug"


def build_trace_review_session_bundle_url(session_id: str) -> str:
    """Return the canonical TraceReview session bundle export path."""

    return f"/api/traces/sessions/{_path_segment(session_id)}/export?source=remote"
