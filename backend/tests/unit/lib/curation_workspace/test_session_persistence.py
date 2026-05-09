"""Unit tests for curation workspace persistence primitives."""

from __future__ import annotations

import pytest

from src.lib.curation_workspace.session_persistence import (
    _validate_prepared_candidate_projection_ref,
)
from src.lib.curation_workspace.session_types import PreparedCandidateInput
from src.schemas.curation_workspace import (
    CurationCandidateSource,
    CurationCandidateStatus,
)


def _candidate_input(**overrides) -> PreparedCandidateInput:
    payload = {
        "source": CurationCandidateSource.EXTRACTED,
        "status": CurationCandidateStatus.PENDING,
        "order": 0,
        "adapter_key": "reference_adapter",
    }
    payload.update(overrides)
    return PreparedCandidateInput(**payload)


def test_prepared_candidate_projection_ref_allows_absent_or_complete_refs():
    _validate_prepared_candidate_projection_ref(_candidate_input())
    _validate_prepared_candidate_projection_ref(
        _candidate_input(
            envelope_id="env-1",
            object_id="object-1",
            envelope_revision=1,
        )
    )


def test_prepared_candidate_projection_ref_rejects_partial_or_invalid_refs():
    with pytest.raises(ValueError, match="must include envelope_id"):
        _validate_prepared_candidate_projection_ref(
            _candidate_input(envelope_id="env-1")
        )

    with pytest.raises(ValueError, match="greater than zero"):
        _validate_prepared_candidate_projection_ref(
            _candidate_input(
                envelope_id="env-1",
                object_id="object-1",
                envelope_revision=0,
            )
        )
