"""Unit tests for feedback debug URL builders."""

from src.lib.feedback.debug_links import (
    build_feedback_debug_url,
    build_trace_review_session_bundle_url,
)


def test_build_feedback_debug_url_encodes_path_segment():
    assert build_feedback_debug_url("feedback/with spaces?x=1") == (
        "/api/feedback/feedback%2Fwith%20spaces%3Fx%3D1/debug"
    )


def test_build_trace_review_session_bundle_url_encodes_path_segment():
    assert build_trace_review_session_bundle_url("session/with spaces?x=1") == (
        "/api/traces/sessions/session%2Fwith%20spaces%3Fx%3D1/export?source=remote"
    )


def test_debug_url_builders_keep_empty_path_segments_stable():
    assert build_feedback_debug_url("") == "/api/feedback//debug"
    assert build_trace_review_session_bundle_url("") == (
        "/api/traces/sessions//export?source=remote"
    )
