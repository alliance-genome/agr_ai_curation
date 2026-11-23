"""The removed-file refusal through the real Prepare pipeline (KANBAN-1976 / ALL-1460, P10b).

Every Prepare path (chat prep, Review & Curate's bootstrap, flow handoffs and batches)
runs ``run_post_curation_pipeline``. These tests run it on real PostgreSQL with a
real envelope whose evidence quotes a file that is not a ready file of its paper.
"""

# ruff: noqa: F811 - pytest fixtures are imported from the chat review adapter tests and used by name.

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from src.lib.validation_history import store
from tests.integration.persistence.test_chat_review_adapter import migrated as migrated  # noqa: F401
from tests.integration.persistence.test_chat_review_adapter import planned as planned  # noqa: F401
from tests.integration.persistence.test_chat_review_adapter import scope as scope  # noqa: F401


def _request(item, *, quoted_file_id: str, execution_mode=None):
    """A Prepare request for the fixture's real envelope, its evidence now quoting ``quoted_file_id``."""

    from src.lib.curation_workspace import pipeline
    from src.lib.curation_workspace.curation_prep_constants import CURATION_PREP_AGENT_ID
    from src.lib.curation_workspace.models import CurationExtractionResultRecord, DomainEnvelopeModel
    from src.schemas.curation_prep import CurationPrepAgentOutput
    from src.schemas.curation_workspace import CurationExtractionSourceKind

    from src.schemas.domain_envelope import DomainEnvelopeStatus

    with store.SessionLocal.begin() as db:
        # A stored result of this paper whose evidence names the file (the shape an extraction writes).
        row = DomainEnvelopeModel(
            envelope_id=f"env-{uuid4()}", project_key="example", domain_pack_key="example", adapter_key="fixture",
            source_payload_hash="a" * 64, status=DomainEnvelopeStatus.EXTRACTED,
            document_id=UUID(item.scope.document_id), source_extraction_result_id=str(uuid4()),
            envelope_json={"objects": [{"evidence": [{"file_id": quoted_file_id, "quote": "Made-up."}]}]},
        )
        db.add(row)
        db.flush()
        prep = CurationPrepAgentOutput.model_validate({"envelope_refs": [{
            "envelope_id": row.envelope_id, "envelope_revision": row.revision,
            "source_extraction_result_id": str(uuid4()),
            "domain_pack_id": item.pack.pack_id, "review_row_count": 0, "retired_record_count": 0}],
            "review_row_count": 0, "run_metadata": {"model_name": "deterministic_programmatic_mapper_v1",
                "token_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}})
        record = CurationExtractionResultRecord(
            document_id=UUID(item.scope.document_id), adapter_key="fixture", agent_key=CURATION_PREP_AGENT_ID,
            source_kind=CurationExtractionSourceKind.CHAT, user_id=item.scope.owner_id, candidate_count=0,
            payload_json=prep.model_dump(mode="json"),
            extraction_metadata={"final_run_metadata": prep.run_metadata.model_dump(mode="json")},
        )
        db.add(record)
        db.flush()
        record_id = str(record.id)
    extra = {} if execution_mode is None else {"execution_mode": execution_mode}
    return pipeline.PostCurationPipelineRequest(
        prep_output=prep, document_id=item.scope.document_id, source_kind=CurationExtractionSourceKind.CHAT,
        adapter_key="fixture", user_id=item.scope.owner_id, created_by_id=item.scope.owner_id,
        active_groups=(), prep_extraction_result_id=record_id, **extra,
    )


def _bound_session(envelope_id: str) -> object:
    from src.lib.curation_workspace.models import DomainEnvelopeModel

    with store.SessionLocal() as db:
        return db.get(DomainEnvelopeModel, envelope_id).session_id


def test_the_pipeline_refuses_a_result_quoting_a_file_that_is_not_a_ready_file_of_the_paper(planned):
    from src.lib.curation_workspace import pipeline
    from src.lib.curation_workspace.removed_file_gate import ResultQuotesRemovedFile

    item = planned
    request = _request(item, quoted_file_id=str(uuid4()))  # a file that is gone
    with pytest.raises(ResultQuotesRemovedFile):
        with store.SessionLocal.begin() as db:
            pipeline.execute_post_curation_pipeline(request, db=db)
    assert _bound_session(request.prep_output.envelope_refs[0].envelope_id) is None  # nothing was prepared


@pytest.mark.asyncio
async def test_a_background_preparation_is_refused_before_it_is_scheduled(planned):
    from src.lib.curation_workspace import pipeline
    from src.lib.curation_workspace.removed_file_gate import ResultQuotesRemovedFile

    scheduled = []

    class Recorder:
        def schedule(self, work, *, task_name):
            scheduled.append(task_name)
            return task_name

    item = planned
    request = _request(item, quoted_file_id=str(uuid4()), execution_mode=pipeline.PipelineExecutionMode.ASYNC)
    with pytest.raises(ResultQuotesRemovedFile):
        await pipeline.run_post_curation_pipeline(
            request, dependencies=pipeline.PostCurationPipelineDependencies(task_scheduler=Recorder()),
        )
    assert scheduled == []
