"""A topic-tag proposal has no validators, and its review can still finish (ALL-1461).

The review waits for one explicit acceptance of the extraction itself.
"""

from datetime import datetime, timezone
from pathlib import Path
import sys
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.lib.curation_workspace.adapter_registry import resolve_curation_domain_pack_by_id
from src.lib.curation_workspace.domain_envelope_normalization import (
    domain_envelope_from_extraction_result,
)
from src.lib.domain_packs.validator_dispatch import (
    ValidatorRuntimeContext,
    prepare_validator_dispatch,
)
from src.lib.openai_agents.extraction_builder_workspace import ExtractionBuilderWorkspace
from src.lib.domain_envelopes.persistence import DomainEnvelopeCheckpointRequest
from src.lib.validation_history import runtime, selections, store
from src.lib.validation_history.projection import latest_publication_reference
from src.lib.validation_review import admission, decisions
from src.lib.validation_review.validator_adapter import ValidatorAdapter, save_plan
from src.schemas.curation_workspace import CurationExtractionResultRecord
from tests.cancellation_test_support import install_cancel_reader
from tests.integration.persistence.test_validation_history import (
    migrated as migrated,
    scope as scope,
)

BACKEND_ROOT = Path(__file__).resolve().parents[3]
ALLIANCE_PYTHON_SRC = BACKEND_ROOT.parent / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.tools import topic_entity_tag_builder_tools as tools  # noqa: E402


@pytest.mark.asyncio
async def test_review_of_a_proposal_without_validators_reaches_a_ready_state(
    scope, migrated, monkeypatch
):
    install_cancel_reader(monkeypatch, AsyncMock(return_value=False))
    workspace = ExtractionBuilderWorkspace(
        run_id=f"topic-tag-{uuid4()}", agent_id="gene_expression_topic_tag"
    )
    monkeypatch.setattr(tools, "get_active_extraction_builder_workspace", lambda: workspace)
    monkeypatch.setattr(tools, "get_active_evidence_records_snapshot", lambda: [])
    monkeypatch.setattr(tools, "write_extraction_trace_event", lambda **_event: None)
    staged = tools._stage_topic_entity_tag_impl(negated=True, rationale="No expression data.")
    assert tools._finalize_topic_entity_tag_extraction_impl([staged.data["candidate_id"]]).status == "ok"
    record = CurationExtractionResultRecord.model_validate(
        {
            "extraction_result_id": str(uuid4()),
            "document_id": scope.document_id,
            "adapter_key": "topic_entity_tag",
            "agent_key": "gene_expression_topic_tag",
            "source_kind": "flow",
            "candidate_count": 1,
            "payload_json": workspace.finalization.payload,
            "created_at": datetime.now(timezone.utc),
            "metadata": {},
        }
    )
    envelope = domain_envelope_from_extraction_result(record, stored=False)
    envelope = runtime.capture_extraction(
        scope=scope,
        producer_id=uuid4().hex,
        typed=envelope.model_dump(mode="json"),
        envelope=envelope,
        provenance={},
    )
    pack = resolve_curation_domain_pack_by_id(envelope.domain_pack_id)
    context = ValidatorRuntimeContext(
        user_id=scope.owner_id, document_id=scope.document_id, authenticated_groups=("WB",)
    )
    plan = prepare_validator_dispatch(envelope, pack, runtime_context=context)
    plan_ref = save_plan(plan, context=context)
    artifact_id = plan.history_binding.artifact_id
    episode_id = plan.envelope.metadata["review_episode_id"]
    scope_id = admission.create_scope(
        scope, operation_id=uuid4().hex, kind="discovered_document_run", targets=0, episodes=0
    )
    admission.attach_episode(scope, artifact_id=artifact_id, episode_id=episode_id, scope_id=scope_id)
    adapter = ValidatorAdapter(scope, artifact_id, episode_id, plan_ref, authenticated_groups=("WB",))

    assert adapter.plan["initial_requests"] == []
    await adapter.initial()
    manifest = selections.read_manifest(scope, artifact_id, episode_id=episode_id)
    assert [blocker["code"] for blocker in manifest["blockers"]] == ["unreviewed"]
    assert admission.status(scope, episode_id)["ready"] is False

    unit = manifest["units"][0]
    with store.SessionLocal() as db:
        baseline = store._artifact(db, artifact_id, scope).baseline_sha
    response = decisions.record_decisions(
        decisions.ReviewContext(scope, episode_id, "supervisor"),
        checkpoint_request=DomainEnvelopeCheckpointRequest(
            project_key="fixture",
            envelope=plan.envelope,
            expected_revision=0,
            document_id=scope.document_id,
            adapter_key="topic_entity_tag",
            source_extraction_result_id=uuid4(),
        ),
        operation_id="accept-extraction",
        entries=[
            {
                "target_id": unit["target_id"],
                "decision_unit_id": unit["target_id"],
                "expected_version": unit["version"],
                "action": "accept",
                "reason": "The proposal reflects the paper.",
                "extraction_ref": baseline,
            }
        ],
    )

    assert response["ready"] is True
    assert admission.status(scope, episode_id)["ready"] is True
    assert latest_publication_reference(scope, artifact_id) is not None


