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


def test_prepared_candidates_inherit_envelope_receipt_not_combined_prep_identity():
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from unittest.mock import Mock
    from uuid import uuid4
    from src.lib.curation_workspace.models import CurationCandidate, DomainEnvelopeModel
    from src.lib.curation_workspace.session_persistence import (
        _persist_prepared_candidates,
    )
    from src.schemas.agent_execution_revision import AgentExecutionReceipt

    receipt = AgentExecutionReceipt(
        agent_id=uuid4(),
        agent_key="ca_fixture",
        agent_revision_id=uuid4(),
        revision=3,
        fingerprint="sha256:" + "a" * 64,
        output_contract={
            "output_state": "structured_extraction",
            "output_mode": "unprofiled_generic",
        },
    )
    db = Mock()
    db.get.return_value = SimpleNamespace(
        execution_receipt=receipt.model_dump(mode="json"), envelope_json={}
    )

    def assign_id(row):
        row.id = uuid4()

    db.add.side_effect = assign_id
    prep_id = str(uuid4())
    _persist_prepared_candidates(
        db,
        SimpleNamespace(id=uuid4()),
        [
            _candidate_input(
                envelope_id="env-1",
                object_id="object-1",
                envelope_revision=1,
                extraction_result_id=prep_id,
            ),
        ],
        prepared_at=datetime.now(timezone.utc),
    )
    db.get.assert_called_once_with(DomainEnvelopeModel, "env-1")
    candidate = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], CurationCandidate)
    )
    assert candidate.agent_revision_id == receipt.agent_revision_id
    assert candidate.execution_receipt == receipt.model_dump(mode="json")
    assert str(candidate.extraction_result_id) == prep_id
    assert candidate.profile_key is None
