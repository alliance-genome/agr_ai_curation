"""Explicit published-source doubles for rendering/saving unit tests only.

These tests exercise presentation after acceptance. Real selection/publication
and rejection of unreviewed sources remain in the authority persistence suite
and test_output_projection's gate tests; this helper is opt-in, never autouse.
"""

import pytest
from types import SimpleNamespace


def mark_reviewed_output_bundle(bundle):
    bundle.document_id = "00000000-0000-0000-0000-000000000001"
    bundle.authorization_user_id = "unit-curator"
    for artifact in bundle.artifacts:
        artifact.review_session_id = "unit-review-session"
        artifact.selected_projection = {"fact_id": "reviewed-output-unit-fixture"}
    return bundle


def build_reviewed_output_bundle(**kwargs):
    from src.lib.flows.output_projection import build_flow_output_artifact_bundle
    return mark_reviewed_output_bundle(build_flow_output_artifact_bundle(**kwargs))


@pytest.fixture
def reviewed_output_reader(monkeypatch):
    def read(scope, reference, *, finalized=False, **kwargs):
        assert reference == {"fact_id": "reviewed-output-unit-fixture"}
        assert finalized is True
        return SimpleNamespace(metadata={}, extracted_objects=[]), {"ready": True}
    monkeypatch.setattr("src.lib.validation_history.projection.read_published_projection", read)