@pytest.mark.asyncio
async def test_negated_proposal_opens_as_a_curation_session_and_can_be_signed_off(
    scope, migrated, monkeypatch
):
    from sqlalchemy import select

    from src.lib.curation_workspace.bootstrap_service import run_flow_curation_handoff
    from src.lib.curation_workspace.extraction_results import persist_extraction_result
    from src.lib.curation_workspace.models import CurationCandidate
    from src.lib.curation_workspace.session_mutation_service import decide_candidate
    from src.models.sql.database import SessionLocal
    from src.schemas.curation_workspace import (
        CurationCandidateAction,
        CurationCandidateDecisionRequest,
        CurationExtractionPersistenceRequest,
        CurationExtractionSourceKind,
    )

    workspace = ExtractionBuilderWorkspace(
        run_id=f"topic-tag-{uuid4()}", agent_id="gene_expression_topic_tag"
    )
    monkeypatch.setattr(tools, "get_active_extraction_builder_workspace", lambda: workspace)
    monkeypatch.setattr(tools, "get_active_evidence_records_snapshot", lambda: [])
    monkeypatch.setattr(tools, "write_extraction_trace_event", lambda **_event: None)
    staged = tools._stage_topic_entity_tag_impl(negated=True, rationale="No expression data.")
    assert tools._finalize_topic_entity_tag_extraction_impl([staged.data["candidate_id"]]).status == "ok"

    flow_run_id = str(uuid4())
    origin_session_id = str(uuid4())
    with SessionLocal() as db:
        record = persist_extraction_result(
            CurationExtractionPersistenceRequest(
                document_id=scope.document_id,
                agent_key="gene_expression_topic_tag",
                adapter_key="topic_entity_tag",
                source_kind=CurationExtractionSourceKind.FLOW,
                flow_run_id=flow_run_id,
                origin_session_id=origin_session_id,
                user_id=scope.owner_id,
                candidate_count=1,
                payload_json=workspace.finalization.payload,
            ),
            db=db,
        ).extraction_result
        db.commit()

        result = await run_flow_curation_handoff(
            extraction_results=[record],
            document_id=scope.document_id,
            runner_user_id=scope.owner_id,
            active_groups=("WB",),
            flow_run_id=flow_run_id,
            origin_session_id=origin_session_id,
            conversation_summary="One negated proposal",
            db=db,
        )

        assert len(result.review_session_ids) == 1
        session_id = result.review_session_ids[0]
        candidates = list(
            db.scalars(select(CurationCandidate).where(CurationCandidate.session_id == session_id))
        )
        assert len(candidates) == 1

        from src.lib.curation_workspace.session_queries import get_session_workspace

        workspace_payload = get_session_workspace(db, session_id)
        assert workspace_payload.workspace.candidates[0].has_active_validators is False

        decision = decide_candidate(
            db,
            candidates[0].id,
            CurationCandidateDecisionRequest(
                session_id=str(session_id),
                candidate_id=str(candidates[0].id),
                action=CurationCandidateAction.ACCEPT,
            ),
            {"sub": scope.owner_id},
        )
        db.rollback()

    assert decision.candidate.status.value == "accepted"
